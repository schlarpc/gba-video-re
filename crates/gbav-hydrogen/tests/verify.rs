//! Whole-family verification against `verify/hydrogen/` (needs `GBAV_ROMS`): every frame of the
//! 13 Hydrogen carts and the Sonic X Vol 2 prototype, their audio (per player call / per block
//! when the large per-block manifests are present, else the ~30 s segment manifests), cold-start
//! seeks at every segment start and at every audio block, and the trait's audio seek.
//!
//! `GBAV_ROMS=... cargo test --release -p gbav-hydrogen --test verify -- --nocapture`

use gbav_common::manifest::{self, Manifest};
use gbav_common::md5;
use gbav_common::{AudioDecoder, Codec, Frame, Rom, VideoDecoder};
use gbav_hydrogen::{Cart, Hydrogen};
use gbav_majesco::audio::FRAME_SAMPLES_X16;
use gbav_testkit as tk;

#[derive(Default, Debug, Clone)]
struct Tally {
    streams: usize,
    frames: u64,
    frames_ok: u64,
    audio: u64,
    audio_ok: u64,
    audio_unit: &'static str,
    seeks: usize,
    seeks_ok: usize,
    aseeks: usize,
    aseeks_ok: usize,
    bad: Vec<String>,
}

impl Tally {
    fn add(&mut self, o: &Tally) {
        self.streams += o.streams;
        self.frames += o.frames;
        self.frames_ok += o.frames_ok;
        self.audio += o.audio;
        self.audio_ok += o.audio_ok;
        self.seeks += o.seeks;
        self.seeks_ok += o.seeks_ok;
        self.aseeks += o.aseeks;
        self.aseeks_ok += o.aseeks_ok;
        self.bad.extend(o.bad.iter().cloned());
    }
}

/// md5 of every frame's manifest bytes, decoding front to back.
fn decode_all(rom: &Rom, id: &str) -> Vec<String> {
    let mut v = Hydrogen::video(rom, id).unwrap();
    let mut out = Vec::new();
    while let Some(f) = v.next_frame().unwrap_or_else(|e| panic!("stream {id}: {e}")) {
        out.push(md5::hex(f.manifest_bytes()));
    }
    out
}

/// The whole track, block by block: (s8 samples concatenated, md5 of each block).
fn decode_track(rom: &Rom, id: &str) -> (Vec<i8>, Vec<String>) {
    let mut a = Hydrogen::audio(rom, id).unwrap();
    let (mut all, mut md5s) = (Vec::new(), Vec::new());
    while a.next_block().unwrap() {
        all.extend_from_slice(a.last_samples());
        md5s.push(md5::hex(a.manifest_part(0)));
    }
    (all, md5s)
}

