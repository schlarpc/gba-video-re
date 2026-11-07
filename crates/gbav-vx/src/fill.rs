//! The bilinear (mode 4) and "plane" (intra mode 3) recursive-bisection fills.
//!
//! The player implements each block size as straight-line ARM code. decoder/gen_fill.py
//! symbolically executed those routines into exact op lists; the lists are checked in as
//! Rust data (`fill_data.rs`, converted by tools/fill_to_rs.py). They are not ROM bytes: they
//! record which bytes each store averages, in the routine's order, which also fixes what an
//! out-of-range corner value turns into (an op can read the untruncated register value of an
//! earlier op, or the truncated byte it stored). Both player builds run identical routines.
//!
//! Op semantics, executed in order:
//!   kind 0 AVG:    val[i] = (A + B) >> 1
//!   kind 1 CORNER: val[i] = ((A + B + 1) >> 1) + 2*se(v)
//!   then           plane[dst] = val[i] & 0xff
//! Operand ref 0 = the byte now in memory at that offset (of the op's plane), 1 = val[k],
//! 2 = the caller's corner value V.

#[derive(Debug, Clone, Copy)]
pub(crate) struct FillOp {
    pub plane: u8,
    pub kind: u8,
    pub aref: u8,
    pub bref: u8,
    pub dst: i16,
    pub a: i16,
    pub b: i16,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Family {
    /// mode 4 bilinear, every block size 2..16 x 2..16
    Mode4 = 0,
    /// intra mode 3 luma plane inner fill, 4..16 x 4..16
    PlaneLuma = 1,
    /// intra mode 3 chroma plane inner fill (run once per U / V)
    PlaneChroma = 2,
}

/// Largest op list (mode 4, 16x16).
pub(crate) const MAX_OPS: usize = 400;

/// op list per (family, log2 w - 1, log2 h - 1)
pub(crate) struct FillIndex {
    lists: [[[&'static [FillOp]; 4]; 4]; 3],
}

fn lg(n: u8) -> usize {
    match n {
        2 => 0,
        4 => 1,
        8 => 2,
        16 => 3,
        _ => unreachable!(),
    }
}

impl FillIndex {
    fn build() -> FillIndex {
        let mut lists: [[[&'static [FillOp]; 4]; 4]; 3] = [[[&[]; 4]; 4]; 3];
        for &(fam, w, h, start, n, _arm) in crate::fill_data::INDEX.iter() {
            let (s, n) = (start as usize, n as usize);
            assert!(n <= MAX_OPS);
            lists[fam as usize][lg(w)][lg(h)] = &crate::fill_data::OPS[s..s + n];
        }
        FillIndex { lists }
    }

    pub fn get() -> &'static FillIndex {
        static IDX: std::sync::OnceLock<FillIndex> = std::sync::OnceLock::new();
        IDX.get_or_init(FillIndex::build)
    }

    #[inline]
    pub fn ops(&self, fam: Family, w: usize, h: usize) -> &'static [FillOp] {
        self.lists[fam as usize][lg(w as u8)][lg(h as u8)]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn index_complete() {
        let ix = FillIndex::get();
        for w in [2, 4, 8, 16] {
            for h in [2, 4, 8, 16] {
                assert!(!ix.ops(Family::Mode4, w, h).is_empty(), "m4 {w}x{h}");
                if w >= 4 && h >= 4 {
                    assert!(!ix.ops(Family::PlaneLuma, w, h).is_empty());
                    assert!(!ix.ops(Family::PlaneChroma, w, h).is_empty());
                }
            }
        }
    }
}
