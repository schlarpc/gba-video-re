//! Whole-family verification against the ADS lane's hash manifests (GBAV_ROMS + GBAV_CREF,
//! `ads/src/manifests`): every frame and every audio block of every stream of the 19 ADS carts and
//! the two Hydrogen-packaged LZMA carts, cold-start seeks at every chunk start and audio block.
//!
//! The 41 frames of the Noah proto intro that the cartridge shows with a stale codebook are
//! checked two ways: the decoder's frame equals the C reference's (`ads/src/adsdec`, the intended
//! output), and rendering the same index plane with the codebook of an earlier chunk reproduces
//! the cartridge's framebuffer.
//!
//! `GBAV_ROMS=... GBAV_CREF=... cargo test --release -p gbav-ads --test verify -- --nocapture`

#[path = "../../gbav-majesco/tests/support/mod.rs"]
#[macro_use]
mod support;

use gbav_ads::{AdsCart, AdsVideo};
use gbav_common::{AudioDecoder, Codec, Frame, VideoDecoder};
use gbav_majesco::vq::{self, Row159};
use std::path::Path;
use support::{md5_hex, par_map, read_manifest};

const CARTS: &[(&str, &str)] = &[
    ("srmt", "Super Robot Monkey Team - Hyper Force Go! - Volume 1 (USA)"),
    ("proud", "The Proud Family - Volume 1 (USA)"),
    ("disney1", "Disney Channel Collection - Volume 1 (USA)"),
    ("disney2", "Disney Channel Collection - Volume 2 (USA) (Rev 5)"),
    ("cn-limited", "Cartoon Network Collection - Limited Edition (USA)"),
    ("cn-platinum", "Cartoon Network Collection - Platinum Edition (USA, Europe)"),
    ("cn-premium", "Cartoon Network Collection - Premium Edition (USA, Europe)"),
    ("cn-special", "Cartoon Network Collection - Special Edition (USA, Europe)"),
    ("cn-vol2", "Cartoon Network Collection - Volume 2 (USA, Europe)"),
    ("cnf-platinum", "Cartoon Network Collection - Edition Platinum (France)"),
    ("cnf-premium", "Cartoon Network Collection - Edition Premium (France)"),
    ("cnf-speciale", "Cartoon Network Collection - Edition Speciale (France)"),
    ("nick2", "Nicktoons Collection - Volume 2 (USA)"),
    ("nick3", "Nicktoons - Volume 3 (USA)"),
    ("sponge3", "SpongeBob SquarePants - Volume 3 (USA)"),
    ("dbgt", "Dragon Ball GT - Volume 1 (USA)"),
    ("tmnt-fr", "Teenage Mutant Ninja Turtles - Le Demenagement (France)"),
    ("yugioh-fr", "Yu-Gi-Oh! - Yugi vs. Joey (France)"),
    ("tmnt-proto", "Teenage Mutant Ninja Turtles - The Shredder Strikes (USA) (Proto)"),
    ("jimmy", "The Adventures of Jimmy Neutron Boy Genius - Volume 1 (USA)"),
    ("noah-proto", "Yu-Gi-Oh! - Noah's Final Threat (USA) (Proto)"),
];

#[derive(Default, Debug, Clone)]
struct Tally {
    streams: usize,
    frames: usize,
    frames_ok: usize,
    stale: usize,
    stale_explained: usize,
    stale_eq_c: usize,
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
        macro_rules! s {
            ($($f:ident),*) => { $( self.$f += o.$f; )* };
        }
        s!(streams, frames, frames_ok, stale, stale_explained, stale_eq_c, audio, audio_ok, samples, seeks, seeks_ok, aseeks, aseeks_ok);
        self.bad.extend(o.bad.iter().cloned());
    }
}

/// `adsdec ROM --stream NAME --md5` (the C reference), frame -> md5.
fn c_md5s(suite: &Path, rom: &Path, name: &str) -> Vec<String> {
    let exe = suite.join("ads/src/adsdec");
    let out = std::process::Command::new(&exe).arg(rom).args(["--stream", name, "--md5"]).output().expect("run adsdec");
    String::from_utf8(out.stdout).unwrap().lines().map(|l| l.split_whitespace().nth(1).unwrap().to_owned()).collect()
}

