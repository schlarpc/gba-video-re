//! Whole-stream verification against the emulator hash manifests (GBAV_ROMS gated).
//!
//! Per stream:
//!  * video: one pass from power-on; md5 of every 0xF000-byte slot == manifest yuv_md5, md5
//!    of every displayed BGR555 picture == vram_md5 (a screen equal to the previous one is
//!    accepted only where the cart skipped a late blit: it must equal one of our recent
//!    frames); every seek-table bit offset == the continuous decode's frame start bit;
//!  * seek points: a cold decoder started at every seek point decodes its whole segment
//!    (up to the next seek point) to the manifest slots;
//!  * audio: the cart's played 512-byte blocks (streams.json aggregate digest and/or per-block
//!    manifest), and, with GBAV_VX_PCM_REF, every AFrame's 16 kHz samples and 8-bit block
//!    against container/vxaudio.py.
//!
//! Run with `cargo test --release -p gbav-vx --test streams -- --nocapture`.

mod common;

use common::*;
use gbav_common::{AudioDecoder, VideoDecoder};
use gbav_vx::util::{hex, md5_hex, md5_hex_u16, Md5};
use gbav_vx::VxVideo;
use std::time::Instant;

fn verify_video(rom: &[u8], c: &Cart, off: usize) {
    let id = format!("{off:x}");
    let Some((mpath, man)) = frame_manifest(c.short, off) else {
        panic!("{}:{id}: no frame manifest", c.short);
    };
    let mut v = VxVideo::open(rom, &id).unwrap();
    let h = v.header().clone();
    assert_eq!(
        man.len(),
        h.frames as usize,
        "{}: manifest {} frame count",
        c.short,
        mpath.display()
    );
    let mut px = vec![0u16; (h.width * h.height) as usize];
    let (mut bad_y, mut bad_v, mut skipped, mut shown) = (Vec::new(), Vec::new(), 0usize, 0usize);
    let mut starts = Vec::with_capacity(h.frames as usize);
    let mut recent: Vec<String> = Vec::new();
    let t0 = Instant::now();
    while let Some(n) = v.decode_next().unwrap() {
        starts.push(v.frame_bit());
        let (my, mv) = &man[n as usize];
        if md5_hex(v.slot()) != *my {
            bad_y.push(n);
        }
        v.bgr555_into(&mut px);
        let ours = md5_hex_u16(&px);
        if !mv.is_empty() {
            shown += 1;
            if ours != *mv {
                let prev_same = n > 0 && man[n as usize - 1].1 == *mv;
                if prev_same && recent.iter().any(|r| r == mv) {
                    skipped += 1;
                } else {
                    bad_v.push(n);
                }
            }
        }
        recent.push(ours);
        if recent.len() > 8 {
            recent.remove(0);
        }
    }
    let dt = t0.elapsed();
    let end_bit = v.core().bits.bitpos();
    if let Some(fb) = frame_bits(c.short, off) {
        assert_eq!(fb.len(), starts.len() + 1);
        let bad: Vec<usize> = (0..starts.len()).filter(|&i| fb[i] != starts[i]).collect();
        assert!(
            bad.is_empty() && fb[starts.len()] == end_bit,
            "{}:{id}: frame start bits differ from the walker at {:?}",
            c.short,
            &bad[..bad.len().min(10)]
        );
        println!(
            "{:22} {:>9} frame start bits: {}/{} + end bit equal the grammar walker's",
            c.short,
            format!("0x{off:x}"),
            starts.len(),
            starts.len()
        );
    }
    let st = v.stats();
    let seek = v.seek_table().to_vec();
    let bad_seekbits: Vec<u32> = seek
        .iter()
        .filter(|e| e.frame < h.frames && e.frame > 0)
        .filter(|e| starts[e.frame as usize] != e.video_bit)
        .map(|e| e.frame)
        .collect();
    println!(
        "{:22} {:>9} {} {}x{}: yuv {}/{} screen {}/{} (+{} skipped blits) err {} seekbits {}/{}  [{:.1}s decode+blit+md5]",
        c.short,
        format!("0x{off:x}"),
        h.variant.magic(),
        h.width,
        h.height,
        man.len() - bad_y.len(),
        man.len(),
        shown - bad_v.len() - skipped,
        shown,
        skipped,
        st.err,
        seek.iter().filter(|e| e.frame < h.frames && e.frame > 0).count() - bad_seekbits.len(),
        seek.iter().filter(|e| e.frame < h.frames && e.frame > 0).count(),
        dt.as_secs_f64()
    );
    assert!(
        bad_y.is_empty(),
        "{}:{id}: YUV mismatches at {:?}",
        c.short,
        &bad_y[..bad_y.len().min(20)]
    );
    assert!(
        bad_v.is_empty(),
        "{}:{id}: BGR555 mismatches at {:?}",
        c.short,
        &bad_v[..bad_v.len().min(20)]
    );
    assert_eq!(st.err, 0);
    assert!(
        bad_seekbits.is_empty(),
        "seek bit mismatches {bad_seekbits:?}"
    );

    // cold start at every seek point; decode the whole segment
    let pts = h.seek_points(rom);
    let mut bad = Vec::new();
    let mut nframes = 0usize;
    let t0 = Instant::now();
    for (i, &p) in pts.iter().enumerate() {
        let end = pts.get(i + 1).copied().unwrap_or(h.frames);
        v.seek(p).unwrap();
        for f in p..end {
            let fr = v.next_frame().unwrap().unwrap();
            assert_eq!(fr.index, f);
            nframes += 1;
            if md5_hex(&fr.slot) != man[f as usize].0 {
                bad.push(f);
            }
        }
    }
    println!(
        "{:22} {:>9} cold start at {} seek points: {}/{} segment frames match  [{:.1}s]",
        c.short,
        format!("0x{off:x}"),
        pts.len(),
        nframes - bad.len(),
        nframes,
        t0.elapsed().as_secs_f64()
    );
    assert!(
        bad.is_empty(),
        "{}:{id}: cold-start mismatches at {:?}",
        c.short,
        &bad[..bad.len().min(20)]
    );
}

