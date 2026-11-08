//! AAC-LC as the Play-Yan Micro (`mi_fw0`) and `playan.fup` (`fup_sec0`) video firmware decodes
//! it: a FAAD2-derived decoder on Alphamosaic fixed-point helpers, reimplemented bit-exactly.
//!
//! What the firmware does, and this module reproduces (docs/playyan/decoders.md has the evidence):
//! * syntax as FAAD2 (SCE/LFE, CPE, DSE and FIL skipped, CCE rejected), but no bounds checks:
//!   section counts, sfb_cb and quantised-value writes run on through the decoder's memory;
//! * inverse quantisation `q * cbrt[|q|]` (Q12; `2q * cbrt[round(|q|/8)]` from 1280), scale
//!   factors as `(x << e) * 2^(k/4) >> 32` or `(x * 2^(k/4) >> 32) >> e`, zero below 2^-30;
//! * M/S before PNS (per channel, a global LCG, no correlated noise), intensity after the left
//!   channel's PNS, TNS in Q-format with a 16-bit LPC, the firmware's block-floating-point IMDCT
//!   ([`imdct`]), Q15 windows, PCM `clip16((x + 512) >> 10)`, mono duplicated to stereo;
//! * the ic_streams are kept in the firmware's byte layout and read with its load widths, so
//!   corrupt streams (no bounds checks) behave as on the cartridge;
//! * the first two decoded frames report 0 samples.
//!
//! Tables (inverse-quantisation cube roots, 2^(k/4), IS/PNS gains, TNS coefficients, band tables,
//! windows, twiddles) are read from the firmware image and checked by md5; the Huffman codebooks
//! ([`hcb`]) are the ISO ones.

pub mod hcb;
mod imdct;

use crate::firmware::{Image, ImageId};
use gbav_common::md5;
use gbav_common::{Error, Result};
use std::sync::Arc;

/// VC addresses of what the decoder reads, per image.
#[derive(Clone, Copy, Debug)]
struct Addr {
    sf_pow2: u32,
    cbrt: u32,
    is_pow2: u32,
    pns_pow2: u32,
    tns_coef: [u32; 4],
    tns_max_sfb: u32,
    win: [u32; 8],
    imdct_long_c: u32,
    imdct_long_s: u32,
    imdct_short_c: u32,
    imdct_short_s: u32,
    fft_tw4: u32,
    fft_tw8: u32,
    tw_b1: u32,
    tw_b2: u32,
    tw_b4: u32,
    tw_b8: u32,
    tw_b16: u32,
    num_swb_long: u32,
    num_swb_short: u32,
    swb_long: u32,
    swb_short: u32,
    pns_norm_long: u32,
    pns_norm_short: u32,
    sample_rates: u32,
    rng: u32,
}

const MI_FW0: Addr = Addr {
    sf_pow2: 0x8938,
    cbrt: 0x8948,
    is_pow2: 0x438b0,
    pns_pow2: 0x438b8,
    tns_coef: [0x4369c, 0x4368c, 0x436dc, 0x436bc],
    tns_max_sfb: 0x437fc,
    win: [0x4d40, 0x4440, 0x5540, 0x4c40, 0x5f40, 0x5640, 0x6740, 0x5e40],
    imdct_long_c: 0xa180,
    imdct_long_s: 0x9d80,
    imdct_short_c: 0xb0a0,
    imdct_short_s: 0xb020,
    fft_tw4: 0xa9c0,
    fft_tw8: 0xaa40,
    tw_b1: 0xbd80,
    tw_b2: 0xbe00,
    tw_b4: 0xbf00,
    tw_b8: 0xb920,
    tw_b16: 0xb120,
    num_swb_long: 0x438c8 + 0x5290,
    num_swb_short: 0x438c8 + 0x529c,
    swb_long: 0x438c8 + 0x5d7c,
    swb_short: 0x438c8 + 0x5e64,
    pns_norm_long: 0x49104,
    pns_norm_short: 0x49280,
    sample_rates: 0x438c8 + 0x5a08,
    rng: 0x438c8 + 756,
};

/// fup_sec0: the same code; data moved (located through the code that references it).
const FUP_SEC0: Addr = Addr {
    is_pow2: 0x43c28,
    pns_pow2: 0x43c30,
    tns_coef: [0x43a14, 0x43a04, 0x43a54, 0x43a34],
    tns_max_sfb: 0x43b74,
    num_swb_long: 0x43c40 + 0x629c,
    num_swb_short: 0x43c40 + 0x62a8,
    swb_long: 0x43c40 + 0x6e24,
    swb_short: 0x43c40 + 0x6f0c,
    pns_norm_long: 0x4a488,
    pns_norm_short: 0x4a604,
    sample_rates: 0x43c40 + 0x6ab0,
    rng: 0x43c40 + 996,
    ..MI_FW0
};

/// (name, MI_FW0 address, FUP_SEC0 address, length, md5): every fixed table region.
const TABLES: [(&str, u32, u32, usize, &str); 17] = [
    ("sf_pow2", 0x8938, 0x8938, 16, "c07b1b41f4e6c66ddb3c2710e632a6c8"),
    ("cbrt", 0x8948, 0x8948, 2560, "837607de4d6fb4e319d0c005013b58e2"),
    ("is_pow2", 0x438b0, 0x43c28, 8, "57dd0206ef79773e0d611a19010aff33"),
    ("pns_pow2", 0x438b8, 0x43c30, 8, "57dd0206ef79773e0d611a19010aff33"),
    ("tns_coef", 0x4368c, 0x43a04, 144, "efd52cc19043fe288ac01e867ffde4d6"),
    ("tns_max_sfb", 0x437fc, 0x43b74, 144, "9d05716c7d97dc3fddf81a2544c2253f"),
    ("windows", 0x4440, 0x4440, 9216, "29f4eab7365750e63efc528603575ec8"),
    ("imdct_long", 0x9d80, 0x9d80, 2048, "d0cdd1bae19bf4a619ad8759f8ebca07"),
    ("fft_tw", 0xa9c0, 0xa9c0, 256, "f5b8a3813bb9dea2feed49cd521c2f67"),
    ("imdct_short", 0xb020, 0xb020, 256, "bf295a06bf4b9e0abd764e98e5a3ae9a"),
    ("tw_blocks", 0xb120, 0xb120, 4096, "6ec167355407f1589e6e3b433b0832c6"),
    ("num_swb", 0x48b58, 0x49edc, 24, "4c33196d192790f71a625d2dc670b59e"),
    ("sample_rates", 0x492d0, 0x4a6f0, 48, "14820d968c069f59a72689230333f6a0"),
    ("swb_ptrs_long", 0x49644, 0x4aa64, 48, ""),
    ("swb_ptrs_short", 0x4972c, 0x4ab4c, 48, ""),
    ("pns_ptrs_long", 0x49104, 0x4a488, 48, ""),
    ("pns_ptrs_short", 0x49280, 0x4a604, 48, ""),
];

/// The image plus its address map.
#[derive(Clone)]
pub(crate) struct Fw {
    img: Arc<[u8]>,
    a: Addr,
}

impl Fw {
    #[inline]
    fn rd8(&self, a: u32) -> u32 {
        *self.img.get(a as usize).unwrap_or(&0) as u32
    }
    #[inline]
    fn rd16(&self, a: u32) -> u32 {
        self.rd8(a) | self.rd8(a + 1) << 8
    }
    #[inline]
    fn rds16(&self, a: u32) -> i32 {
        self.rd16(a) as u16 as i16 as i32
    }
    #[inline]
    fn rd32(&self, a: u32) -> u32 {
        self.rd16(a) | self.rd16(a + 2) << 16
    }
}

