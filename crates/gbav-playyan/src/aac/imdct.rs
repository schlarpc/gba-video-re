//! The firmware's fixed-point IMDCT (mi_fw0 / fup_sec0 0x97d6 long, 0x99d8 short) and its filter
//! bank (0x21bb2 / 0x21f46): block floating point, u16 pre/post twiddles, a 512- or 64-point
//! complex FFT built from 16-point in-register FFTs and radix-2 block stages, FAAD2's output
//! reordering, Q15 windows. Every stage is bit-exact against the emulator (see docs/playyan/decoders.md).

use super::Fw;

#[inline]
fn w32(x: i64) -> i32 {
    x as i32
}
#[inline]
fn sat16(x: i32) -> i32 {
    x.clamp(-32768, 32767)
}

/// 32x32 -> high 32 product without the lo*lo partial (0xa5ec); cross partials rounded (op21).
#[inline]
fn mw(x: i32, w: i32) -> i32 {
    let (xh, xl, wh, wl) = ((x >> 16) as i64, (x & 0xffff) as i64, (w >> 16) as i64, (w & 0xffff) as i64);
    w32(xh * wh + ((xh * wl + 0x8000) >> 16) + ((wh * xl + 0x8000) >> 16))
}
#[inline]
fn cmul(x: i32, y: i32, c: i32, s: i32) -> (i32, i32) {
    (w32(mw(x, c) as i64 - mw(y, s) as i64), w32(mw(x, s) as i64 + mw(y, c) as i64))
}
/// x << 2 with the high half saturating (vshls + accumulator).
#[inline]
fn shl2_sat(x: i32) -> i32 {
    let lo = x & 0xffff;
    let hi = x >> 16;
    let nhi = sat16(sat16(hi * 4) + (lo >> 14));
    ((nhi as u32) << 16 | ((lo << 2) & 0xffff) as u32) as i32
}

#[derive(Clone, Copy, Default)]
struct Blk {
    re: [i32; 16],
    im: [i32; 16],
}

fn tw_block(fw: &Fw, a: u32) -> ([i32; 16], [i32; 16]) {
    let mut c = [0i32; 16];
    let mut s = [0i32; 16];
    for t in 0..16u32 {
        c[t as usize] = ((fw.rd16(a + 2 * (16 + t)) << 16) | fw.rd16(a + 2 * t)) as i32;
        s[t as usize] = ((fw.rd16(a + 2 * (48 + t)) << 16) | fw.rd16(a + 2 * (32 + t))) as i32;
    }
    (c, s)
}

fn fft16(fw: &Fw, b: &mut Blk) {
    let mut dd = 1usize;
    while dd <= 8 {
        if dd == 2 {
            for t in (3..16).step_by(4) {
                let r = b.re[t];
                b.re[t] = b.im[t];
                b.im[t] = w32(-(r as i64));
            }
        }
        if dd >= 4 {
            let tab = if dd == 4 { fw.a.fft_tw4 } else { fw.a.fft_tw8 };
            let (c, s) = tw_block(fw, tab);
            for t in 0..16 {
                if fw.rd16(tab + 2 * (48 + t as u32)) == 0 {
                    continue;
                }
                let (r, i) = cmul(shl2_sat(b.re[t]), shl2_sat(b.im[t]), c[t], s[t]);
                b.re[t] = r;
                b.im[t] = i;
            }
        }
        let (mut nr, mut ni) = ([0i32; 16], [0i32; 16]);
        for t in 0..16 {
            if (t / dd).is_multiple_of(2) {
                nr[t] = w32(b.re[t] as i64 + b.re[t + dd] as i64);
                ni[t] = w32(b.im[t] as i64 + b.im[t + dd] as i64);
            } else {
                nr[t] = w32(b.re[t - dd] as i64 - b.re[t] as i64);
                ni[t] = w32(b.im[t - dd] as i64 - b.im[t] as i64);
            }
        }
        b.re = nr;
        b.im = ni;
        dd <<= 1;
    }
}

/// Radix-2 stage over block pairs (bl[a + k], bl[b + k]), k < n, twiddle table `tw`
/// (one 128-byte block per pair).
fn radix2(fw: &Fw, bl: &mut [Blk], a: usize, b: usize, n: usize, tw: u32) {
    for k in 0..n {
        let (c, s) = tw_block(fw, tw + 128 * k as u32);
        for t in 0..16 {
            let (tr, ti) = cmul(bl[b + k].re[t].wrapping_shl(2), bl[b + k].im[t].wrapping_shl(2), c[t], s[t]);
            let (ar, ai) = (bl[a + k].re[t], bl[a + k].im[t]);
            bl[a + k].re[t] = w32(ar as i64 + tr as i64);
            bl[a + k].im[t] = w32(ai as i64 + ti as i64);
            bl[b + k].re[t] = w32(ar as i64 - tr as i64);
            bl[b + k].im[t] = w32(ai as i64 - ti as i64);
        }
    }
}

