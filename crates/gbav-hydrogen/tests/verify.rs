//! Whole-family verification against the lanes' hash manifests (GBAV_ROMS + GBAV_CREF):
//! every frame and every audio call of the 13 Hydrogen carts (hydrogen/src/manifests), every frame
//! and audio block of the Sonic X Vol 2 prototype (sonicx2-proto/src/manifests), plus cold-start
//! seeks at every segment start and at every audio block.
//!
//! Run in release: `GBAV_ROMS=... GBAV_CREF=... cargo test --release -p gbav-hydrogen --test verify -- --nocapture`

#[path = "../../gbav-majesco/tests/support/mod.rs"]
#[macro_use]
mod support;

use gbav_common::{AudioDecoder, Codec, Frame, VideoDecoder};
use gbav_hydrogen::{Cart, Hydrogen};
use gbav_majesco::adpcm::BLOCK;
use support::{md5_hex, par_map, read_manifest};

const CARTS: &[(&str, &str)] = &[
    ("agu1", "All Grown Up! - Volume 1 (USA)"),
    ("sonicx1", "Sonic X - Volume 1 (USA)"),
    ("yugioh", "Yu-Gi-Oh! - Yugi vs. Joey (USA, Europe)"),
    ("sponge1", "SpongeBob SquarePants - Volume 1 (USA) (Rev 1)"),
    ("sponge2", "SpongeBob SquarePants - Volume 2 (USA) (Rev 1)"),
    ("fop1", "The Fairly OddParents! - Volume 1 (USA)"),
    ("fop2", "The Fairly OddParents! - Volume 2 (USA) (Rev 1)"),
    ("tmnt", "Teenage Mutant Ninja Turtles - Things Change (USA, Europe)"),
    ("nick1", "Nicktoons Collection - Volume 1 (USA)"),
    ("strawb1", "Strawberry Shortcake - Volume 1 (USA)"),
    ("knd1", "Codename - Kids Next Door - Volume 1 (USA, Europe)"),
    ("cn1", "Cartoon Network Collection - Volume 1 (USA, Europe)"),
    ("dora1", "Dora the Explorer - Volume 1 (USA)"),
];
const PROTO: &str = "Sonic X - Volume 2 (USA) (Proto)";
const PROTO_STREAMS: &[&str] = &["ep103", "ep104", "intro"];

#[derive(Default, Debug, Clone)]
struct Tally {
    streams: usize,
    frames: usize,
    frames_ok: usize,
    idx_ok: usize,
    audio: usize,
    audio_ok: usize,
    samples: usize,
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
        self.idx_ok += o.idx_ok;
        self.audio += o.audio;
        self.audio_ok += o.audio_ok;
        self.samples += o.samples;
        self.seeks += o.seeks;
        self.seeks_ok += o.seeks_ok;
        self.aseeks += o.aseeks;
        self.aseeks_ok += o.aseeks_ok;
        self.bad.extend(o.bad.iter().cloned());
    }
}

/// (frame md5 over the manifest lines, idx md5) for every frame, decoding front to back.
fn decode_all(rom: &[u8], id: &str) -> Vec<(String, String, usize)> {
    let mut v = Hydrogen::video(rom, id).unwrap();
    let mut out = Vec::new();
    while let Some(f) = v.next_frame().unwrap_or_else(|e| panic!("stream {id}: {e}")) {
        out.push((md5_hex(f.manifest_bytes()), md5_hex(&f.index), f.lines));
    }
    out
}

/// Cold start at every segment start: first frame equals the sequential decode.
fn seeks(rom: &[u8], id: &str, seq: &[(String, String, usize)], t: &mut Tally, name: &str) {
    let info = &Hydrogen::streams(rom).unwrap()[id.parse::<usize>().unwrap()];
    for &p in &info.seek_points {
        let mut v = Hydrogen::video(rom, id).unwrap();
        v.seek(p).unwrap();
        let f = v.next_frame().unwrap().unwrap();
        t.seeks += 1;
        if md5_hex(f.manifest_bytes()) == seq[p as usize].0 {
            t.seeks_ok += 1;
        } else {
            t.bad.push(format!("{name}: cold seek to frame {p} differs"));
        }
    }
}