// ------------------------------------------------------------------ ic_stream byte layout
const ICS_SZ: usize = 9272;
const ELEM_SZ: usize = 2 * ICS_SZ + 12;
const O_MS_USED: usize = 0;
const O_MAX_SFB: usize = 408;
const O_NUM_SWB: usize = 409;
const O_NUM_GROUPS: usize = 410;
const O_NUM_WINDOWS: usize = 411;
const O_WSEQ: usize = 412;
const O_GROUP_LEN: usize = 413;
const O_WSHAPE: usize = 421;
const O_SFGROUP: usize = 422;
const O_SECT_SFB_OFF: usize = 424;
const O_SWB_OFF: usize = 2344;
const O_SECT_CB: usize = 2448;
const O_SECT_START: usize = 3408;
const O_SECT_END: usize = 4368;
const O_SFB_CB: usize = 6288;
const O_NUM_SEC: usize = 7248;
const O_GLOBAL_GAIN: usize = 7264;
const O_MS_MASK: usize = 7266;
const O_PULSE: usize = 7268;
const O_TNS: usize = 7269;
const O_GAIN: usize = 7270;
const O_PRED: usize = 7271;
const O_SF: usize = 7272;
const O_PNS_NORM: usize = 8088;
const O_PULSE_DATA: usize = 8092;
const O_TNS_DATA: usize = 8102;
const fn t_nfilt(w: usize) -> usize {
    O_TNS_DATA + w
}
const fn t_res(w: usize) -> usize {
    O_TNS_DATA + 8 + w
}
const fn t_len(w: usize, f: usize) -> usize {
    O_TNS_DATA + 16 + 4 * w + f
}
const fn t_order(w: usize, f: usize) -> usize {
    O_TNS_DATA + 48 + 4 * w + f
}
const fn t_dir(w: usize, f: usize) -> usize {
    O_TNS_DATA + 80 + 4 * w + f
}
const fn t_comp(w: usize, f: usize) -> usize {
    O_TNS_DATA + 112 + 4 * w + f
}
const fn t_coef(w: usize, f: usize, i: usize) -> usize {
    O_TNS_DATA + 144 + 128 * w + 32 * f + i
}

const ONLY_LONG: i32 = 0;
const LONG_START: i32 = 1;
const EIGHT_SHORT: i32 = 2;
const LONG_STOP: i32 = 3;
const ZERO_HCB: i32 = 0;
const ESC_HCB: usize = 11;
const NOISE_HCB: i32 = 13;
const INTENSITY_HCB2: i32 = 14;
const INTENSITY_HCB: i32 = 15;

/// Why a frame produced no PCM.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Fail {
    /// The firmware's own error code.
    Fw(i32),
    /// Depends on memory outside the model (heap after the decoder, ...).
    Undefined(u32),
    /// The firmware never returns.
    Hang,
    /// program_config_element: not modelled.
    Unsupported,
}
type R<T> = std::result::Result<T, Fail>;

/// The element buffer: two ic_streams, contiguous as in the firmware.
struct Elem(Box<[u8; ELEM_SZ]>);

/// One ic_stream view: offset into the element buffer.
#[derive(Clone, Copy)]
struct Ics(usize);

impl Elem {
    #[inline]
    fn u8(&self, i: Ics, o: usize) -> i32 {
        self.0.get(i.0 + o).copied().unwrap_or(0) as i32
    }
    /// The 48-bit '...11' byte load the firmware uses for most fields: signed (docs/playyan/decoders.md).
    #[inline]
    fn ld11(&self, i: Ics, o: usize) -> i32 {
        self.0.get(i.0 + o).copied().unwrap_or(0) as i8 as i32
    }
    #[inline]
    fn s8(&self, i: Ics, o: usize) -> i32 {
        self.ld11(i, o)
    }
    #[inline]
    fn s16(&self, i: Ics, o: usize) -> i32 {
        (self.u8(i, o) | self.u8(i, o + 1) << 8) as u16 as i16 as i32
    }
    #[inline]
    fn u32(&self, i: Ics, o: usize) -> u32 {
        (self.u8(i, o) | self.u8(i, o + 1) << 8 | self.u8(i, o + 2) << 16) as u32 | (self.u8(i, o + 3) as u32) << 24
    }
    #[inline]
    fn w8(&mut self, i: Ics, o: usize, v: i32) -> R<()> {
        match self.0.get_mut(i.0 + o) {
            Some(b) => {
                *b = v as u8;
                Ok(())
            }
            None => Err(Fail::Undefined(line!())),
        }
    }
    #[inline]
    fn w16(&mut self, i: Ics, o: usize, v: i32) -> R<()> {
        self.w8(i, o, v)?;
        self.w8(i, o + 1, v >> 8)
    }
    fn w32(&mut self, i: Ics, o: usize, v: u32) -> R<()> {
        self.w16(i, o, v as i32)?;
        self.w16(i, o + 2, (v >> 16) as i32)
    }
}

// ------------------------------------------------------------------ arithmetic (VC01 semantics)
#[inline]
fn w32(x: i64) -> i32 {
    x as i32
}
#[inline]
fn mulhd_ss(a: i32, b: i32) -> i32 {
    ((a as i64 * b as i64) >> 32) as i32
}
#[inline]
fn mulhd_su(a: i32, b: u32) -> i32 {
    ((a as i64 * b as i64) >> 32) as i32
}
#[inline]
fn msb32(x: u32) -> i32 {
    if x == 0 {
        -1
    } else {
        31 - x.leading_zeros() as i32
    }
}
#[inline]
fn shl32(x: i32, s: i32) -> i32 {
    (x as u32).wrapping_shl(s as u32 & 31) as i32
}
#[inline]
fn asr32(x: i32, s: i32) -> i32 {
    x >> (s as u32 & 31)
}
fn shl_sat(x: i32, s: i32) -> i32 {
    if x >= 0 {
        if s < 31 - msb32(x as u32) {
            shl32(x, s)
        } else {
            0x7fff_ffff
        }
    } else if s < 31 - msb32(!(x as u32)) {
        shl32(x, s)
    } else {
        i32::MIN
    }
}
fn shift_9348(x: i32, s: i32) -> i32 {
    if s >= 0 {
        asr32(x, s)
    } else {
        shl_sat(x, -s)
    }
}

// ------------------------------------------------------------------ bits + Huffman
struct Bits<'a> {
    p: &'a [u8],
    pos: usize,
}
impl Bits<'_> {
    #[inline]
    fn get(&mut self, n: u32) -> u32 {
        let mut v = 0u32;
        for _ in 0..n {
            let byte = self.pos >> 3;
            let bit = self.p.get(byte).map_or(0, |b| (b >> (7 - (self.pos & 7))) & 1) as u32;
            v = v << 1 | bit;
            self.pos += 1;
        }
        v
    }
}

/// Binary decoding tree for one codebook (ISO tables, [`hcb`]).
struct Tree(Vec<[i32; 2]>);
impl Tree {
    fn new(code: &[u32], bits: &[u8]) -> Tree {
        let mut t = vec![[0i32; 2]];
        for (sym, (&c, &n)) in code.iter().zip(bits).enumerate() {
            let mut node = 0usize;
            for i in (0..n).rev() {
                let b = (c >> i & 1) as usize;
                if i == 0 {
                    t[node][b] = -(sym as i32) - 1;
                } else {
                    if t[node][b] <= 0 {
                        t.push([0, 0]);
                        let k = t.len() - 1;
                        t[node][b] = k as i32;
                    }
                    node = t[node][b] as usize;
                }
            }
        }
        Tree(t)
    }
    fn decode(&self, b: &mut Bits) -> i32 {
        let mut node = 0usize;
        loop {
            let v = self.0[node][b.get(1) as usize];
            if v < 0 {
                return -v - 1;
            }
            if v == 0 {
                return -1;
            }
            node = v as usize;
        }
    }
}

