//! Shared pieces of the ROM-gated verification tests.
//!
//! Environment:
//!   GBAV_ROMS              directory with the No-Intro named ROMs (tests skip without it)
//!   GBAV_VX_MANIFESTS      optional extra manifest directory (`<cart>_<off>.frame_md5.csv.gz`,
//!                          `<cart>_<off>.audio_md5.csv.gz`: the suite capture of all 10 streams);
//!                          the repo's verify/ is searched first for frame manifests
//!   GBAV_VX_PCM_REF        optional directory of `<cart>_<off>.pcm.csv.gz` files written by
//!                          tests/pcm_ref.py (container/vxaudio.py's 16 kHz samples per AFrame)
#![allow(dead_code)]

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;

pub struct Cart {
    pub file: &'static str,
    pub short: &'static str,
    /// sha1 key in verify/streams.json (None: not listed there)
    pub sha1: Option<&'static str>,
    pub streams: &'static [usize],
}

pub const CARTS: &[Cart] = &[
    Cart {
        file: "Game Boy Advance Video - Shrek (USA) (Rev 6).gba",
        short: "shrek-rev6",
        sha1: Some("95d612057682b7b886441d387b167ba2493e49ef"),
        streams: &[0x20200],
    },
    Cart {
        file: "Game Boy Advance Video - Shrek (USA) (Rev 5).gba",
        short: "shrek-rev5",
        sha1: Some("2dedea86ec289a0e31089299613d9f74cca03609"),
        streams: &[0x20200],
    },
    Cart {
        file: "Game Boy Advance Video - Shrek 2 (USA) (Rev 6).gba",
        short: "shrek2-rev6",
        sha1: Some("3bcb4acc5da539bc1b91c9537bf7ffa081ded1d4"),
        streams: &[0x20200],
    },
    Cart {
        file: "Game Boy Advance Video - Shrek 2 (USA) (Rev 5).gba",
        short: "shrek2-rev5",
        sha1: None,
        streams: &[0x20200],
    },
    Cart {
        file: "Game Boy Advance Video - Shark Tale (USA) (Rev 6).gba",
        short: "sharktale-rev6",
        sha1: Some("9bc5b60793b8a6de3b80eb2c213cd125e1fa468e"),
        streams: &[0x20200],
    },
    Cart {
        file: "Game Boy Advance Video - Shark Tale (USA) (Rev 5).gba",
        short: "sharktale-rev5",
        sha1: None,
        streams: &[0x20200],
    },
    Cart {
        file: "Game Boy Advance Video - Shrek + Shark Tale (USA) (Rev 5).gba",
        short: "shrek-sharktale-rev5",
        sha1: Some("f23390b7a62c605fbce6730addc29d381488e076"),
        streams: &[0x20200, 0x1eef800, 0x3dd3600, 0x3e76a00],
    },
];

/// First audio block the emulated cart played from a misaligned window (an emulator-side
/// audio underrun, docs perf-vx): blocks from here on are not comparable.
pub fn known_underrun(short: &str, off: usize) -> Option<usize> {
    (short == "shrek2-rev5" && off == 0x20200).then_some(296039)
}

pub fn roms_dir() -> Option<PathBuf> {
    std::env::var_os("GBAV_ROMS")
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
}

pub fn cart(short: &str) -> &'static Cart {
    CARTS.iter().find(|c| c.short == short).unwrap()
}

pub fn load_rom(c: &Cart) -> Option<Vec<u8>> {
    let p = roms_dir()?.join(c.file);
    match std::fs::read(&p) {
        Ok(b) => Some(b),
        Err(e) => {
            eprintln!("skip: {}: {e}", p.display());
            None
        }
    }
}

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// gunzip via the system `gzip` (test-only; keeps the crate dependency-free).
pub fn gunzip(p: &Path) -> String {
    let out = Command::new("gzip")
        .arg("-dc")
        .arg(p)
        .output()
        .expect("run gzip");
    assert!(out.status.success(), "gzip -dc {} failed", p.display());
    String::from_utf8(out.stdout).unwrap()
}

