//! The Nintendo MP3 Player's MP3 decoder, as its TI TMS320VC5416 DSP firmware implements it.
//!
//! The DSP image is uploaded by the GBA from a standard C54x boot table in the cartridge ROM
//! (ROM 0x0A8F34: keyword 0x08AA, entry 0x42FC, 91 sections). [`Dsp::from_rom`] rebuilds the DSP's
//! data memory from that table and runs the TI C runtime's `cinit` records (program 0x48C2) the way
//! `c_int00` does, so every table the decoder needs is read from the user's ROM; only md5s of the
//! table regions are in this source.
//!
//! The model is bit-exact against the DSP firmware running in the C54x emulator
//! (local, not in this repository; oracle harness tools/playyan/nmp): per stage and end to end, see
//! docs/playyan/decoders.md. Stages, with the DSP routine each one reproduces:
//!
//! * bitstream: MPEG-1, MPEG-2 and MPEG-2.5 Layer III (the DSP decodes all three, unlike the
//!   Play-Yan's VC01 music firmware): frames, side info, scalefactors (ISO 13818-3 for LSF),
//!   Huffman (standard; the DSP's binary search over 32-bit code words gives the ISO values);
//! * requantiser 2:810E: |v| < 128 from a table, otherwise a cubic polynomial seed and three
//!   Newton steps for |v|^(1/3) times |v|, then a 32x32 gain multiply and an exponent shift;
//!   count1 lines (2:99C6) use a 4-entry table instead;
//! * M/S stereo 2:A0C0 (x 0x5A82799A, 32x32 with the lo*lo term dropped);
//! * reorder 2:A0F7, antialias 2:850C, IMDCT 2:82BF (direct 18x18 / 6x6 matrices) with the
//!   per-block-type data moves of 2:9715 and the window rotation 2:A51B, frequency inversion;
//! * DCT-32 2:899A ([`dct32`]: even/odd decomposition with rotations) and the 512-tap window 2:81B0.
//!
//! Intensity stereo (MPEG-1 and MPEG-2, including the firmware's own slen formulas for the
//! intensity-coded channel) and mixed blocks (MPEG-1, and MPEG-2 / 2.5 with the firmware's own
//! scalefactor reader and 8 kHz band layout) are modelled. Not modelled (the decoder returns
//! `Error::Undefined`): a mixed block on the MPEG-2 intensity-coded channel (see docs/playyan/decoders.md).

mod dct32;
mod huff;

use gbav_common::md5;
use gbav_common::{Error, Result};

const BOOT_OFF: usize = 0x0A8F34;
const BOOT_WORDS: usize = 0xF730;
const BOOT_MD5: &str = "38b484de6408453ceeacbb65c409d58b";
const CINIT: usize = 0x48C2;

/// Table regions in DSP data memory (after cinit) with their md5.
const TABLES: [(&str, usize, usize, &str); 8] = [
    ("dct32 constants", 0x9400, 204, "dd02c862a67b4c43124a46d8fd52bab4"),
    ("antialias", 0x94CC, 32, "b025a43d8602c6830c93da69ee8716ad"),
    ("short window", 0x94EC, 12, "cc5204f9ffa59b1a6d76460098d8c893"),
    ("long window", 0x94F8, 36, "c8db18a164ff9d28df2fe12fc2e08400"),
    ("requantiser", 0xB182, 826, "f98f852b0c34edd575469c0b74e9352a"),
    ("imdct 18x18", 0xB4BC, 648, "1af9305d2a1cca310cade0597c5f2afc"),
    ("imdct 6x6", 0xB744, 72, "289d7f02171e5123c304301f447c2dd5"),
    ("synthesis window", 0xB78C, 1024, "a7e1f96494446d38aac109ea2457e268"),
];

/// The speaker path's sample-rate converter tables (steps, FIR coefficient sets and their pointer
/// table, 2nd-order section coefficients): data 0xE013..0xE5E9.
const SRC_TABLES: (usize, usize, &str) = (0xE013, 1495, "26ea593d71051ca179599d1c1f1c4929");

/// The DSP's data memory (64 Ki words, DROM = 1) as it is after the C runtime initialisation.
#[derive(Clone)]
pub struct Dsp {
    pub data: Vec<u16>,
}

fn hpi_fold(a: usize) -> usize {
    if (0x8000..=0xFFFF).contains(&a) {
        a | 0x10000
    } else if (1..=4).contains(&(a >> 16)) && (a & 0xFFFF) < 0x8000 {
        a & 0xFFFF
    } else {
        a
    }
}

impl Dsp {
    /// Build from the Nintendo MP3 Player ROM (the 1 MiB image or a 4 MiB mirror).
    pub fn from_rom(rom: &[u8]) -> Result<Dsp> {
        let bt = rom
            .get(BOOT_OFF..BOOT_OFF + 2 * BOOT_WORDS)
            .ok_or_else(|| Error::Format("ROM too small for the NMP boot table".into()))?;
        if md5::hex(bt) != BOOT_MD5 {
            return Err(Error::Format("not the Nintendo MP3 Player ROM (boot table md5 differs)".into()));
        }
        let w: Vec<u16> = bt.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        let mut hpi = vec![0u16; 0x40000];
        let mut i = 5;
        while w[i] != 0 {
            let n = w[i] as usize;
            let dest = (w[i + 1] as usize) << 16 | w[i + 2] as usize;
            for k in 0..n {
                hpi[hpi_fold(dest + k)] = w[i + 3 + k];
            }
            i += 3 + n;
        }
        let mut p = CINIT;
        loop {
            let n = hpi[p] as usize;
            if n == 0 {
                break;
            }
            let dest = hpi[p + 1] as usize;
            for k in 0..n {
                let a = (dest + k) & 0xFFFF;
                hpi[if a < 0x8000 { a } else { 0x10000 | a }] = hpi[p + 2 + k];
            }
            p += 2 + n;
        }
        let mut data = hpi[0..0x8000].to_vec();
        data.extend_from_slice(&hpi[0x18000..0x20000]);
        for (name, a, n, h) in TABLES {
            let b: Vec<u8> = data[a..a + n].iter().flat_map(|v| v.to_le_bytes()).collect();
            if md5::hex(&b) != h {
                return Err(Error::Format(format!("NMP DSP table '{name}' has an unexpected md5")));
            }
        }
        let (a, n, h) = SRC_TABLES;
        let b: Vec<u8> = data[a..a + n].iter().flat_map(|v| v.to_le_bytes()).collect();
        if md5::hex(&b) != h {
            return Err(Error::Format("NMP DSP speaker tables have an unexpected md5".into()));
        }
        Ok(Dsp { data })
    }
    #[inline]
    fn w(&self, a: i64) -> i64 {
        self.data[(a & 0xFFFF) as usize] as i64
    }
    fn d32(&self, a: usize) -> i32 {
        ((self.data[a] as u32) << 16 | self.data[a + 1] as u32) as i32
    }
    fn d32s(&self, a: usize, n: usize) -> Vec<i32> {
        (0..n).map(|k| self.d32(a + 2 * k)).collect()
    }
}

// ------------------------------------------------------------------------------ fixed-point helpers

#[inline]
fn sx(v: i64, b: u32) -> i64 {
    let s = 64 - b;
    (v << s) >> s
}
#[inline]
fn s32(v: i64) -> i32 {
    v as u32 as i32
}
#[inline]
fn sat32(v: i64) -> i32 {
    v.clamp(i32::MIN as i64, i32::MAX as i64) as i32
}
/// NORM-style shift by a 6-bit signed count, 40-bit accumulator.
#[inline]
fn shl40(a: i64, t: i64) -> i64 {
    let t = sx(t, 6);
    if t >= 0 {
        sx(a << t, 40)
    } else {
        sx(a, 40) >> -t
    }
}
fn exp40(a: i64) -> i64 {
    let a = sx(a, 40);
    if a == 0 {
        return 0;
    }
    let s = (a >> 39) & 1;
    let mut n = 0;
    for bit in (0..39).rev() {
        if (a >> bit) & 1 == s {
            n += 1;
        } else {
            break;
        }
    }
    n - 8
}
#[inline]
fn hl(x: i32) -> (i64, i64) {
    ((x >> 16) as i64, (x as u32 & 0xFFFF) as i64)
}
/// hi*hi and the two cross terms of a 32x32 product (lo*lo dropped).
#[inline]
fn mul_parts(x: i32, y: i32) -> (i64, i64) {
    let (xh, xl) = hl(x);
    let (yh, yl) = hl(y);
    (xh * yh, xl * yh + yl * xh)
}

// ------------------------------------------------------------------------------ requantiser

