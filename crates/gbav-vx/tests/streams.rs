//! Whole-stream verification against the emulator hash manifests (GBAV_ROMS gated).
//!
//! Per stream:
//!  * video: one pass from power-on; md5 of every 0xF000-byte slot == manifest yuv_md5, md5
//!    of every displayed BGR555 picture == vram_md5 (a screen equal to the previous one is
//!    accepted only where the cart skipped a late blit: it must equal one of our recent
//!    frames); every seek-table bit offset == the continuous decode's frame start bit;
//!  * seek points: a cold decoder started at every seek point decodes its whole segment
//!    (up to the next seek point) to the manifest slots;
//!  * audio: the cart's played 512-byte blocks: the streams.json aggregate digest, and the
//!    per-block manifest (verify/vx/<stem>.audio_md5.csv.gz) or, without it, the ~30 s segment
//!    manifest (<stem>.audio_seg_md5.csv.gz), with the known cart-side differences of
//!    verify/known.csv; with GBAV_VX_PCM_REF, every AFrame's 16 kHz samples and 8-bit block
//!    against tools/vx/container/vxaudio.py.
//!
//! Run with `cargo test --release -p gbav-vx --test streams -- --nocapture`.

mod common;

use common::*;
use gbav_common::{AudioDecoder, VideoDecoder};
use gbav_common::manifest::{self, Manifest};
use gbav_common::md5::{self, to_hex as hex, Md5};
use gbav_vx::VxVideo;
use std::time::Instant;

