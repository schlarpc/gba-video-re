//! Shared pieces of the ROM-gated verification tests (gbav-testkit underneath).
//!
//! Environment:
//!   GBAV_ROMS              directory with your ROMs, No-Intro names or verify/carts.csv short
//!                          ids (tests skip without it)
//!   GBAV_VERIFY            the verify/ directory (default: this source tree's)
//!   GBAV_VX_PCM_REF        optional directory of `<cart>_<off>.pcm.csv.gz` files written by
//!                          tests/pcm_ref.py (tools/vx/container/vxaudio.py's 16 kHz samples
//!                          per AFrame)
#![allow(dead_code)]

use gbav_common::manifest::Manifest;
use gbav_common::Rom;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub use gbav_testkit as kit;

pub struct Cart {
    pub short: String,
    pub sha1: String,
}

/// A VX cart from verify/carts.csv.
pub fn cart(short: &str) -> Cart {
    let c = kit::carts("vx")
        .into_iter()
        .find(|c| c.short_id == short)
        .unwrap_or_else(|| panic!("{short} not in verify/carts.csv"));
    Cart {
        short: c.short_id,
        sha1: c.sha1,
    }
}

pub fn roms_dir() -> Option<PathBuf> {
    kit::roms_dir()
}

pub fn load_rom(c: &Cart) -> Option<Rom> {
    let r = kit::load_rom(&c.short);
    if r.is_none() {
        eprintln!("skip: GBAV_ROMS not set or no ROM for {}", c.short);
    }
    r
}

pub fn verify_dir() -> PathBuf {
    kit::verify_dir().expect("verify/ directory (set GBAV_VERIFY)")
}

fn vx_file(short: &str, off: usize, ext: &str) -> PathBuf {
    verify_dir().join("vx").join(format!("{short}_{off:x}.{ext}"))
}

fn load(p: &Path) -> Option<Manifest> {
    Manifest::load(p).unwrap_or_else(|e| panic!("{}: {e}", p.display()))
}

/// frame -> (yuv_md5, vram_md5 or "" when the frame was never displayed)
pub fn frame_manifest(short: &str, off: usize) -> Option<(PathBuf, Vec<(String, String)>)> {
    let p = vx_file(short, off, "frame_md5.csv.gz");
    let m = load(&p)?;
    let mut v = Vec::new();
    for (i, r) in m.rows.iter().enumerate() {
        assert_eq!(r[0].parse::<usize>().unwrap(), i, "manifest {} not in frame order", p.display());
        v.push((r[1].clone(), r.get(2).cloned().unwrap_or_default()));
    }
    Some((p, v))
}

/// (audio_blocks, audio_md5) from verify/vx/streams.json, where md5 = md5 of the concatenated
/// hex md5s of the played blocks.
pub fn streams_json_audio(sha1: &str, off: usize) -> Option<(usize, String)> {
    let js = std::fs::read_to_string(verify_dir().join("vx/streams.json")).ok()?;
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
    let m = load(&d.join(format!("{short}_{off:x}.pcm.csv.gz")))?;
    Some(m.rows.into_iter().map(|f| (f[1].clone(), f[2].clone())).collect())
}

pub fn index_of(v: &[(String, String)]) -> HashMap<String, usize> {
    v.iter().enumerate().map(|(i, (y, _))| (y.clone(), i)).collect()
}

/// Frame start bits from the independent grammar walker (tools/vx/container/vxvwalk.py),
/// where verify/vx ships them: nframes (+ 1 = end of the last frame) entries.
pub fn frame_bits(short: &str, off: usize) -> Option<Vec<u32>> {
    let p = vx_file(short, off, "frame_bits.json.gz");
    let gz = std::fs::read(&p).ok()?;
    let txt = String::from_utf8(gbav_common::gzip::gunzip(&gz).unwrap()).unwrap();
    let s = &txt[txt.find("\"frame_start_bits\"")?..];
    let s = &s[s.find('[')? + 1..];
    let s = &s[..s.find(']')?];
    Some(s.split(',').map(|x| x.trim().parse().unwrap()).collect())
}