fn find_manifest(name: &str) -> Option<PathBuf> {
    let mut dirs = vec![repo_root().join("verify")];
    if let Some(d) = std::env::var_os("GBAV_VX_MANIFESTS") {
        dirs.push(PathBuf::from(d));
    }
    dirs.into_iter().map(|d| d.join(name)).find(|p| p.exists())
}

/// frame -> (yuv_md5, vram_md5 or "" when the frame was never displayed)
pub fn frame_manifest(short: &str, off: usize) -> Option<(PathBuf, Vec<(String, String)>)> {
    let name = if short == "shrek-rev6" && off == 0x20200 {
        "frame_md5.csv.gz".to_string()
    } else {
        format!("{short}_{off:x}.frame_md5.csv.gz")
    };
    let p = find_manifest(&name)?;
    let txt = gunzip(&p);
    let mut v = Vec::new();
    for (i, line) in txt.lines().skip(1).enumerate() {
        let f: Vec<&str> = line.trim_end().split(',').collect();
        assert_eq!(
            f[0].parse::<usize>().unwrap(),
            i,
            "manifest {} not in frame order",
            p.display()
        );
        v.push((f[1].to_string(), f.get(2).unwrap_or(&"").to_string()));
    }
    Some((p, v))
}

/// Per-block md5 of the 512-byte blocks the emulated cart played.
pub fn audio_block_manifest(short: &str, off: usize) -> Option<Vec<String>> {
    let p = find_manifest(&format!("{short}_{off:x}.audio_md5.csv.gz"))?;
    Some(
        gunzip(&p)
            .lines()
            .skip(1)
            .map(|l| l.split(',').nth(1).unwrap().trim().to_string())
            .collect(),
    )
}

/// (audio_blocks, audio_md5) from verify/streams.json, where md5 = md5 of the concatenated
/// hex md5s of the played blocks.
pub fn streams_json_audio(sha1: &str, off: usize) -> Option<(usize, String)> {
    let js = std::fs::read_to_string(repo_root().join("verify/streams.json")).ok()?;
    let cart = &js[js.find(&format!("\"{sha1}\""))?..];
    let cart = &cart[..cart[1..].find("\n }").map(|i| i + 1).unwrap_or(cart.len())];
    let st = &cart[cart.find(&format!("\"0x{off:x}\""))?..];
    let num = |k: &str| -> Option<String> {
        let s = &st[st.find(&format!("\"{k}\": "))? + k.len() + 4..];
        let e = s.find([',', '\n']).unwrap();
        Some(s[..e].trim().trim_matches('"').to_string())
    };
    Some((num("audio_blocks")?.parse().ok()?, num("audio_md5")?))
}

/// vxaudio.py reference: per AFrame (pcm md5 of 128 x i32 LE, 8-bit output md5).
pub fn pcm_reference(short: &str, off: usize) -> Option<Vec<(String, String)>> {
    let d = PathBuf::from(std::env::var_os("GBAV_VX_PCM_REF")?);
    let p = d.join(format!("{short}_{off:x}.pcm.csv.gz"));
    if !p.exists() {
        return None;
    }
    Some(
        gunzip(&p)
            .lines()
            .skip(1)
            .map(|l| {
                let f: Vec<&str> = l.split(',').collect();
                (f[1].to_string(), f[2].to_string())
            })
            .collect(),
    )
}

pub fn index_of(v: &[(String, String)]) -> HashMap<String, usize> {
    v.iter()
        .enumerate()
        .map(|(i, (y, _))| (y.clone(), i))
        .collect()
}

/// Frame start bits from the independent grammar walker (container/vxvwalk.py), when the repo
/// ships them: nframes + 1 entries (the last = end of the last frame).
pub fn frame_bits(short: &str, off: usize) -> Option<Vec<u32>> {
    let name = if short == "shrek-rev6" && off == 0x20200 {
        "frame_bits.json.gz".to_string()
    } else {
        format!("{short}_{off:x}.frame_bits.json.gz")
    };
    let txt = gunzip(&find_manifest(&name)?);
    let s = &txt[txt.find("\"frame_start_bits\"")?..];
    let s = &s[s.find('[')? + 1..];
    let s = &s[..s.find(']')?];
    Some(s.split(',').map(|x| x.trim().parse().unwrap()).collect())
}