/// Audio cold starts at every block: equals the sequential decode of that block.
fn audio_seeks(rom: &[u8], id: &str, seq_blocks: &[String], t: &mut Tally, name: &str) {
    let mut a = Hydrogen::audio(rom, id).unwrap();
    for (i, want) in seq_blocks.iter().enumerate() {
        a.seek_block(i).unwrap();
        let b = a.next_block().unwrap().unwrap();
        t.aseeks += 1;
        if &md5_hex(&b) == want {
            t.aseeks_ok += 1;
        } else {
            t.bad.push(format!("{name}: cold seek to audio block {i} differs"));
        }
    }
}

fn block_md5s(rom: &[u8], id: &str) -> Vec<String> {
    let mut a = Hydrogen::audio(rom, id).unwrap();
    let mut v = Vec::new();
    while let Some(b) = a.next_block().unwrap() {
        v.push(md5_hex(&b));
    }
    v
}

fn verify_cart(short: &str, name: &str, man: &std::path::Path) -> Tally {
    let mut t = Tally::default();
    let Some(rom) = support::load_rom(name) else {
        t.bad.push(format!("{short}: ROM missing"));
        return t;
    };
    let cart = Cart::open(&rom).unwrap();
    for i in 0..cart.streams.len() {
        let sname = format!("{short}_{i}");
        let id = i.to_string();
        let Some(rows) = read_manifest(&man.join(format!("{sname}.frame_md5.csv.gz"))) else {
            t.bad.push(format!("{sname}: no manifest"));
            continue;
        };
        t.streams += 1;
        let seq = decode_all(&rom, &id);
        assert_eq!(seq.len() as u32, cart.streams[i].frames);
        for r in &rows {
            let f: i64 = r[0].parse().unwrap();
            t.frames += 1;
            let got = (f >= 0).then(|| &seq[f as usize]);
            if got.is_some_and(|g| g.0 == r[1]) {
                t.frames_ok += 1;
            } else if t.bad.len() < 50 {
                t.bad.push(format!("{sname}: frame {f} differs"));
            }
            if got.is_some_and(|g| g.1 == r[2]) {
                t.idx_ok += 1;
            }
        }
        seeks(&rom, &id, &seq, &mut t, &sname);
        // audio: the player's calls (sizes from the manifest), md5 of each call's output
        let arows = read_manifest(&man.join(format!("{sname}.audio_md5.csv.gz"))).unwrap();
        let mut a = Hydrogen::audio(&rom, &id).unwrap();
        for r in &arows {
            let n: usize = r[2].parse().unwrap();
            let mut buf = vec![0i8; n];
            let got = a.decode(&mut buf);
            t.audio += 1;
            t.samples += n;
            let bytes: Vec<u8> = buf.iter().map(|&s| s as u8).collect();
            if got == n && md5_hex(&bytes) == r[3] {
                t.audio_ok += 1;
            } else if t.bad.len() < 50 {
                t.bad.push(format!("{sname}: audio call {} differs", r[0]));
            }
        }
        audio_seeks(&rom, &id, &block_md5s(&rom, &id), &mut t, &sname);
    }
    t
}

