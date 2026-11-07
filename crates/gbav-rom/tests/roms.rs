//! Real-ROM checks, gated on `GBAV_ROMS` (a directory of `<No-Intro name>.gba` files):
//!
//! ```sh
//! GBAV_ROMS=/path/to/roms cargo test -p gbav-rom --release
//! ```
//!
//! `nointro.csv` was filled from this crate's classifier and checked against the triage
//! classifier (`classify_carts.py`) on all 46 available carts, so agreement with the table is
//! agreement with the classifier (except the intended Sonic X Vol 2 proto family: hydrogen,
//! where the triage classifier said UNKNOWN).

use std::path::PathBuf;
use std::time::Instant;

use gbav_rom::{identify, Family, NoIntroMatch, OverdumpTail};

fn roms() -> Option<Vec<PathBuf>> {
    let Some(dir) = std::env::var_os("GBAV_ROMS") else {
        eprintln!("GBAV_ROMS not set; skipping real-ROM tests");
        return None;
    };
    let mut v: Vec<PathBuf> = std::fs::read_dir(&dir)
        .expect("GBAV_ROMS is a readable directory")
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "gba"))
        .collect();
    v.sort();
    assert!(!v.is_empty(), "no .gba files in {dir:?}");
    Some(v)
}

fn stem(p: &std::path::Path) -> String {
    p.file_stem().unwrap().to_string_lossy().into_owned()
}

#[test]
fn every_rom_matches_its_table_row() {
    let Some(files) = roms() else { return };
    let mut slowest = (0.0f64, String::new());
    for path in &files {
        let rom = std::fs::read(path).unwrap();
        let t = Instant::now();
        let id = identify(&rom);
        let secs = t.elapsed().as_secs_f64();
        if secs > slowest.0 {
            slowest = (secs, stem(path));
        }
        let name = stem(path);
        let NoIntroMatch::Exact(e) = id.nointro else {
            panic!("{name}: no exact No-Intro match ({})", id.sha1)
        };
        assert_eq!(e.name, name);
        assert_eq!(e.sha1, id.sha1);
        assert_eq!(e.size, rom.len() as u64, "{name}");
        let c = &id.classification;
        assert_eq!(c.family, e.family, "{name}: family");
        assert_eq!(c.engine(), e.engine, "{name}: engine");
        assert_eq!(c.build_id, e.build, "{name}: build id");
        assert!(
            c.family.is_some(),
            "{name}: every dumped cart has a known family"
        );
        assert!(!c.streams.is_empty(), "{name}: no streams");
        match c.family {
            Some(Family::Vx) => {
                let v = c.vx_variant.expect("vx variant");
                assert!(
                    e.notes.contains(&format!("{} stream", v.as_str())),
                    "{name}: variant {v:?}"
                );
            }
            _ => assert_eq!(c.vx_variant, None),
        }
        for s in &c.streams {
            assert!(
                s.width == 240 && (s.height == 160 || s.height == 112) && s.frames > 0,
                "{name}: {s:?}"
            );
        }
    }
    eprintln!(
        "{} ROMs identified; slowest {:.3} s ({})",
        files.len(),
        slowest.0,
        slowest.1
    );
    assert!(
        slowest.0 < 2.0,
        "identify took {:.3} s on {}",
        slowest.0,
        slowest.1
    );
}

/// A 32 MiB cart padded with 0xFF (and mirrored) to 64 MiB is reported as that cart, overdumped.
#[test]
fn overdump_is_recognised() {
    let Some(files) = roms() else { return };
    let path = files
        .iter()
        .find(|p| stem(p).contains("Disney Channel Collection - Volume 2"))
        .or_else(|| {
            files
                .iter()
                .find(|p| std::fs::metadata(p).unwrap().len() == 32 << 20)
        })
        .expect("a 32 MiB ROM");
    let rom = std::fs::read(path).unwrap();
    let plain = identify(&rom);
    let entry = plain.nointro.entry().expect("exact match");

    let mut padded = rom.clone();
    padded.resize(64 << 20, 0xff);
    let t = Instant::now();
    let id = identify(&padded);
    eprintln!("overdump identify: {:.3} s", t.elapsed().as_secs_f64());
    assert_eq!(
        id.nointro,
        NoIntroMatch::Overdump {
            entry,
            tail: OverdumpTail::Padding
        }
    );
    assert!(id.nointro.is_overdump());
    assert_eq!(id.size, 64 << 20);
    assert_ne!(id.sha1, plain.sha1);
    assert_eq!(id.classification, plain.classification);

    let mut mirrored = rom.clone();
    mirrored.extend_from_slice(&rom);
    assert_eq!(
        identify(&mirrored).nointro,
        NoIntroMatch::Overdump {
            entry,
            tail: OverdumpTail::Mirror
        }
    );

    // One changed byte in the tail or the data: no match.
    padded[(48 << 20) + 7] = 0;
    assert_eq!(identify(&padded).nointro, NoIntroMatch::None);
    let mut changed = rom;
    changed[0x100] ^= 1;
    assert_eq!(identify(&changed).nointro, NoIntroMatch::None);
}
