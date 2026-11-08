//! The DSP's DCT-32 (routine 2:899A), written by hand from its structure.
//!
//! It is an even/odd decomposition of the 32-point matrixing:
//! * input butterflies x[i] +- x[31-i], scaled by 1/4; three further sum/difference stages of the
//!   even half (16, 8, 4 points) give outputs 0, 8, 16, 24;
//! * every odd half is folded with one constant C5 (a pair (s, t) feeds two partners as
//!   `hi +- M(s - t)` and `lo +- M(s + t)`, M = x * C5) and then combined by two-term
//!   rotations `base +- (m1 * Ck + m2 * Ck+1)`;
//! * outputs are two-term products scaled by 4.
//!
//! Products are the DSP's 32x32 multiply with the low x low partial dropped and FRCT on:
//! `hh = 2 * hi(a) * hi(c)`, `cross = 2 * (lo(a) * hi(c) + lo(c) * hi(a))` (lo unsigned); a
//! rotation term is `hh + (cross >> 16)`, an output term `(hh << 2) + (cross >> 14)`, summed over
//! the terms before the shift. Every stored value wraps to 32 bits. The 102 constants (data
//! 0x9400) come from the ROM.

#[inline]
fn hl(v: i32) -> (i64, i64) {
    ((v >> 16) as i64, (v as u32 & 0xFFFF) as i64)
}

fn parts(terms: &[(i32, i32)]) -> (i64, i64) {
    let (mut hh, mut cr) = (0i64, 0i64);
    for &(a, c) in terms {
        let (ah, al) = hl(a);
        let (ch, cl) = hl(c);
        hh += 2 * ah * ch;
        cr += 2 * (al * ch + cl * ah);
    }
    (hh, cr)
}

/// Rotation product (unwrapped; the caller adds and wraps).
fn r(terms: &[(i32, i32)]) -> i64 {
    let (hh, cr) = parts(terms);
    hh + (cr >> 16)
}

/// Output product, x4.
fn q(terms: &[(i32, i32)]) -> i32 {
    let (hh, cr) = parts(terms);
    ((hh << 2) + (cr >> 14)) as i32
}

#[inline]
fn w(v: i64) -> i32 {
    v as i32
}

