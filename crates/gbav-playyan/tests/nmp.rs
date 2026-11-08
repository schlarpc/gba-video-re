//! Nintendo MP3 Player DSP decoder (gbav_playyan::nmp) against hashes of the DSP firmware's own
//! output (the C54x emulator, which is local and not in this repository; harness tools/playyan/nmp/oracle_md5.py).
//!
//! Gates: `PY_ROMS` (directory with `Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba`) and
//! `PY_MEDIA` (Nintendo's sample files, `playan_support_sample_music/sample_music.mp3`); without
//! them the tests print a notice and pass.
//!
//! `NMP_FILE=x.mp3 NMP_OUT=x.raw cargo test -p gbav-playyan --test nmp -- --ignored nmp_dump`
//! writes the synthesis output of any file (int16 LE, 576 per granule and channel, DSP call order)
//! for comparison with tools/playyan/nmp/oracle_md5.py.

use gbav_common::md5::Md5;
use gbav_playyan::nmp;
use std::path::PathBuf;

const ROM_NAME: &str = "Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba";

fn rom() -> Option<Vec<u8>> {
    std::fs::read(gbav_testkit::playyan_roms()?.join(ROM_NAME)).ok()
}

fn media(rel: &str) -> Option<Vec<u8>> {
    let d = PathBuf::from(std::env::var_os("PY_MEDIA")?);
    std::fs::read(d.join(rel)).ok()
}

/// Decode a whole file; returns (number of synthesis calls, md5 over them, md5 after 1000 calls).
fn decode_md5(rom: &[u8], file: &[u8]) -> (usize, String, Option<String>) {
    let mut dec = nmp::Decoder::from_rom(rom).expect("NMP ROM");
    let mut h = Md5::new();
    let mut n = 0;
    let mut cp = None;
    for fr in nmp::frames(file) {
        dec.decode_frame(fr).expect("decode");
        for call in dec.calls() {
            for s in call {
                h.update(&s.to_le_bytes());
            }
            n += 1;
            if n == 1000 {
                cp = Some(h.clone().finalize_hex());
            }
        }
    }
    (n, h.finalize_hex(), cp)
}

#[test]
fn nmp_tables_from_rom() {
    let Some(rom) = rom() else {
        eprintln!("PY_ROMS not set or NMP ROM missing; skipped");
        return;
    };
    let dsp = nmp::Dsp::from_rom(&rom).expect("boot table + cinit");
    assert_eq!(dsp.data.len(), 0x10000);
}

/// Every granule and channel of Nintendo's sample, bit-exact against the DSP.
#[test]
fn nmp_sample_music_bit_exact() {
    let (Some(rom), Some(file)) = (rom(), media("playan_support_sample_music/sample_music.mp3")) else {
        eprintln!("PY_ROMS / PY_MEDIA not set; skipped");
        return;
    };
    let (n, md5, cp) = decode_md5(&rom, &file);
    assert_eq!(cp.as_deref(), Some(SAMPLE_MUSIC_MD5_1000), "first 1000 synthesis calls");
    assert_eq!((n, md5.as_str()), (SAMPLE_MUSIC_CALLS, SAMPLE_MUSIC_MD5), "whole file");
}

#[test]
#[ignore]
fn nmp_dump() {
    let (Some(rom), Some(f), Some(out)) = (rom(), std::env::var_os("NMP_FILE"), std::env::var_os("NMP_OUT")) else {
        eprintln!("NMP_FILE / NMP_OUT / PY_ROMS not set");
        return;
    };
    let file = std::fs::read(f).unwrap();
    let mut dec = nmp::Decoder::from_rom(&rom).unwrap();
    let mut raw = Vec::new();
    for fr in nmp::frames(&file) {
        match dec.decode_frame(fr) {
            Ok(_) => {}
            Err(e) => {
                eprintln!("stopped: {e}");
                break;
            }
        }
        for call in dec.calls() {
            for s in call {
                raw.extend_from_slice(&s.to_le_bytes());
            }
        }
    }
    std::fs::write(out, raw).unwrap();
}

// Oracle: tools/playyan/nmp/oracle_md5.py on sample_music.mp3 (DSP synthesis output, all calls).
const SAMPLE_MUSIC_CALLS: usize = 22120;
const SAMPLE_MUSIC_MD5: &str = "4994d7e896e252d361f4bd6d62d3706b";
const SAMPLE_MUSIC_MD5_1000: &str = "12382117c6ae8cd7801970ef3c3b42f0";

