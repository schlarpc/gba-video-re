//! Decoder tables read from the cart's player images at runtime (the C reference generated
//! these at build time: iwram_lut.h, vxgb_tables.h, and the literal tables in residual.c).

use crate::cart::{PlayerLayout, Variant};
use gbav_common::bytes::le32;
use gbav_common::{Error, Result};

/// IRQ-table entries `main()` installs in IWRAM before the movie plays (the clamp table's
/// index range reaches back into them).
const IRQ_PATCHES: [(usize, u8); 4] = [(0x10, 0x00), (0x11, 0x01), (0x18, 0x2C), (0x19, 0x01)];

/// VX++ residual tables (residual.c / docs/vx/residual.md).
#[derive(Clone)]
pub struct VxppTables {
    /// EWRAM 0x020010e4: coefficient VLC LUT, index = next 12 bits.
    /// Entry: bits 0-3 length (incl. sign), 4-8 |level|, 9-14 run, 15 last.
    pub coef_lut: Box<[u16; 4096]>,
    /// EWRAM 0x020030e4: escape mode 1 level offset LMAX[last*64 + run].
    pub lmax: [u8; 128],
    /// EWRAM 0x02003164: escape mode 2 run offset (RMAX+1)[last*64 + |level|].
    pub rmax: [u8; 128],
    /// IWRAM 0x03005610: ue(v) -> CBP.
    pub cbp: [u8; 64],
    /// IWRAM 0x03000680: LevelScale[QP%6][3].
    pub level_scale: [[u32; 3]; 6],
}

/// VXGB (CAVLC) residual tables, in the player's own layout (gen_vxgb_tables.py).
#[derive(Clone)]
pub struct VxgbTables {
    /// IWRAM 0x03005e90: ue(v) -> cbp (bits 0..3 luma 4x4, bit 4 = U and V).
    pub cbp: [u8; 32],
    /// IWRAM 0x03006120: coeff_token start node per nC 0..7.
    pub ct_node: [u8; 8],
    /// IWRAM 0x03006128 - 16: offset into `ct_len` per nC 0..7.
    pub ct_lenoff: [u8; 8],
    /// IWRAM 0x03006130: coeff_token code lengths [3][68], index T1 | TC<<2.
    pub ct_len: [u8; 204],
    /// IWRAM 0x030079c0: coeff_token nibble tree [49][16] (<0: next node -v, else symbol).
    pub ct_tree: [i8; 784],
    /// total_zeros per TotalCoeff 1..15: (peek width, len[16] + lut[1 << width]).
    pub tz: Vec<(u8, Vec<u8>)>,
    /// run_before per zerosLeft 1..6.
    pub rb: Vec<(u8, Vec<u8>)>,
    /// IWRAM 0x03006080: suffixLength increment thresholds.
    pub level_thr: [u32; 6],
    /// EWRAM 0x02002a14: dequant scales [QP%6][8], index (y&1)*4 + x.
    pub deq: [u8; 48],
}

/// All tables one stream needs.
#[derive(Clone)]
pub struct Tables {
    pub variant: Variant,
    /// Runtime IWRAM 0x03000000..0x030003ff with the IRQ entries installed; the blitter's
    /// size immediates are patched per stream by [`crate::blit::BlitLut::new`].
    pub iwram_lo: [u8; 0x400],
    /// EWRAM image prefix (up to frame slot 0), so reads before slot 0 return the player's bytes.
    pub ewram_image: Vec<u8>,
    pub vxpp: Option<VxppTables>,
    pub vxgb: Option<VxgbTables>,
}

fn arr<const N: usize>(s: &[u8]) -> [u8; N] {
    let mut a = [0u8; N];
    a.copy_from_slice(&s[..N]);
    a
}