impl Dsp {
    /// 2:810E..2:8198: one big-values line.
    fn requant(&self, v: i32, s: i64) -> i32 {
        let m = |a: i64| self.w(a);
        let neg = v < 0;
        let av = v.unsigned_abs() as i64;
        let (e, mh, ml);
        if av < 128 {
            e = sx(m(0xB286 + av), 16);
            mh = sx(m(0xB306 + 2 * av), 16);
            ml = m(0xB307 + 2 * av);
        } else {
            let mut a = av << 16;
            let t = exp40(a);
            a = shl40(a, t);
            let x40 = a;
            let x32 = sx(a, 32);
            e = 15 - t;
            let tm = sx(a >> 16, 16);
            a = sx(m(0xB282), 16) << 16;
            let mut b = sx(m(0xB283), 16) << 16;
            for c in [0xB284i64, 0xB285, 0xB286] {
                a = sx(sx(sx(sx(a >> 16, 17) * tm * 2 + b, 40) + 0x8000, 40) & !0xFFFF, 40);
                b = sx(m(c), 16) << 16;
            }
            let mut p = sx(a, 32);
            let y0 = m((sx(sx(a >> 16, 16) >> 7, 16) + 0xB182) & 0xFFFF) & 0xFFFF;
            for _ in 0..3 {
                let (ph, pl) = (sx(p >> 16, 16), p & 0xFFFF);
                let mut a = 2 * pl * ph + 2 * pl * ph;
                a = (a >> 16) + 2 * ph * ph;
                let q = sx(a, 32);
                let (qh, ql) = (sx(q >> 16, 16), q & 0xFFFF);
                a = 2 * ql * ph + 2 * pl * qh;
                a = (a >> 16) + 2 * ph * qh;
                a = sx(-sx(a - x32, 40), 40);
                let d3 = sx(a >> 16, 16);
                let d4 = a & 0xFFFF;
                a = d4 * y0;
                a = (a >> 16) + y0 * d3;
                p = sx(sx(a + p, 40), 32);
            }
            let (ph, pl) = (sx(p >> 16, 16), p & 0xFFFF);
            let t = sx(x40 >> 16, 16);
            let mut a = (t & 0xFFFF) * pl * 2;
            a = (a >> 16) + t * ph * 2;
            let r = sx(a, 32);
            mh = sx(r >> 16, 16);
            ml = r & 0xFFFF;
        }
        let shift = (sx(s, 16) >> 2) + sx(m(0xB406 + e), 16);
        let g = 3 * (4 - (s & 3)) + sx(m(0xB416 + e), 16);
        let ga = (2 * g + 0xB42F) & 0xFFFF;
        let glo = m(ga);
        let ghi = sx(m(ga - 1), 16);
        let mut a = 2 * glo * mh + 2 * ml * ghi;
        a = (a >> 16) + 2 * ghi * mh;
        if neg {
            a = sx(-a, 40);
        }
        let d = shift - 3;
        if d + 16 >= 0 {
            a = shl40(a, d);
        } else {
            a = shl40(a >> 16, d + 16);
            if d + 32 < 0 {
                a >>= 16;
            }
        }
        s32(a)
    }
    /// 2:99C6: a count1 line (+-1).
    fn count1(&self, v: i32, s: i64) -> i32 {
        let k = 2 * (s & 3);
        let t1 = sx(self.w(0xB426 + k) << 16 | self.w(0xB427 + k), 32);
        let d = (sx(s, 16) >> 2) - 3;
        let a = if v < 0 { -t1 } else { t1 };
        let a = if d + 16 >= 0 {
            shl40(a, d)
        } else if d + 32 >= 0 {
            shl40(a >> 16, d + 16)
        } else {
            a >> 32
        };
        s32(a)
    }
}

const MS_C: i32 = 0x5A82799A;
fn ms_mul(x: i32) -> i32 {
    let (xh, xl) = hl(x);
    let (ch, cl) = hl(MS_C);
    s32(((2 * cl * xh + 2 * xl * ch) >> 16) + 2 * xh * ch)
}

// ------------------------------------------------------------------------------ bitstream

