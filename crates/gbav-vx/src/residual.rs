//! VX++ residual decoder: IWRAM 0x03005650 residual_block and 0x03005794 residual_4x4
//! (escape path 0x030059b4, IDCT/add 0x03005a94/0x03005b2c/0x03005be4, DC-only
//! 0x03005c9c/0x03005e30/0x03006044). Port of decoder/residual.c; spec docs/residual.md.
//!
//! Coefficient VLC = MPEG-4 Part 2 intra TCOEF table with the sign folded into the LUT
//! entry length; MPEG-4 style 3-mode escape; H.264 4x4 transform.

use crate::bits::Bits;
use crate::tables::VxppTables;
use crate::video::EWMASK;

const ZIGZAG: [u8; 16] = [0, 1, 4, 8, 5, 2, 3, 6, 9, 12, 13, 10, 7, 11, 14, 15];

#[inline(always)]
fn clip8(v: i32) -> u8 {
    v.clamp(0, 255) as u8
}

#[inline(always)]
fn add_px(ew: &mut [u8], a: u32, d: i32) {
    let p = &mut ew[(a & EWMASK) as usize];
    *p = clip8((*p as i32).wrapping_add(d));
}

/// One 4x4 block. plane 0 = luma, 1 = U (bytes 0,2,4,6), 2 = V (bytes 1,3,5,7); stride 256.
/// Returns <0 for a position the ARM cannot survive, 1 for a harmless overrun, else 0.
fn block4x4(
    bs: &mut Bits,
    t: &VxppTables,
    s: &[i32; 3],
    ew: &mut [u8],
    dst: u32,
    plane: u32,
) -> i32 {
    let mut c = [0i32; 32]; // scan-order levels; 16..31 = harmless overflow (ARM temp buf)
    let (mut pos, mut err) = (0usize, 0);
    let mut lastpos: i32;
    let step = if plane != 0 { 2 } else { 1 };
    let off = if plane == 2 { 1 } else { 0 };
    let lut = &*t.coef_lut;
    loop {
        let (last, run, level): (u32, u32, i32);
        if bs.buf >> 25 == 3 {
            // escape 0000011
            if (bs.buf >> 24) & 1 == 0 {
                // mode 1: level += LMAX[last][run]
                bs.skip(8);
                let e = lut[(bs.buf >> 20) as usize] as u32;
                run = (e >> 9) & 63;
                last = e >> 15;
                let mut l = ((e >> 4) & 31) as i32 + t.lmax[(e >> 9) as usize] as i32;
                if bs.buf.wrapping_shr(32 - (e & 15)) & 1 != 0 {
                    l = -l;
                }
                bs.skip(e & 15);
                level = l;
            } else if (bs.buf >> 23) & 1 == 0 {
                // mode 2: run += RMAX[last][level] + 1
                bs.skip(9);
                let e = lut[(bs.buf >> 20) as usize] as u32;
                last = e >> 15;
                let mut l = ((e >> 4) & 31) as i32;
                run = ((e >> 9) & 63) + t.rmax[(l as u32 + (last << 6)) as usize] as u32;
                if bs.buf.wrapping_shr(32 - (e & 15)) & 1 != 0 {
                    l = -l;
                }
                bs.skip(e & 15);
                level = l;
            } else {
                // mode 3: last(1) run(6) level(s12)
                bs.skip(9);
                last = bs.buf >> 31;
                run = (bs.buf >> 25) & 63;
                bs.skip(7);
                level = (bs.buf as i32) >> 20;
                bs.skip(12);
            }
        } else {
            let e = lut[(bs.buf >> 20) as usize] as u32;
            run = (e >> 9) & 63;
            last = e >> 15;
            let mut l = ((e >> 4) & 31) as i32;
            if bs.buf.wrapping_shr(32 - (e & 15)) & 1 != 0 {
                l = -l;
            }
            bs.skip(e & 15);
            level = l;
        }
        pos += run as usize;
        if pos >= 32 {
            return -1; // ARM would overwrite the dequant scales / jump table (0x03005770+)
        }
        c[pos] = level;
        lastpos = pos as i32;
        pos += 1;
        if last != 0 {
            break;
        }
    }
    if pos > 16 {
        err = 1;
    }
    if lastpos == 0 {
        // DC-only fast path 0x03005990
        let dc = ((c[0] as u32).wrapping_mul(s[0] as u32).wrapping_add(32) as i32) >> 6;
        for y in 0..4 {
            for x in 0..4 {
                add_px(ew, dst + y * 256 + x * step + off, dc);
            }
        }
        return err;
    }
    let mut m = [0u32; 16];
    for i in 0..16 {
        let r = ZIGZAG[i] as usize;
        let si = ((r >> 2) & 1) + (r & 1); // 0 even/even, 1 mixed, 2 odd/odd
        m[r] = (c[i] as u32).wrapping_mul(s[si] as u32);
    }
    let mut tt = [0u32; 16];
    for x in 0..4 {
        let (r0, r1, r2, r3) = (m[x], m[4 + x], m[8 + x], m[12 + x]);
        let e0 = r0.wrapping_add(r2);
        let e1 = r0.wrapping_sub(r2);
        let e2 = ((r1 as i32 >> 1) as u32).wrapping_sub(r3);
        let e3 = r1.wrapping_add((r3 as i32 >> 1) as u32);
        tt[x] = e0.wrapping_add(e3);
        tt[4 + x] = e1.wrapping_add(e2);
        tt[8 + x] = e1.wrapping_sub(e2);
        tt[12 + x] = e0.wrapping_sub(e3);
    }
    for y in 0..4u32 {
        let k = 4 * y as usize;
        let (r0, r1, r2, r3) = (tt[k].wrapping_add(32), tt[k + 1], tt[k + 2], tt[k + 3]);
        let e0 = r0.wrapping_add(r2);
        let e1 = r0.wrapping_sub(r2);
        let e2 = ((r1 as i32 >> 1) as u32).wrapping_sub(r3);
        let e3 = r1.wrapping_add((r3 as i32 >> 1) as u32);
        let o = [
            e0.wrapping_add(e3),
            e1.wrapping_add(e2),
            e1.wrapping_sub(e2),
            e0.wrapping_sub(e3),
        ];
        for x in 0..4u32 {
            add_px(
                ew,
                dst + y * 256 + x * step + off,
                o[x as usize] as i32 >> 6,
            );
        }
    }
    err
}

