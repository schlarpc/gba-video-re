//! The music firmware's IMDCTs with window and overlap (vector routines 0xa96c, long blocks, and
//! 0x12446, short blocks), written out from their algorithm. All multiplies are the firmware's
//! [`mulfw`] by constants held in the vector code's immediates (read at run time, see
//! [`ImdctConsts`]); additions wrap at 32 bits, so their order is free.
//!
//! **Long (18 -> 36).** Input prefix sums `x[i] += x[i-1]` (i = 17..1), then `x[i] += x[i-2]` for
//! odd i (17..3). Two 9-point transforms, on the even and the odd values (the odd one's middle
//! term scaled by sqrt(1/2)), each giving four sum terms A0..A3 (cosine constants a, b, c), a
//! middle term A4 and four terms B0..B3 (sqrt(3) and constants d, e, f). Nine butterflies
//! j = 0..8 pair `E[j]` (A+B for j < 4, A4, A-B mirrored) with `O[j] * sec[j]` and scale the sum
//! and difference by `post_p[j]`, `post_q[j]`. Output k < 9 = `-q[8-k]`, k >= 9 = `q[k-9]`,
//! times window k, plus the overlap; new overlap k = `p[8-k]` / `p[k-9]` times window 18 + k.
//! Window positions that are 1.0 for a block type are additions only (block type 1: overlap
//! 0..5; type 3: outputs 12..17).
//!
//! **Short (3 x 6 -> 12, overlapped into 36).** Per window: prefix sums, a 3-point transform on
//! the even and the odd values, secants on the odd results, three butterflies with post-scales,
//! then twelve window products added at 6 + 6w.

/// The firmware multiply: `x * (c0:c1 / 2^24)` from 16x16 partial products in a 48-bit
/// accumulator, the low x low term approximated as `(L >> 8) * c2`, result bits 8..39.
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct Kc {
    pub c0: i32,
    pub c1: u32,
    pub c2: u32,
}

#[inline(always)]
pub fn mulfw(x: i32, k: &Kc) -> i32 {
    let h = (x >> 16) as i64;
    let l = (x & 0xffff) as i64;
    let acc = h * (k.c0 as i64 * 65536 + k.c1 as i64) + l * k.c0 as i64 + (((l >> 8) * k.c2 as i64) & 0xffff);
    (acc >> 8) as u32 as i32
}

/// A constant's location: function (0 = 0xa96c, 1 = 0x12446, 2 = 0x49c in py_fw1) and the
/// instructions holding c2, c1, c0, each as `2 * offset + form` (form 1: 48-bit vector
/// instruction, simm6 in h2 bits 5..0; form 0: 80-bit, imm16 = h3 & 0xfc00 | h2 & 0x3ff);
/// c0 = -1: no c0 instruction (0). Function id -1 marks a window tap of 1.0 (no multiply).
pub type KAddr = [i32; 4];
pub const FUNC_BASE: [u32; 3] = [0xa96c, 0x12446, 0x49c];
const UNIT: KAddr = [-1, 0, 0, 0];