struct Books {
    sf: Tree,
    spec: Vec<Tree>,
}
impl Books {
    fn new() -> Books {
        use hcb::*;
        let codes: [(&[u32], &[u8]); 11] = [
            (&CB1_CODE, &CB1_BITS),
            (&CB2_CODE, &CB2_BITS),
            (&CB3_CODE, &CB3_BITS),
            (&CB4_CODE, &CB4_BITS),
            (&CB5_CODE, &CB5_BITS),
            (&CB6_CODE, &CB6_BITS),
            (&CB7_CODE, &CB7_BITS),
            (&CB8_CODE, &CB8_BITS),
            (&CB9_CODE, &CB9_BITS),
            (&CB10_CODE, &CB10_BITS),
            (&CB11_CODE, &CB11_BITS),
        ];
        Books { sf: Tree::new(&SF_CODE, &SF_BITS), spec: codes.iter().map(|(c, b)| Tree::new(c, b)).collect() }
    }
}

const CB_DIM: [usize; 12] = [0, 4, 4, 4, 4, 2, 2, 2, 2, 2, 2, 2];
const CB_SIGNED: [bool; 12] = [false, true, true, false, false, true, true, false, false, false, false, false];
const CB_MOD: [i32; 12] = [0, 3, 3, 3, 3, 9, 9, 8, 8, 13, 13, 17];
const CB_OFF: [i32; 12] = [0, 1, 1, 0, 0, 4, 4, 0, 0, 0, 0, 0];

/// One decoded frame's outcome.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FrameStatus {
    /// Decoded; the firmware reports this many samples (channels x 1024; 0 for the first two
    /// decoded frames and frames without a channel element).
    Samples(u32),
}

/// The decoder: state as the firmware keeps it between frames.
pub struct Decoder {
    fw: Fw,
    books: Books,
    sf_index: i32,
    object_type: i32,
    channel_config: i32,
    sample_rate: i32,
    rng: u32,
    frame_count: u32,
    wshape_prev: [i32; 2],
    /// decoder + 0x40f8 / 0x40fa: the tag of the first SCE ever decoded (set once per init).
    sce_tag: Option<i32>,
    time: [Box<[i32; 2048]>; 2],
    elem: Elem,
    quant: [Box<[i16; 1024]>; 2],
    spec: [Box<[i32; 1024]>; 2],
    pcm: Box<[i16; 2048]>,
    reported: u32,
    has_pcm: bool,
}

impl Decoder {
    /// A decoder for `asc` (the MP4 esds AudioSpecificConfig) on a video image that plays MP4
    /// (`mi_fw0` or `fup_sec0`); tables are checked against their md5s.
    pub fn new(img: &Image, asc: &[u8]) -> Result<Decoder> {
        let (a, col) = match img.id {
            ImageId::MiFw0 => (MI_FW0, 0),
            ImageId::FupSec0 => (FUP_SEC0, 1),
            other => return Err(Error::Invalid(format!("{}: no AAC decoder in this image", other.name()))),
        };
        for (name, am, af, len, h) in TABLES {
            let at = if col == 0 { am } else { af } as usize;
            let s = img
                .bytes
                .get(at..at + len)
                .ok_or_else(|| Error::Format(format!("{}: AAC table {name} out of range", img.id.name())))?;
            if !h.is_empty() && md5::hex(s) != h {
                return Err(Error::Format(format!("{}: AAC table {name} does not match", img.id.name())));
            }
        }
        let fw = Fw { img: img.bytes.clone(), a };
        let mut d = Decoder {
            rng: fw.rd32(a.rng),
            fw,
            books: Books::new(),
            sf_index: 0,
            object_type: 0,
            channel_config: 0,
            sample_rate: 0,
            frame_count: 0,
            wshape_prev: [0; 2],
            sce_tag: None,
            time: [Box::new([0; 2048]), Box::new([0; 2048])],
            elem: Elem(Box::new([0; ELEM_SZ])),
            quant: [Box::new([0; 1024]), Box::new([0; 1024])],
            spec: [Box::new([0; 1024]), Box::new([0; 1024])],
            pcm: Box::new([0; 2048]),
            reported: 0,
            has_pcm: false,
        };
        d.init(asc)?;
        Ok(d)
    }

    /// 0xd4d0 -> 0xd712: object type, sampling index, channel configuration (explicit
    /// frequencies are not read: the firmware reads 4 bits).
    fn init(&mut self, asc: &[u8]) -> Result<()> {
        let mut b = Bits { p: asc, pos: 0 };
        let aot = b.get(5) as i32;
        let sf = b.get(4) as i32;
        let ch = b.get(4) as i32;
        self.sf_index = sf;
        self.channel_config = ch;
        self.sample_rate = self.fw.rd32(self.fw.a.sample_rates + 4 * sf as u32) as i32;
        self.object_type = if aot < 5 { aot - 1 } else { aot };
        if aot != 2 {
            return Err(Error::Invalid(format!("AAC object type {aot}: only LC is modelled")));
        }
        Ok(())
    }

    /// Sampling rate from the firmware's table.
    pub fn sample_rate(&self) -> u32 {
        self.sample_rate as u32
    }
    /// Channel configuration from the AudioSpecificConfig.
    pub fn channel_config(&self) -> u32 {
        self.channel_config as u32
    }
    /// The PNS random state (a firmware global, 1 at boot).
    pub fn rng(&self) -> u32 {
        self.rng
    }
    pub fn set_rng(&mut self, v: u32) {
        self.rng = v;
    }
    /// Samples the firmware reported for the last frame (see [`Decoder::decode_frame`]).
    pub fn reported_samples(&self) -> u32 {
        self.reported
    }
    /// The 1024 interleaved stereo frames written by the last decoded frame, whether or not the
    /// firmware reported them.
    pub fn pcm_buffer(&self) -> &[i16] {
        &self.pcm[..]
    }

    /// Decode one raw_data_block (an MP4 sample). Returns the headphone PCM the firmware reports:
    /// 1024 interleaved stereo frames (mono duplicated), or an empty slice for the first two
    /// frames and frames without audio. Errors: `Format` = the firmware rejects the frame (its
    /// error code in the text; its state is left as the firmware leaves it), `Undefined` = the
    /// firmware's result depends on memory this model does not cover, or it never returns.
    pub fn decode_frame(&mut self, raw: &[u8]) -> Result<&[i16]> {
        self.has_pcm = false;
        self.reported = 0;
        match self.frame(raw) {
            Ok(n) => {
                self.reported = n;
                Ok(if n > 0 { &self.pcm[..] } else { &[] })
            }
            Err(Fail::Fw(e)) => Err(Error::Format(format!("AAC frame rejected by the firmware (error {e})"))),
            Err(Fail::Undefined(l)) => Err(Error::Undefined(format!("AAC: depends on unmodelled memory (aac/mod.rs:{l})"))),
            Err(Fail::Hang) => Err(Error::Undefined("AAC: the firmware never returns on this frame".into())),
            Err(Fail::Unsupported) => Err(Error::Undefined("AAC: program_config_element is not modelled".into())),
        }
    }

    /// The firmware's own outcome code: `Ok(samples)`, `Err(code)` for a firmware error, or
    /// `None` when the frame is outside the model (for differential tests).
    pub fn decode_frame_raw(&mut self, raw: &[u8]) -> Option<std::result::Result<u32, i32>> {
        self.has_pcm = false;
        match self.frame(raw) {
            Ok(n) => {
                self.reported = n;
                Some(Ok(n))
            }
            Err(Fail::Fw(e)) => Some(Err(e)),
            Err(_) => None,
        }
    }
}