fn verify_cart(cid: &str, name: &str, suite: &Path) -> Tally {
    let man = suite.join("ads/src/manifests");
    let mut t = Tally::default();
    let Some(rom) = support::load_rom(name) else {
        t.bad.push(format!("{cid}: ROM missing"));
        return t;
    };
    let cart = AdsCart::open(&rom).unwrap();
    for (i, m) in cart.movies.iter().enumerate() {
        let sid = m.id();
        let sname = format!("{cid}_{sid}");
        let Some(rows) = read_manifest(&man.join(format!("{sname}.frame_md5.csv.gz"))) else {
            t.bad.push(format!("{sname}: no manifest"));
            continue;
        };
        t.streams += 1;
        let mut v = AdsVideo::new(&rom, &cart, i).unwrap();
        let mut seq = Vec::new();
        let mut planes = Vec::new();
        while let Some(f) = v.next_frame().unwrap_or_else(|e| panic!("{sname}: {e}")) {
            seq.push(md5_hex(f.manifest_bytes()));
            planes.push((f.segment as usize, f.mode, f.index.clone()));
        }
        assert_eq!(seq.len() as u32, m.total_frames(), "{sname} frames");
        assert_eq!(rows.len(), seq.len(), "{sname}: manifest rows");
        let mut c_ref: Option<Vec<String>> = None;
        for r in &rows {
            let f: usize = r[0].parse().unwrap();
            t.frames += 1;
            if seq[f] == r[1] {
                t.frames_ok += 1;
                continue;
            }
            // not what the cartridge showed: is it the cartridge showing a stale codebook?
            t.stale += 1;
            let (ci, mode, ref idx) = planes[f];
            let hit = (1..=16).filter(|&k| k <= ci).find(|&k| {
                let cb = v.segment_vcb(ci - k).unwrap();
                let mut fb = vec![0u16; 240 * 160];
                vq::blit(idx, vq::Mode::new(mode as u32), &cb, Row159::BLACK, &mut fb).unwrap();
                let b: Vec<u8> = fb.iter().flat_map(|p| p.to_le_bytes()).collect();
                md5_hex(&b) == r[1]
            });
            if let Some(k) = hit {
                t.stale_explained += 1;
                if t.stale_explained <= 3 || f.is_multiple_of(100) {
                    eprintln!("{sname}: frame {f} (chunk {ci}) is shown by the cartridge with the codebook of chunk {}", ci - k);
                }
            } else {
                t.bad.push(format!("{sname}: frame {f} differs and is not a stale codebook"));
            }
            let c = c_ref.get_or_insert_with(|| c_md5s(suite, &support::rom_path(name).unwrap(), &m.name));
            if c.get(f) == Some(&seq[f]) {
                t.stale_eq_c += 1;
            } else {
                t.bad.push(format!("{sname}: frame {f} differs from the C reference"));
            }
        }
        // cold starts at every chunk start
        for p in m.seek_points() {
            let mut s = AdsVideo::new(&rom, &cart, i).unwrap();
            s.seek(p).unwrap();
            let f = s.next_frame().unwrap().unwrap();
            t.seeks += 1;
            if md5_hex(f.manifest_bytes()) == seq[p as usize] {
                t.seeks_ok += 1;
            } else {
                t.bad.push(format!("{sname}: cold seek to frame {p} differs"));
            }
        }
        // audio: per 16458-sample block, the last only as far as the cartridge decoded it
        let Some(arows) = read_manifest(&man.join(format!("{sname}.audio_md5.csv.gz"))) else { continue };
        let mut a = gbav_ads::Ads::audio(&rom, &sid).unwrap();
        let mut blocks = Vec::new();
        while let Some(b) = a.next_block().unwrap() {
            blocks.push(md5_hex(&b));
            let r = arows.get(blocks.len() - 1);
            if let Some(r) = r {
                let n: usize = r[1].parse().unwrap();
                t.audio += 1;
                t.samples += n;
                if b.len() >= n && md5_hex(&b[..n]) == r[2] {
                    t.audio_ok += 1;
                } else {
                    t.bad.push(format!("{sname}: audio block {} differs", r[0]));
                }
            }
        }
        if blocks.len() < arows.len() {
            t.bad.push(format!("{sname}: only {} of {} audio blocks decoded", blocks.len(), arows.len()));
        }
        for (k, want) in blocks.iter().enumerate() {
            a.seek_block(k).unwrap();
            let b = a.next_block().unwrap().unwrap();
            t.aseeks += 1;
            if &md5_hex(&b) == want {
                t.aseeks_ok += 1;
            } else {
                t.bad.push(format!("{sname}: cold seek to audio block {k} differs"));
            }
        }
    }
    t
}

#[test]
fn ads_family_matches_manifests() {
    let (Some(_), Some(suite)) = (support::roms_dir(), support::cref_dir()) else {
        return eprintln!("skip: GBAV_ROMS / GBAV_CREF not set");
    };
    let jobs: Vec<(&str, &str)> = CARTS.iter().copied().filter(|(s, _)| support::only(s)).collect();
    let t0 = std::time::Instant::now();
    let res = par_map(&jobs, |&(cid, name)| {
        let t = verify_cart(cid, name, &suite);
        eprintln!(
            "{cid:12} streams {:2} frames {}/{} (stale {} explained {} = C {}) audio {}/{} seeks {}/{} audio seeks {}/{}",
            t.streams, t.frames_ok, t.frames, t.stale, t.stale_explained, t.stale_eq_c, t.audio_ok, t.audio, t.seeks_ok,
            t.seeks, t.aseeks_ok, t.aseeks
        );
        t
    });
    let mut tot = Tally::default();
    for t in &res {
        tot.add(t);
    }
    eprintln!(
        "TOTAL {} streams: frames {}/{} (+{} stale-codebook frames: {} reproduced with the old codebook, {} equal to the C reference), \
         audio blocks {}/{} ({} samples), chunk seeks {}/{}, audio block seeks {}/{} in {:.1}s",
        tot.streams, tot.frames_ok, tot.frames, tot.stale, tot.stale_explained, tot.stale_eq_c, tot.audio_ok, tot.audio,
        tot.samples, tot.seeks_ok, tot.seeks, tot.aseeks_ok, tot.aseeks, t0.elapsed().as_secs_f64()
    );
    for b in tot.bad.iter().take(30) {
        eprintln!("  {b}");
    }
    assert!(tot.bad.is_empty());
    assert_eq!(tot.frames_ok + tot.stale, tot.frames);
    assert_eq!(tot.audio_ok, tot.audio);
}