/// Long-block constants: the six 9-point cosines a..f, sqrt(3), sqrt(1/2).
pub const L_NINE: [KAddr; 6] = [
    [0, 1328, 1348, 1444],
    [0, 1712, 1732, 1828],
    [0, 2096, 2116, 2212],
    [0, 5424, 5444, 5540],
    [0, 5808, 5828, 5924],
    [0, 6192, 6212, 6308],
];
pub const L_SQRT3: KAddr = [0, 5072, 5092, 5188];
pub const L_HALF_SQRT2: KAddr = [0, 13104, 13124, 13220];
/// Secants per butterfly (j = 4 uses the odd half's sqrt(1/2) instead).
pub const L_SEC: [KAddr; 9] = [
    [0, 17744, 17764, 17860],
    [0, 18928, 18948, 19044],
    [0, 20112, 20132, 20228],
    [0, 21296, 21316, 21412],
    UNIT,
    [0, 23248, 23268, 23364],
    [0, 24432, 24452, 24548],
    [0, 25616, 25636, 25732],
    [0, 26800, 26820, 26916],
];
pub const L_POST_P: [KAddr; 9] = [
    [0, 18128, 18148, 18244],
    [0, 19312, 19332, 19428],
    [0, 20496, 20516, 20612],
    [0, 21680, 21700, 21796],
    [0, 22448, 22468, 22564],
    [0, 23632, 23652, 23748],
    [0, 24816, 24836, 24932],
    [0, 26000, 26020, 26116],
    [0, 27184, 27204, 27300],
];
pub const L_POST_Q: [KAddr; 9] = [
    [0, 18512, 18532, 18628],
    [0, 19696, 19716, 19812],
    [0, 20880, 20900, 20996],
    [0, 22064, 22084, 22180],
    [0, 22832, 22852, 22948],
    [0, 24016, 24036, 24132],
    [0, 25200, 25220, 25316],
    [0, 26384, 26404, 26500],
    [0, 27568, 27588, 27684],
];
/// Long windows for block types 0, 1, 3: 18 output taps, then 18 overlap taps.
pub const L_WIN: [[KAddr; 36]; 3] = [
    [
        [0, 28020, 28040, 28137], [0, 28456, 28476, 28572], [0, 28932, 28952, 29048], [0, 29400, 29420, 29516],
        [0, 29868, 29888, 29984], [0, 30332, 30352, 30448], [0, 30800, 30820, 30916], [0, 31268, 31288, 31384],
        [0, 31736, 31756, 31852], [0, 32180, 32200, 32296], [0, 32624, 32644, 32740], [0, 33068, 33088, 33184],
        [0, 33512, 33532, 33628], [0, 33956, 33976, 34072], [0, 34400, 34420, 34516], [0, 34840, 34860, 34956],
        [0, 35280, 35300, 35396], [0, 35720, 35740, 35836], [0, 36160, 36180, 36276], [0, 36536, 36556, 36652],
        [0, 36924, 36944, 37040], [0, 37312, 37332, 37428], [0, 54660, 54680, 54776], [0, 58000, 58020, 58116],
        [0, 58380, 58400, 58496], [0, 58760, 58780, 58876], [0, 59140, 59160, 59256], [0, 59520, 59540, 59636],
        [0, 59900, 59920, 60016], [0, 60280, 60300, 60396], [0, 60660, 60680, 60776], [0, 61040, 61060, 61156],
        [0, 61420, 61440, 61536], [0, 61796, 61816, 61912], [0, 62172, 62192, 62288], [0, 62548, 62568, 62665],
    ],
    [
        [0, 28020, 28040, 28137], [0, 28456, 28476, 28572], [0, 28932, 28952, 29048], [0, 29400, 29420, 29516],
        [0, 29868, 29888, 29984], [0, 30332, 30352, 30448], [0, 30800, 30820, 30916], [0, 31268, 31288, 31384],
        [0, 31736, 31756, 31852], [0, 32180, 32200, 32296], [0, 32624, 32644, 32740], [0, 33068, 33088, 33184],
        [0, 33512, 33532, 33628], [0, 33956, 33976, 34072], [0, 34400, 34420, 34516], [0, 34840, 34860, 34956],
        [0, 35280, 35300, 35396], [0, 35720, 35740, 35836], UNIT, UNIT, UNIT, UNIT, UNIT, UNIT,
        [0, 44976, 44996, 45092], [0, 46420, 46440, 46536], [0, 46800, 46820, 46916], [0, 47180, 47200, 47296],
        [0, 47560, 47580, 47676], [0, 47940, 47960, 48056], [0, 48320, 48341, 48421], [0, 48652, 48673, 48753],
        [0, 48984, 49005, 49085], [0, 49312, 49333, 49413], [0, 49640, 49661, 49741], [0, 49968, 49989, 50069],
    ],
    [
        [0, 48320, 48341, 48421], [0, 48652, 48673, 48753], [0, 48984, 49005, 49085], [0, 49312, 49333, 49413],
        [0, 49640, 49661, 49741], [0, 49968, 49989, 50069], [0, 52836, 52856, 52952], [0, 53304, 53324, 53420],
        [0, 53772, 53792, 53888], [0, 54216, 54236, 54332], [0, 54660, 54680, 54776], [0, 55104, 55124, 55220],
        UNIT, UNIT, UNIT, UNIT, UNIT, UNIT,
        [0, 56084, 56104, 56200], [0, 56460, 56480, 56576], [0, 56848, 56868, 56964], [0, 57236, 57256, 57352],
        [0, 57624, 57644, 57740], [0, 58000, 58020, 58116], [0, 58380, 58400, 58496], [0, 58760, 58780, 58876],
        [0, 59140, 59160, 59256], [0, 59520, 59540, 59636], [0, 59900, 59920, 60016], [0, 60280, 60300, 60396],
        [0, 60660, 60680, 60776], [0, 61040, 61060, 61156], [0, 61420, 61440, 61536], [0, 61796, 61816, 61912],
        [0, 62172, 62192, 62288], [0, 62548, 62568, 62665],
    ],
];
/// Short blocks: 1/2, sqrt(3)/2, secants for the odd 3-point results (sum, difference, the
/// other sum), post-scales of the butterfly sums P0..P2 and differences Q0..Q2, 12 window taps.
pub const S_HALF: KAddr = [1, 660, 681, 760];
pub const S_SQRT3_2: KAddr = [1, 996, 1016, 1112];
pub const S_SEC: [KAddr; 3] = [[1, 2996, 3016, 3112], [1, 2644, 2664, 2760], [1, 2292, 2312, 2408]];
pub const S_POST_P: [KAddr; 3] = [[1, 3612, 3632, 3728], [1, 3964, 3984, 4080], [1, 4316, 4336, 4432]];
pub const S_POST_Q: [KAddr; 3] = [[1, 5020, 5040, 5136], [1, 4668, 4688, 4784], [1, 7484, 7504, 7600]];
pub const S_WIN: [KAddr; 12] = [
    [1, 9596, 9616, 9712], [1, 7836, 7856, 7952], [1, 8188, 8208, 8304], [1, 8540, 8560, 8656],
    [1, 8892, 8912, 9008], [1, 9244, 9264, 9360], [1, 6780, 6800, 6896], [1, 6076, 6096, 6192],
    [1, 5372, 5392, 5488], [1, 5724, 5744, 5840], [1, 6428, 6448, 6544], [1, 7132, 7152, 7248],
];

