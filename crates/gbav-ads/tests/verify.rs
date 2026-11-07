//! Whole-family verification against `verify/ads/` (needs `GBAV_ROMS`): every frame and the audio
//! (per block when the large per-block manifests are present, else the ~30 s segment manifests)
//! of every stream of the 19 ADS carts and the two Hydrogen-packaged LZMA carts, cold-start seeks
//! at every chunk start and audio block, and the trait's audio seek.
//!
//! The 41 frames of the Noah proto intro that the cartridge shows with a stale codebook are
//! checked three ways: rendering the same index plane with the codebook of an earlier chunk
//! reproduces the cartridge's framebuffer; they are exactly the `verify/known.csv` ranges, so the
//! generic checker (`gbav_common::manifest::check_video`) accepts them; and, with `GBAV_CDIFF=1`,
//! the decoder's frame equals the C reference's (`adsdec` built from `reference/`, the intended
//! output).
//!
//! `GBAV_ROMS=... [GBAV_CDIFF=1] cargo test --release -p gbav-ads --test verify -- --nocapture`

use gbav_ads::{AdsCart, AdsVideo};
use gbav_common::manifest::{self, Manifest};
use gbav_common::md5;
use gbav_common::{AudioDecoder, Codec, Frame, VideoDecoder};
use gbav_majesco::audio::FRAME_SAMPLES_X16;
use gbav_majesco::vq::{self, Row159};
use gbav_testkit as tk;
use std::path::{Path, PathBuf};

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
    audio_unit: &'static str,
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
        s!(streams, frames, frames_ok, stale, stale_explained, stale_eq_c, audio, audio_ok, seeks, seeks_ok, aseeks, aseeks_ok);
        self.bad.extend(o.bad.iter().cloned());
    }
}

/// `adsdec` built from `reference/` into the test tmpdir (once), when `GBAV_CDIFF=1`.
fn adsdec() -> Option<PathBuf> {
    static EXE: std::sync::OnceLock<Option<PathBuf>> = std::sync::OnceLock::new();
    EXE.get_or_init(|| {
        let r = tk::cref_dir()?;
        let out = Path::new(env!("CARGO_TARGET_TMPDIR")).join("cref").join("adsdec");
        std::fs::create_dir_all(out.parent().unwrap()).ok()?;
        let st = std::process::Command::new(std::env::var("CC").unwrap_or_else(|_| "cc".into()))
            .args(["-O2", "-std=c99", "-D_POSIX_C_SOURCE=200809L", "-w", "-o"])
            .arg(&out)
            .args(["ads/adsdec_main.c", "ads/ads.c", "ads/lzma_dec.c", "majesco/adpcm.c", "majesco/vq.c"].map(|f| r.join(f)))
            .status()
            .expect("run the C compiler");
        assert!(st.success(), "building adsdec failed");
        Some(out)
    })
    .clone()
}

/// `adsdec ROM --stream NAME --md5` (the C reference), frame -> md5.
fn c_md5s(exe: &Path, rom: &Path, name: &str) -> Vec<String> {
    let out = std::process::Command::new(exe).arg(rom).args(["--stream", name, "--md5"]).output().expect("run adsdec");
    String::from_utf8(out.stdout).unwrap().lines().map(|l| l.split_whitespace().nth(1).unwrap().to_owned()).collect()
}