fn verify_proto(man: &std::path::Path) -> Tally {
    let mut t = Tally::default();
    let Some(rom) = support::load_rom(PROTO) else {
        t.bad.push("sx2p: ROM missing".into());
        return t;
    };
    let cart = Cart::open(&rom).unwrap();
    assert_eq!(cart.player.output, gbav_majesco::adpcm::Output::Wrap, "proto ADPCM store not detected");
    for (i, sn) in PROTO_STREAMS.iter().enumerate() {
        let sname = format!("sx2p_{sn}");
        let id = i.to_string();
        let rows = read_manifest(&man.join(format!("{sname}.frame_md5.csv.gz"))).unwrap();
        t.streams += 1;
        let seq = decode_all(&rom, &id);
        assert_eq!(seq.len(), rows.len(), "{sname} frame count");
        for (f, r) in rows.iter().enumerate() {
            t.frames += 1;
            let lines: usize = r[1].parse().unwrap();
            if seq[f].0 == r[2] && seq[f].2 == lines {
                t.frames_ok += 1;
                t.idx_ok += 1;
            } else if t.bad.len() < 50 {
                t.bad.push(format!("{sname}: frame {f} differs (lines {} vs {lines})", seq[f].2));
            }
        }
        seeks(&rom, &id, &seq, &mut t, &sname);
        let arows = read_manifest(&man.join(format!("{sname}.audio_md5.csv.gz"))).unwrap();
        let mut a = Hydrogen::audio(&rom, &id).unwrap();
        for r in &arows {
            let n: usize = r[1].parse().unwrap();
            let b = a.next_block().unwrap().unwrap();
            assert!(b.len() >= n && b.len() <= BLOCK, "{sname} block {} has {} samples", r[0], b.len());
            t.audio += 1;
            t.samples += n;
            if md5_hex(&b[..n]) == r[2] {
                t.audio_ok += 1;
            } else if t.bad.len() < 50 {
                t.bad.push(format!("{sname}: audio block {} differs", r[0]));
            }
        }
        audio_seeks(&rom, &id, &block_md5s(&rom, &id), &mut t, &sname);
    }
    t
}

#[test]
fn md5_agrees() {
    let mut r = support::Rng::new(9);
    for n in [0, 1, 55, 56, 63, 64, 65, 119, 120, 1000, 76800] {
        let b = r.bytes(n);
        assert_eq!(md5_hex(&b), gbav_common::md5::hex(&b));
    }
}

#[test]
fn hydrogen_family_matches_manifests() {
    let (Some(_), Some(suite)) = (support::roms_dir(), support::cref_dir()) else {
        return eprintln!("skip: GBAV_ROMS / GBAV_CREF not set");
    };
    let hman = suite.join("hydrogen/src/manifests");
    let pman = suite.join("sonicx2-proto/src/manifests");
    let mut jobs: Vec<(&str, &str)> = CARTS.iter().copied().filter(|(s, _)| support::only(s)).collect();
    if support::only("sx2p") {
        jobs.push(("sx2p", PROTO));
    }
    let t0 = std::time::Instant::now();
    let res = par_map(&jobs, |&(short, name)| {
        let t = if short == "sx2p" { verify_proto(&pman) } else { verify_cart(short, name, &hman) };
        eprintln!(
            "{short:8} streams {:2} frames {}/{} idx {} audio {}/{} seeks {}/{} audio seeks {}/{}",
            t.streams, t.frames_ok, t.frames, t.idx_ok, t.audio_ok, t.audio, t.seeks_ok, t.seeks, t.aseeks_ok, t.aseeks
        );
        t
    });
    let mut tot = Tally::default();
    for t in &res {
        tot.add(t);
    }
    eprintln!(
        "TOTAL {} streams: frames {}/{}, audio {}/{} ({} samples), segment seeks {}/{}, audio block seeks {}/{} in {:.1}s",
        tot.streams, tot.frames_ok, tot.frames, tot.audio_ok, tot.audio, tot.samples, tot.seeks_ok, tot.seeks,
        tot.aseeks_ok, tot.aseeks, t0.elapsed().as_secs_f64()
    );
    for b in tot.bad.iter().take(30) {
        eprintln!("  {b}");
    }
    assert!(tot.bad.is_empty());
    assert_eq!(tot.frames_ok, tot.frames);
    assert_eq!(tot.audio_ok, tot.audio);
}