fn verify_video(rom: &gbav_common::Rom, c: &Cart, off: usize) {
    let id = format!("{off:x}");
    let Some((mpath, man)) = frame_manifest(&c.short, off) else {
        panic!("{}:{id}: no frame manifest", c.short);
    };
    let mut v = VxVideo::open(rom.clone(), &id).unwrap();
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
        if md5::hex(v.slot()) != *my {
            bad_y.push(n);
        }
        v.bgr555_into(&mut px);
        let ours = md5::hex_u16(&px);
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
    if let Some(fb) = frame_bits(&c.short, off) {
        // nframes entries, or nframes + 1 with the end of the last frame
        assert!(fb.len() == starts.len() || fb.len() == starts.len() + 1);
        let end_ok = fb.get(starts.len()).is_none_or(|&e| e == end_bit);
        let bad: Vec<usize> = (0..starts.len()).filter(|&i| fb[i] != starts[i]).collect();
        assert!(
            bad.is_empty() && end_ok,
            "{}:{id}: frame start bits differ from the walker at {:?}",
            c.short,
            &bad[..bad.len().min(10)]
        );
        println!(
            "{:22} {:>9} frame start bits: {}/{}{} equal the grammar walker's",
            c.short,
            format!("0x{off:x}"),
            starts.len(),
            starts.len(),
            if fb.len() > starts.len() { " + end bit" } else { "" }
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
            if md5::hex(&fr.slot) != man[f as usize].0 {
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

fn verify_audio(rom: &gbav_common::Rom, c: &Cart, off: usize) {
    let id = format!("{off:x}");
    let mut a = gbav_vx::VxAudio::open(rom.clone(), &id).unwrap();
    let mut blocks: Vec<[u8; 16]> = Vec::new();
    let mut pcm: Vec<[u8; 16]> = Vec::new();
    let (mut lo, mut hi) = (i32::MAX, i32::MIN);
    let t0 = Instant::now();
    while a.next_block().unwrap() {
        assert_eq!(a.key().index as usize, blocks.len());
        blocks.push(md5::digest(a.manifest_part(0)));
        let s = a.last_samples();
        let mut m = Md5::new();
        for x in s {
            m.update(&x.to_le_bytes());
            lo = lo.min(*x);
            hi = hi.max(*x);
        }
        pcm.push(m.finalize());
        assert_eq!(a.pcm().len(), 128);
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
    if let Some((n, digest)) = streams_json_audio(&c.sha1, off) {
        let mut m = Md5::new();
        for b in blocks.iter().take(n) {
            m.update(hex(b).as_bytes());
        }
        let ok = blocks.len() >= n && hex(&m.finalize()) == digest;
        line += &format!(
            " | streams.json {n} played blocks {}",
            if ok { "match" } else { "MISMATCH" }
        );
        assert!(ok, "{}:{id}: audio digest mismatch", c.short);
        checked = true;
    }
    let known: Vec<_> = kit::known("vx", &c.short, &id);
    let known: Vec<_> = known.iter().collect();
    let paths = kit::stream_manifests("vx", &c.short, &id).unwrap();
    let open = || gbav_vx::VxAudio::open(rom.clone(), &id);
    if let Some(m) = Manifest::load(&paths.audio).unwrap() {
        let r = manifest::check_audio_blocks(&mut open().unwrap(), &m, &known, None).unwrap();
        line += &format!(" | per-block {r}");
        assert!(r.passed(), "{}:{id}: audio per-block manifest: {r}", c.short);
        checked = true;
    } else if let Some(m) = Manifest::load(&paths.audio_seg).unwrap() {
        let r = manifest::check_audio_segments(&mut open().unwrap(), &m, 1).unwrap();
        line += &format!(" | segments {r}");
        assert!(r.passed(), "{}:{id}: audio segment manifest: {r}", c.short);
        checked = true;
    } else {
        line += " | no per-block or segment audio manifest (skipped)";
    }
    if let Some(r) = pcm_reference(&c.short, off) {
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
    if !checked {
        eprintln!("{}:{id}: no audio reference found; audio not checked", c.short);
    }
}

fn run(short: &str, off: usize) {
    let c = cart(short);
    let Some(rom) = load_rom(&c) else {
        return;
    };
    verify_video(&rom, &c, off);
    verify_audio(&rom, &c, off);
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
    let Some(rom) = load_rom(&c) else { return };
    let st = gbav_vx::Vx::streams(&rom).unwrap();
    let ids: Vec<&str> = st.iter().map(|s| s.id.as_str()).collect();
    assert_eq!(ids, ["20200", "1eef800", "3dd3600", "3e76a00"]);
    assert_eq!((st[0].width, st[0].height, st[0].frames), (240, 112, 34874));
    assert_eq!(st[2].seek_points, vec![0]);
    assert_eq!(st[2].chapters.iter().map(|c| c.frame).collect::<Vec<_>>(), vec![0]);
    assert!(st[0].chapters.len() > 1);
    for s in &st {
        assert!(s.chapters.iter().all(|c| s.seek_points.contains(&c.frame)));
    }
    assert_eq!(st[0].audio_rate_hz, Some(16384));
    // 2^24 / (64 * 3 * 12483) = 7.00003 fps
    assert!((st[0].fps_num as f64 / st[0].fps_den as f64 - 7.00003).abs() < 1e-4);
    // decode the first frame of a stream through the trait objects
    let mut v = gbav_vx::Vx::video(&rom, "1").unwrap();
    let f = v.next_frame().unwrap().unwrap();
    use gbav_common::Frame;
    assert_eq!(f.manifest_bytes().len(), 0xF000);
    assert_eq!(f.size(), (240, 112));
    assert_eq!(f.bgr555().len(), 240 * 112);
    // audio cold start at a seek point continues the AFrame index and equals a continuous
    // decode from there (the seek point's AFrame is intra)
    use gbav_common::AudioDecoder;
    let sp = st[1].seek_points[3];
    let mut a = gbav_vx::Vx::audio(&rom, "1").unwrap();
    a.seek(sp).unwrap();
    assert!(a.next_block().unwrap());
    let (k, first) = (a.key().index, a.manifest_part(0).to_vec());
    let mut b = gbav_vx::Vx::audio(&rom, "1").unwrap();
    for _ in 0..=k {
        assert!(b.next_block().unwrap());
    }
    assert_eq!(b.key().index, k);
    assert_eq!(b.manifest_part(0), &first[..]);
    assert!(a.next_block().unwrap() && b.next_block().unwrap());
    assert_eq!((a.key(), a.pcm()), (b.key(), b.pcm()));
    // a VXGB cart reports its variant
    let c5 = cart("shrek-rev5");
    if let Some(rom5) = load_rom(&c5) {
        let h = gbav_vx::pick_stream(&rom5, "20200").unwrap();
        assert_eq!(h.variant, gbav_vx::Variant::Vxgb);
        assert_eq!(h.qp, 32);
        // tables of the other build are refused
        assert!(gbav_vx::Tables::from_rom(&rom5, gbav_vx::Variant::VxPP).is_err());
    }
    // a non-VX cart is rejected
    if let Some(o) = kit::load_rom("pokemon1") {
        assert!(gbav_vx::Vx::streams(&o).is_err());
    }
}