fn verify_stream(rom: &Rom, short: &str, id: &str, mname: &str, t: &mut Tally) {
    let sname = format!("{short}_{mname}");
    let paths = tk::stream_manifests("hydrogen", short, mname).unwrap();
    let Some(m) = Manifest::load(&paths.frames).unwrap() else {
        t.bad.push(format!("{sname}: no frame manifest"));
        return;
    };
    t.streams += 1;
    let seq = decode_all(rom, id);
    let c = m.md5_cols()[0];
    for r in 0..m.rows.len() {
        let f = m.num(r, 0).unwrap() as usize;
        t.frames += 1;
        if seq.get(f) == Some(&m.rows[r][c]) {
            t.frames_ok += 1;
        } else if t.bad.len() < 50 {
            t.bad.push(format!("{sname}: frame {f} differs"));
        }
    }
    // cold start at every segment start
    let info = Hydrogen::streams(rom).unwrap().into_iter().find(|s| s.id == id).unwrap();
    for &p in &info.seek_points {
        let mut v = Hydrogen::video(rom, id).unwrap();
        v.seek(p).unwrap();
        let f = v.next_frame().unwrap().unwrap();
        t.seeks += 1;
        if md5::hex(f.manifest_bytes()) == seq[p as usize] {
            t.seeks_ok += 1;
        } else {
            t.bad.push(format!("{sname}: cold seek to frame {p} differs"));
        }
    }
    if info.audio_rate_hz.is_none() {
        return;
    }
    // audio against the manifests
    let check = if let Some(am) = Manifest::load(&paths.audio).unwrap() {
        if am.col("n").is_some() {
            // per player call
            Some(manifest::check_audio_blocks(&mut Hydrogen::audio(rom, id).unwrap(), &am, &[], None).unwrap())
        } else {
            // per block (the prototype); the last row covers only what the cart decoded
            let mut a = Hydrogen::audio(rom, id).unwrap();
            let mut c = manifest::Check { total: am.rows.len() as u64, unit: "blocks", ..Default::default() };
            for r in 0..am.rows.len() {
                if !a.next_block().unwrap() {
                    c.missing = (am.rows.len() - r) as u64;
                    break;
                }
                let n = am.num(r, 1).unwrap() as usize;
                let b = a.manifest_part(0);
                if b.len() >= n && md5::hex(&b[..n]) == am.rows[r][2] {
                    c.ok += 1;
                } else if c.first_bad.is_none() {
                    c.first_bad = Some(r as u64);
                }
            }
            Some(c)
        }
    } else if let Some(sm) = Manifest::load(&paths.audio_seg).unwrap() {
        Some(manifest::check_audio_segments(&mut Hydrogen::audio(rom, id).unwrap(), &sm, 1).unwrap())
    } else {
        eprintln!("{sname}: no audio manifest, audio not checked");
        None
    };
    if let Some(c) = check {
        t.audio += c.total;
        t.audio_ok += c.ok;
        t.audio_unit = c.unit;
        if !c.passed() {
            t.bad.push(format!("{sname}: audio {c}"));
        }
    }
    // cold starts at every audio block, and the trait's seek at the video seek points
    let (all, blocks) = decode_track(rom, id);
    let mut a = Hydrogen::audio(rom, id).unwrap();
    for (k, want) in blocks.iter().enumerate() {
        a.seek_block(k).unwrap();
        assert!(a.next_block().unwrap());
        t.aseeks += 1;
        if &md5::hex(a.manifest_part(0)) == want {
            t.aseeks_ok += 1;
        } else {
            t.bad.push(format!("{sname}: cold seek to audio block {k} differs"));
        }
    }
    for &p in info.seek_points.iter().take(8) {
        let first = (p as u64 * FRAME_SAMPLES_X16 / 16) as usize;
        if first + 1000 > all.len() {
            continue;
        }
        let mut a = Hydrogen::audio(rom, id).unwrap();
        a.seek(p).unwrap();
        assert!(a.decode_samples(1000).unwrap());
        t.aseeks += 1;
        if a.last_samples() == &all[first..first + 1000] {
            t.aseeks_ok += 1;
        } else {
            t.bad.push(format!("{sname}: AudioDecoder::seek({p}) differs"));
        }
    }
}

fn verify_cart(short: &str) -> Tally {
    let mut t = Tally::default();
    let Some(rom) = tk::load_rom(short) else {
        t.bad.push(format!("{short}: ROM missing"));
        return t;
    };
    let cart = Cart::open(&rom).unwrap();
    if short == "sx2p" {
        assert_eq!(cart.player.output, gbav_majesco::adpcm::Output::Wrap, "proto ADPCM store not detected");
    }
    for i in 0..cart.streams.len() {
        let id = i.to_string();
        verify_stream(&rom, short, &id, &id, &mut t);
    }
    t
}

#[test]
fn hydrogen_family_matches_manifests() {
    if tk::roms_dir().is_none() || tk::verify_dir().is_none() {
        return tk::skip("GBAV_ROMS not set (or verify/ not found)");
    }
    let jobs: Vec<String> = tk::carts("hydrogen").into_iter().map(|c| c.short_id).filter(|s| tk::only(s)).collect();
    let t0 = std::time::Instant::now();
    let res = tk::par_map(&jobs, |short| {
        let t = verify_cart(short);
        eprintln!(
            "{short:8} streams {:2} frames {}/{} audio {}/{} {} seeks {}/{} audio seeks {}/{}",
            t.streams, t.frames_ok, t.frames, t.audio_ok, t.audio, t.audio_unit, t.seeks_ok, t.seeks, t.aseeks_ok, t.aseeks
        );
        t
    });
    let mut tot = Tally::default();
    for t in &res {
        tot.add(t);
    }
    eprintln!(
        "TOTAL {} streams: frames {}/{}, audio {}/{} (calls, blocks or segments), segment seeks {}/{}, audio seeks {}/{} in {:.1}s",
        tot.streams, tot.frames_ok, tot.frames, tot.audio_ok, tot.audio, tot.seeks_ok, tot.seeks, tot.aseeks_ok, tot.aseeks,
        t0.elapsed().as_secs_f64()
    );
    for b in tot.bad.iter().take(30) {
        eprintln!("  {b}");
    }
    assert!(tot.bad.is_empty());
    assert_eq!(tot.frames_ok, tot.frames);
    assert_eq!(tot.audio_ok, tot.audio);
}
