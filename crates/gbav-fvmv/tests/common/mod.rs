//! Shared test helpers: the FVMV carts and their manifests (through gbav-testkit), a PRNG.
#![allow(dead_code)]

use gbav_common::Rom;

#[allow(unused_imports)]
pub use gbav_testkit::{cref_dir, roms_dir, stream_manifests};

/// The FVMV carts found in `GBAV_ROMS`, as (short id, ROM), sorted by short id
/// (`pokemon1` .. `pokemon4`, see verify/carts.csv).
pub fn fvmv_carts() -> Vec<(String, Rom)> {
    let mut out: Vec<(String, Rom)> = gbav_testkit::carts("fvmv")
        .into_iter()
        .filter(|c| gbav_testkit::only(&c.short_id))
        .filter_map(|c| Some((c.short_id.clone(), gbav_testkit::load_rom(&c.short_id)?)))
        .collect();
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
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