/// In-place DCT-32 of one hybrid slot (32 int32). `k` = the 102 int32 constants.
pub fn dct32(x: &mut [i32; 32], k: &[i32]) {
    let x64 = |i: usize| x[i] as i64;
    let a: [i32; 16] = core::array::from_fn(|i| w((x64(i) + x64(31 - i)) >> 2));
    let b: [i32; 16] = core::array::from_fn(|i| w((x64(i) - x64(31 - i)) >> 2));
    let e: [i32; 8] = core::array::from_fn(|i| a[i].wrapping_add(a[15 - i]));
    let f: [i32; 8] = core::array::from_fn(|i| a[i].wrapping_sub(a[15 - i]));
    let g: [i32; 4] = core::array::from_fn(|i| e[i].wrapping_add(e[7 - i]));
    let h: [i32; 4] = core::array::from_fn(|i| e[i].wrapping_sub(e[7 - i]));
    let (p0, p1) = (g[0].wrapping_add(g[3]), g[0].wrapping_sub(g[3]));
    let (q0, q1) = (g[1].wrapping_add(g[2]), g[1].wrapping_sub(g[2]));
    let mut y = [0i32; 32];
    y[0] = w((q0 as i64 + p0 as i64) << 2);
    y[16] = q(&[(p0.wrapping_sub(q0), k[0])]);
    y[8] = q(&[(q1, k[1]), (p1, k[2])]);
    y[24] = q(&[(q1, k[3]), (p1, k[4])]);

    // (s, t) folded with C5 onto two partners: (hi + M(s-t), hi - M(s-t), lo + M(s+t), lo - M(s+t))
    let bfly = |s: i32, t: i32, hi: i32, lo: i32| -> (i32, i32, i32, i32) {
        let md = r(&[(s.wrapping_sub(t), k[5])]);
        let ms = r(&[(s.wrapping_add(t), k[5])]);
        (w(hi as i64 + md), w(hi as i64 - md), w(lo as i64 + ms), w(lo as i64 - ms))
    };
    // base +- (m1 * K[j] + m2 * K[j+1])
    let rot = |m1: i32, m2: i32, j: usize, base: i32| -> (i32, i32) {
        let v = r(&[(m1, k[j]), (m2, k[j + 1])]);
        (w(base as i64 + v), w(base as i64 - v))
    };

    // h -> outputs 4, 12, 20, 28
    let (hp, hm, lp, lm) = bfly(h[1], h[2], h[3], h[0]);
    y[4] = q(&[(hp, k[46]), (lp, k[47])]);
    y[20] = q(&[(hm, k[48]), (lm, k[49])]);
    y[12] = q(&[(lm, k[50]), (hm, k[51])]);
    y[28] = q(&[(lp, k[52]), (hp, k[53])]);

    // f -> outputs 2, 6, ..., 30
    let (ap, am, bp, bm) = bfly(f[2], f[5], f[6], f[1]);
    let (cp, cm, dp, dm) = bfly(f[3], f[4], f[7], f[0]);
    let (r1, r2) = rot(ap, bp, 6, cp);
    let (r3, r4) = rot(ap, bp, 8, dp);
    let (r5, r6) = rot(am, bm, 10, cm);
    let (r7, r8) = rot(am, bm, 12, dm);
    y[2] = q(&[(r1, k[54]), (r3, k[55])]);
    y[18] = q(&[(r2, k[56]), (r4, k[57])]);
    y[10] = q(&[(r6, k[58]), (r8, k[59])]);
    y[26] = q(&[(r5, k[60]), (r7, k[61])]);
    y[6] = q(&[(r7, k[62]), (r5, k[63])]);
    y[22] = q(&[(r8, k[64]), (r6, k[65])]);
    y[14] = q(&[(r4, k[66]), (r2, k[67])]);
    y[30] = q(&[(r3, k[68]), (r1, k[69])]);

    // b -> odd outputs: four folds, two rotation layers, output products
    let (p0_, m0_, l0, n0) = bfly(b[4], b[11], b[12], b[3]);
    let (p1_, m1_, l1, n1) = bfly(b[5], b[10], b[13], b[2]);
    let (p2_, m2_, l2, n2) = bfly(b[6], b[9], b[14], b[1]);
    let (p3_, m3_, l3, n3) = bfly(b[7], b[8], b[15], b[0]);
    let (s116, s115) = rot(p1_, l1, 16, l2);
    let (s117, s118) = rot(p1_, l1, 14, p2_);
    let (s120, s119) = rot(p0_, l0, 20, l3);
    let (s121, s122) = rot(p0_, l0, 18, p3_);
    let (s124, s123) = rot(m0_, n0, 24, n3);
    let (s125, s126) = rot(m0_, n0, 22, m3_);
    let (s128, s127) = rot(m1_, n1, 28, n2);
    let (s129, s130) = rot(m1_, n1, 26, m2_);
    let (t1, t2) = rot(s117, s116, 30, s121);
    let (t3, t4) = rot(s117, s116, 32, s120);
    let (t5, t6) = rot(s118, s115, 34, s122);
    let (t7, t8) = rot(s118, s115, 36, s119);
    let (t9, t10) = rot(s130, s127, 38, s126);
    let (t11, t12) = rot(s130, s127, 40, s123);
    let (t13, t14) = rot(s129, s128, 42, s125);
    let (t15, t16) = rot(s129, s128, 44, s124);
    for (u, v, j, o1, o2) in [
        (t1, t3, 70, 1, 31),
        (t2, t4, 74, 17, 15),
        (t6, t8, 78, 9, 23),
        (t5, t7, 82, 25, 7),
        (t9, t11, 86, 5, 27),
        (t10, t12, 90, 21, 11),
        (t14, t16, 94, 13, 19),
        (t13, t15, 98, 29, 3),
    ] {
        y[o1] = q(&[(u, k[j]), (v, k[j + 1])]);
        y[o2] = q(&[(u, k[j + 2]), (v, k[j + 3])]);
    }
    *x = y;
}