// ------------------------------------------------------------------ syntax
impl Decoder {
    fn window_grouping_info(&mut self, i: Ics) -> R<()> {
        let sf = self.sf_index;
        if sf > 11 {
            return Err(Fail::Fw(10));
        }
        let fw = self.fw.clone();
        let e = &mut self.elem;
        if e.u8(i, O_WSEQ) != EIGHT_SHORT {
            e.w8(i, O_NUM_WINDOWS, 1)?;
            e.w8(i, O_NUM_GROUPS, 1)?;
            e.w8(i, O_GROUP_LEN, 1)?;
            let n = fw.rd8(fw.a.num_swb_long + sf as u32) as usize;
            e.w8(i, O_NUM_SWB, n as i32)?;
            e.w32(i, O_PNS_NORM, fw.rd32(fw.a.pns_norm_long + 4 * sf as u32))?;
            let t = fw.rd32(fw.a.swb_long + 4 * sf as u32);
            for k in 0..=n {
                let v = fw.rd16(t + 2 * k as u32) as i32;
                e.w16(i, O_SECT_SFB_OFF + 2 * k, v)?;
                e.w16(i, O_SWB_OFF + 2 * k, v)?;
            }
            return Ok(());
        }
        e.w8(i, O_NUM_WINDOWS, 8)?;
        e.w8(i, O_GROUP_LEN, 1)?;
        let n = fw.rd8(fw.a.num_swb_short + sf as u32) as usize;
        e.w8(i, O_NUM_SWB, n as i32)?;
        e.w32(i, O_PNS_NORM, fw.rd32(fw.a.pns_norm_short + 4 * sf as u32))?;
        let t = fw.rd32(fw.a.swb_short + 4 * sf as u32);
        for k in 0..=n {
            e.w16(i, O_SWB_OFF + 2 * k, fw.rd16(t + 2 * k as u32) as i32)?;
        }
        let grp = e.u8(i, O_SFGROUP);
        let mut ng = 1usize;
        for k in 0..7 {
            if grp >> (6 - k) & 1 == 0 {
                ng += 1;
                e.w8(i, O_GROUP_LEN + ng - 1, 1)?;
            } else {
                let v = e.u8(i, O_GROUP_LEN + ng - 1);
                e.w8(i, O_GROUP_LEN + ng - 1, v + 1)?;
            }
        }
        e.w8(i, O_NUM_GROUPS, ng as i32)?;
        for g in 0..ng {
            let mut off = 0i32;
            let gl = e.u8(i, O_GROUP_LEN + g);
            for k in 0..n {
                e.w16(i, O_SECT_SFB_OFF + 240 * g + 2 * k, off)?;
                off += (fw.rd16(t + 2 * k as u32 + 2) as i32 - fw.rd16(t + 2 * k as u32) as i32) * gl;
            }
            e.w16(i, O_SECT_SFB_OFF + 240 * g + 2 * n, off)?;
        }
        Ok(())
    }

    fn ics_info(&mut self, b: &mut Bits, i: Ics) -> R<()> {
        b.get(1);
        self.elem.w8(i, O_WSEQ, b.get(2) as i32)?;
        self.elem.w8(i, O_WSHAPE, b.get(1) as i32)?;
        if self.elem.u8(i, O_WSEQ) == EIGHT_SHORT {
            self.elem.w8(i, O_MAX_SFB, b.get(4) as i32)?;
            self.elem.w8(i, O_SFGROUP, b.get(7) as i32)?;
        } else {
            self.elem.w8(i, O_MAX_SFB, b.get(6) as i32)?;
            let pred = b.get(1) as i32;
            self.elem.w8(i, O_PRED, pred)?;
            if pred & 1 == 0 && self.object_type == 0 {
                self.elem.w8(i, O_PRED, b.get(1) as i32)?;
            }
        }
        self.window_grouping_info(i)
    }

    /// 0x2e094: no bound on the section count.
    fn section_data(&mut self, b: &mut Bits, i: Ics) -> R<()> {
        let e = &mut self.elem;
        let sect_bits = if e.ld11(i, O_WSEQ) == EIGHT_SHORT { 3 } else { 5 };
        let esc = (1u32 << sect_bits) - 1;
        let limit = 8 * b.p.len() + 64 * 1024;
        let mut g = 0usize;
        while (g as i32) < e.ld11(i, O_NUM_GROUPS) {
            let (mut k, mut n) = (0i32, 0usize);
            while k < e.ld11(i, O_MAX_SFB) {
                let cb = b.get(4) as i32;
                let mut len = 0i32;
                loop {
                    let inc = b.get(sect_bits);
                    if inc != esc {
                        len += inc as i32;
                        break;
                    }
                    len += inc as i32;
                    if b.pos > limit {
                        return Err(Fail::Hang);
                    }
                }
                e.w8(i, O_SECT_CB + 120 * g + n, cb)?;
                e.w8(i, O_SECT_START + 120 * g + n, k)?;
                e.w16(i, O_SECT_END + 240 * g + 2 * n, k + len)?;
                for s in k..k + len {
                    e.w8(i, O_SFB_CB + 120 * g + s as usize, cb)?;
                }
                k += len;
                n += 1;
                if len == 0 && b.pos > limit {
                    return Err(Fail::Hang);
                }
            }
            e.w16(i, O_NUM_SEC + 2 * g, n as i32)?;
            g += 1;
        }
        Ok(())
    }

    /// 0x2cbbc
    fn scale_factor_data(&mut self, b: &mut Bits, i: Ics) -> R<()> {
        let gg = self.elem.s16(i, O_GLOBAL_GAIN);
        let (mut sf, mut is_pos, mut noise, mut npcm) = (gg, 0i32, gg - 90, true);
        let groups = self.elem.u8(i, O_NUM_GROUPS) as usize;
        let max_sfb = self.elem.u8(i, O_MAX_SFB) as usize;
        for g in 0..groups {
            for s in 0..max_sfb {
                let cb = self.elem.u8(i, O_SFB_CB + 120 * g + s);
                let v = match cb {
                    ZERO_HCB => 0,
                    // reserved codebook: the switch at 0x2cc14 returns 2
                    12 => return Err(Fail::Fw(2)),
                    INTENSITY_HCB | INTENSITY_HCB2 => {
                        is_pos += self.books.sf.decode(b) - 60;
                        is_pos
                    }
                    NOISE_HCB => {
                        let t = if npcm {
                            npcm = false;
                            b.get(9) as i32 - 256
                        } else {
                            self.books.sf.decode(b) - 60
                        };
                        noise += t;
                        noise
                    }
                    _ => {
                        sf += self.books.sf.decode(b) - 60;
                        if sf < 0 {
                            return Err(Fail::Fw(3));
                        }
                        sf
                    }
                };
                self.elem.w16(i, O_SF + 102 * g + 2 * s, v)?;
            }
        }
        Ok(())
    }

    /// 0x3011e
    fn tns_data(&mut self, b: &mut Bits, i: Ics) -> R<()> {
        let e = &mut self.elem;
        let short = e.u8(i, O_WSEQ) == EIGHT_SHORT;
        let (nfb, lb, ob) = if short { (1, 4, 3) } else { (2, 6, 5) };
        for w in 0..e.u8(i, O_NUM_WINDOWS) as usize {
            let nf = b.get(nfb) as i32;
            e.w8(i, t_nfilt(w), nf)?;
            let mut res = 3;
            if nf != 0 {
                let r = b.get(1) as i32;
                e.w8(i, t_res(w), r)?;
                res = if r & 1 != 0 { 4 } else { 3 };
            }
            for f in 0..nf as usize {
                e.w8(i, t_len(w, f), b.get(lb) as i32)?;
                let ord = b.get(ob) as usize;
                e.w8(i, t_order(w, f), ord as i32)?;
                if ord != 0 {
                    e.w8(i, t_dir(w, f), b.get(1) as i32)?;
                    let c = b.get(1) as i32;
                    e.w8(i, t_comp(w, f), c)?;
                    let cbits = (res - c) as u32;
                    for k in 0..ord {
                        e.w8(i, t_coef(w, f, k), b.get(cbits) as i32)?;
                    }
                }
            }
        }
        Ok(())
    }

