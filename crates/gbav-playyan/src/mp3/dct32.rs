//! The music firmware's polyphase matrixing (vector routine 0x49c): a 32-point DCT-II by Lee's
//! recursion, the same decomposition as libmad's `dct32`. Each level splits the input into sums
//! `x[n] + x[N-1-n]` (the even outputs, by a half-size DCT) and differences multiplied by
//! `2cos((2n+1) pi / 2N)` (the odd outputs: `X[1] = B[0]`, `X[2m+1] = 2 B[m] - X[2m-1]`).
//!
//! In the firmware's integers each value carries a power-of-two scale ("depth"): the constants
//! are 2cos rather than cos, so a multiply raises the depth by one and the doubling in the odd
//! recurrence is free (it lowers the depth). The firmware stores rows of 2X: values at depth 0
//! are shifted left by one. All additions wrap at 32 bits.

use super::imdct::{mulfw, KAddr, Kc};

/// `2cos(k pi / 64)`, k = 1..31: the instruction immediates in 0x49c (function id 2).
pub const D_COS: [KAddr; 31] = [
    [2, 92, 112, 208], [2, 7468, 7488, 7584], [2, 556, 576, 672], [2, 10796, 10816, 10912],
    [2, 1020, 1040, 1136], [2, 7884, 7904, 8000], [2, 1484, 1504, 1600], [2, 14124, 14144, 14240],
    [2, 1948, 1968, 2064], [2, 8716, 8736, 8832], [2, 2412, 2432, 2528], [2, 11212, 11232, 11328],
    [2, 2876, 2896, 2992], [2, 8300, 8320, 8416], [2, 3340, 3360, 3456], [2, 17476, 17496, 17592],
    [2, 3804, 3824, 3920], [2, 9964, 9984, 10080], [2, 4268, 4288, 4384], [2, 12044, 12064, 12160],
    [2, 4732, 4752, 4848], [2, 10380, 10400, 10496], [2, 5196, 5216, 5312], [2, 14540, 14560, 14656],
    [2, 5660, 5680, 5776], [2, 9548, 9568, 9664], [2, 6124, 6144, 6240], [2, 11628, 11648, 11744],
    [2, 6588, 6608, 6704], [2, 9132, 9152, 9248], [2, 7052, 7072, 7169],
];

/// DCT of `x[..n]` (all at depth `d`) into `out[..n]` with per-value depths `dep[..n]`.
/// `ck[k]` = 2cos(k pi / 64).
fn dct(x: &[i32], d: i8, out: &mut [i32], dep: &mut [i8], ck: &[Kc; 32]) {
    let n = x.len();
    if n == 1 {
        out[0] = x[0];
        dep[0] = d;
        return;
    }
    let h = n / 2;
    let mut a = [0i32; 16];
    let mut b = [0i32; 16];
    for i in 0..h {
        a[i] = x[i].wrapping_add(x[n - 1 - i]);
        b[i] = mulfw(x[i].wrapping_sub(x[n - 1 - i]), &ck[(2 * i + 1) * 32 / n]);
    }
    let (mut ao, mut ad, mut bo, mut bd) = ([0i32; 16], [0i8; 16], [0i32; 16], [0i8; 16]);
    dct(&a[..h], d, &mut ao[..h], &mut ad[..h], ck);
    dct(&b[..h], d + 1, &mut bo[..h], &mut bd[..h], ck);
    for m in 0..h {
        out[2 * m] = ao[m];
        dep[2 * m] = ad[m];
    }
    out[1] = bo[0];
    dep[1] = bd[0];
    for m in 1..h {
        // 2 B[m] - X[2m-1]: the doubling is a depth change only
        debug_assert_eq!(bd[m] - 1, dep[2 * m - 1]);
        out[2 * m + 1] = bo[m].wrapping_sub(out[2 * m - 1]);
        dep[2 * m + 1] = dep[2 * m - 1];
    }
}

/// 32 subband samples -> the 34 rows 0x49c stores: rows 0..15 = 2X[16+k], row 16 = 0,
/// rows 17..33 = -2X[16-j].
pub fn dct32(x: &[i32; 32], row: &mut [i32; 34], ck: &[Kc; 32]) {
    let mut o = [0i32; 32];
    let mut d = [0i8; 32];
    dct(x, 0, &mut o, &mut d, ck);
    let two = |i: usize| -> i32 {
        debug_assert!(d[i] == 0 || d[i] == 1);
        if d[i] == 0 {
            o[i].wrapping_shl(1)
        } else {
            o[i]
        }
    };
    for r in 0..16 {
        row[r] = two(16 + r);
    }
    row[16] = 0;
    for j in 0..17 {
        row[17 + j] = two(16 - j).wrapping_neg();
    }
}
