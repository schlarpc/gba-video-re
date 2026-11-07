//! Bit-exact check against the hash manifests in `verify/fvmv/` (mGBA whole-episode captures).
//!
//! For every stream of every FVMV cart found in `GBAV_ROMS`:
//! 1. decode all frames from power-on state and compare every frame md5 (16-bit buffer, and
//!    bit 15 cleared) with `<short id>_<stream>.frame_md5.csv.gz`;
//! 2. decode all audio blocks from power-on state and compare them with the per-block audio
//!    manifest (int32 synthesis and s8 PCM md5, keyed by frame and block) when it is present,
//!    else with the ~30 s segment manifest `<stem>.audio_seg_md5.csv.gz`;
//! 3. cold-start a fresh decoder at every seek point and decode up to the next one,
//!    comparing every frame again.
//!
//! Needs `GBAV_ROMS`; skips otherwise. Run in release mode:
//! `cargo test --release -p gbav-fvmv --test manifests -- --nocapture`.

mod common;

use gbav_common::manifest::{check_audio, Check, Manifest};
use gbav_common::md5::hex;
use gbav_common::{Codec, Frame, Rom, VideoDecoder};
use gbav_fvmv::{Fvmv, Stream, Video};
use std::sync::Arc;

#[derive(Default, Debug)]
struct Res {
    frames: usize,
    frames_ok: usize,
    frames_15_ok: usize,
    audio: Option<Check>,
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

fn check(short: &str, rom: &Rom, s: usize) -> Res {
    let sid = s.to_string();
    let name = format!("{short}_{s}");
    let paths = common::stream_manifests("fvmv", short, &sid).expect("verify/ directory");
    let fm = Manifest::load(&paths.frames).unwrap().unwrap_or_else(|| panic!("{name}: no frame manifest")).rows;
    let stream = Arc::new(Stream::open(rom, &sid).expect("open stream"));
    let mut r = Res {
        frames: fm.len(),
        ..Res::default()
    };

    // 1. whole stream through the trait interface
    let mut v = Fvmv::video(rom, &sid).unwrap();
    let mut i = 0;
    while let Some(f) = v.next_frame().expect("decode") {
        assert_eq!(f.index as usize, i);
        match fm.get(i) {
            Some(row) => {
                assert_eq!(row[0].parse::<usize>().unwrap(), i, "{name}: manifest order");
                let b = f.manifest_bytes();
                r.frames_ok += (hex(b) == row[1]) as usize;
                r.frames_15_ok += (md5_15(b) == row[2]) as usize;
            }
            None => r.extra += 1,
        }
        i += 1;
    }

    // 2. audio: per-block manifest if present, else segments
    r.audio = check_audio(|| Fvmv::audio(rom, &sid), &paths, &[]).expect("audio check");

    // 3. cold start at every seek point, through to the next one
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
        "{name}: video {}/{} (bit15-cleared {}/{}), audio {}, seek points {} cold-start frames {}/{}, extra {}",
        r.frames_ok,
        r.frames,
        r.frames_15_ok,
        r.frames,
        r.audio.as_ref().map_or("no manifest".to_string(), |c| c.to_string()),
        r.seek_points,
        r.seek_ok,
        r.seek_frames,
        r.extra
    );
    r
}

#[test]
fn all_streams_match_manifests() {
    if common::roms_dir().is_none() || common::stream_manifests("fvmv", "pokemon1", "0").is_none() {
        eprintln!("skipping: set GBAV_ROMS (and keep verify/ in the source tree or set GBAV_VERIFY)");
        return;
    }
    let carts = common::fvmv_carts();
    if carts.is_empty() {
        eprintln!("skipping: no FVMV carts in GBAV_ROMS");
        return;
    }
    let t0 = std::time::Instant::now();
    let handles: Vec<_> = carts
        .into_iter()
        .flat_map(|(short, rom)| {
            let n = Fvmv::streams(&rom).unwrap().len();
            assert_eq!(n, 2, "{short}: expected two streams");
            (0..n).map(move |s| {
                let (short, rom) = (short.clone(), rom.clone());
                std::thread::spawn(move || check(&short, &rom, s))
            })
        })
        .collect();
    let (mut frames, mut units, mut seek_points, mut audio_kind) = (0, 0, 0, "");
    for h in handles {
        let r = h.join().unwrap();
        assert_eq!(r.frames_ok, r.frames);
        assert_eq!(r.frames_15_ok, r.frames);
        assert_eq!(r.seek_ok, r.frames);
        assert_eq!(r.seek_frames, r.frames);
        assert_eq!(r.extra, 0);
        match &r.audio {
            Some(c) => {
                assert!(c.passed(), "audio: {c}");
                units += c.total;
                audio_kind = c.unit;
            }
            None => eprintln!("audio skipped: no per-block or segment manifest"),
        }
        frames += r.frames;
        seek_points += r.seek_points;
    }
    println!(
        "ALL OK: {frames} frames, {units} audio {audio_kind}, {seek_points} seek points (cold start), {:.1} s",
        t0.elapsed().as_secs_f64()
    );
}