const BITRATE: [u32; 15] = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320];
const BITRATE_LSF: [u32; 15] = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160];
/// Sample rates by index: MPEG-1 0..2, MPEG-2 3..5, MPEG-2.5 6..8.
const RATES: [u32; 9] = [44100, 48000, 32000, 22050, 24000, 16000, 11025, 12000, 8000];
const SFB_LONG: [[u16; 23]; 9] = [
    [0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 52, 62, 74, 90, 110, 134, 162, 196, 238, 288, 342, 418, 576],
    [0, 4, 8, 12, 16, 20, 24, 30, 36, 42, 50, 60, 72, 88, 106, 128, 156, 190, 230, 276, 330, 384, 576],
    [0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 54, 66, 82, 102, 126, 156, 194, 240, 296, 364, 448, 550, 576],
    [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
    [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 114, 136, 162, 194, 232, 278, 332, 394, 464, 540, 576],
    [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
    [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
    [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
    [0, 12, 24, 36, 48, 60, 72, 88, 108, 132, 160, 192, 232, 280, 336, 400, 476, 566, 568, 570, 572, 574, 576],
];
const SFB_SHORT: [[u16; 14]; 9] = [
    [0, 4, 8, 12, 16, 22, 30, 40, 52, 66, 84, 106, 136, 192],
    [0, 4, 8, 12, 16, 22, 28, 38, 50, 64, 80, 100, 126, 192],
    [0, 4, 8, 12, 16, 22, 30, 42, 58, 78, 104, 138, 180, 192],
    [0, 4, 8, 12, 18, 24, 32, 42, 56, 74, 100, 132, 174, 192],
    [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 136, 180, 192],
    [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192],
    [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192],
    [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192],
    [0, 8, 16, 24, 36, 52, 72, 96, 124, 160, 162, 164, 166, 192],
];
/// ISO 13818-3 nr_of_sfb per (slen group set, block kind long/short/mixed).
const LSF_NSF: [[[usize; 4]; 3]; 6] = [
    [[6, 5, 5, 5], [9, 9, 9, 9], [6, 9, 9, 9]],
    [[6, 5, 7, 3], [9, 9, 12, 6], [6, 9, 12, 6]],
    [[11, 10, 0, 0], [18, 18, 0, 0], [15, 18, 0, 0]],
    [[7, 7, 7, 0], [12, 12, 12, 0], [6, 15, 12, 0]],
    [[6, 6, 6, 3], [12, 9, 9, 6], [6, 12, 9, 6]],
    [[8, 8, 5, 0], [15, 12, 9, 0], [6, 18, 9, 0]],
];
const SLEN: [(u32, u32); 16] = [
    (0, 0), (0, 1), (0, 2), (0, 3), (3, 0), (1, 1), (1, 2), (1, 3),
    (2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3), (4, 2), (4, 3),
];
const PRETAB: [i64; 22] = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 3, 3, 3, 2, 0];

struct Bits<'a> {
    d: &'a [u8],
    p: usize,
}
impl Bits<'_> {
    fn get(&mut self, n: u32) -> u32 {
        let mut v = 0u32;
        for _ in 0..n {
            let byte = *self.d.get(self.p >> 3).unwrap_or(&0);
            v = v << 1 | (byte >> (7 - (self.p & 7)) as u32) as u32 & 1;
            self.p += 1;
        }
        v
    }
}

/// MPEG-1 / MPEG-2 / MPEG-2.5 Layer III frame header fields (the DSP decodes all three).
#[derive(Debug, Clone, Copy)]
pub struct Header {
    pub rate: u32,
    rate_idx: usize,
    /// MPEG-2 / 2.5 low sampling frequency frame (one granule).
    pub lsf: bool,
    pub mode: u8,
    pub mode_ext: u8,
    pub crc: bool,
    /// Frame length in bytes.
    pub size: usize,
}

/// Parse a frame header; `None` if the 4 bytes are not a Layer III header.
pub fn header(b: &[u8]) -> Option<Header> {
    if b.len() < 4 {
        return None;
    }
    let h = u32::from_be_bytes([b[0], b[1], b[2], b[3]]);
    let ver = (h >> 19) & 3;
    if (h >> 21) & 0x7FF != 0x7FF || ver == 1 || (h >> 17) & 3 != 1 {
        return None;
    }
    let bri = ((h >> 12) & 15) as usize;
    let sri = ((h >> 10) & 3) as usize;
    if bri == 0 || bri == 15 || sri == 3 {
        return None;
    }
    let pad = ((h >> 9) & 1) as usize;
    let rate_idx = sri + match ver { 3 => 0, 2 => 3, _ => 6 };
    let rate = RATES[rate_idx];
    let lsf = ver != 3;
    Some(Header {
        rate,
        rate_idx,
        lsf,
        mode: ((h >> 6) & 3) as u8,
        mode_ext: ((h >> 4) & 3) as u8,
        crc: (h >> 16) & 1 == 0,
        size: if lsf { (72000 * BITRATE_LSF[bri] / rate) as usize } else { (144000 * BITRATE[bri] / rate) as usize } + pad,
    })
}

/// The Layer III frames of a file (after any ID3v2 tags), as slices.
pub fn frames(data: &[u8]) -> Vec<&[u8]> {
    let mut p = 0;
    while data.len() >= p + 10 && &data[p..p + 3] == b"ID3" {
        let sz = (data[p + 6] as usize & 0x7f) << 21
            | (data[p + 7] as usize & 0x7f) << 14
            | (data[p + 8] as usize & 0x7f) << 7
            | (data[p + 9] as usize & 0x7f);
        p += 10 + sz + if data[p + 5] & 0x10 != 0 { 10 } else { 0 };
    }
    let mut out = Vec::new();
    while p + 4 <= data.len() {
        match header(&data[p..]) {
            Some(h) if p + h.size <= data.len() => {
                out.push(&data[p..p + h.size]);
                p += h.size;
            }
            _ => p += 1,
        }
    }
    out
}

#[derive(Default, Clone)]
struct GranuleChannel {
    part2_3_length: usize,
    big_values: usize,
    global_gain: i64,
    scalefac_compress: usize,
    window_switching: bool,
    block_type: u8,
    mixed: bool,
    table_select: [usize; 3],
    subblock_gain: [i64; 3],
    region0_count: usize,
    region1_count: usize,
    preflag: bool,
    scalefac_scale: bool,
    count1table: bool,
}

fn huff_pair(b: &mut Bits, tab: usize) -> (i32, i32) {
    let (off, tlen, lin) = huff::MAIN[tab];
    if off == u16::MAX {
        return (0, 0);
    }
    let off = off as usize;
    let tlen = tlen as usize;
    let (mut x, mut y);
    let mut point = 0usize;
    let mut left = 32;
    loop {
        let e = huff::TREE[off + point];
        if e & 0xff00 == 0 {
            x = (e >> 4 & 15) as i32;
            y = (e & 15) as i32;
            break;
        }
        if b.get(1) != 0 {
            while huff::TREE[off + point] & 0xff >= 250 {
                point += (huff::TREE[off + point] & 0xff) as usize;
            }
            point += (huff::TREE[off + point] & 0xff) as usize;
        } else {
            while huff::TREE[off + point] >> 8 >= 250 {
                point += (huff::TREE[off + point] >> 8) as usize;
            }
            point += (huff::TREE[off + point] >> 8) as usize;
        }
        left -= 1;
        if left == 0 || point >= tlen {
            x = 0;
            y = 0;
            break;
        }
    }
    if lin > 0 && x == 15 {
        x += b.get(lin as u32) as i32;
    }
    if x != 0 && b.get(1) != 0 {
        x = -x;
    }
    if lin > 0 && y == 15 {
        y += b.get(lin as u32) as i32;
    }
    if y != 0 && b.get(1) != 0 {
        y = -y;
    }
    (x, y)
}

fn count1_quad(b: &mut Bits, table_b: bool) -> [i32; 4] {
    let v = if table_b {
        15 - b.get(4) as u16
    } else {
        let off = huff::COUNT1_A;
        let mut point = 0usize;
        loop {
            let e = huff::TREE[off + point];
            if e & 0xff00 == 0 {
                break e & 15;
            }
            if b.get(1) != 0 {
                while huff::TREE[off + point] & 0xff >= 250 {
                    point += (huff::TREE[off + point] & 0xff) as usize;
                }
                point += (huff::TREE[off + point] & 0xff) as usize;
            } else {
                while huff::TREE[off + point] >> 8 >= 250 {
                    point += (huff::TREE[off + point] >> 8) as usize;
                }
                point += (huff::TREE[off + point] >> 8) as usize;
            }
        }
    };
    let mut out = [0i32; 4];
    for (j, k) in [3, 2, 1, 0].into_iter().enumerate() {
        let mut q = ((v >> k) & 1) as i32;
        if q != 0 && b.get(1) != 0 {
            q = -q;
        }
        out[j] = q;
    }
    out
}

/// 2:9143.. the MPEG-2 / 2.5 mixed-block scalefactor reader, as the firmware runs it, on the
/// channel's 62-word area (see `Decoder::lsf_area`):
/// * long bands 0..5 at slen0 (zeroed if slen0 = 0), then (nr0 - 6) / 3 short bands at slen0; the
///   short band counter continues the long one, so these land at short band 6;
/// * groups k = 1..3: nr[k] / 3 short bands x 3 windows at slen[k]. At the first group with
///   slen[k] = 0 the firmware instead zeroes (nr1 + nr2 + nr3) / 3 bands from the current band,
///   reads no further groups, and the writes run on past band 12 into the next window's row and
///   beyond the area (channel 1 reaches 0x80E7: main_data_begin and channel 0's side info);
/// * short band 12 is zeroed in every window.
///
/// The scale builder then takes long 0..5 from area[0..5] and short band b (3..12) window w from
/// area[23 + b + 13w]; bands 3..5 are whatever an earlier granule left there. At 8 kHz it reads
/// one band further on (see the 8 kHz path in `decode_frame`) and the returned value is the
/// 12-wide short band's scalefactors (area band 3).
#[allow(clippy::too_many_arguments)]
fn lsf_mixed(
    b: &mut Bits,
    slen: [usize; 4],
    nr: &[usize; 4],
    area: &mut [i64; 125],
    c: usize,
    k8: bool,
    sfl: &mut [i64; 22],
    sfs: &mut [[i64; 3]; 13],
) -> Option<[i64; 3]> {
    let base = 62 * c;
    let mut put = |i: usize, v: i64| {
        if base + i < area.len() {
            area[base + i] = v;
        }
    };
    for j in 0..6 {
        put(j, if slen[0] > 0 { b.get(slen[0] as u32) as i64 } else { 0 });
    }
    let mut band = 6;
    for _ in 0..nr[0].saturating_sub(6) / 3 {
        for w in 0..3 {
            put(23 + band + 13 * w, if slen[0] > 0 { b.get(slen[0] as u32) as i64 } else { 0 });
        }
        band += 1;
    }
    for k in 1..4 {
        if slen[k] == 0 {
            for _ in 0..(nr[1] / 3 + nr[2] / 3 + nr[3] / 3) {
                for w in 0..3 {
                    put(23 + band + 13 * w, 0);
                }
                band += 1;
            }
            break;
        }
        for _ in 0..nr[k] / 3 {
            for w in 0..3 {
                put(23 + band + 13 * w, b.get(slen[k] as u32) as i64);
            }
            band += 1;
        }
    }
    for w in 0..3 {
        put(35 + 13 * w, 0);
    }
    let at = |i: usize| if base + i < area.len() { area[base + i] } else { 0 };
    sfl[..6].copy_from_slice(&area[base..base + 6]);
    let sh = k8 as usize;
    for (band, row) in sfs.iter_mut().enumerate().skip(if k8 { 0 } else { 3 }) {
        for (w, v) in row.iter_mut().enumerate() {
            *v = at(23 + band + sh + 13 * w);
        }
    }
    if k8 {
        Some([at(26), at(39), at(52)])
    } else {
        None
    }
}

/// The intensity-coded right channel's scalefactors (positions) and their illegal values.
#[derive(Default, Clone)]
struct IsInfo {
    sfl: [i64; 22],
    sfs: [[i64; 3]; 13],
    is_maxl: [i64; 22],
    is_maxs: [[i64; 3]; 13],
    intensity_scale: usize,
    short: bool,
    mixed: bool,
}

/// Band ranges in xr order (long bands; short bands per window; mixed: long to 36, short from 3).
fn band_ranges(gc: &GranuleChannel, rate: usize) -> Vec<(usize, usize)> {
    let mut v = Vec::new();
    if gc.window_switching && gc.block_type == 2 {
        let sfb = &SFB_SHORT[rate];
        let mut i = 0;
        let mut b0 = 0;
        if gc.mixed {
            // the firmware's band list: long bands to 36 lines (72 at 8 kHz), then short bands
            let sl = &SFB_LONG[rate];
            let l: usize = if rate == 8 { 72 } else { 36 };
            for b in 0..sl.iter().position(|&x| x as usize == l).unwrap() {
                v.push((sl[b] as usize, sl[b + 1] as usize));
            }
            i = l;
            b0 = sfb.iter().position(|&x| x as usize == l / 3).unwrap();
        }
        for b in b0..13 {
            let w_ = (sfb[b + 1] - sfb[b]) as usize;
            for _ in 0..3 {
                v.push((i, i + w_));
                i += w_;
            }
        }
    } else {
        let sl = &SFB_LONG[rate];
        for b in 0..22 {
            v.push((sl[b] as usize, sl[b + 1] as usize));
        }
    }
    v
}

/// x * k as the IS kernels compute it: 2*hi*hi + ((2*cross) >> 16), FRCT on.
fn fm(x: i32, k: i32) -> i32 {
    let (xh, xl) = hl(x);
    let (kh, kl) = hl(k);
    s32(2 * xh * kh + ((2 * (xl * kh + kl * xh)) >> 16))
}

impl Dsp {
    /// Joint stereo with intensity (2:A214 positions, 2:A404 M/S, 2:A441 -> 2:9AD1 / 2:9AF8).
    /// Per window (or for the long bands), the bands above the right channel's highest non-zero
    /// band are intensity bands; band 21 / short band 12 (no scalefactor) uses position 0. An
    /// illegal position (7, MPEG-2: 2^slen - 1 for slen > 0) leaves the band to M/S (or L/R).
    fn intensity(&self, l: &mut [i32], r: &mut [i32], ri: &IsInfo, rate: usize, lsf: bool, ms: bool) {
        // bands in xr order: (band, window (3 = long), lo, hi)
        let mut bl: Vec<(usize, usize, usize, usize)> = Vec::new();
        if ri.short {
            let sfb = &SFB_SHORT[rate];
            let mut i = 0;
            let mut b0 = 0;
            if ri.mixed {
                let sl = &SFB_LONG[rate];
                for b in 0..8 {
                    bl.push((b, 3, sl[b] as usize, sl[b + 1] as usize));
                }
                i = 36;
                b0 = 3;
            }
            for b in b0..13 {
                let w_ = (sfb[b + 1] - sfb[b]) as usize;
                for win in 0..3 {
                    bl.push((b, win, i, i + w_));
                    i += w_;
                }
            }
        } else {
            let sl = &SFB_LONG[rate];
            for b in 0..22 {
                bl.push((b, 3, sl[b] as usize, sl[b + 1] as usize));
            }
        }
        let mut is_band = vec![false; bl.len()];
        for win in 0..4 {
            let idx: Vec<usize> = (0..bl.len()).filter(|&j| bl[j].1 == win).collect();
            let last = idx.iter().copied().filter(|&j| r[bl[j].2..bl[j].3].iter().any(|&v| v != 0)).max();
            for &j in &idx {
                if last.is_none_or(|lj| j > lj) {
                    is_band[j] = true;
                }
            }
        }
        let t = |a: usize, n: usize| self.d32s(a, n);
        let (tl, tr) = (t(0xB46E, 7), t(0xB460, 7));
        let tk = t(0xB47C + 32 * ri.intensity_scale, 16);
        for (j, &(b, w, lo, hi)) in bl.iter().enumerate() {
            let mut pos = None;
            if is_band[j] {
                let (p, mx) = if w == 3 {
                    (if b < 21 { ri.sfl[b] } else { 0 }, if lsf { ri.is_maxl[b] } else { 7 })
                } else {
                    (if b < 12 { ri.sfs[b][w] } else { 0 }, if lsf { ri.is_maxs[b][w] } else { 7 })
                };
                if p != mx {
                    pos = Some(p as usize);
                }
            }
            match pos {
                None => {
                    if ms {
                        for i in lo..hi {
                            let (m, sd) = (l[i], r[i]);
                            l[i] = ms_mul(m.wrapping_add(sd));
                            r[i] = ms_mul(m.wrapping_sub(sd));
                        }
                    }
                }
                Some(p) => {
                    for i in lo..hi {
                        let x = l[i];
                        if !lsf {
                            l[i] = fm(x, tl[p]);
                            r[i] = fm(x, tr[p]);
                        } else if p & 1 == 1 {
                            l[i] = fm(x, tk[(p + 1) >> 1]);
                            r[i] = x;
                        } else {
                            r[i] = fm(x, tk[p >> 1]);
                        }
                    }
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------ hybrid filter bank

#[derive(Clone)]
struct Channel {
    ov: [[i32; 9]; 32],
    ring: Vec<u16>,
    prev_sf: [i64; 22],
}

impl Channel {
    fn new() -> Channel {
        Channel { ov: [[0; 9]; 32], ring: vec![0; 1024], prev_sf: [0; 22] }
    }
}

fn imdct_matrix(x: &[i32], t: &[i32], n: usize) -> Vec<i32> {
    (0..n)
        .map(|k| {
            let (mut b, mut a) = (0i64, 0i64);
            for i in 0..n {
                let (th, tl) = hl(t[k * n + i]);
                let (xh, xl) = hl(x[i]);
                b += th * xh;
                a += tl * xh + xl * th;
            }
            b += a >> 16;
            s32(b << 1)
        })
        .collect()
}

/// 2:A51B: returns (forward outputs, backward outputs) in j order.
fn win_rot(y5: &[i32], y4: &[i32], w: &[i32], n: usize) -> (Vec<i32>, Vec<i32>) {
    let mut fw = Vec::with_capacity(n);
    let mut bw = Vec::with_capacity(n);
    for j in 0..n {
        let (w1, w2) = (w[2 * j], w[2 * j + 1]);
        let (h1, c1) = mul_parts(y5[j], w1);
        let (h2, c2) = mul_parts(y4[j], w2);
        bw.push(sat32((h1 + h2 + ((c1 + c2) >> 16)) << 1));
        let (h1, c1) = mul_parts(y4[j], w1);
        let (h2, c2) = mul_parts(y5[j], w2);
        fw.push(sat32((-h1 + h2 + ((-c1 + c2) >> 16)) << 1));
    }
    (fw, bw)
}

struct Tables {
    t18: Vec<i32>,
    t6: Vec<i32>,
    wl: Vec<i32>,
    ws: Vec<i32>,
    aa: Vec<i32>,
    dct: Vec<i32>,
    syn: Vec<u16>,
}

fn neg32(v: i32) -> i32 {
    v.wrapping_neg()
}

/// One subband of 2:9715 for block type `bt` (0..3).
fn imdct_sb(x: &[i32; 18], ov: &mut [i32; 9], bt: u8, t: &Tables) -> [i32; 18] {
    let mut c: Vec<i32> = if bt != 2 {
        imdct_matrix(x, &t.t18, 18).into_iter().map(neg32).collect()
    } else {
        (0..3).flat_map(|w| imdct_matrix(&x[6 * w..6 * w + 6], &t.t6, 6)).map(neg32).collect()
    };
    let old = *ov;
    let mut out = [0i32; 18];
    match bt {
        0 | 1 => {
            for r in 0..9 {
                ov[8 - r] = c[r];
                c[r] = old[8 - r];
            }
            let y5: Vec<i32> = (0..9).map(|j| c[8 - j]).collect();
            let y4: Vec<i32> = (0..9).map(|j| c[9 + j]).collect();
            let (fw, bw) = win_rot(&y5, &y4, &t.wl, 9);
            out[..9].copy_from_slice(&fw);
            for j in 0..9 {
                out[17 - j] = bw[j];
            }
        }
        2 => {
            let c0 = c.clone();
            for i in 0..3 {
                c[i] = old[8 - i];
                c[6 + i] = c0[i];
                c[12 + i] = c0[6 + i];
                ov[8 - i] = c0[12 + i];
            }
            out[..6].copy_from_slice(&old[..6]);
            for w in 0..3 {
                let b = 6 * w;
                let y5: Vec<i32> = (0..3).map(|j| c[b + 2 - j]).collect();
                let y4: Vec<i32> = (0..3).map(|j| c[b + 3 + j]).collect();
                let (fw, bw) = win_rot(&y5, &y4, &t.ws, 3);
                let s: Vec<i32> = fw.into_iter().chain(bw.into_iter().rev()).collect();
                if w < 2 {
                    out[6 + 6 * w..12 + 6 * w].copy_from_slice(&s);
                } else {
                    ov[..6].copy_from_slice(&s);
                }
            }
        }
        _ => {
            c.swap(12, 14);
            for i in 0..3 {
                c.swap(9 + i, 17 - i);
            }
            c.swap(9, 11);
            for j in 0..9 {
                ov[j] = c[8 - j];
            }
            c[..6].copy_from_slice(&old[..6]);
            c[6] = old[8];
            c[7] = old[7];
            c[8] = old[6];
            let y5: Vec<i32> = (0..3).map(|j| c[8 - j]).collect();
            let y4: Vec<i32> = (0..3).map(|j| c[9 + j]).collect();
            let (fw, bw) = win_rot(&y5, &y4, &t.ws, 3);
            out[..6].copy_from_slice(&c[..6]);
            out[6..9].copy_from_slice(&fw);
            for j in 0..3 {
                out[11 - j] = bw[j];
            }
            out[12..18].copy_from_slice(&c[12..18]);
        }
    }
    out
}

/// 2:81B0: the 512-tap window over 18 slots; `words` = 18 x 64 words (hi, lo per int32).
fn synth(words: &[u16], ring: &mut [u16], win: &[u16], out: &mut [i16]) {
    let s16 = |v: u16| v as i16 as i64;
    let circ = |p: usize, d: isize| ((p as isize + d) & 0x3FF) as usize;
    let store = |a: i64| -> i16 {
        let v = (a << 6).clamp(i32::MIN as i64, i32::MAX as i64);
        (v >> 16) as i16
    };
    let mut o = 0;
    let mut p8 = 0usize;
    for slot in 0..18 {
        p8 = circ(p8, -0x40);
        for k in 0..64 {
            ring[circ(p8, k as isize)] = words[64 * slot + k];
        }
        let mut w2 = 0usize;
        let mut w4 = 0x200usize;
        let mut r3 = circ(p8, 0x20);
        let mut r5 = circ(p8, 0x60);
        // mac form: A += hi*hi, B += cross; mas form: A -= hi*hi, B += cross (B is negated
        // between the two halves of the first block only)
        let tap = |acc: &mut (i64, i64), w: usize, r: usize, mas: bool| {
            let (xh, xl) = (s16(win[w]), win[w + 1] as i64);
            let (yh, yl) = (s16(ring[r]), ring[r + 1] as i64);
            if mas {
                acc.0 -= xh * yh;
            } else {
                acc.0 += xh * yh;
            }
            acc.1 += yl * xh + xl * yh;
        };
        for _ in 0..16 {
            let mut ac = (0i64, 0i64);
            for _ in 0..8 {
                tap(&mut ac, w2, r3, false);
                w2 += 2;
                r3 = circ(r3, 128);
            }
            ac.1 = -ac.1;
            for _ in 0..8 {
                tap(&mut ac, w4, r5, true);
                w4 += 2;
                r5 = circ(r5, 128);
            }
            out[o] = store(ac.0 - (ac.1 >> 16) + 512);
            o += 1;
            r3 = circ(r3, -0x3fe);
            r5 = circ(r5, -0x402);
        }
        let mut ac = (0i64, 0i64);
        for _ in 0..8 {
            tap(&mut ac, w4, r5, true);
            w4 += 2;
            r5 = circ(r5, 128);
        }
        out[o] = store(ac.0 - (ac.1 >> 16) + 512);
        o += 1;
        w2 += 0x10;
        r3 = circ(r3, -2);
        r5 = circ(r5, -0x3fe);
        for _ in 0..15 {
            let mut ac = (0i64, 0i64);
            for _ in 0..8 {
                tap(&mut ac, w2, r3, true);
                w2 += 2;
                r3 = circ(r3, 128);
                tap(&mut ac, w4, r5, true);
                w4 += 2;
                r5 = circ(r5, 128);
            }
            out[o] = store(ac.0 - (ac.1 >> 16) + 512);
            o += 1;
            r3 = circ(r3, -0x402);
            r5 = circ(r5, -0x3fe);
        }
    }
    // the routine rotates its ring by +0x80 words at the end (it restarts at the same base)
    let old = ring.to_vec();
    for i in 0..0x400 {
        ring[(i + 0x80) & 0x3FF] = old[i];
    }
}

// ------------------------------------------------------------------------------ decoder

/// Frame decoder. Feed every frame of the stream in order (the bit reservoir spans frames).
pub struct Decoder {
    dsp: Dsp,
    t: Tables,
    ch: [Channel; 2],
    res: Vec<u8>,
    pcm: Vec<i16>,
    /// Per (granule, channel) synthesis output of the last frame, in the DSP's call order.
    calls: Vec<Vec<i16>>,
    /// 0x8061: the MPEG-2 / 2.5 scalefactor areas, 62 words per channel (long[i] at i, short
    /// band b window w at 23 + b + 13w), kept across granules. Word 124 (0x80DD) is the frame's
    /// main_data_begin; channel 1's mixed reader always overwrites it (band 13, window 2) before
    /// the 8 kHz builder reads it, and its writes beyond (channel 0's side info) are dead by then.
    lsf_area: [i64; 125],
}

impl Decoder {
    pub fn from_rom(rom: &[u8]) -> Result<Decoder> {
        Decoder::new(Dsp::from_rom(rom)?)
    }
    pub fn new(dsp: Dsp) -> Result<Decoder> {
        let t = Tables {
            t18: dsp.d32s(0xB4BC, 324),
            t6: dsp.d32s(0xB744, 36),
            wl: dsp.d32s(0x94F8, 18),
            ws: dsp.d32s(0x94EC, 6),
            aa: dsp.d32s(0x94CC, 16),
            dct: dsp.d32s(0x9400, 102),
            syn: dsp.data[0xB78C..0xB78C + 1024].to_vec(),
        };
        Ok(Decoder { dsp, t, ch: [Channel::new(), Channel::new()], res: Vec::new(), pcm: Vec::new(), calls: Vec::new(), lsf_area: [0; 125] })
    }

    /// The synthesis outputs (576 samples per granule and channel) of the last decoded frame, in
    /// the order the DSP produces them (g0 L, g0 R, g1 L, g1 R; mono: g0, g1).
    pub fn calls(&self) -> &[Vec<i16>] {
        &self.calls
    }

    /// Decode one frame. Returns the headphone PCM, interleaved stereo (mono is duplicated, as
    /// the DSP does), 1152 sample pairs (576 for MPEG-2 / 2.5); empty if the frame's main data starts before the data
    /// the reservoir holds (the stream's first frames).
    pub fn decode_frame(&mut self, frame: &[u8]) -> Result<&[i16]> {
        let h = header(frame).ok_or_else(|| Error::Format("not an MPEG-1 Layer III frame".into()))?;
        if frame.len() < h.size {
            return Err(Error::Format("truncated frame".into()));
        }
        let nch = if h.mode == 3 { 1 } else { 2 };
        let mut b = Bits { d: frame, p: 32 + if h.crc { 16 } else { 0 } };
        let lsf = h.lsf;
        let main_data_begin = b.get(if lsf { 8 } else { 9 }) as usize;
        b.get(match (lsf, nch) { (true, 1) => 1, (true, _) => 2, (false, 1) => 5, _ => 3 });
        let mut scfsi = [[false; 4]; 2];
        if !lsf {
            for c in scfsi.iter_mut().take(nch) {
                for s in c.iter_mut() {
                    *s = b.get(1) != 0;
                }
            }
        }
        let mut gcs = vec![vec![GranuleChannel::default(); nch]; if lsf { 1 } else { 2 }];
        for gr in gcs.iter_mut() {
            for gc in gr.iter_mut() {
                gc.part2_3_length = b.get(12) as usize;
                gc.big_values = b.get(9) as usize;
                gc.global_gain = b.get(8) as i64;
                gc.scalefac_compress = b.get(if lsf { 9 } else { 4 }) as usize;
                gc.window_switching = b.get(1) != 0;
                if gc.window_switching {
                    gc.block_type = b.get(2) as u8;
                    gc.mixed = b.get(1) != 0;
                    gc.table_select = [b.get(5) as usize, b.get(5) as usize, 0];
                    for s in gc.subblock_gain.iter_mut() {
                        *s = b.get(3) as i64;
                    }
                    gc.region0_count = if gc.block_type == 2 && !gc.mixed { 8 } else { 7 };
                    gc.region1_count = 36;
                } else {
                    gc.table_select = [b.get(5) as usize, b.get(5) as usize, b.get(5) as usize];
                    gc.region0_count = b.get(4) as usize;
                    gc.region1_count = b.get(3) as usize;
                }
                gc.preflag = !lsf && b.get(1) != 0;
                gc.scalefac_scale = b.get(1) != 0;
                gc.count1table = b.get(1) != 0;
            }
        }
        if lsf && h.mode == 1 && h.mode_ext & 1 != 0 && gcs[0].len() == 2 {
            let gc = &gcs[0][1];
            if gc.window_switching && gc.block_type == 2 && gc.mixed {
                return Err(Error::Undefined("MPEG-2/2.5 mixed block with intensity stereo is not modelled".into()));
            }
        }
        let hdr = b.p / 8;
        let start = self.res.len() as isize - main_data_begin as isize;
        self.res.extend_from_slice(&frame[hdr..h.size]);
        let buf = self.res.clone();
        if self.res.len() > 4096 {
            self.res.drain(..self.res.len() - 4096);
        }
        self.pcm.clear();
        self.calls.clear();
        if start < 0 {
            return Ok(&self.pcm);
        }
        let mut b = Bits { d: &buf, p: start as usize * 8 };
        let ri = h.rate_idx;
        let mut pcm_gr: Vec<[Vec<i16>; 2]> = Vec::new();
        for (g, gr) in gcs.iter_mut().enumerate() {
            let mut xs: Vec<Vec<i32>> = Vec::new();
            let mut right = IsInfo::default();
            for (c, gc) in gr.iter_mut().enumerate() {
                let is_right = c == 1 && h.mode == 1 && h.mode_ext & 1 != 0;
                let mut is_maxl = [-1i64; 22];
                let mut is_maxs = [[-1i64; 3]; 13];
                let mut intensity_scale = 0usize;
                let p0 = b.p;
                let (sl1, sl2) = SLEN[gc.scalefac_compress & 15];
                let mut sfl = [0i64; 22];
                let mut sfs = [[0i64; 3]; 13];
                let mut pseudo: Option<[i64; 3]> = None;
                let short = gc.window_switching && gc.block_type == 2;
                if lsf {
                    // ISO 13818-3 2.4.3.4; the intensity-coded right channel has the firmware's
                    // own slen formulas for isfc >= 180 (2:90A3 / 2:908F): no -180 / -244 offset
                    // and % 63, % 15, % 3 where ISO has % 64, % 16, % 4.
                    let sf = gc.scalefac_compress;
                    let (slen, t2) = if is_right {
                        intensity_scale = sf & 1;
                        let i = sf >> 1;
                        if i < 180 {
                            ([i / 36, (i % 36) / 6, (i % 36) % 6, 0], 3)
                        } else if i < 244 {
                            ([(i % 63) >> 4, (i % 15) >> 2, i % 3, 0], 4)
                        } else {
                            ([i / 3, i % 3, 0, 0], 5)
                        }
                    } else if sf < 400 {
                        ([(sf >> 4) / 5, (sf >> 4) % 5, (sf % 16) >> 2, sf % 4], 0)
                    } else if sf < 500 {
                        let sf = sf - 400;
                        ([(sf >> 2) / 5, (sf >> 2) % 5, sf % 4, 0], 1)
                    } else {
                        let sf = sf - 500;
                        ([sf / 3, sf % 3, 0, 0], 2)
                    };
                    gc.preflag = t2 == 2;
                    if short && gc.mixed {
                        pseudo = lsf_mixed(&mut b, slen, &LSF_NSF[t2][2], &mut self.lsf_area, c, ri == 8, &mut sfl, &mut sfs);
                    }
                    let mut j = 0usize;
                    for k in 0..if short && gc.mixed { 0 } else { 4 } {
                        // illegal intensity position: 2^slen - 1 (none for slen 0)
                        let mx = if slen[k] > 0 { (1i64 << slen[k]) - 1 } else { -1 };
                        for _ in 0..LSF_NSF[t2][if short { 1 } else { 0 }][k] {
                            let v = b.get(slen[k] as u32) as i64;
                            if short {
                                if j / 3 < 13 {
                                    sfs[j / 3][j % 3] = v;
                                    is_maxs[j / 3][j % 3] = mx;
                                }
                            } else if j < 22 {
                                sfl[j] = v;
                                is_maxl[j] = mx;
                            }
                            j += 1;
                        }
                    }
                    // the plain long / short readers store into the same area (2:9143..)
                    let base = 62 * c;
                    if short && !gc.mixed {
                        for w in 0..3 {
                            for band in 0..12 {
                                self.lsf_area[base + 23 + band + 13 * w] = sfs[band][w];
                            }
                            self.lsf_area[base + 35 + 13 * w] = 0;
                        }
                    } else if !short {
                        for (k, &v) in sfl.iter().enumerate().take(j.min(22)) {
                            self.lsf_area[base + k] = v;
                        }
                    }
                } else if short && gc.mixed {
                    for v in sfl.iter_mut().take(8) {
                        *v = b.get(sl1) as i64;
                    }
                    for (s, row) in sfs.iter_mut().enumerate().take(12).skip(3) {
                        for v in row.iter_mut() {
                            *v = b.get(if s < 6 { sl1 } else { sl2 }) as i64;
                        }
                    }
                } else if short {
                    for (s, row) in sfs.iter_mut().enumerate().take(12) {
                        for v in row.iter_mut() {
                            *v = b.get(if s < 6 { sl1 } else { sl2 }) as i64;
                        }
                    }
                } else {
                    for (k, (lo, hi)) in [(0, 6), (6, 11), (11, 16), (16, 21)].into_iter().enumerate() {
                        for s in lo..hi {
                            sfl[s] = if g == 1 && scfsi[c][k] {
                                self.ch[c].prev_sf[s]
                            } else {
                                b.get(if k < 2 { sl1 } else { sl2 }) as i64
                            };
                        }
                    }
                    self.ch[c].prev_sf = sfl;
                }
                let end = p0 + gc.part2_3_length;
                let mut isv = [0i32; 576];
                let bv = (2 * gc.big_values).min(576);
                let (r1, r2) = if gc.window_switching {
                    let r1 = if gc.block_type == 2 && gc.mixed && lsf {
                        if ri == 8 { 96 } else { 48 }
                    } else if gc.block_type == 2 {
                        if ri == 8 { 72 } else { 36 }
                    } else if lsf {
                        if ri == 8 { 108 } else { 54 }
                    } else {
                        36
                    };
                    (r1, 576)
                } else {
                    let sfb = &SFB_LONG[ri];
                    (sfb[gc.region0_count + 1] as usize, sfb[(gc.region0_count + gc.region1_count + 2).min(22)] as usize)
                };
                let mut i = 0;
                while i < bv {
                    let tab = gc.table_select[if i < r1 { 0 } else if i < r2 { 1 } else { 2 }];
                    let (x, y) = huff_pair(&mut b, tab);
                    isv[i] = x;
                    isv[i + 1] = y;
                    i += 2;
                }
                // a quad starting at line 574 is decoded and its first two values kept
                while i < 576 && b.p < end {
                    let q = count1_quad(&mut b, gc.count1table);
                    for (k, &v) in q.iter().enumerate() {
                        if i + k < 576 {
                            isv[i + k] = v;
                        }
                    }
                    i += 4;
                }
                if b.p > end {
                    i -= 4;
                    for v in isv.iter_mut().skip(i).take(4) {
                        *v = 0;
                    }
                }
                let nz = i.min(576);
                b.p = end;
                // scale index per line
                let mult = 2 * (1 + gc.scalefac_scale as i64);
                let gg = gc.global_gain - 210;
                let mut s = [0i64; 576];
                if gc.window_switching && gc.block_type == 2 {
                    let sfb = &SFB_SHORT[ri];
                    let mut k = 0;
                    let mut b0 = 0;
                    if let Some(ps) = pseudo {
                        // 8 kHz MPEG-2.5 mixed: the builder walks the reorder's band list - long
                        // bands 0..2 (lines 0..35), lines 36..71 as one 12-wide short band
                        // (scalefactors from area band 3), then short bands 3..12 (area band b + 1)
                        let sl = &SFB_LONG[ri];
                        for band in 0..3 {
                            for v in s.iter_mut().take(sl[band + 1] as usize).skip(sl[band] as usize) {
                                *v = gg - mult * sfl[band];
                            }
                        }
                        k = 36;
                        for (win, &sf) in ps.iter().enumerate() {
                            for _ in 0..12 {
                                s[k] = gg - 8 * gc.subblock_gain[win] - mult * sf;
                                k += 1;
                            }
                        }
                        for band in 3..13 {
                            let w_ = (sfb[band + 1] - sfb[band]) as usize;
                            for win in 0..3 {
                                for _ in 0..w_ {
                                    s[k] = gg - 8 * gc.subblock_gain[win] - mult * sfs[band][win];
                                    k += 1;
                                }
                            }
                        }
                        b0 = 13;
                    } else if gc.mixed {
                        // long bands up to line 36, short bands from 3
                        let sl = &SFB_LONG[ri];
                        for band in 0..sl.iter().position(|&x| x == 36).unwrap() {
                            for v in s.iter_mut().take(sl[band + 1] as usize).skip(sl[band] as usize) {
                                *v = gg - mult * (sfl[band] + gc.preflag as i64 * PRETAB[band]);
                            }
                        }
                        k = 36;
                        b0 = 3;
                    }
                    for band in b0..13 {
                        let w_ = (sfb[band + 1] - sfb[band]) as usize;
                        for win in 0..3 {
                            let sf = if band < 12 { sfs[band][win] } else { 0 };
                            for _ in 0..w_ {
                                s[k] = gg - 8 * gc.subblock_gain[win] - mult * sf;
                                k += 1;
                            }
                        }
                    }
                } else {
                    let sfb = &SFB_LONG[ri];
                    for band in 0..22 {
                        let sf = if band < 21 { sfl[band] + gc.preflag as i64 * PRETAB[band] } else { 0 };
                        for v in s.iter_mut().take(sfb[band + 1] as usize).skip(sfb[band] as usize) {
                            *v = gg - mult * sf;
                        }
                    }
                }
                let x: Vec<i32> = (0..576)
                    .map(|i| {
                        let v = isv[i];
                        if i >= nz || v == 0 {
                            0
                        } else if i < bv {
                            self.dsp.requant(v, s[i])
                        } else {
                            self.dsp.count1(v, s[i])
                        }
                    })
                    .collect();
                let mut x = x;
                // 2:8720: a band whose scale index is positive (or has low byte 0x80) is zeroed
                for (lo, hi) in band_ranges(gc, ri) {
                    let sv = s[lo];
                    if sv > 0 || sv & 0xFF == 0x80 {
                        x[lo..hi].fill(0);
                    }
                }
                xs.push(x);
                if is_right {
                    right = IsInfo {
                        sfl,
                        sfs,
                        is_maxl,
                        is_maxs,
                        intensity_scale,
                        short: gc.window_switching && gc.block_type == 2,
                        mixed: gc.mixed,
                    };
                }
            }
            if h.mode == 1 && h.mode_ext & 1 != 0 {
                let (l, r) = xs.split_at_mut(1);
                self.dsp.intensity(&mut l[0], &mut r[0], &right, ri, lsf, h.mode_ext & 2 != 0);
            } else if h.mode == 1 && h.mode_ext & 2 != 0 {
                let (l, r) = xs.split_at_mut(1);
                for i in 0..576 {
                    let (m, sd) = (l[0][i], r[0][i]);
                    l[0][i] = ms_mul(m.wrapping_add(sd));
                    r[0][i] = ms_mul(m.wrapping_sub(sd));
                }
            }
            let mut outs: [Vec<i16>; 2] = [Vec::new(), Vec::new()];
            for (c, gc) in gr.iter().enumerate() {
                let pcm = self.backend(&xs[c], gc.block_type, gc.block_type == 2 && gc.mixed, ri, c);
                self.calls.push(pcm.clone());
                outs[c] = pcm;
            }
            if nch == 1 {
                outs[1] = outs[0].clone();
            }
            pcm_gr.push(outs);
        }
        for o in &pcm_gr {
            for i in 0..576 {
                self.pcm.push(o[0][i]);
                self.pcm.push(o[1][i]);
            }
        }
        Ok(&self.pcm)
    }

    fn backend(&mut self, xr: &[i32], bt: u8, mixed: bool, ri: usize, c: usize) -> Vec<i16> {
        let t = &self.t;
        let mut buf = [0i32; 576];
        // 2:A0F7 reorder: long = transpose; short = row w*6 + f%6, column f/6; a mixed block
        // has its first 36 lines long and short bands from 3
        let long_lines = if bt != 2 { 576 } else if mixed { 36 } else { 0 };
        for (i, &v) in xr.iter().enumerate().take(long_lines) {
            buf[(i % 18) * 32 + i / 18] = v;
        }
        if bt == 2 {
            let sfb = &SFB_SHORT[ri];
            let mut i = long_lines;
            if mixed && ri == 8 {
                // 8 kHz: the band list is long to line 72, so lines 36..71 are reordered as one
                // 12-wide short band at f = 12..23
                for win in 0..3 {
                    for f in 12..24 {
                        buf[(win * 6 + f % 6) * 32 + f / 6] = xr[i];
                        i += 1;
                    }
                }
            }
            for band in (if mixed { 3 } else { 0 })..13 {
                let w_ = (sfb[band + 1] - sfb[band]) as usize;
                for win in 0..3 {
                    for k in 0..w_ {
                        let f = sfb[band] as usize + k;
                        buf[(win * 6 + f % 6) * 32 + f / 6] = xr[i];
                        i += 1;
                    }
                }
            }
        }
        // 2:850C antialias: 31 boundaries from subband 1 (long), 1 (mixed), none (short)
        let nb = if bt != 2 { 31 } else if mixed { 1 } else { 0 };
        for sb in 1..1 + nb {
            for i in 0..8 {
                let (ca, cs) = (t.aa[2 * i], t.aa[2 * i + 1]);
                let up = buf[(17 - i) * 32 + sb - 1];
                let dn = buf[i * 32 + sb];
                let (h1, c1) = mul_parts(up, ca);
                let (h2, c2) = mul_parts(dn, cs);
                let nd = s32((h1 + h2 + ((c1 + c2) >> 16)) << 1);
                let (h1, c1) = mul_parts(dn, ca);
                let (h2, c2) = mul_parts(up, cs);
                let nu = s32((-h1 + h2 + ((-c1 + c2) >> 16)) << 1);
                buf[i * 32 + sb] = nd;
                buf[(17 - i) * 32 + sb - 1] = nu;
            }
        }
        let ch = &mut self.ch[c];
        for sb in 0..32 {
            let x: [i32; 18] = core::array::from_fn(|r| buf[r * 32 + sb]);
            // a mixed block's two long subbands use the long (type 0) path
            let out = imdct_sb(&x, &mut ch.ov[sb], if mixed && sb < 2 { 0 } else { bt }, t);
            for r in 0..18 {
                buf[r * 32 + sb] = out[r];
            }
        }
        for r in (1..18).step_by(2) {
            for sb in (1..32).step_by(2) {
                buf[r * 32 + sb] = buf[r * 32 + sb].wrapping_neg();
            }
        }
        let mut words = vec![0u16; 18 * 64];
        for r in 0..18 {
            let mut row: [i32; 32] = core::array::from_fn(|k| buf[r * 32 + k]);
            dct32::dct32(&mut row, &t.dct);
            for k in 0..32 {
                words[r * 64 + 2 * k] = (row[k] as u32 >> 16) as u16;
                words[r * 64 + 2 * k + 1] = row[k] as u16;
            }
        }
        let mut pcm = vec![0i16; 576];
        synth(&words, &mut ch.ring, &t.syn, &mut pcm);
        pcm
    }
}

// ------------------------------------------------------------------------------ ID3 / length

/// What the DSP returns for AUDIO_INFO (command 0x0040, record 0x4040, a 0x88-word block):
/// title and artist as 16-bit units (at most 64 each), the title's ID3v2 text encoding byte
/// (0xFFFF when the strings come from ID3v1) and the length in whole seconds.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Id3 {
    pub title: Vec<u16>,
    pub artist: Vec<u16>,
    pub encoding: u16,
    pub seconds: u32,
}

fn syncsafe(b: &[u8]) -> usize {
    (b[0] as usize & 127) << 21 | (b[1] as usize & 127) << 14 | (b[2] as usize & 127) << 7 | (b[3] as usize & 127)
}

/// An ID3v2 text frame as units: encoding 0 = one byte per unit up to a NUL; any other encoding
/// = little-endian byte pairs up to a NUL pair, after skipping two bytes (the BOM) for encoding
/// 1 only. So UTF-16BE and UTF-8 text arrive byte-swapped / paired, as on the cartridge.
fn text_units(p: &[u8]) -> (Vec<u16>, u16) {
    let Some((&enc, d)) = p.split_first() else { return (Vec::new(), 0) };
    let mut out: Vec<u16> = if enc == 0 {
        d.iter().take_while(|&&c| c != 0).map(|&c| c as u16).collect()
    } else {
        let d = if enc == 1 { d.get(2..).unwrap_or(&[]) } else { d };
        d.as_chunks::<2>().0.iter().map(|c| c[0] as u16 | (c[1] as u16) << 8).take_while(|&v| v != 0).collect()
    };
    out.truncate(64);
    (out, enc as u16)
}

/// ID3 and length as the NMP DSP computes them (checked against the DSP on 32 files: ID3v2.2 /
/// 2.3 / 2.4, all text encodings, zero-size and unknown frames, long strings, ID3v1 only, both
/// tags, CBR, VBR with and without a Xing frame count, tag sizes at second boundaries).
/// * an ID3v2 tag, if present, is used exclusively (TT2/TP1 for v2.2, TIT2/TPE1 otherwise;
///   v2.4 frame sizes syncsafe; the unsynchronisation flag is ignored); else ID3v1 (30 bytes);
/// * length: a Xing header with a frame count in the first frame gives
///   frames * (1152 or 576) / rate; otherwise (file size - ID3v2 tag - 128 if ID3v1) * 8 /
///   (bit rate of the first frame), both rounded down.
pub fn id3(data: &[u8]) -> Id3 {
    let mut r = Id3 { title: Vec::new(), artist: Vec::new(), encoding: 0, seconds: 0 };
    let mut v2len = 0;
    let mut have_v2 = false;
    if data.len() >= 10 && &data[..3] == b"ID3" {
        have_v2 = true;
        let ver = data[3];
        let size = syncsafe(&data[6..10]);
        v2len = 10 + size + if data[5] & 0x10 != 0 { 10 } else { 0 };
        let end = (10 + size).min(data.len());
        let (tt, ta): (&[u8], &[u8]) = match ver {
            2 => (b"TT2", b"TP1"),
            3 | 4 => (b"TIT2", b"TPE1"),
            _ => (b"", b""),
        };
        let mut p = 10;
        while !tt.is_empty() && p + if ver == 2 { 6 } else { 10 } <= end {
            let (fid, fs, hdr) = if ver == 2 {
                (&data[p..p + 3], (data[p + 3] as usize) << 16 | (data[p + 4] as usize) << 8 | data[p + 5] as usize, 6)
            } else {
                let fs = if ver == 4 {
                    syncsafe(&data[p + 4..p + 8])
                } else {
                    u32::from_be_bytes([data[p + 4], data[p + 5], data[p + 6], data[p + 7]]) as usize
                };
                (&data[p..p + 4], fs, 10)
            };
            if fid[0] == 0 {
                break;
            }
            let pl = &data[(p + hdr).min(data.len())..(p + hdr + fs).min(data.len())];
            if fid == tt {
                let (t, e) = text_units(pl);
                r.title = t;
                r.encoding = e;
            } else if fid == ta {
                r.artist = text_units(pl).0;
            }
            p += hdr + fs;
        }
    }
    let v1 = data.len() >= 128 && &data[data.len() - 128..data.len() - 125] == b"TAG";
    if !have_v2 && v1 {
        let t = &data[data.len() - 128..];
        let f = |b: &[u8]| b.iter().take_while(|&&c| c != 0).map(|&c| c as u16).collect::<Vec<u16>>();
        r.title = f(&t[3..33]);
        r.artist = f(&t[33..63]);
        r.encoding = 0xFFFF;
    }
    let audio = data.get(v2len..).unwrap_or(&[]);
    if let Some(fr) = frames(audio).first() {
        let h = header(fr).unwrap();
        let nch = if h.mode == 3 { 1 } else { 2 };
        let off = 4 + if h.crc { 2 } else { 0 } + match (h.lsf, nch) { (true, 1) => 9, (true, _) => 17, (false, 1) => 17, _ => 32 };
        let bri = ((u32::from_be_bytes([fr[0], fr[1], fr[2], fr[3]]) >> 12) & 15) as usize;
        let kbps = if h.lsf { BITRATE_LSF[bri] } else { BITRATE[bri] } as u64;
        let be32 = |o: usize| fr.get(o..o + 4).map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]));
        if fr.get(off..off + 4) == Some(b"Xing") && be32(off + 4).unwrap_or(0) & 1 != 0 {
            let n = be32(off + 8).unwrap_or(0) as u64;
            r.seconds = (n * if h.lsf { 576 } else { 1152 } / h.rate as u64) as u32;
        } else {
            let bytes = data.len().saturating_sub(v2len).saturating_sub(if v1 { 128 } else { 0 }) as u64;
            r.seconds = (bytes * 8 / (kbps * 1000)) as u32;
        }
    }
    r
}

// ------------------------------------------------------------------------------ speaker stream

const SRC_RATES: [u32; 9] = [8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000];

#[inline]
fn sth_sat(a: i64, sh: u32) -> i16 {
    ((a << sh).clamp(i32::MIN as i64, i32::MAX as i64) >> 16) as i16
}

/// One channel of 2:E309, the converter to the 44.1 kHz DAC rate (FRCT off, SST on). Path A
/// (every rate but 48 kHz): phase step 0xE013[i], modulus 3528, the 2nd-order section filters
/// the outputs; path B (48 kHz): step 0xE01C[i], modulus 3520, 0xE025[i] inputs per output, the
/// section filters the inputs. An output is the 17-tap FIR over the last 17 inputs with the
/// coefficient set ptr[0xE5B4 + (phase * 26215 >> 21)].
struct Src {
    path_a: bool,
    step: i64,
    modulus: i64,
    per_out: usize,
    c: [i64; 5],
    coef_sets: Vec<[i64; 17]>,
    f: i64,
    ring: [i64; 17],
    p: usize,
    st: [i64; 4],
    q: usize,
    started: bool,
    // resumable position: 0 = before an output, 1 = feeding inputs after an output (n done),
    // 2 = phase advance, 3 = the extra input of a phase wrap
    pos: u8,
    n1d: usize,
}

impl Src {
    fn new(dsp: &Dsp, rate: u32) -> Src {
        let i = SRC_RATES.iter().position(|&r| r == rate).unwrap();
        let m = |a: usize| dsp.data[a & 0xFFFF] as i64;
        let s16 = |v: i64| v as u16 as i16 as i64;
        let path_a = rate != 48000;
        let coef_sets = (0..45)
            .map(|k| {
                let ptr = m(0xE5B4 + k) as usize;
                core::array::from_fn(|t| s16(m(ptr + t)))
            })
            .collect();
        let cp = m(0xE5E1 + i) as usize;
        Src {
            path_a,
            step: m(if path_a { 0xE013 } else { 0xE01C } + i),
            modulus: if path_a { 3528 } else { 3520 },
            per_out: if path_a { 0 } else { m(0xE025 + i) as usize },
            c: core::array::from_fn(|k| s16(m(cp + k))),
            coef_sets,
            f: 0,
            ring: [0; 17],
            p: 0,
            st: [0; 4],
            q: 0,
            started: false,
            pos: 0,
            n1d: 0,
        }
    }
    fn iir(&mut self, x: i64) -> i64 {
        let (q, c) = (self.q, self.c);
        let b: i64 = (0..4).map(|k| c[k] * self.st[(q + k) % 4]).sum::<i64>() + x * c[4];
        let y = sth_sat(b, 2) as i64;
        self.st[(q + 3) % 4] = x;
        self.st[(q + 2) % 4] = y;
        self.q = (q + 2) % 4;
        y
    }
    fn fir(&self) -> i64 {
        let h = &self.coef_sets[((self.f * 26215) >> 21) as usize];
        let a: i64 = (0..17).map(|k| self.ring[(self.p + 1 + k) % 17] * h[k]).sum();
        sth_sat(a, 1) as i64
    }
    fn push(&mut self, x: i64) {
        self.p = (self.p + 1) % 17;
        self.ring[self.p] = x;
    }
    /// Feed inputs; the converted samples are appended to `out`.
    fn feed(&mut self, xs: &[i16], out: &mut Vec<i16>) {
        let mut it = xs.iter().map(|&v| v as i64);
        if !self.started {
            self.started = true;
            // path B enters at E3A0: inputs, phase, then the first output
            self.pos = if self.path_a { 0 } else { 1 };
            self.n1d = 0;
        }
        loop {
            match self.pos {
                0 => {
                    let y = self.fir();
                    let y = if self.path_a { self.iir(y) } else { y };
                    out.push(y as i16);
                    self.n1d = 0;
                    self.pos = if self.path_a { 2 } else { 1 };
                }
                1 => {
                    let Some(x) = it.next() else { return };
                    let y = self.iir(x);
                    self.push(y);
                    self.n1d += 1;
                    if self.n1d >= self.per_out {
                        self.pos = 2;
                    }
                }
                2 => {
                    self.f += self.step;
                    if self.f - self.modulus >= 0 {
                        self.f -= self.modulus;
                        self.pos = 3;
                    } else {
                        self.pos = 0;
                    }
                }
                _ => {
                    let Some(x) = it.next() else { return };
                    let x = if self.path_a { x } else { self.iir(x) };
                    self.push(x);
                    self.pos = 0;
                }
            }
        }
    }
}

/// One 0x480-byte 0x8100 sample block: 288 words per channel, two signed 8-bit samples per word
/// (the earlier sample in the low byte). Channel 1 (address A) plays on the right speaker and holds
/// the right channel; channel 2 (A + 0x120) the left.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpeakerBlock {
    pub ch1: Vec<u16>,
    pub ch2: Vec<u16>,
}

/// The 8-bit stream the NMP sends to the GBA when no headphones are plugged in (and command 0x0030
/// = 1): the synthesis output converted to 44.1 kHz ([`Src`], none for 44.1 kHz sources), then
/// 0:3088 / 0:3005 / 0:3030: a DAC frame is kept when (c << 14) / 44100 changes (c counts frames
/// 0..44100, then restarts; 16384 frames per second), and its high bytes are packed.
/// Checked against the DSP on all nine sample rates (verify/playyan/oracle/nmp/nmp_spk_manifest.csv). The DSP's
/// own stream also depends on its real-time behaviour: the counter starts with the DAC at play
/// start, and a DAC underrun repeats a buffer; neither happens in a run that keeps up.
pub struct SpeakerStream {
    src: Option<[Src; 2]>,
    c: u32,
    last: i64,
    pend: Option<(u16, u16)>,
    ch1: Vec<u16>,
    ch2: Vec<u16>,
}

impl SpeakerStream {
    /// `rate` = the stream's sample rate (one of the nine MPEG rates).
    pub fn new(dsp: &Dsp, rate: u32) -> Result<SpeakerStream> {
        if !SRC_RATES.contains(&rate) {
            return Err(Error::Invalid(format!("no speaker converter for {rate} Hz")));
        }
        Ok(SpeakerStream {
            src: (rate != 44100).then(|| [Src::new(dsp, rate), Src::new(dsp, rate)]),
            c: 0,
            last: -1,
            pend: None,
            ch1: Vec::new(),
            ch2: Vec::new(),
        })
    }
    /// Feed synthesis output (left, right; the same slice twice for mono); complete blocks are
    /// returned.
    pub fn push(&mut self, left: &[i16], right: &[i16]) -> Vec<SpeakerBlock> {
        let (l, r);
        let (l, r): (&[i16], &[i16]) = match &mut self.src {
            None => (left, right),
            Some([a, b]) => {
                let (mut lo, mut ro) = (Vec::new(), Vec::new());
                a.feed(left, &mut lo);
                b.feed(right, &mut ro);
                l = lo;
                r = ro;
                (&l, &r)
            }
        };
        let mut blocks = Vec::new();
        for (&x, &y) in l.iter().zip(r.iter()) {
            let q = ((self.c as i64) << 14) / 44100;
            self.c += 1;
            if self.c > 44100 {
                self.c = 0;
            }
            if q == self.last {
                continue;
            }
            self.last = q;
            let (hl, hr) = ((x as u16) >> 8, (y as u16) >> 8);
            match self.pend.take() {
                None => self.pend = Some((hl, hr)),
                Some((pl, pr)) => {
                    self.ch2.push(hl << 8 | pl);
                    self.ch1.push(hr << 8 | pr);
                    if self.ch1.len() == 288 {
                        blocks.push(SpeakerBlock { ch1: std::mem::take(&mut self.ch1), ch2: std::mem::take(&mut self.ch2) });
                    }
                }
            }
        }
        blocks
    }
}