/// Residual of a WxH block (16x16, 16x8, 8x16, 8x8), added in place to the prediction at
/// EWRAM offsets `y` (luma) and `uv` (interleaved chroma). Walks 8x8 units in raster order;
/// per unit one ue(v) -> CBP (bits 0..3 luma 4x4, bit 4 U, bit 5 V).
/// Returns <0 for a stream the ARM cannot survive (decoding of the block stops there).
pub fn vx_residual(
    bs: &mut Bits,
    t: &VxppTables,
    s: &[i32; 3],
    ew: &mut [u8],
    y: u32,
    uv: u32,
    w: u32,
    h: u32,
) -> i32 {
    let mut err = 0;
    for by in (0..h).step_by(8) {
        for bx in (0..w).step_by(8) {
            let yy = y + by * 256 + bx;
            let uu = uv + (by >> 1) * 256 + bx;
            let idx = bs.ue();
            if idx >= 64 {
                return -2; // ARM would index past the table into code bytes
            }
            let cbp = t.cbp[idx as usize];
            let mut r = 0;
            let blocks = [
                (0x01, yy, 0),
                (0x02, yy + 4, 0),
                (0x04, yy + 4 * 256, 0),
                (0x08, yy + 4 * 256 + 4, 0),
                (0x10, uu, 1),
                (0x20, uu, 2),
            ];
            for (bit, a, p) in blocks {
                if cbp & bit != 0 {
                    r |= block4x4(bs, t, s, ew, a, p);
                }
                if r < 0 {
                    return r;
                }
            }
            err |= r;
        }
    }
    err
}