    /// Quantised values at decoder + 0x800 * ch (int16), unbounded: past the buffer the other
    /// channel's buffer, channel 0's overlap (decoder + 0x1000), channel 1's dead output half,
    /// channel 1's overlap (decoder + 0x3000).
    fn qput(&mut self, ch: usize, p: i64, v: i32) -> R<()> {
        let off = ch as i64 * 0x800 + 2 * p;
        fn put16(w: &mut [i32; 2048], byteoff: usize, v: i32) {
            let x = w[byteoff >> 2] as u32;
            let sh = (byteoff & 2) * 8;
            w[byteoff >> 2] = ((x & !(0xffff << sh)) | ((v as u16 as u32) << sh)) as i32;
        }
        match off {
            0..=0xfff => self.quant[(off >= 0x800) as usize][((off & 0x7ff) >> 1) as usize] = v as i16,
            0x1000..=0x1fff => put16(&mut self.time[0], off as usize, v),
            0x2000..=0x2fff => {}
            0x3000..=0x3fff => put16(&mut self.time[1], off as usize - 0x2000, v),
            _ => return Err(Fail::Undefined(line!())),
        }
        Ok(())
    }

    /// 0x8014: the escape prefix is found in a 9-bit peek: N = leading ones (0..9), N + 1 bits
    /// are skipped (with 9 ones the 10th bit is skipped unread), then N + 4 bits follow.
    fn get_escape(b: &mut Bits, v: i32) -> i32 {
        if v.abs() != 16 {
            return v;
        }
        let p0 = b.pos;
        let peek = b.get(9);
        let mut n = 0;
        while n < 9 && peek >> (8 - n) & 1 != 0 {
            n += 1;
        }
        b.pos = p0 + n as usize + 1;
        let i = n + 4;
        let j = (b.get(i) + (1 << i)) as i32;
        if v < 0 {
            -j
        } else {
            j
        }
    }

    /// 0x2eca2
    fn spectral_data(&mut self, b: &mut Bits, i: Ics, ch: usize) -> R<()> {
        self.quant[ch].fill(0);
        let mut groups = 0i64;
        let ng = self.elem.u8(i, O_NUM_GROUPS) as usize;
        for g in 0..ng {
            let mut p = groups * 128;
            for n in 0..self.elem.s16(i, O_NUM_SEC + 2 * g).max(0) as usize {
                let cb = self.elem.u8(i, O_SECT_CB + 120 * g + n) as usize;
                let ss = self.elem.u8(i, O_SECT_START + 120 * g + n) as usize;
                let se = self.elem.s16(i, O_SECT_END + 240 * g + 2 * n);
                let start = self.elem.s16(i, O_SECT_SFB_OFF + 240 * g + 2 * ss) as i64;
                let end = self.elem.s16(i, O_SECT_SFB_OFF + 240 * g + (2 * se as i64) as usize) as i64;
                if cb == 0 || cb >= 12 {
                    p += end - start;
                    continue;
                }
                let dim = CB_DIM[cb];
                let mut k = start;
                while k < end {
                    let idx = self.books.spec[cb - 1].decode(b);
                    if idx < 0 {
                        return Err(Fail::Fw(9));
                    }
                    let m = CB_MOD[cb];
                    let mut v = [0i32; 4];
                    if dim == 4 {
                        v = [idx / 27, idx / 9 % 3, idx / 3 % 3, idx % 3];
                    } else {
                        v[0] = idx / m;
                        v[1] = idx % m;
                    }
                    for x in v.iter_mut().take(dim) {
                        *x -= CB_OFF[cb];
                    }
                    if !CB_SIGNED[cb] {
                        for x in v.iter_mut().take(dim) {
                            if *x != 0 && b.get(1) != 0 {
                                *x = -*x;
                            }
                        }
                    }
                    if cb == ESC_HCB {
                        v[0] = Self::get_escape(b, v[0]);
                        v[1] = Self::get_escape(b, v[1]);
                    }
                    for (t, &x) in v.iter().enumerate().take(dim) {
                        self.qput(ch, p + t as i64, x)?;
                    }
                    p += dim as i64;
                    k += dim as i64;
                }
            }
            groups += self.elem.u8(i, O_GROUP_LEN + g) as i64;
        }
        Ok(())
    }

    /// Read an int16 at quant index `p` of channel `ch` (same memory map as [`Decoder::qput`]).
    fn qget(&self, ch: usize, p: i64) -> R<i32> {
        let off = ch as i64 * 0x800 + 2 * p;
        let get16 = |w: &[i32; 2048], byteoff: usize| ((w[byteoff >> 2] as u32) >> ((byteoff & 2) * 8)) as u16 as i16 as i32;
        Ok(match off {
            0..=0xfff => self.quant[(off >= 0x800) as usize][((off & 0x7ff) >> 1) as usize] as i32,
            0x1000..=0x1fff => get16(&self.time[0], off as usize),
            0x3000..=0x3fff => get16(&self.time[1], off as usize - 0x2000),
            _ => return Err(Fail::Undefined(line!())),
        })
    }

    /// 0x2b064: no range checks; k = swb_offset[start_sfb] + offsets, int16 read-modify-write.
    fn pulse_decode(&mut self, i: Ics, ch: usize) -> R<()> {
        let start = self.elem.ld11(i, O_PULSE_DATA + 1);
        let mut k = self.elem.s16(i, (O_SWB_OFF as i64 + 2 * start as i64) as usize) as i64;
        for n in 0..=self.elem.ld11(i, O_PULSE_DATA).max(0) as usize {
            k += self.elem.ld11(i, O_PULSE_DATA + 2 + n) as i64;
            let amp = self.elem.ld11(i, O_PULSE_DATA + 6 + n);
            let q = self.qget(ch, k)?;
            self.qput(ch, k, if q > 0 { q + amp } else { q - amp })?;
        }
        Ok(())
    }

    /// 0x21ec4
    fn individual_channel_stream(&mut self, b: &mut Bits, i: Ics, common: bool, ch: usize) -> R<()> {
        self.elem.w16(i, O_GLOBAL_GAIN, b.get(8) as i32)?;
        if !common {
            self.ics_info(b, i)?;
        }
        self.section_data(b, i)?;
        self.scale_factor_data(b, i)?;
        let pulse = b.get(1) as i32;
        self.elem.w8(i, O_PULSE, pulse)?;
        if pulse != 0 {
            let np = b.get(2) as i32;
            self.elem.w8(i, O_PULSE_DATA, np)?;
            self.elem.w8(i, O_PULSE_DATA + 1, b.get(6) as i32)?;
            for n in 0..=np as usize {
                self.elem.w8(i, O_PULSE_DATA + 2 + n, b.get(5) as i32)?;
                self.elem.w8(i, O_PULSE_DATA + 6 + n, b.get(4) as i32)?;
            }
        }
        let tns = b.get(1) as i32;
        self.elem.w8(i, O_TNS, tns)?;
        if tns != 0 {
            self.tns_data(b, i)?;
        }
        let gain = b.get(1) as i32;
        self.elem.w8(i, O_GAIN, gain)?;
        if gain != 0 {
            return Err(Fail::Fw(8));
        }
        self.spectral_data(b, i, ch)?;
        if pulse != 0 {
            if self.elem.u8(i, O_WSEQ) == EIGHT_SHORT {
                return Err(Fail::Fw(1));
            }
            self.pulse_decode(i, ch)?;
        }
        Ok(())
    }
}