fn fft_blocks(fw: &Fw, bl: &mut [Blk]) {
    for b in bl.iter_mut() {
        fft16(fw, b);
    }
    let a = &fw.a;
    if bl.len() == 4 {
        radix2(fw, bl, 0, 1, 1, a.tw_b1);
        radix2(fw, bl, 2, 3, 1, a.tw_b1);
        radix2(fw, bl, 0, 2, 2, a.tw_b2);
        return;
    }
    for q in 0..4 {
        let o = 8 * q;
        for p in 0..4 {
            radix2(fw, bl, o + 2 * p, o + 2 * p + 1, 1, a.tw_b1);
        }
        radix2(fw, bl, o, o + 2, 2, a.tw_b2);
        radix2(fw, bl, o + 4, o + 6, 2, a.tw_b2);
        radix2(fw, bl, o, o + 4, 4, a.tw_b4);
    }
    radix2(fw, bl, 0, 8, 8, a.tw_b8);
    radix2(fw, bl, 16, 24, 8, a.tw_b8);
    radix2(fw, bl, 0, 16, 16, a.tw_b16);
}

/// 0xa7dc / 0xa858: left shift with the high half saturating, or rounded right shift.
fn shift32(x: i32, n: i32) -> i32 {
    if n > 0 {
        let lo = x & 0xffff;
        let hi = x >> 16;
        let nhi = sat16(((hi as i64) << n).clamp(-32768, 32767) as i32 + (lo >> (16 - n)));
        ((nhi as u32) << 16 | ((lo << n) & 0xffff) as u32) as i32
    } else if n < 0 {
        let m = -n;
        w32(x as i64 + (1i64 << (m - 1))) >> m
    } else {
        x
    }
}
#[inline]
fn p16(c: u32, v: i32) -> i32 {
    w32((v >> 16) as i64 * c as i64 + ((c as i64 * (v & 0xffff) as i64 + 0x8000) >> 16))
}

/// Whole IMDCT: `x` = N/2 spectral values -> `out` = N samples (N = 2048 or 256).
pub(super) fn imdct(fw: &Fw, x: &[i32], n: usize, out: &mut [i32]) {
    let (n4, n8, n2) = (n / 4, n / 8, n / 2);
    let long = n == 2048;
    let (bf, r8) = if long { (4, -8) } else { (6, -5) };
    let (ct, st) = if long { (fw.a.imdct_long_c, fw.a.imdct_long_s) } else { (fw.a.imdct_short_c, fw.a.imdct_short_s) };
    let mut zr = [0i32; 512];
    let mut zi = [0i32; 512];
    let mut tr = [0i32; 512];
    let mut ti = [0i32; 512];
    let mut mx = 0i32;
    for k in 0..n4 {
        zr[k] = x[2 * k];
        zi[k] = x[2 * k + 1];
        for h in [zr[k] >> 16, zi[k] >> 16] {
            let m = h.max(!h);
            mx = mx.max(m);
        }
    }
    let msb = if mx == 0 { -1 } else { 31 - (mx as u32).leading_zeros() as i32 };
    let r7 = msb - bf;
    for k in 0..n4 {
        zr[k] = shift32(zr[k], -r7);
        zi[k] = shift32(zi[k], -r7);
    }
    for k in 0..n4 {
        tr[k] = w32(-(zr[k] as i64));
        ti[k] = zi[n4 - 1 - k];
    }
    for k in 0..n4 {
        let c = fw.rd16(ct + 2 * k as u32);
        let s = fw.rd16(st + 2 * k as u32);
        let (a, b) = (tr[k], ti[k]);
        zr[k] = w32(p16(c, a) as i64 - p16(s, b) as i64);
        zi[k] = w32(-(w32(p16(s, a) as i64 + p16(c, b) as i64) as i64));
    }
    let nb = if n4 == 512 { 9 } else { 6 };
    let mut bl = [Blk::default(); 32];
    for k in 0..n4 {
        let r = (k as u32).reverse_bits() >> (32 - nb);
        bl[k >> 4].re[k & 15] = zr[r as usize];
        bl[k >> 4].im[k & 15] = zi[r as usize];
    }
    fft_blocks(fw, &mut bl[..n4 / 16]);
    let r0 = r7 + 10 + r8;
    for k in 0..n4 {
        let a = bl[k >> 4].re[k & 15];
        let b = w32(-(bl[k >> 4].im[k & 15] as i64));
        let c = fw.rd16(ct + 2 * k as u32);
        let s = fw.rd16(st + 2 * k as u32);
        let pr = w32(p16(c, a) as i64 - p16(s, b) as i64);
        let pi = w32(p16(s, a) as i64 + p16(c, b) as i64);
        tr[k] = shift32(pr, r0);
        ti[k] = shift32(pi, r0);
    }
    for j in 0..n8 {
        out[2 * j] = w32(-(tr[n8 + j] as i64));
        out[2 * j + 1] = w32(-(ti[n8 - 1 - j] as i64));
        out[n4 + 2 * j] = ti[j];
        out[n4 + 2 * j + 1] = tr[n4 - 1 - j];
        out[n2 + 2 * j] = ti[n8 + j];
        out[n2 + 2 * j + 1] = tr[n8 - 1 - j];
        out[n2 + n4 + 2 * j] = tr[j];
        out[n2 + n4 + 2 * j + 1] = ti[n4 - 1 - j];
    }
}

/// 0x6840: 2 * ((a >> 16) * w + rnd16(w * (a & 0xffff))), Q15 window `w`.
#[inline]
pub(super) fn wmul(a: i32, w: i32) -> i32 {
    w32(2 * ((a >> 16) as i64 * w as i64 + ((w as i64 * (a & 0xffff) as i64 + 0x8000) >> 16)))
}
