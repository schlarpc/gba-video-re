//! VXGB residual decoder: the Rev 5 player's IWRAM 0x03005eb0 residual_block and its callee
//! 0x030062e0 (4x4 coefficient decode + dequant + IDCT + add). Port of
//! decoder/vxgb_residual.c; spec docs/vxgb.md section 5.
//!
//! Coefficients use the H.264 CAVLC code tables (read from the ROM, in the player's layout)
//! with a VXGB-specific syntax order and level code. Every bit-reader operation mirrors one
//! ARM instruction group so the refill phase stays register-exact.

use crate::bits::Bits;
use crate::tables::VxgbTables;
use crate::video::EWMASK;

pub const NZY_STRIDE: u32 = 0x41;
pub const NZC_STRIDE: u32 = 0x21;

const ZZ: [u8; 16] = [0, 1, 4, 8, 5, 2, 3, 6, 9, 12, 13, 10, 7, 11, 14, 15];

#[inline(always)]
fn rd(ew: &[u8], a: u32) -> u8 {
    ew[(a & EWMASK) as usize]
}

#[inline(always)]
fn wr(ew: &mut [u8], a: u32, v: u8) {
    ew[(a & EWMASK) as usize] = v;
}

/// Table walk "len[16] + lut[1 << width]" (total_zeros 0x03006390, run_before 0x03006464).
#[inline(always)]
fn vlc_tab(bs: &mut Bits, t: &(u8, Vec<u8>)) -> u32 {
    let v = t.1[16 + (bs.buf >> (32 - t.0 as u32)) as usize];
    bs.skip(t.1[v as usize] as u32);
    v as u32
}

/// 0x030062e0: one 4x4 block at EWRAM offset `dst` (step 1 = luma, 2 = U/V). `nc` = context;
/// the TotalCoeff is written to EWRAM `nz`. Returns 0, or <0 for a code the ARM cannot
/// survive (out-of-range writes / endless loops).
fn coef4x4(
    bs: &mut Bits,
    t: &VxgbTables,
    s: &[i32; 8],
    ew: &mut [u8],
    dst: u32,
    step: u32,
    nc: u32,
    nz: u32,
) -> i32 {
    let mut c = [0i32; 16];
    let mut p = 16usize; // write pointer: c[--p], highest scan position first
    let (mut t1, mut tc): (i32, i32);
    let mut zl: i32;
    let mut sl: u32 = 0;

    // ---- coeff_token
    if nc >= 8 {
        // 0x03006270: 6-bit FLC
        let v = bs.buf >> 26;
        bs.skip(6);
        t1 = (v & 3) as i32;
        tc = (v >> 2) as i32 + 1;
        if t1 > tc {
            tc = 0;
        }
    } else {
        // nibble tree, up to 4 levels
        let mut peek = bs.buf;
        let mut node = t.ct_node[nc as usize] as usize;
        let mut sym: i32 = 0;
        for lvl in 0..4 {
            sym = t.ct_tree[node * 16 + (peek >> 28) as usize] as i32;
            if sym >= 0 || lvl == 3 {
                break;
            }
            peek <<= 4;
            node = (-sym) as usize;
            if node >= 49 {
                return -3; // the tree has no such node (never in valid codes)
            }
        }
        if sym < 0 {
            return -3; // no codeword: the ARM reads lengths before the table
        }
        let Some(&len) = t
            .ct_len
            .get(t.ct_lenoff[nc as usize] as usize + sym as usize)
        else {
            return -3;
        };
        bs.skip(len as u32);
        t1 = sym & 3;
        tc = sym >> 2;
    }
    wr(ew, nz, tc as u8);
    if tc == 0 {
        return 0;
    }

    // ---- total_zeros (0x0300637c)
    if tc == 16 {
        zl = 0;
    } else {
        zl = vlc_tab(bs, &t.tz[(tc - 1) as usize]) as i32;
        let pad = 16 - (tc + zl);
        if pad < 0 {
            return -4; // ARM: zero-fill loop runs ~2^32 times
        }
        for _ in 0..pad {
            p -= 1;
            c[p] = 0;
        }
    }

    // ---- levels interleaved with run_before (0x030063e0 .. 0x030064cc)
    loop {
        let v: i32;
        t1 -= 1;
        if t1 >= 0 {
            // trailing one: sign bit only
            v = if bs.buf >> 31 != 0 { -1 } else { 1 };
            bs.skip(1);
        } else {
            let mut z: u32 = 0;
            while bs.buf & 0x8000_0000 == 0 {
                z += 1;
                if z >= 32 {
                    return -5; // ARM loops forever on an all-zero buffer
                }
                bs.buf <<= 1;
            }
            bs.buf <<= 1; // the terminating 1
            bs.cnt -= z as i32 + 1;
            if bs.cnt < 0 {
                bs.refill();
            }
            let (lc, neg): (u32, u32);
            if z == 15 {
                // escape 0x030064d0: 11-bit suffix + sign
                lc = (bs.buf >> 21).wrapping_add(15u32 << sl);
                neg = (bs.buf >> 20) & 1;
                if lc as i32 > t.level_thr[sl as usize] as i32 {
                    sl += 1;
                }
                bs.skip(12);
            } else {
                let n = sl + 1;
                lc = (if sl != 0 { bs.buf >> (32 - sl) } else { 0 }).wrapping_add(z << sl);
                neg = (bs.buf >> (31 - sl)) & 1;
                if lc as i32 > t.level_thr[sl as usize] as i32 {
                    sl += 1;
                }
                bs.skip(n);
            }
            let mut x = lc.wrapping_add(1) as i32;
            if neg != 0 {
                x = x.wrapping_neg();
            }
            v = x;
        }
        if p == 0 {
            return -6;
        }
        p -= 1;
        c[p] = v;
        tc -= 1;
        if tc == 0 {
            break;
        }
        if zl == 0 {
            continue;
        }
        let run: u32;
        if zl > 6 {
            // 0x030062a0
            let mut n: u32 = 1;
            let mut tt = bs.buf;
            while tt & 0x8000_0000 == 0 {
                n += 1;
                if n > 32 {
                    return -5;
                }
                tt <<= 1;
            }
            if n >= 4 {
                run = n + 3;
                bs.skip(n);
            } else {
                run = 7 - (bs.buf >> 29);
                bs.skip(3);
            }
        } else {
            run = vlc_tab(bs, &t.rb[(zl - 1) as usize]);
        }
        if run == 0 {
            continue;
        }
        if run as i32 > zl {
            return -7; // ARM: zerosLeft < 0, zero-fill runs ~2^32 times
        }
        zl -= run as i32;
        if (p as i32) - (run as i32) < 0 {
            return -6;
        }
        for _ in 0..run {
            p -= 1;
            c[p] = 0;
        }
    }
    while zl > 0 {
        zl -= 1;
        if p == 0 {
            return -6;
        }
        p -= 1;
        c[p] = 0;
    }

    // ---- dequant (per position: s[(y&1)*4 + x]) + H.264 4x4 IDCT + add (0x03006524..)
    let mut m = [0u32; 16];
    for i in 0..16 {
        let r = ZZ[i] as usize;
        m[r] = (c[i] as u32).wrapping_mul(s[((r >> 2) & 1) * 4 + (r & 3)] as u32);
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
        let (r0, r1, r2, r3) = (tt[k], tt[k + 1], tt[k + 2], tt[k + 3]);
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
            let a = dst + y * 256 + x * step;
            let q = rd(ew, a) as i32;
            wr(
                ew,
                a,
                q.wrapping_add(o[x as usize].wrapping_add(32) as i32 >> 6)
                    .clamp(0, 255) as u8,
            );
        }
    }
    0
}

