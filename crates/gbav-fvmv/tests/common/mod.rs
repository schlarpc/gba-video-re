//! Shared test helpers: locating the carts and manifests, reading gzip manifests, a PRNG.
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::process::Command;

/// The four FVMV carts by game code.
pub const CODES: [&str; 4] = ["MPAE", "MPBE", "MPCE", "MPDE"];

/// `GBAV_ROMS`, or `None` (tests skip).
pub fn roms_dir() -> Option<PathBuf> {
    std::env::var_os("GBAV_ROMS").map(PathBuf::from)
}

/// Directory of the `<code>_<stream>.{frame,audio}_md5.csv.gz` manifests:
/// `GBAV_FVMV_MANIFESTS`, else `<crate>/manifests` if present.
pub fn manifests_dir() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("GBAV_FVMV_MANIFESTS") {
        return Some(PathBuf::from(p));
    }
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("manifests");
    p.is_dir().then_some(p)
}

/// FVMV carts in the ROM directory, as (game code, path), sorted by code. The game code at
/// 0xac identifies them (the header's title field is the same "GBA Video" on all carts).
pub fn fvmv_carts(dir: &Path) -> Vec<(String, PathBuf)> {
    use std::io::Read;
    let mut out = Vec::new();
    for e in std::fs::read_dir(dir).expect("read GBAV_ROMS") {
        let p = e.unwrap().path();
        if p.extension()
            .and_then(|x| x.to_str())
            .map(|x| x.eq_ignore_ascii_case("gba"))
            != Some(true)
        {
            continue;
        }
        let mut hdr = [0u8; 0xb0];
        if std::fs::File::open(&p)
            .and_then(|mut f| f.read_exact(&mut hdr))
            .is_err()
        {
            continue;
        }
        let code = String::from_utf8_lossy(&hdr[0xac..0xb0]).to_string();
        if CODES.contains(&code.as_str()) {
            out.push((code, p));
        }
    }
    out.sort();
    out
}

/// Lines of a gzip CSV manifest (header line dropped), split on commas.
pub fn read_manifest(path: &Path) -> Vec<Vec<String>> {
    let out = Command::new("gzip")
        .arg("-dc")
        .arg(path)
        .output()
        .expect("run gzip -dc");
    assert!(out.status.success(), "gzip -dc {}", path.display());
    String::from_utf8(out.stdout)
        .unwrap()
        .lines()
        .skip(1)
        .map(|l| l.split(',').map(str::to_string).collect())
        .collect()
}

/// splitmix64.
pub struct Rng(pub u64);

impl Rng {
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }

    /// Uniform in lo..hi.
    pub fn range(&mut self, lo: u64, hi: u64) -> u64 {
        lo + self.next_u64() % (hi - lo)
    }

    pub fn chance(&mut self, p: f64) -> bool {
        ((self.next_u64() >> 11) as f64 / (1u64 << 53) as f64) < p
    }

    pub fn bytes(&mut self, n: usize) -> Vec<u8> {
        let mut v = Vec::with_capacity(n + 8);
        while v.len() < n {
            v.extend_from_slice(&self.next_u64().to_le_bytes());
        }
        v.truncate(n);
        v
    }
}

/// An env var parsed as a count, with a default.
pub fn env_count(name: &str, default: usize) -> usize {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