/// The constants of both IMDCTs, as read from an image. `None` = window tap of 1.0.
#[derive(Clone, Debug)]
pub struct ImdctConsts {
    pub nine: [Kc; 6],
    pub sqrt3: Kc,
    pub half_sqrt2: Kc,
    pub sec: [Kc; 9],
    pub post_p: [Kc; 9],
    pub post_q: [Kc; 9],
    pub win: [[Option<Kc>; 36]; 3],
    pub s_half: Kc,
    pub s_sqrt3_2: Kc,
    pub s_sec: [Kc; 3],
    pub s_post_p: [Kc; 3],
    pub s_post_q: [Kc; 3],
    pub s_win: [Kc; 12],
}

impl ImdctConsts {
    pub fn read(k: &dyn Fn(&KAddr) -> Kc) -> ImdctConsts {
        let opt = |a: &KAddr| if a[0] < 0 { None } else { Some(k(a)) };
        ImdctConsts {
            nine: L_NINE.map(|a| k(&a)),
            sqrt3: k(&L_SQRT3),
            half_sqrt2: k(&L_HALF_SQRT2),
            sec: L_SEC.map(|a| if a[0] < 0 { Kc::default() } else { k(&a) }),
            post_p: L_POST_P.map(|a| k(&a)),
            post_q: L_POST_Q.map(|a| k(&a)),
            win: L_WIN.map(|w| w.map(|a| opt(&a))),
            s_half: k(&S_HALF),
            s_sqrt3_2: k(&S_SQRT3_2),
            s_sec: S_SEC.map(|a| k(&a)),
            s_post_p: S_POST_P.map(|a| k(&a)),
            s_post_q: S_POST_Q.map(|a| k(&a)),
            s_win: S_WIN.map(|a| k(&a)),
        }
    }

    /// Every constant in a fixed order (for the tables digest).
    pub fn all(&self) -> Vec<Kc> {
        let mut v = Vec::new();
        v.extend(self.nine);
        v.push(self.sqrt3);
        v.push(self.half_sqrt2);
        v.extend(self.sec);
        v.extend(self.post_p);
        v.extend(self.post_q);
        for w in &self.win {
            v.extend(w.iter().map(|x| x.unwrap_or_default()));
        }
        v.push(self.s_half);
        v.push(self.s_sqrt3_2);
        v.extend(self.s_sec);
        v.extend(self.s_post_p);
        v.extend(self.s_post_q);
        v.extend(self.s_win);
        v
    }
}

/// One 9-point half: returns (A0..A4, B0..B3) with A4 = x0 - x2 + x4 - x6 + x8 (unscaled).
fn nine(x: [i32; 9], k: &ImdctConsts) -> ([i32; 5], [i32; 4]) {
    let m = mulfw;
    let [a, b, c, d, e, f] = &k.nine;
    let t = x[0].wrapping_mul(2).wrapping_add(x[6]);
    let aa = [
        t.wrapping_add(m(x[2], a)).wrapping_add(m(x[4], b)).wrapping_add(m(x[8], c)),
        x[0].wrapping_mul(2).wrapping_add(x[2]).wrapping_sub(x[4]).wrapping_sub(x[6].wrapping_mul(2)).wrapping_sub(x[8]),
        t.wrapping_sub(m(x[2], c)).wrapping_sub(m(x[4], a)).wrapping_add(m(x[8], b)),
        t.wrapping_sub(m(x[2], b)).wrapping_add(m(x[4], c)).wrapping_sub(m(x[8], a)),
        x[0].wrapping_sub(x[2]).wrapping_add(x[4]).wrapping_sub(x[6]).wrapping_add(x[8]),
    ];
    let s3 = m(x[3], &k.sqrt3);
    let bb = [
        m(x[1], d).wrapping_add(s3).wrapping_add(m(x[5], e)).wrapping_add(m(x[7], f)),
        m(x[1].wrapping_sub(x[5]).wrapping_sub(x[7]), &k.sqrt3),
        m(x[1], e).wrapping_sub(s3).wrapping_sub(m(x[5], f)).wrapping_add(m(x[7], d)),
        m(x[1], f).wrapping_sub(s3).wrapping_add(m(x[5], d)).wrapping_sub(m(x[7], e)),
    ];
    (aa, bb)
}

