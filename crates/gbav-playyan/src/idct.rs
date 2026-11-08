//! The VC01 video firmware's 8x8 inverse DCT (py_fw0 0x26002, prescale table loaded by 0x26450).
//!
//! An AP-922 / xvid-style tan-butterfly applied separably to prescaled coefficients, in 16-bit
//! arithmetic with a rounding multiply-high `MR(x, c) = (x * c + 0x8000) >> 16` (c unsigned):
//!
//! 1. input `x = s16(min(c, 2047) << 4)`: a `vmin #2047` then a *wrapping* shift, so coefficients
//!    below -2048 wrap (only invalid streams reach them; the firmware's MPEG-quant intra dequant
//!    can produce down to -4096);
//! 2. `x = MR(x, S[v][u])` with the prescale table `S` (Q15 of s(u) s(v)), read from the firmware;
//! 3. a 1-D butterfly along u for every row v, then along v for every column u;
//! 4. output `MR(y, 1024)` = `(y + 32) >> 6`.
//!
//! It is not ffmpeg's xvid IDCT (about 29 % of blocks identical); with any of ffmpeg's IDCTs none
//! of the 13 Garage Game thumbnail hashes match, with this one all 13 do.

use crate::firmware::Image;
use gbav_common::{Error, Result};

const TAN1: i32 = 0x32EC;
const TAN2: i32 = 0x6A0A;
const TAN3: i32 = 0xAB0E;
const SQRT2: i32 = 0xB505;

#[inline(always)]
fn s16(x: i32) -> i32 {
    x as i16 as i32
}
#[inline(always)]
fn mr(x: i32, c: i32) -> i32 {
    s16((x * c + 0x8000) >> 16)
}

/// Prescale table location per video image: (offset, md5 of the 256 bytes).
fn prescale_loc(img: &Image) -> Result<usize> {
    use crate::ImageId::*;
    match img.id {
        PyFw0 => Ok(0xf240),
        MiFw0 | FupSec0 => Ok(0x1bec0),
        _ => Err(Error::Invalid(format!("{} is not a video image", img.id.name()))),
    }
}
const PRESCALE_MD5: &str = "9b55e2afdb1e92ac7e0539c2825c8fa0";

/// The firmware IDCT with its prescale table.
#[derive(Clone)]
pub struct Idct {
    /// S[v][u], Q15 unsigned.
    scale: [[i32; 8]; 8],
}

impl Idct {
    /// Read the prescale table (8 rows x 16 lanes of u16, two side-by-side copies) from a video
    /// image.
    pub fn from_firmware(img: &Image) -> Result<Idct> {
        let t = img.u16_table(prescale_loc(img)?, 128, PRESCALE_MD5)?;
        Ok(Idct::from_table(&t))
    }
    /// From the raw 8x16 table.
    pub fn from_table(t: &[u16]) -> Idct {
        let mut scale = [[0i32; 8]; 8];
        for v in 0..8 {
            for u in 0..8 {
                scale[v][u] = t[v * 16 + u] as i32;
            }
        }
        Idct { scale }
    }

    #[inline(always)]
    fn bfly(x: &mut [i32; 8]) {
        let a = |p: i32, q: i32| s16(p + q);
        let d = |p: i32, q: i32| s16(p - q);
        let mut t8 = mr(x[1], TAN1);
        let mut t9 = mr(x[7], TAN1);
        t9 = a(t9, x[1]);
        x[1] = d(t8, x[7]);
        t8 = mr(x[3], TAN3);
        x[7] = mr(x[5], TAN3);
        x[3] = a(x[3], x[7]);
        t8 = d(x[5], t8);
        x[5] = d(t9, x[3]);
        t9 = a(t9, x[3]);
        x[3] = a(x[1], t8);
        x[1] = d(x[1], t8);
        t8 = mr(x[1], SQRT2);
        x[7] = mr(x[5], SQRT2);
        x[1] = a(x[7], t8);
        x[5] = d(x[7], t8);
        x[7] = mr(x[6], TAN2);
        t8 = mr(x[2], TAN2);
        x[2] = a(x[2], x[7]);
        x[6] = d(t8, x[6]);
        t8 = d(x[0], x[4]);
        x[4] = a(x[0], x[4]);
        x[0] = a(t8, x[6]);
        x[6] = d(t8, x[6]);
        t8 = a(x[4], x[2]);
        x[2] = d(x[4], x[2]);
        x[4] = d(x[2], x[3]);
        x[3] = a(x[2], x[3]);
        x[2] = a(x[6], x[5]);
        x[5] = d(x[6], x[5]);
        x[6] = d(x[0], x[1]);
        x[1] = a(x[0], x[1]);
        x[0] = a(t8, t9);
        x[7] = d(t8, t9);
    }

    /// Transform one block in place: `blk[8*v + u]` coefficients (16-bit, as the dequantiser
    /// stored them) -> 16-bit spatial values (before the 0..255 clamp / prediction add).
    pub fn transform(&self, blk: &mut [i16; 64]) {
        let mut rows = [[0i32; 8]; 8];
        for v in 0..8 {
            let mut x = [0i32; 8];
            for u in 0..8 {
                let c = (blk[8 * v + u] as i32).min(2047);
                x[u] = mr(s16(c << 4), self.scale[v][u]);
            }
            Self::bfly(&mut x);
            rows[v] = x;
        }
        for u in 0..8 {
            let mut y = [0i32; 8];
            for v in 0..8 {
                y[v] = rows[v][u];
            }
            Self::bfly(&mut y);
            for v in 0..8 {
                blk[8 * v + u] = mr(y[v], 1024) as i16;
            }
        }
    }
}