#[inline(always)]
fn nc_of(ew: &[u8], nz: u32, stride: u32) -> u32 {
    (rd(ew, nz.wrapping_sub(1)) as u32 + rd(ew, nz.wrapping_sub(stride)) as u32 + 1) >> 1
}

/// Residual of a WxH block (16x16, 16x8, 8x16, 8x8) at EWRAM offsets `y` / `uv`; `nzy` / `nzc`
/// are the block's entries in the persistent luma (stride 0x41) / chroma (stride 0x21)
/// nonzero-count grids, read (left and top neighbours) and written.
#[allow(clippy::too_many_arguments)]
pub fn vxgb_residual(
    bs: &mut Bits,
    t: &VxgbTables,
    s: &[i32; 8],
    ew: &mut [u8],
    y: u32,
    uv: u32,
    nzy: u32,
    nzc: u32,
    w: u32,
    h: u32,
) -> i32 {
    for by in (0..h).step_by(8) {
        for bx in (0..w).step_by(8) {
            let yy = y + by * 256 + bx;
            let uu = uv + (by >> 1) * 256 + bx;
            let ny = nzy + (by >> 2) * NZY_STRIDE + (bx >> 2);
            let nc = nzc + (by >> 3) * NZC_STRIDE + (bx >> 3);
            let idx = bs.ue();
            if idx >= 32 {
                return -2; // ARM indexes past the table into code bytes
            }
            let cbp = t.cbp[idx as usize];
            for k in 0..4u32 {
                let a = yy + (k >> 1) * 4 * 256 + (k & 1) * 4;
                let nn = ny + (k >> 1) * NZY_STRIDE + (k & 1);
                if cbp & (1 << k) == 0 {
                    wr(ew, nn, 0);
                    continue;
                }
                let ctx = nc_of(ew, nn, NZY_STRIDE);
                let r = coef4x4(bs, t, s, ew, a, 1, ctx, nn);
                if r < 0 {
                    return r;
                }
            }
            if cbp & 0x10 == 0 {
                wr(ew, nc, 0);
            } else {
                let ncx = nc_of(ew, nc, NZC_STRIDE);
                let r = coef4x4(bs, t, s, ew, uu, 2, ncx, nc); // U, then V with the same nC
                if r < 0 {
                    return r;
                }
                let tu = rd(ew, nc) as u32;
                let r = coef4x4(bs, t, s, ew, uu + 1, 2, ncx, nc);
                if r < 0 {
                    return r;
                }
                wr(ew, nc, ((tu + rd(ew, nc) as u32 + 1) >> 1) as u8);
            }
        }
    }
    0
}