/// LAME test streams and crafted intensity-stereo / mixed-block streams (MPEG-1 and MPEG-2 / 2.5,
/// including the MPEG-2 mixed reader's zero path and its 8 kHz band layout)
/// (tools/playyan/nmp/make_test_mp3.sh -> `NMP_TEST_DIR`) against the DSP oracle's
/// hashes in verify/playyan/oracle/nmp/nmp_manifest.csv: short/start/stop blocks, M/S, L/R, mono, 32/44.1/48 kHz,
/// MPEG-2 22.05/24/16 kHz, MPEG-2.5 11.025/8 kHz, VBR, clipping signals.  The DSP stops one or
/// two granules before the end of these files (player behaviour), so only its call count is hashed.
#[test]
fn nmp_lame_streams_bit_exact() {
    let (Some(rom), Some(dir)) = (rom(), std::env::var_os("NMP_TEST_DIR")) else {
        eprintln!("PY_ROMS / NMP_TEST_DIR not set; skipped");
        return;
    };
    let man = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../verify/playyan/oracle/nmp/nmp_manifest.csv");
    let man = std::fs::read_to_string(man).expect("nmp_manifest.csv");
    let mut checked = 0;
    for line in man.lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
        let f: Vec<&str> = line.split(',').collect();
        let (name, calls, md5) = (f[0], f[1].parse::<usize>().unwrap(), f[2]);
        let Ok(file) = std::fs::read(PathBuf::from(&dir).join(name)) else {
            eprintln!("{name}: missing, skipped");
            continue;
        };
        let mut dec = nmp::Decoder::from_rom(&rom).unwrap();
        let mut h = Md5::new();
        let mut n = 0;
        'outer: for fr in nmp::frames(&file) {
            dec.decode_frame(fr).unwrap_or_else(|e| panic!("{name}: {e}"));
            for call in dec.calls() {
                if n == calls {
                    break 'outer;
                }
                for s in call {
                    h.update(&s.to_le_bytes());
                }
                n += 1;
            }
        }
        assert_eq!((n, h.finalize_hex()), (calls, md5.to_string()), "{name}");
        checked += 1;
    }
    eprintln!("{checked} streams bit-exact");
}

/// AUDIO_INFO (ID3 + length) against the DSP: tools/playyan/nmp/make_id3_tests.py -> `NMP_ID3_DIR`,
/// expected values in verify/playyan/oracle/nmp/nmp_id3_manifest.csv; sample_music.mp3 from `PY_MEDIA`.
#[test]
fn nmp_id3_matches_dsp() {
    let man = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../verify/playyan/oracle/nmp/nmp_id3_manifest.csv");
    let man = std::fs::read_to_string(man).expect("nmp_id3_manifest.csv");
    let dir = std::env::var_os("NMP_ID3_DIR").map(PathBuf::from);
    let mut checked = 0;
    for line in man.lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
        let f: Vec<&str> = line.split(',').collect();
        let data = if f[0] == "sample_music.mp3" {
            media("playan_support_sample_music/sample_music.mp3")
        } else {
            dir.as_ref().and_then(|d| std::fs::read(d.join(f[0])).ok())
        };
        let Some(data) = data else { continue };
        let units = |s: &str| -> Vec<u16> {
            if s.is_empty() { Vec::new() } else { s.split('.').map(|x| u16::from_str_radix(x, 16).unwrap()).collect() }
        };
        let want = nmp::Id3 { encoding: f[1].parse().unwrap(), seconds: f[2].parse().unwrap(), title: units(f[3]), artist: units(f[4]) };
        assert_eq!(nmp::id3(&data), want, "{}", f[0]);
        checked += 1;
    }
    eprintln!("{checked} files checked");
}

/// The 8-bit speaker stream (no headphones) against the DSP: verify/playyan/oracle/nmp/nmp_spk_manifest.csv,
/// files from `NMP_TEST_DIR` (make_test_mp3.sh) and `PY_MEDIA` (sample_music.mp3).
#[test]
fn nmp_speaker_stream_matches_dsp() {
    let Some(rom) = rom() else {
        eprintln!("PY_ROMS not set; skipped");
        return;
    };
    let man = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../verify/playyan/oracle/nmp/nmp_spk_manifest.csv");
    let man = std::fs::read_to_string(man).expect("nmp_spk_manifest.csv");
    let dir = std::env::var_os("NMP_TEST_DIR").map(PathBuf::from);
    let mut checked = 0;
    for line in man.lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
        let f: Vec<&str> = line.split(',').collect();
        let (name, rate, nblocks, md5) = (f[0], f[1].parse::<u32>().unwrap(), f[3].parse::<usize>().unwrap(), f[4]);
        let data = if name == "sample_music.mp3" {
            media("playan_support_sample_music/sample_music.mp3")
        } else {
            dir.as_ref().and_then(|d| std::fs::read(d.join(name)).ok())
        };
        let Some(data) = data else { continue };
        let dsp = nmp::Dsp::from_rom(&rom).unwrap();
        let mut dec = nmp::Decoder::new(dsp.clone()).unwrap();
        let mut sp = nmp::SpeakerStream::new(&dsp, rate).unwrap();
        let mut blocks = Vec::new();
        for fr in nmp::frames(&data) {
            dec.decode_frame(fr).unwrap();
            let calls = dec.calls();
            let nch = if nmp::header(fr).unwrap().mode == 3 { 1 } else { 2 };
            for g in calls.chunks(nch) {
                blocks.extend(sp.push(&g[0], &g[nch - 1]));
            }
            if blocks.len() >= nblocks {
                break;
            }
        }
        let mut h = Md5::new();
        for b in blocks.iter().take(nblocks) {
            for w in b.ch1.iter().chain(b.ch2.iter()) {
                h.update(&w.to_le_bytes());
            }
        }
        assert!(blocks.len() >= nblocks, "{name}: only {} blocks", blocks.len());
        assert_eq!(h.finalize_hex(), md5, "{name}");
        checked += 1;
    }
    eprintln!("{checked} speaker streams bit-exact");
}