impl Tables {
    /// Read the tables for `variant` from the cart's player. The player build must be the one
    /// that plays this variant (VX++ streams need the Rev 6 player, VXGB the Rev 5 player).
    pub fn from_rom(rom: &[u8], variant: Variant) -> Result<Tables> {
        let l = PlayerLayout::parse(rom)?;
        match l.build() {
            Some(b) if b == variant => {}
            Some(b) => {
                return Err(Error::Format(format!(
                    "{} stream but the cart's player is the {} build",
                    variant.magic(),
                    b.magic()
                )))
            }
            None => {
                return Err(Error::Format(format!(
                    "unknown movie player build (md5 {}); the decoder tables' addresses are only known for the VX++ and VXGB players",
                    gbav_common::md5::to_hex(&l.md5)
                )))
            }
        }
        let mut iwram_lo = arr::<0x400>(l.iw(rom, 0x0300_0000, 0x400)?);
        for (o, v) in IRQ_PATCHES {
            iwram_lo[o] = v;
        }
        let slot0 = crate::video::Layout::of(variant).slot0 as usize;
        let ewram_image = rom[l.ewram.0..l.ewram.0 + l.ewram.1.min(slot0)].to_vec();
        let mut t = Tables {
            variant,
            iwram_lo,
            ewram_image,
            vxpp: None,
            vxgb: None,
        };
        match variant {
            Variant::VxPP => {
                let lut = l.ew(rom, 0x0200_10e4, 8192)?;
                let mut coef_lut = Box::new([0u16; 4096]);
                for (i, v) in coef_lut.iter_mut().enumerate() {
                    *v = u16::from_le_bytes([lut[2 * i], lut[2 * i + 1]]);
                }
                let ls = l.iw(rom, 0x0300_0680, 72)?;
                let mut level_scale = [[0u32; 3]; 6];
                for (i, v) in level_scale.iter_mut().flatten().enumerate() {
                    *v = le32(ls, 4 * i).unwrap();
                }
                t.vxpp = Some(VxppTables {
                    coef_lut,
                    lmax: arr(l.ew(rom, 0x0200_30e4, 128)?),
                    rmax: arr(l.ew(rom, 0x0200_3164, 128)?),
                    cbp: arr(l.iw(rom, 0x0300_5610, 64)?),
                    level_scale,
                });
            }
            Variant::Vxgb => {
                let w32 = |a: u32| -> Result<u32> { Ok(le32(l.iw(rom, a, 4)?, 0).unwrap()) };
                let vlc = |wa: u32, pa: u32, n: usize| -> Result<Vec<(u8, Vec<u8>)>> {
                    let wid = l.iw(rom, wa, n)?;
                    (0..n)
                        .map(|k| {
                            let p = w32(pa + 4 * k as u32)?;
                            Ok((wid[k], l.iw(rom, p, 16 + (1usize << wid[k]))?.to_vec()))
                        })
                        .collect()
                };
                let lo = l.iw(rom, 0x0300_6128, 8)?;
                let mut ct_lenoff = [0u8; 8];
                for (d, s) in ct_lenoff.iter_mut().zip(lo) {
                    *d = s
                        .checked_sub(16)
                        .ok_or_else(|| Error::Format("bad coeff_token length table".into()))?;
                }
                let tree = l.iw(rom, 0x0300_79c0, 784)?;
                let mut ct_tree = [0i8; 784];
                for (d, s) in ct_tree.iter_mut().zip(tree) {
                    *d = *s as i8;
                }
                let mut level_thr = [0u32; 6];
                for (i, v) in level_thr.iter_mut().enumerate() {
                    *v = w32(0x0300_6080 + 4 * i as u32)?;
                }
                t.vxgb = Some(VxgbTables {
                    cbp: arr(l.iw(rom, 0x0300_5e90, 32)?),
                    ct_node: arr(l.iw(rom, 0x0300_6120, 8)?),
                    ct_lenoff,
                    ct_len: arr(l.iw(rom, 0x0300_6130, 204)?),
                    ct_tree,
                    tz: vlc(0x0300_6200, 0x0300_6210, 15)?,
                    rb: vlc(0x0300_6250, 0x0300_6258, 6)?,
                    level_thr,
                    deq: arr(l.ew(rom, 0x0200_2a14, 48)?),
                });
            }
        }
        Ok(t)
    }
}