fn verify_cart(cid: &str) -> Tally {
    let mut t = Tally::default();
    let Some(rom) = tk::load_rom(cid) else {
        t.bad.push(format!("{cid}: ROM missing"));
        return t;
    };
    let cart = AdsCart::open(&rom).unwrap();
    for (i, m) in cart.movies.iter().enumerate() {
        let sid = m.id();
        let sname = format!("{cid}_{sid}");
        let paths = tk::stream_manifests("ads", cid, &sid).unwrap();
        let Some(fm) = Manifest::load(&paths.frames).unwrap() else {
            t.bad.push(format!("{sname}: no manifest"));
            continue;
        };
        t.streams += 1;
        let mut v = AdsVideo::new(&rom, &cart, i).unwrap();
        let mut seq = Vec::new();
        let mut planes = Vec::new();
        while let Some(f) = v.next_frame().unwrap_or_else(|e| panic!("{sname}: {e}")) {
            seq.push(md5::hex(f.manifest_bytes()));
            planes.push((f.segment as usize, f.mode, f.index.clone()));
        }
        assert_eq!(seq.len() as u32, m.total_frames(), "{sname} frames");
        assert_eq!(fm.rows.len(), seq.len(), "{sname}: manifest rows");
        let mut c_ref: Option<Vec<String>> = None;
        let mut stale = Vec::new();
        for r in &fm.rows {
            let f: usize = r[0].parse().unwrap();
            t.frames += 1;
            if seq[f] == r[1] {
                t.frames_ok += 1;
                continue;
            }
            // not what the cartridge showed: is it the cartridge showing a stale codebook?
            t.stale += 1;
            stale.push(f as u64);
            let (ci, mode, ref idx) = planes[f];
            let hit = (1..=16).filter(|&k| k <= ci).find(|&k| {
                let cb = v.segment_vcb(ci - k).unwrap();
                let mut fb = vec![0u16; 240 * 160];
                vq::blit(idx, vq::Mode::new(mode as u32), &cb, Row159::BLACK, &mut fb).unwrap();
                md5::hex_u16(&fb) == r[1]
            });
            if let Some(k) = hit {
                t.stale_explained += 1;
                if t.stale_explained <= 3 || f.is_multiple_of(100) {
                    eprintln!("{sname}: frame {f} (chunk {ci}) is shown by the cartridge with the codebook of chunk {}", ci - k);
                }
            } else {
                t.bad.push(format!("{sname}: frame {f} differs and is not a stale codebook"));
            }
            if let Some(exe) = adsdec() {
                let c = c_ref.get_or_insert_with(|| c_md5s(&exe, &tk::rom_path(cid).unwrap(), &m.name));
                if c.get(f) == Some(&seq[f]) {
                    t.stale_eq_c += 1;
                } else {
                    t.bad.push(format!("{sname}: frame {f} differs from the C reference"));
                }
            }
        }
        // the stale frames are exactly verify/known.csv's ranges, and the generic checker agrees
        let known = tk::known("ads", cid, &sid);
        let in_known: Vec<u64> = (0..seq.len() as u64).filter(|&f| known.iter().any(|k| k.kind == "video" && k.contains(f))).collect();
        if in_known != stale {
            t.bad.push(format!("{sname}: known.csv video ranges {in_known:?} != stale frames {stale:?}"));
        }
        let kref: Vec<&manifest::Known> = known.iter().collect();
        let gc = manifest::check_video(&mut AdsVideo::new(&rom, &cart, i).unwrap(), &fm, &kref).unwrap();
        if !gc.passed() || gc.known_bad != stale.len() as u64 {
            t.bad.push(format!("{sname}: generic check_video {gc}"));
        }
        // cold starts at every chunk start
        for p in m.seek_points() {
            let mut s = AdsVideo::new(&rom, &cart, i).unwrap();
            s.seek(p).unwrap();
            let f = s.next_frame().unwrap().unwrap();
            t.seeks += 1;
            if md5::hex(f.manifest_bytes()) == seq[p as usize] {
                t.seeks_ok += 1;
            } else {
                t.bad.push(format!("{sname}: cold seek to frame {p} differs"));
            }
        }
        if m.audio.is_none() {
            continue;
        }
        // audio: per 16458-sample block (the last only as far as the cartridge decoded it), else segments
        let mut a = gbav_ads::Ads::audio(&rom, &sid).unwrap();
        let mut blocks = Vec::new();
        let mut all: Vec<i8> = Vec::new();
        let am = Manifest::load(&paths.audio).unwrap();
        while a.next_block().unwrap() {
            let b = a.manifest_part(0);
            blocks.push(md5::hex(b));
            all.extend_from_slice(a.last_samples());
            if let Some(r) = am.as_ref().and_then(|am| am.rows.get(blocks.len() - 1)) {
                let n: usize = r[1].parse().unwrap();
                t.audio += 1;
                if b.len() >= n && md5::hex(&b[..n]) == r[2] {
                    t.audio_ok += 1;
                } else {
                    t.bad.push(format!("{sname}: audio block {} differs", r[0]));
                }
            }
        }
        match &am {
            Some(am) if blocks.len() < am.rows.len() => {
                t.bad.push(format!("{sname}: only {} of {} audio blocks decoded", blocks.len(), am.rows.len()))
            }
            Some(_) => t.audio_unit = "blocks",
            None => match Manifest::load(&paths.audio_seg).unwrap() {
                Some(sm) => {
                    let c = manifest::check_audio_segments(&mut gbav_ads::Ads::audio(&rom, &sid).unwrap(), &sm, 1).unwrap();
                    t.audio += c.total as usize;
                    t.audio_ok += c.ok as usize;
                    t.audio_unit = c.unit;
                    if !c.passed() {
                        t.bad.push(format!("{sname}: audio {c}"));
                    }
                }
                None => eprintln!("{sname}: no audio manifest, audio not checked"),
            },
        }
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
        for p in m.seek_points().into_iter().take(8) {
            let first = (p as u64 * FRAME_SAMPLES_X16 / 16) as usize;
            if first + 1000 > all.len() {
                continue;
            }
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
    t
}

#[test]
fn ads_family_matches_manifests() {
    if tk::roms_dir().is_none() || tk::verify_dir().is_none() {
        return tk::skip("GBAV_ROMS not set (or verify/ not found)");
    }
    let jobs: Vec<&str> = CARTS.iter().map(|c| c.0).filter(|s| tk::only(s)).collect();
    let t0 = std::time::Instant::now();
    let res = tk::par_map(&jobs, |&cid| {
        let t = verify_cart(cid);
        eprintln!(
            "{cid:12} streams {:2} frames {}/{} (stale {} explained {} = C {}) audio {}/{} {} seeks {}/{} audio seeks {}/{}",
            t.streams, t.frames_ok, t.frames, t.stale, t.stale_explained, t.stale_eq_c, t.audio_ok, t.audio, t.audio_unit,
            t.seeks_ok, t.seeks, t.aseeks_ok, t.aseeks
        );
        t
    });
    let mut tot = Tally::default();
    for t in &res {
        tot.add(t);
    }
    eprintln!(
        "TOTAL {} streams: frames {}/{} (+{} stale-codebook frames: {} reproduced with the old codebook, {} equal to the C reference), \
         audio {}/{} (blocks or segments), chunk seeks {}/{}, audio seeks {}/{} in {:.1}s",
        tot.streams, tot.frames_ok, tot.frames, tot.stale, tot.stale_explained, tot.stale_eq_c, tot.audio_ok, tot.audio,
        tot.seeks_ok, tot.seeks, tot.aseeks_ok, tot.aseeks, t0.elapsed().as_secs_f64()
    );
    for b in tot.bad.iter().take(30) {
        eprintln!("  {b}");
    }
    assert!(tot.bad.is_empty());
    assert_eq!(tot.frames_ok + tot.stale, tot.frames);
    assert_eq!(tot.audio_ok, tot.audio);
}