fn verify_audio(rom: &[u8], c: &Cart, off: usize) {
    let id = format!("{off:x}");
    let mut a = gbav_vx::VxAudio::open(rom, &id).unwrap();
    let mut blocks: Vec<[u8; 16]> = Vec::new();
    let mut pcm: Vec<[u8; 16]> = Vec::new();
    let (mut lo, mut hi) = (i32::MAX, i32::MIN);
    let t0 = Instant::now();
    while let Some(b) = a.next_block().unwrap() {
        blocks.push(Md5::digest(&b));
        let s = a.last_samples();
        let mut m = Md5::new();
        for x in s {
            m.update(&x.to_le_bytes());
            lo = lo.min(*x);
            hi = hi.max(*x);
        }
        pcm.push(m.finish());
        assert_eq!(a.last_pcm().len(), 128);
    }
    let dt = t0.elapsed();
    let mut line = format!(
        "{:22} {:>9} audio: {} AFrames (samples {}..{})  [{:.1}s]",
        c.short,
        format!("0x{off:x}"),
        blocks.len(),
        lo,
        hi,
        dt.as_secs_f64()
    );
    let mut checked = false;
    if let Some((n, digest)) = c.sha1.and_then(|s| streams_json_audio(s, off)) {
        let mut m = Md5::new();
        for b in blocks.iter().take(n) {
            m.update(hex(b).as_bytes());
        }
        let ok = blocks.len() >= n && hex(&m.finish()) == digest;
        line += &format!(
            " | streams.json {n} played blocks {}",
            if ok { "match" } else { "MISMATCH" }
        );
        assert!(ok, "{}:{id}: audio digest mismatch", c.short);
        checked = true;
    }
    if let Some(man) = audio_block_manifest(c.short, off) {
        let first_bad = man
            .iter()
            .enumerate()
            .find(|(i, m)| blocks.get(*i).map(|b| hex(b)) != Some((*m).clone()))
            .map(|(i, _)| i);
        match (first_bad, known_underrun(c.short, off)) {
            (None, _) => line += &format!(" | per-block {}/{} match", man.len(), man.len()),
            (Some(k), Some(u)) if k == u => {
                line += &format!(
                    " | per-block {k}/{} match before the known emulator underrun at {u}",
                    man.len()
                )
            }
            (Some(k), _) => panic!(
                "{}:{id}: audio block {k} differs from the per-block manifest",
                c.short
            ),
        }
        checked = true;
    }
    if let Some(r) = pcm_reference(c.short, off) {
        assert_eq!(
            r.len(),
            blocks.len(),
            "{}:{id}: AFrame count differs from vxaudio.py",
            c.short
        );
        let bad_pcm = r.iter().zip(&pcm).position(|((p, _), q)| *p != hex(q));
        let bad_out = r.iter().zip(&blocks).position(|((_, o), q)| *o != hex(q));
        line += &format!(
            " | vxaudio.py: pcm {} out {} ({} AFrames)",
            if bad_pcm.is_none() {
                "match"
            } else {
                "MISMATCH"
            },
            if bad_out.is_none() {
                "match"
            } else {
                "MISMATCH"
            },
            r.len()
        );
        assert!(
            bad_pcm.is_none() && bad_out.is_none(),
            "{}:{id}: vxaudio.py mismatch pcm {bad_pcm:?} out {bad_out:?}",
            c.short
        );
        checked = true;
    }
    println!("{line}");
    assert!(checked, "{}:{id}: no audio reference found", c.short);
}