// ------------------------------------------------------------------ reconstruction
macro_rules! chk {
    ($idx:expr) => {
        if !(0..1024).contains(&($idx)) {
            return Err(Fail::Undefined(line!()));
        }
    };
}

impl Decoder {
    /// 0x885c
    fn iquant(&mut self, ch: usize) {
        let fw = &self.fw;
        for (x, &q) in self.spec[ch].iter_mut().zip(self.quant[ch].iter()) {
            let v = q as i64;
            let a = v.unsigned_abs() as u32;
            *x = if a < 1280 {
                w32(v * fw.rd16(fw.a.cbrt + 2 * a) as i64)
            } else {
                // 16-bit lanes: int16(2q) (wraps from 16384 on); indices past the 1280-entry
                // table read the image bytes after it
                w32((2 * v) as i16 as i64 * fw.rd16(fw.a.cbrt + 2 * ((a + 4) >> 3)) as i64)
            };
        }
    }

    /// 0x88e4 on spec[ch][base .. base + n]
    fn scale_band(&mut self, ch: usize, sf: i32, base: usize, n: usize) {
        if n == 0 {
            return;
        }
        let e = (sf - 144) >> 2;
        let c = self.fw.rd32(self.fw.a.sf_pow2 + 4 * (sf & 3) as u32);
        let x = &mut self.spec[ch][base..];
        if e >= 0 {
            for v in x.iter_mut().take(n) {
                *v = mulhd_su(shl32(*v, e), c);
            }
        } else if -e < 30 {
            for v in x.iter_mut().take(n) {
                *v = asr32(mulhd_su(*v, c), -e);
            }
        } else {
            for v in x.iter_mut().take((n + 3) & !3) {
                *v = 0;
            }
        }
    }

    /// 0x18dd4
    fn apply_scalefactors(&mut self, ch: usize, i: Ics) -> R<()> {
        let mut gstart = 0i32;
        let mut g = 0usize;
        while (g as i32) < self.elem.ld11(i, O_NUM_GROUPS) {
            let base = 128 * gstart;
            let mut k = 0i32;
            let mut s = 0usize;
            while (s as i32) < self.elem.ld11(i, O_MAX_SFB) {
                let n = (self.elem.s16(i, O_SECT_SFB_OFF + 240 * g + 2 * (s + 1)) - k).max(0);
                if n > 0 {
                    chk!(base + k);
                    chk!(base + k + n - 1);
                    if (base + k + ((n + 3) & !3) - 1) >= 1024 {
                        return Err(Fail::Undefined(line!()));
                    }
                }
                let sf = self.elem.s16(i, O_SF + 102 * g + 2 * s);
                self.scale_band(ch, sf, (base + k).max(0) as usize, n as usize);
                k += n;
                s += 1;
            }
            gstart += self.elem.ld11(i, O_GROUP_LEN + g);
            g += 1;
        }
        Ok(())
    }

    /// 0x2b334: grouped -> window-major in the 64x64-byte VRF, stored back by 0x3d72 (four
    /// REP-64 stores at a 64-byte stride: 4 KB).
    fn reorder_short(&mut self, ch: usize, i: Ics) {
        let mut vrf = [0i32; 1024];
        let x = &self.spec[ch];
        let (mut win0, mut src) = (0i32, 0usize);
        let e = &self.elem;
        let mut g = 0usize;
        while (g as i32) < e.ld11(i, O_NUM_GROUPS) {
            let glen = e.ld11(i, O_GROUP_LEN + g);
            let mut s = 0usize;
            while (s as i32) < e.ld11(i, O_NUM_SWB) {
                let w0 = e.s16(i, O_SWB_OFF + 2 * s);
                let width = e.s16(i, O_SWB_OFF + 2 * s + 2) - w0;
                for w in 0..glen.max(0) {
                    let mut bin = 0;
                    while bin < width {
                        let dst = (win0 + w) * 128 + w0 + bin;
                        for t in 0..4 {
                            vrf[((dst + t) & 1023) as usize] = x[(src + t as usize) & 1023];
                        }
                        src += 4;
                        bin += 4;
                    }
                }
                s += 1;
            }
            win0 += glen;
            g += 1;
        }
        *self.spec[ch] = vrf;
    }

    fn is_intensity(&self, i: Ics, g: usize, s: usize) -> i32 {
        match self.elem.ld11(i, O_SFB_CB + 120 * g + s) {
            INTENSITY_HCB => 1,
            INTENSITY_HCB2 => -1,
            _ => 0,
        }
    }
    fn is_noise(&self, i: Ics, g: usize, s: usize) -> bool {
        self.elem.ld11(i, O_SFB_CB + 120 * g + s) == NOISE_HCB
    }
    fn invert_intensity(&self, i: Ics, g: usize, s: usize) -> i32 {
        if self.elem.s16(i, O_MS_MASK) == 1 {
            1 - 2 * self.elem.s8(i, O_MS_USED + 51 * g + s)
        } else {
            1
        }
    }

    /// Iterate (group, window, band) as the firmware's per-window loops do.
    fn gws(&self, i: Ics) -> Vec<(usize, usize, usize)> {
        let e = &self.elem;
        let mut v = Vec::new();
        let mut win = 0usize;
        let mut g = 0usize;
        while (g as i32) < e.ld11(i, O_NUM_GROUPS) {
            for _ in 0..e.ld11(i, O_GROUP_LEN + g).max(0) {
                let mut s = 0usize;
                while (s as i32) < e.ld11(i, O_MAX_SFB) {
                    v.push((g, win, s));
                    s += 1;
                }
                win += 1;
            }
            g += 1;
        }
        v
    }

    /// 0x263ae
    fn ms_decode(&mut self, l: Ics, r: Ics) -> R<()> {
        let mm = self.elem.s16(l, O_MS_MASK);
        if mm < 1 {
            return Ok(());
        }
        for (g, win, s) in self.gws(l) {
            if !(self.elem.s8(l, O_MS_USED + 51 * g + s) != 0 || mm == 2) {
                continue;
            }
            if self.is_intensity(r, g, s) != 0 || self.is_noise(l, g, s) {
                continue;
            }
            let k = self.elem.s16(l, O_SWB_OFF + 2 * s);
            let n = self.elem.s16(l, O_SWB_OFF + 2 * s + 2) - k;
            for t in 0..n.max(0) {
                let idx = win as i32 * 128 + k + t;
                chk!(idx);
                let (a, c) = (self.spec[0][idx as usize], self.spec[1][idx as usize]);
                self.spec[0][idx as usize] = w32(a as i64 + c as i64);
                self.spec[1][idx as usize] = w32(a as i64 - c as i64);
            }
        }
        Ok(())
    }

    fn rnd(&mut self) -> u32 {
        self.rng = self.rng.wrapping_mul(1664525).wrapping_add(1013904223);
        self.rng
    }

    /// 0x2ab88 / 0x407c
    fn pns_decode(&mut self, ch: usize, i: Ics) -> R<()> {
        for (g, win, s) in self.gws(i) {
            if !self.is_noise(i, g, s) {
                continue;
            }
            let k = self.elem.s16(i, O_SWB_OFF + 2 * s);
            let n = self.elem.s16(i, O_SWB_OFF + 2 * s + 2) - k;
            let sf = self.elem.s16(i, O_SF + 102 * g + 2 * s);
            let norm = self.fw.rd32(self.elem.u32(i, O_PNS_NORM).wrapping_add(4 * s as u32)) as i32;
            let base = win as i32 * 128 + k;
            if n > 0 {
                chk!(base);
                chk!(base + n - 1);
            }
            for t in 0..n.max(0) {
                let r = self.rnd() as i32;
                self.spec[ch][(base + t) as usize] = r;
            }
            let m = self.fw.rds16(self.fw.a.pns_pow2 + 2 * (sf & 3) as u32);
            let sh = 14 - (sf >> 2) + 16;
            let r8 = asr32(shl32(mulhd_ss(shl32(m, 16), norm), 1), 16);
            for t in 0..n.max(0) {
                let p = &mut self.spec[ch][(base + t) as usize];
                *p = shift_9348(shl32(mulhd_ss(shl32(r8, 16), *p), 1), sh);
            }
        }
        Ok(())
    }