/// Long-block IMDCT, window `win` (0, 1 or 3 -> index 0, 1, 2), overlap-add.
pub fn imdct_long(x: &[i32; 18], ov: &[i32; 18], out: &mut [i32; 18], nov: &mut [i32; 18], bt: u32, k: &ImdctConsts) {
    let mut s = *x;
    for i in (1..18).rev() {
        s[i] = s[i].wrapping_add(s[i - 1]);
    }
    for i in (3..18).rev().step_by(2) {
        s[i] = s[i].wrapping_add(s[i - 2]);
    }
    let (ea, eb) = nine(std::array::from_fn(|i| s[2 * i]), k);
    let (mut oa, ob) = nine(std::array::from_fn(|i| s[2 * i + 1]), k);
    oa[4] = mulfw(oa[4], &k.half_sqrt2);
    let side = |a: &[i32; 5], b: &[i32; 4], j: usize| match j {
        0..=3 => a[j].wrapping_add(b[j]),
        4 => a[4],
        _ => a[8 - j].wrapping_sub(b[8 - j]),
    };
    let mut p = [0i32; 9];
    let mut q = [0i32; 9];
    for j in 0..9 {
        let e = side(&ea, &eb, j);
        let o = if j == 4 { oa[4] } else { mulfw(side(&oa, &ob, j), &k.sec[j]) };
        p[j] = mulfw(e.wrapping_add(o), &k.post_p[j]);
        q[j] = mulfw(e.wrapping_sub(o), &k.post_q[j]);
    }
    let w = &k.win[match bt {
        1 => 1,
        3 => 2,
        _ => 0,
    }];
    let tap = |v: i32, t: &Option<Kc>| t.as_ref().map_or(v, |c| mulfw(v, c));
    for i in 0..18 {
        let y = if i < 9 { q[8 - i].wrapping_neg() } else { q[i - 9] };
        out[i] = tap(y, &w[i]).wrapping_add(ov[i]);
        let z = if i < 9 { p[8 - i] } else { p[i - 9] };
        nov[i] = tap(z, &w[18 + i]);
    }
}

/// Short-block IMDCT: three 12-point windows at 6, 12, 18 of the 36-sample span.
pub fn imdct_short(x: &[i32; 18], ov: &[i32; 18], out: &mut [i32; 18], nov: &mut [i32; 18], k: &ImdctConsts) {
    let m = mulfw;
    let mut buf = [0i32; 36];
    for w in 0..3 {
        let v = &x[6 * w..6 * w + 6];
        let a0 = v[0];
        let a1 = v[1].wrapping_add(v[0]);
        let a2 = v[2].wrapping_add(v[1]);
        let a3 = v[3].wrapping_add(v[2]).wrapping_add(a1);
        let a4 = v[4].wrapping_add(v[3]);
        let a5 = v[5].wrapping_add(v[4]).wrapping_add(v[3]).wrapping_add(v[2]);
        let (t, s) = (a0.wrapping_add(m(a4, &k.s_half)), m(a2, &k.s_sqrt3_2));
        let (e_p, e_m, e_n) = (t.wrapping_add(s), a0.wrapping_sub(a4), t.wrapping_sub(s));
        let (t, s) = (a1.wrapping_add(m(a5, &k.s_half)), m(a3, &k.s_sqrt3_2));
        let o_p = m(t.wrapping_add(s), &k.s_sec[0]);
        let o_m = m(a1.wrapping_sub(a5), &k.s_sec[1]);
        let o_n = m(t.wrapping_sub(s), &k.s_sec[2]);
        let pq = |e: i32, o: i32, i: usize| (m(e.wrapping_add(o), &k.s_post_p[i]), m(e.wrapping_sub(o), &k.s_post_q[i]));
        let (p0, q0) = pq(e_p, o_p, 0);
        let (p1, q1) = pq(e_m, o_m, 1);
        let (p2, q2) = pq(e_n, o_n, 2);
        let src = [q2, q1, q0, q0, q1, q2, p2, p1, p0, p0, p1, p2];
        for i in 0..12 {
            let b = &mut buf[6 + 6 * w + i];
            *b = b.wrapping_add(m(src[i], &k.s_win[i]));
        }
    }
    for i in 0..18 {
        out[i] = buf[i].wrapping_add(ov[i]);
        nov[i] = buf[18 + i];
    }
}