fn run(short: &str, off: usize) {
    let c = cart(short);
    let Some(rom) = load_rom(c) else {
        eprintln!("GBAV_ROMS not set or ROM missing: skipping {short}");
        return;
    };
    verify_video(&rom, c, off);
    verify_audio(&rom, c, off);
}

#[test]
fn shrek_rev6() {
    run("shrek-rev6", 0x20200);
}
#[test]
fn shrek_rev5_vxgb() {
    run("shrek-rev5", 0x20200);
}
#[test]
fn shrek2_rev6() {
    run("shrek2-rev6", 0x20200);
}
#[test]
fn shrek2_rev5_vxgb() {
    run("shrek2-rev5", 0x20200);
}
#[test]
fn sharktale_rev6() {
    run("sharktale-rev6", 0x20200);
}
#[test]
fn sharktale_rev5_vxgb() {
    run("sharktale-rev5", 0x20200);
}
#[test]
fn shrek_sharktale_rev5_shrek() {
    run("shrek-sharktale-rev5", 0x20200);
}
#[test]
fn shrek_sharktale_rev5_sharktale() {
    run("shrek-sharktale-rev5", 0x1eef800);
}
#[test]
fn shrek_sharktale_rev5_3dd3600() {
    run("shrek-sharktale-rev5", 0x3dd3600);
}
#[test]
fn shrek_sharktale_rev5_3e76a00() {
    run("shrek-sharktale-rev5", 0x3e76a00);
}

/// Stream discovery and StreamInfo through the Codec trait.
#[test]
fn codec_streams() {
    use gbav_common::Codec;
    let c = cart("shrek-sharktale-rev5");
    let Some(rom) = load_rom(c) else { return };
    let st = gbav_vx::Vx::streams(&rom).unwrap();
    let ids: Vec<&str> = st.iter().map(|s| s.id.as_str()).collect();
    assert_eq!(ids, ["20200", "1eef800", "3dd3600", "3e76a00"]);
    assert_eq!((st[0].width, st[0].height, st[0].frames), (240, 112, 34874));
    assert_eq!(st[2].seek_points, vec![0]);
    assert_eq!(st[0].audio_rate_hz, Some(16384));
    // 2^24 / (64 * 3 * 12483) = 7.00003 fps
    assert!((st[0].fps_num as f64 / st[0].fps_den as f64 - 7.00003).abs() < 1e-4);
    // decode the first frame of a stream through the trait objects
    let mut v = gbav_vx::Vx::video(&rom, "1").unwrap();
    let f = v.next_frame().unwrap().unwrap();
    use gbav_common::Frame;
    assert_eq!(f.manifest_bytes().len(), 0xF000);
    assert_eq!(f.bgr555().len(), 240 * 112);
    // a VXGB cart reports its variant
    let c5 = cart("shrek-rev5");
    if let Some(rom5) = load_rom(c5) {
        let h = gbav_vx::pick_stream(&rom5, "20200").unwrap();
        assert_eq!(h.variant, gbav_vx::Variant::Vxgb);
        assert_eq!(h.qp, 32);
        // tables of the other build are refused
        assert!(gbav_vx::Tables::from_rom(&rom5, gbav_vx::Variant::VxPP).is_err());
    }
    // a non-VX cart is rejected
    let dir = roms_dir().unwrap();
    let other =
        std::fs::read(dir.join("Game Boy Advance Video - Pokemon - Volume 1 (USA).gba")).ok();
    if let Some(o) = other {
        assert!(gbav_vx::Vx::streams(&o).is_err());
    }
}