    /// 0x228f6 / 0x93d4
    fn is_decode(&mut self, l: Ics, r: Ics) -> R<()> {
        for (g, win, s) in self.gws(r) {
            if self.elem.ld11(r, O_SFB_CB + 120 * g + s) <= 11 {
                continue;
            }
            let sign = self.is_intensity(r, g, s) * self.invert_intensity(l, g, s);
            let pos = self.elem.s16(r, O_SF + 102 * g + 2 * s);
            let e = (60 - pos) as i16 as i32;
            let (exp, mant) = if e < 0 {
                (30, 0)
            } else {
                ((29 - (e >> 2)) as i16 as i32, self.fw.rds16(self.fw.a.is_pow2 + 2 * (e & 3) as u32))
            };
            let gain = (mant * sign) as i16 as i32;
            let exp = (exp - 15) as i16 as i32;
            let k = self.elem.s16(r, O_SWB_OFF + 2 * s);
            let n = (self.elem.s16(r, O_SWB_OFF + 2 * s + 2) - k) as i16 as i32;
            let base = win as i32 * 128 + k;
            if n > 0 {
                chk!(base);
                chk!(base + n - 1);
            }
            let g16 = shl32(gain, 16);
            for t in 0..n.max(0) {
                let idx = (base + t) as usize;
                let x = self.spec[0][idx];
                self.spec[1][idx] = if x == 0 {
                    0
                } else {
                    let mut sh = 30 - msb32(x.unsigned_abs());
                    let y = shl32(mulhd_ss(g16, shl32(x, sh)), 1);
                    sh += exp;
                    if sh >= 0 {
                        asr32(y, sh)
                    } else {
                        shl_sat(y, -sh)
                    }
                };
            }
        }
        Ok(())
    }

    /// 0x3020e
    fn tns_decode_coef(&self, i: Ics, w: usize, f: usize, order: usize, res_bits: i32, compress: i32) -> ([i16; 33], i32) {
        let tab = self.fw.a.tns_coef[((compress & 1) + ((res_bits >> 1) & 2)) as usize];
        let mut tmp2 = [0i32; 32];
        for (k, t) in tmp2.iter_mut().enumerate().take(order) {
            *t = self.fw.rd32(tab.wrapping_add((4 * self.elem.s8(i, t_coef(w, f, k))) as u32)) as i32;
        }
        let (mut a, mut b) = ([0i32; 33], [0i32; 33]);
        a[0] = 1 << 18;
        for m in 1..=order {
            let t = shl32(asr32(tmp2[m - 1], 3), 16);
            for k in 1..m {
                b[k] = w32(a[k] as i64 + 2 * mulhd_ss(t, a[m - k]) as i64);
            }
            a[1..m].copy_from_slice(&b[1..m]);
            a[m] = tmp2[m - 1];
        }
        let mut mx = 0u32;
        for &v in a.iter().take(order + 1) {
            mx |= v.unsigned_abs();
        }
        let sh = if mx == 0 {
            0
        } else if (mx as i32) > 0 {
            30 - msb32(mx)
        } else {
            30 - msb32(!mx)
        };
        let mut lpc = [0i16; 33];
        lpc[0] = 32767;
        for k in 1..=order {
            lpc[k] = asr32(shl32(a[k], sh), 16) as i16;
        }
        (lpc, (sh + 2) as i16 as i32)
    }

    /// 0x3032a (tns_decode_frame) / 0x300ca (AR filter)
    fn tns_decode(&mut self, ch: usize, i: Ics) -> R<()> {
        if self.elem.ld11(i, O_TNS) == 0 {
            return Ok(());
        }
        let short = self.elem.ld11(i, O_WSEQ) == EIGHT_SHORT;
        let mts = self.fw.rds16(self.fw.a.tns_max_sfb + 12 * self.sf_index as u32 + 2 * short as u32);
        let msfb = self.elem.ld11(i, O_MAX_SFB);
        let max_order = if short {
            7
        } else {
            match self.object_type {
                0 | 3 => 20,
                1 | 2 => 12,
                _ => 0,
            }
        };
        let mut w = 0usize;
        while (w as i32) < self.elem.ld11(i, O_NUM_WINDOWS) {
            let mut bottom = self.elem.ld11(i, O_NUM_SWB);
            let mut f = 0usize;
            while (f as i32) < self.elem.s8(i, t_nfilt(w)) {
                let top = bottom;
                bottom = (top - self.elem.ld11(i, t_len(w, f))).max(0);
                let order = self.elem.ld11(i, t_order(w, f)).min(max_order);
                if order > 0 {
                    let (lpc, exp) = self.tns_decode_coef(i, w, f, order as usize, self.elem.ld11(i, t_res(w)) + 3, self.elem.ld11(i, t_comp(w, f)));
                    let s0 = bottom.min(mts).min(msfb);
                    let s1 = top.min(mts).min(msfb);
                    let mut start = self.elem.s16(i, O_SWB_OFF + 2 * s0 as usize);
                    let end = self.elem.s16(i, O_SWB_OFF + 2 * s1 as usize);
                    let size = end - start;
                    if size > 0 {
                        let mut inc = 1i32;
                        if self.elem.ld11(i, t_dir(w, f)) != 0 {
                            inc = -1;
                            start = end - 1;
                        }
                        let first = w as i32 * 128 + start;
                        chk!(first);
                        chk!(first + inc * (size - 1));
                        let mut st = [0i32; 33];
                        let sh = (15 - exp) as i16 as i32;
                        let mut idx = first;
                        let order = order as usize;
                        for n in 0..size as usize {
                            let mut y = self.spec[ch][idx as usize];
                            for j in (1..=n.min(order)).rev() {
                                let p = shl32(mulhd_ss(shl32(lpc[j] as i32, 16), st[j - 1]), 1);
                                y = w32(y as i64 - p as i64);
                                st[j] = st[j - 1];
                            }
                            st[0] = shl32(y, sh);
                            self.spec[ch][idx as usize] = y;
                            idx += inc;
                        }
                    }
                }
                f += 1;
            }
            w += 1;
        }
        Ok(())
    }

