//! Bit-exact check against the hash manifests (mGBA whole-episode captures of the carts).
//!
//! For every stream of every FVMV cart found in `GBAV_ROMS`:
//! 1. decode all frames and all audio blocks from power-on state and compare every frame
//!    md5 (16-bit buffer, and bit 15 cleared) and every audio block md5 (int32 synthesis,
//!    s8 PCM) with `<code>_<stream>.{frame,audio}_md5.csv.gz`;
//! 2. cold-start a fresh decoder at every seek point and decode up to the next one,
//!    comparing every frame again.
//!
//! Needs `GBAV_ROMS` and the manifests (`GBAV_FVMV_MANIFESTS`); skips otherwise. Run in
//! release mode: `cargo test --release -p gbav-fvmv --test manifests -- --nocapture`.

mod common;

use gbav_common::md5::hex;
use gbav_common::{Codec, Frame, VideoDecoder};
use gbav_fvmv::{Audio, Fvmv, Stream, Video};
use std::sync::Arc;

#[derive(Default, Debug)]
struct Res {
    frames: usize,
    frames_ok: usize,
    frames_15_ok: usize,
    blocks: usize,
    synth_ok: usize,
    pcm_ok: usize,
    seek_points: usize,
    seek_frames: usize,
    seek_ok: usize,
    extra: usize,
}

fn md5_15(buf: &[u8]) -> String {
    let mut t = buf.to_vec();
    for b in t.iter_mut().skip(1).step_by(2) {
        *b &= 0x7f;
    }
    hex(&t)
}

fn check(code: &str, rom: &[u8], s: usize, mdir: &std::path::Path) -> Res {
    let name = format!("{code}_{s}");
    let fm = common::read_manifest(&mdir.join(format!("{name}.frame_md5.csv.gz")));
    let am = common::read_manifest(&mdir.join(format!("{name}.audio_md5.csv.gz")));
    let stream = Arc::new(Stream::open(rom, &s.to_string()).expect("open stream"));
    let mut r = Res {
        frames: fm.len(),
        blocks: am.len(),
        ..Res::default()
    };

    // 1. whole stream through the trait interface
    let mut v = Fvmv::video(rom, &s.to_string()).unwrap();
    let mut i = 0;
    while let Some(f) = v.next_frame().expect("decode") {
        assert_eq!(f.index as usize, i);
        match fm.get(i) {
            Some(row) => {
                assert_eq!(
                    row[0].parse::<usize>().unwrap(),
                    i,
                    "{name}: manifest order"
                );
                let b = f.manifest_bytes();
                r.frames_ok += (hex(b) == row[1]) as usize;
                r.frames_15_ok += (md5_15(b) == row[2]) as usize;
            }
            None => r.extra += 1,
        }
        i += 1;
    }
    let mut a = Audio::new(stream.clone());
    let mut k = 0;
    while let Some((fr, bl)) = a.decode_next() {
        match am.get(k) {
            Some(row)
                if row[0].parse::<u32>().unwrap() == fr && row[1].parse::<u32>().unwrap() == bl =>
            {
                r.synth_ok += (hex(&a.synth_bytes()) == row[2]) as usize;
                r.pcm_ok += (hex(&a.pcm_bytes()) == row[3]) as usize;
            }
            _ => r.extra += 1,
        }
        k += 1;
    }

    // 2. cold start at every seek point, through to the next one
    let sp = &stream.seek_points;
    r.seek_points = sp.len();
    for (j, &start) in sp.iter().enumerate() {
        let end = sp.get(j + 1).copied().unwrap_or(stream.frames());
        let mut v = Video::new(stream.clone());
        v.seek(start).expect("seek");
        for f in start..end {
            let fr = v.next_frame().expect("decode").expect("frame");
            r.seek_frames += 1;
            r.seek_ok += (hex(fr.manifest_bytes()) == fm[f as usize][1]) as usize;
        }
    }
    println!(
        "{name}: video {}/{} (bit15-cleared {}/{}), audio synth {}/{} pcm {}/{}, \
         seek points {} cold-start frames {}/{}, extra {}",
        r.frames_ok,
        r.frames,
        r.frames_15_ok,
        r.frames,
        r.synth_ok,
        r.blocks,
        r.pcm_ok,
        r.blocks,
        r.seek_points,
        r.seek_ok,
        r.seek_frames,
        r.extra
    );
    r
}

#[test]
fn all_streams_match_manifests() {
    let (Some(rdir), Some(mdir)) = (common::roms_dir(), common::manifests_dir()) else {
        eprintln!("skipping: set GBAV_ROMS and GBAV_FVMV_MANIFESTS");
        return;
    };
    let carts = common::fvmv_carts(&rdir);
    assert!(!carts.is_empty(), "no FVMV carts in {}", rdir.display());
    let t0 = std::time::Instant::now();
    let handles: Vec<_> = carts
        .into_iter()
        .flat_map(|(code, path)| {
            let rom = Arc::new(std::fs::read(&path).unwrap());
            let mdir = mdir.clone();
            let n = Fvmv::streams(&rom).unwrap().len();
            assert_eq!(n, 2, "{code}: expected two streams");
            (0..n).map(move |s| {
                let (code, rom, mdir) = (code.clone(), rom.clone(), mdir.clone());
                std::thread::spawn(move || check(&code, &rom, s, &mdir))
            })
        })
        .collect();
    let mut tot = Res::default();
    for h in handles {
        let r = h.join().unwrap();
        assert_eq!(r.frames_ok, r.frames);
        assert_eq!(r.frames_15_ok, r.frames);
        assert_eq!(r.synth_ok, r.blocks);
        assert_eq!(r.pcm_ok, r.blocks);
        assert_eq!(r.seek_ok, r.frames);
        assert_eq!(r.seek_frames, r.frames);
        assert_eq!(r.extra, 0);
        tot.frames += r.frames;
        tot.blocks += r.blocks;
        tot.seek_points += r.seek_points;
    }
    println!(
        "ALL OK: {} frames, {} audio blocks, {} seek points (cold start), {:.1} s",
        tot.frames,
        tot.blocks,
        tot.seek_points,
        t0.elapsed().as_secs_f64()
    );
}
