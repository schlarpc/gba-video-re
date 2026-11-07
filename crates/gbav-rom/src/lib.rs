//! Cart detection for the GBA Video suite: which cart a ROM is (No-Intro match, including
//! overdumps), which codec family and engine it uses, its player build id, and where its movie
//! streams are.
//!
//! ```no_run
//! let rom = std::fs::read("cart.gba").unwrap();
//! let id = gbav_rom::identify(&rom);
//! match id.classification.family {
//!     Some(f) => println!("{} {:?} build {:?}", f, id.classification.engine(), id.classification.build_id),
//!     None => println!("not a known GBA Video cart"),
//! }
//! if let Some(e) = id.nointro.entry() {
//!     println!("No-Intro: {}{}", e.name, if id.nointro.is_overdump() { " (overdump)" } else { "" });
//! }
//! ```
//!
//! - [`identify`]: everything at once (sha1, No-Intro match, classification).
//! - [`classify`](classify::classify): structural classification only (see [`classify`] module
//!   docs for the rules). Unknown carts get `family == None`, never a guess.
//! - [`nointro`]: the embedded No-Intro table (`src/nointro.csv`).
//! - [`sha1`]: the SHA-1 used for both.
//!
//! The crate holds no ROM bytes: only magics, structural constants, hashes and No-Intro metadata.

pub mod classify;
pub mod nointro;
pub mod sha1;

pub use classify::{
    build_id, classify, locate_player_image, Classification, Engine, Family, PlayerImage,
    StreamKind, StreamLocator, VxVariant,
};
pub use nointro::NoIntroEntry;

/// What follows the real cart data in an overdump.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OverdumpTail {
    /// All 0xFF (open bus / erased flash).
    Padding,
    /// A repeat of the start of the ROM (address-line mirror).
    Mirror,
}

/// How a ROM matched the No-Intro table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NoIntroMatch {
    /// The whole file's sha1 is in the table.
    Exact(&'static NoIntroEntry),
    /// The file is longer than the entry, its first `entry.size` bytes have the entry's sha1,
    /// and the rest is padding or a mirror.
    Overdump {
        entry: &'static NoIntroEntry,
        tail: OverdumpTail,
    },
    /// Not in the table.
    None,
}

impl NoIntroMatch {
    /// The matched entry, exact or overdump.
    pub fn entry(&self) -> Option<&'static NoIntroEntry> {
        match self {
            NoIntroMatch::Exact(e) | NoIntroMatch::Overdump { entry: e, .. } => Some(e),
            NoIntroMatch::None => None,
        }
    }

    pub fn is_overdump(&self) -> bool {
        matches!(self, NoIntroMatch::Overdump { .. })
    }
}

/// Result of [`identify`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Identification {
    /// sha1 of the whole input, 40 lower-case hex digits.
    pub sha1: String,
    /// Input length in bytes.
    pub size: usize,
    pub nointro: NoIntroMatch,
    /// Classification of the cart data (for an overdump: of the trimmed ROM).
    pub classification: Classification,
}

/// Identify a ROM image: sha1, No-Intro match (exact, overdump, or none) and classification.
///
/// Overdump check: when the whole-file sha1 is not in the table, every table size `s` smaller
/// than the file whose tail `rom[s..]` is all 0xFF, or repeats the start of the ROM, is a
/// candidate; the prefix `rom[..s]` is hashed (in the same pass as the whole file) and looked up.
pub fn identify(rom: &[u8]) -> Identification {
    let mut sizes: Vec<usize> = nointro::table()
        .iter()
        .filter_map(|e| usize::try_from(e.size).ok())
        .filter(|&s| s > 0 && s < rom.len())
        .collect();
    sizes.sort_unstable();
    sizes.dedup();
    let candidates: Vec<(usize, OverdumpTail)> = sizes
        .into_iter()
        .filter_map(|s| {
            let tail = &rom[s..];
            if tail.iter().all(|&b| b == 0xff) {
                Some((s, OverdumpTail::Padding))
            } else if tail.len() <= s && tail == &rom[..tail.len()] {
                Some((s, OverdumpTail::Mirror))
            } else {
                None
            }
        })
        .collect();

    // One pass over the ROM; snapshot the hash state at each candidate prefix length.
    let mut h = sha1::Sha1::new();
    let mut pos = 0;
    let mut prefixes = Vec::new();
    for &(s, tail) in &candidates {
        h.update(&rom[pos..s]);
        pos = s;
        prefixes.push((s, tail, sha1::hex(&h.clone().finish())));
    }
    h.update(&rom[pos..]);
    let sha1 = sha1::hex(&h.finish());

    let mut data = rom;
    let nointro = if let Some(e) = nointro::by_sha1(&sha1) {
        NoIntroMatch::Exact(e)
    } else {
        // Largest matching prefix first (a 64 MiB cart padded to 128 MiB is not a 32 MiB one).
        prefixes
            .iter()
            .rev()
            .find_map(|(s, tail, hash)| {
                let e = nointro::by_sha1(hash).filter(|e| e.size == *s as u64)?;
                data = &rom[..*s];
                Some(NoIntroMatch::Overdump {
                    entry: e,
                    tail: *tail,
                })
            })
            .unwrap_or(NoIntroMatch::None)
    };
    Identification {
        sha1,
        size: rom.len(),
        nointro,
        classification: classify(data),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_input() {
        let id = identify(&[0u8; 4096]);
        assert_eq!(id.sha1, sha1::hex_digest(&[0u8; 4096]));
        assert_eq!(id.size, 4096);
        assert_eq!(id.nointro, NoIntroMatch::None);
        assert_eq!(id.nointro.entry(), None);
        assert!(!id.nointro.is_overdump());
        assert_eq!(id.classification.family, None);
        assert_eq!(identify(&[]).nointro, NoIntroMatch::None);
    }
}