    /// 0x21bb2
    fn filter_bank(&mut self, ch: usize, seq: i32, shape: i32, shape_prev: i32) -> R<()> {
        let mut t = vec![0i32; 2048];
        let mut tt = *self.time[ch];
        tt.copy_within(1024..2048, 0);
        // window_sequence (2 bits) and window_shape (1 bit) come straight from the bitstream
        let win = |k: i32| self.fw.a.win[k as usize];
        let fw = self.fw.clone();
        let rw = |a: u32, k: usize| fw.rds16(a + 2 * k as u32);
        let freq = *self.spec[ch];
        if seq != EIGHT_SHORT {
            let (pa, pb) = match seq {
                ONLY_LONG => (win(shape_prev), win(4 + shape)),
                LONG_START => (win(shape_prev), win(6 + shape)),
                _ => (win(2 + shape_prev), win(4 + shape)),
            };
            imdct::imdct(&fw, &freq, 2048, &mut t);
            if seq == LONG_STOP {
                t[..448].fill(0);
                for k in 0..128 {
                    t[448 + k] = imdct::wmul(t[448 + k], rw(pa, k));
                }
            } else {
                for k in 0..1024 {
                    t[k] = imdct::wmul(t[k], rw(pa, k));
                }
            }
            for k in 0..1024 {
                tt[k] = w32(t[k] as i64 + tt[k] as i64);
            }
            if seq == LONG_START {
                tt[1024..1472].copy_from_slice(&t[1024..1472]);
                for k in 0..128 {
                    tt[1472 + k] = imdct::wmul(t[1472 + k], rw(pb, k));
                }
                tt[1600..].fill(0);
            } else {
                for k in 0..1024 {
                    tt[1024 + k] = imdct::wmul(t[1024 + k], rw(pb, k));
                }
            }
        } else {
            let (sprev, s, srev) = (win(2 + shape_prev), win(2 + shape), win(6 + shape));
            tt[1024..].fill(0);
            for w in 0..8 {
                imdct::imdct(&fw, &freq[128 * w..], 256, &mut t);
                let wp = if w == 0 { sprev } else { s };
                let o = 448 + 128 * w;
                for k in 0..128 {
                    tt[o + k] = w32(imdct::wmul(t[k], rw(wp, k)) as i64 + tt[o + k] as i64);
                }
                for k in 0..128 {
                    tt[o + 128 + k] = w32(tt[o + 128 + k] as i64 + imdct::wmul(t[128 + k], rw(srev, k)) as i64);
                }
            }
            tt[1600..].fill(0);
        }
        *self.time[ch] = tt;
        Ok(())
    }

    /// 0x1cab8 dynamic_range_info: FAAD2's, but band_top is read once whatever num_bands is (the
    /// loop has no body). The excluded-channel list writes over the decoder's ADTS/ADIF flags
    /// (decoder + 0x40e0..): not modelled.
    fn drc_info(b: &mut Bits) -> R<i32> {
        let (mut n, mut bands) = (1, 1);
        if b.get(1) != 0 {
            b.get(8);
            n += 1;
        }
        if b.get(1) != 0 {
            return Err(Fail::Undefined(line!()));
        }
        if b.get(1) != 0 {
            let incr = b.get(4) as i32;
            b.get(4);
            n += 1;
            bands += incr;
            b.get(8);
            n += 1;
        }
        if b.get(1) != 0 {
            b.get(8);
            n += 1;
        }
        for _ in 0..bands {
            b.get(8);
            n += 1;
        }
        Ok(n)
    }

    /// 0x1e19a fill_element / 0x1deae extension_payload
    fn fill_element(b: &mut Bits) -> R<()> {
        let mut cnt = b.get(4) as i32;
        if cnt == 15 {
            cnt += b.get(8) as i32 - 1;
        }
        while cnt > 0 {
            let used = if b.get(4) == 11 {
                Self::drc_info(b)?
            } else {
                b.get(4);
                for _ in 0..cnt - 1 {
                    b.get(8);
                }
                cnt
            };
            cnt -= used;
        }
        Ok(())
    }

    /// 0xcee8: one raw_data_block.
    fn frame(&mut self, raw: &[u8]) -> R<u32> {
        let mut b = Bits { p: raw, pos: 0 };
        self.elem.0.fill(0);
        let (ics0, ics1) = (Ics(0), Ics(ICS_SZ));
        // r8 channels, r7 elements, r11 "second SCE", element + 18546 = first channel
        let (mut nch, mut nelem, mut second_sce, mut first_ch) = (0usize, 0, false, 0usize);
        loop {
            let id = b.get(3);
            match id {
                7 => break,
                0 | 3 => {
                    // 0x2e8a0: the ic_stream and buffers are chosen by tag, not by channel
                    if nch > 1 || nelem > 1 {
                        return Err(Fail::Fw(-14));
                    }
                    let tag = b.get(4) as i32;
                    let first = *self.sce_tag.get_or_insert(tag);
                    let k = if tag == first { 0 } else { 1 };
                    first_ch = nch;
                    if nch == 1 {
                        second_sce = true;
                    }
                    self.individual_channel_stream(&mut b, if k == 0 { ics0 } else { ics1 }, false, k)?;
                    nch += 1;
                    nelem = 1;
                }
                1 => {
                    if nelem > 0 {
                        return Err(Fail::Fw(-14));
                    }
                    b.get(4);
                    let common = b.get(1) != 0;
                    if common {
                        self.ics_info(&mut b, ics0)?;
                        let mm = b.get(2) as i32;
                        self.elem.w16(ics0, O_MS_MASK, mm)?;
                        if mm == 1 {
                            let (ng, ms) = (self.elem.ld11(ics0, O_NUM_GROUPS), self.elem.ld11(ics0, O_MAX_SFB));
                            for g in 0..ng.max(0) as usize {
                                for s in 0..ms.max(0) as usize {
                                    self.elem.w8(ics0, O_MS_USED + 51 * g + s, b.get(1) as i32)?;
                                }
                            }
                        }
                        self.elem.0.copy_within(0..ICS_SZ, ICS_SZ);
                    }
                    first_ch = nch;
                    self.individual_channel_stream(&mut b, ics0, common, 0)?;
                    self.individual_channel_stream(&mut b, ics1, common, 1)?;
                    nch += 2;
                    nelem += 1;
                }
                2 => return Err(Fail::Fw(16)),
                4 => {
                    b.get(4);
                    let align = b.get(1);
                    let mut cnt = b.get(8) as usize;
                    if cnt == 255 {
                        cnt += b.get(8) as usize;
                    }
                    if align != 0 {
                        b.pos = (b.pos + 7) & !7;
                    }
                    b.pos += 8 * cnt;
                }
                6 => Self::fill_element(&mut b)?,
                _ => return Err(Fail::Unsupported),
            }
        }
        let nch = nch.min(2);
        // channel c -> ic_stream: the element's first channel gets ic_stream 0, the next one
        // ic_stream 1; after two SCEs simply c
        let ics: [Ics; 2] = if second_sce || first_ch == 0 {
            [ics0, ics1]
        } else {
            [ics1, ics0]
        };
        for c in 0..nch {
            self.iquant(c);
            self.apply_scalefactors(c, ics[c])?;
        }
        for c in 0..nch {
            if self.elem.ld11(ics[c], O_WSEQ) == EIGHT_SHORT {
                self.reorder_short(c, ics[c]);
            }
        }
        for c in 0..nch {
            let i = ics[c];
            // M/S and IS run for the element's first channel (a no-op for a lone SCE)
            let pair = !second_sce && c == first_ch;
            if pair {
                self.ms_decode(ics0, ics1)?;
            }
            self.pns_decode(c, i)?;
            if pair {
                self.is_decode(ics0, ics1)?;
            }
            self.tns_decode(c, i)?;
            let ws = self.elem.ld11(i, O_WSEQ);
            let wsh = self.elem.ld11(i, O_WSHAPE);
            self.filter_bank(c, ws, wsh, self.wshape_prev[c])?;
            self.wshape_prev[c] = wsh;
        }
        self.frame_count = self.frame_count.wrapping_add(1);
        // 0x266de writes PCM even without a channel element: channel 0's output half, both sides
        let (l, r) = (0usize, if nch >= 2 { 1 } else { 0 });
        for k in 0..1024 {
            let a = (w32(self.time[l][k] as i64 + 512) >> 10).clamp(-32768, 32767);
            let b2 = (w32(self.time[r][k] as i64 + 512) >> 10).clamp(-32768, 32767);
            self.pcm[2 * k] = a as i16;
            self.pcm[2 * k + 1] = b2 as i16;
        }
        self.has_pcm = true;
        Ok(if self.frame_count > 2 { nch as u32 * 1024 } else { 0 })
    }
}
