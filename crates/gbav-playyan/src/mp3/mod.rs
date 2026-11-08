//! The Play-Yan music firmware's MP3 decoder (VC01 music image, py_fw1 0x222f2 and below),
//! bit-exact: the 16-bit stereo PCM the firmware sends to its headphone codec.
//!
//! MPEG-1 Layer III only: the player rejects every other format before the decoder sees it.
//! The structure follows the firmware, not ISO 11172-3 reference code:
//!
//! * side info and scalefactors as the firmware stores them (big_values clamped to 288,
//!   scalefactor 21 / short 12 forced to 0, so intensity stereo uses is_pos 0 there);
//! * Huffman decoding on a persistent bit-expanded buffer (one byte per bit, 16 bits per expansion
//!   step), with the firmware's trees and linbits read from the image; the count1 quadruple that
//!   overruns part2_3_length is kept;
//! * requantisation |x|^(4/3) 2^(i/4) with the firmware's tables and partial-product multiply
//!   (0x35058), long-block gain index not bounded below;
//! * MS and intensity stereo (0x221b8, 0x21ef6/0x2c4a0) with Q32 multiplies, intensity positions
//!   7/15/23 skipped, MS on an illegal band only for is_pos 7;
//! * alias reduction (Q31), the vector IMDCTs ([`imdct`]) and matrixing ([`dct32`]) with the
//!   firmware's multiply and constants, the fixed-point synthesis window.
//!
//! Mixed blocks are not decoded: the firmware's short-block reorder for them reads its own stack
//! beyond the copy it made ([`DecodeResult::Unsupported`]).

pub mod dct32;
pub mod imdct;

use crate::firmware::{Image, ImageId};
use gbav_common::{md5, Error, Result};
use imdct::{ImdctConsts, KAddr, Kc};

/// Outcome of [`Decoder::decode_frame_status`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeResult {
    /// The frame was decoded; the PCM holds 1152 stereo samples.
    Ok,
    /// main_data_begin points before the data the reservoir has: the firmware outputs 1152 x 0
    /// without touching the decoder state.
    Silence,
    /// The decoder rejects the header (layer, bitrate or rate index invalid; reservoir overflow):
    /// the firmware returns 0 and writes no PCM.
    BadHeader,
    /// MPEG-2/2.5 or mixed blocks: never decoded by the player (or undefined in the firmware).
    Unsupported,
    /// The firmware's result depends on memory this model does not hold (bits beyond what it
    /// ever wrote, reads past the reservoir's neighbours).
    Undefined,
}

const RES: usize = 2000;
const RES_EXT: usize = 216;
const BITBUF: usize = 8000;
const GAIN_NEG: i32 = 72;

const SFB_L: [[usize; 22]; 3] = [
    [4, 4, 4, 4, 4, 4, 6, 6, 8, 8, 10, 12, 16, 20, 24, 28, 34, 42, 50, 54, 76, 158],
    [4, 4, 4, 4, 4, 4, 6, 6, 6, 8, 10, 12, 16, 18, 22, 28, 34, 40, 46, 54, 54, 192],
    [4, 4, 4, 4, 4, 4, 6, 6, 8, 10, 12, 16, 20, 24, 30, 38, 46, 56, 68, 84, 102, 26],
];
const SFB_S: [[usize; 13]; 3] = [
    [4, 4, 4, 4, 6, 8, 10, 12, 14, 18, 22, 30, 56],
    [4, 4, 4, 4, 6, 6, 10, 12, 14, 16, 20, 26, 66],
    [4, 4, 4, 4, 6, 8, 12, 16, 20, 26, 34, 42, 12],
];
const BITRATE: [i32; 16] =
    [999, 32000, 40000, 48000, 56000, 64000, 80000, 96000, 112000, 128000, 160000, 192000, 224000, 256000, 320000, 999];
const SRATE: [i32; 4] = [44100, 48000, 32000, 999];

/// Firmware locations, as py_fw1 addresses plus per-image section deltas.
struct Layout {
    dcode: i32,
    dgain: i32,
    ddata1: i32,
    dlin: i32,
    dslen: i32,
    dtree: i32,
    dis: i32,
}

fn layout(id: ImageId) -> Option<Layout> {
    Some(match id {
        ImageId::PyFw1 => Layout { dcode: 0, dgain: 0, ddata1: 0, dlin: 0, dslen: 0, dtree: 0, dis: 0 },
        ImageId::MiFw1 => Layout {
            dcode: -0x546,
            dgain: -0x540,
            ddata1: 0x4718,
            dlin: -0x13c0,
            dslen: 0x4718,
            dtree: 0x47b4,
            dis: -0x538,
        },
        ImageId::FupSec1 => Layout {
            dcode: -0x600,
            dgain: -0x600,
            ddata1: 0x2f4,
            dlin: -0xbcc,
            dslen: 0x2f4,
            dtree: 0x2f4,
            dis: -0x5f8,
        },
        _ => return None,
    })
}

/// Huffman tree roots per table_select (py_fw1; 0 = table without codes).
const TREES: [u32; 32] = [
    0, 0x3b3d4, 0x3b3f0, 0x3b434, 0, 0x3b478, 0x3b4f4, 0x3b570, 0x3b68c, 0x3b7a8, 0x3b8c4, 0x3bac0, 0x3bcbc, 0x3beb8,
    0, 0x3c6b4, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3a3dc, 0x3a3dc, 0x3a3dc,
    0x3a3dc, 0x3a3dc, 0x3a3dc, 0x3a3dc, 0x3a3dc,
];
const ALIAS_CS: [u32; 8] = [0x221f8, 0x22200, 0x22208, 0x22210, 0x22218, 0x22220, 0x22228, 0x22230];
const ALIAS_CA: [u32; 8] = [0x2223a, 0x22242, 0x2224a, 0x22252, 0x2225a, 0x22262, 0x2226a, 0x22272];

/// md5 of all constants read from the image (canonical little-endian serialisation, see
/// [`Tables::digest`]); the same for py_fw1, mi_fw1 and fup_sec1.
const TABLES_MD5: &str = "60ea08718b1d692ed485aa3ae9a86ab6";

/// Everything the decoder reads from the firmware image.
#[derive(Clone)]
pub struct Tables {
    img: std::sync::Arc<[u8]>,
    pow43: Vec<u32>,
    /// gain[GAIN_NEG + i] = word at 0x234a0 + 4 i, i = -72..255 (the negative side is code bytes,
    /// reachable with very small global_gain).
    gain: Vec<u32>,
    dw: [[i32; 16]; 32],
    imdct: ImdctConsts,
    /// 2cos(k pi / 64) for dct32, k = 1..31 (index 0 unused)
    ck: [Kc; 32],
    tree: [u32; 32],
    cnt1: [u32; 2],
    lin: [i32; 32],
    slen1: [i32; 16],
    slen2: [i32; 16],
    pretab: [i32; 22],
    iskl: [u32; 24],
    iskr: [u32; 24],
    cs: [i32; 8],
    ca: [i32; 8],
    ms: u32,
}

fn rd16(b: &[u8], a: u32) -> u32 {
    let a = a as usize;
    b[a] as u32 | (b[a + 1] as u32) << 8
}
fn rd32(b: &[u8], a: u32) -> u32 {
    rd16(b, a) | rd16(b, a + 2) << 16
}
fn add(a: u32, d: i32) -> u32 {
    (a as i64 + d as i64) as u32
}

impl Tables {
    pub fn from_image(img: &Image) -> Result<Tables> {
        let l = layout(img.id)
            .ok_or_else(|| Error::Invalid(format!("{} is not a music image", img.id.name())))?;
        let b: &[u8] = &img.bytes;
        if b.len() < 0x3f000 {
            return Err(Error::Format("music image too short".into()));
        }
        let imm = |base: u32, enc: i32| -> u32 {
            let a = base + (enc >> 1) as u32;
            if enc & 1 != 0 {
                let v = rd16(b, a + 4) & 63;
                (if v & 32 != 0 { v.wrapping_sub(64) } else { v }) & 0xffff
            } else {
                (rd16(b, a + 6) & 0xfc00) | (rd16(b, a + 4) & 0x3ff)
            }
        };
        let pow43 = (0..8192).map(|i| rd32(b, add(0x244a0, l.dgain) + 4 * i)).collect();
        let gain = (-GAIN_NEG..256).map(|i| rd32(b, add(0x234a0, l.dgain + 4 * i))).collect();
        let t = add(0x3ee2c, l.ddata1);
        let mut dw = [[0i32; 16]; 32];
        for (k, row) in dw.iter_mut().enumerate() {
            for (n, v) in row.iter_mut().enumerate() {
                *v = if k < 16 {
                    rd32(b, t + 4 * (16 * if k > 0 { 2 * k - 1 } else { 0 } + n) as u32) as i32
                } else if k > 16 {
                    let x = rd32(b, t + 4 * (16 * (63 - 2 * k) + 15 - n) as u32) as i32;
                    if n & 1 != 0 {
                        x.wrapping_neg()
                    } else {
                        x
                    }
                } else {
                    let j = if n & 1 != 0 { 496 + n } else if n <= 8 { n } else { 16 - n };
                    rd32(b, t + 4 * j as u32) as i32
                };
            }
        }
        let kc = |a: &KAddr| -> Kc {
            let base = imdct::FUNC_BASE[a[0] as usize];
            let mut c0 = if a[3] >= 0 { imm(base, a[3]) as i32 } else { 0 };
            if c0 > 32767 {
                c0 -= 65536;
            }
            Kc { c0, c1: imm(base, a[2]), c2: imm(base, a[1]) }
        };
        let imdct = ImdctConsts::read(&kc);
        let mut ck = [Kc::default(); 32];
        for (i, a) in dct32::D_COS.iter().enumerate() {
            ck[i + 1] = kc(a);
        }
        let mut tree = [0u32; 32];
        for (i, &r) in TREES.iter().enumerate() {
            tree[i] = if r == 0 { 0 } else { add(r, l.dtree) };
        }
        let cnt1 = [add(0x3ceb0, l.dtree), add(0x3cf2c, l.dtree)];
        let rdi = |a: u32| rd32(b, a) as i32;
        let lin = std::array::from_fn(|t| rdi(add(0x3a034, l.dlin) + 24 * t as u32));
        let slen1 = std::array::from_fn(|i| rdi(add(0x3d1dc, l.dslen) + 4 * i as u32));
        let slen2 = std::array::from_fn(|i| rdi(add(0x3d21c, l.dslen) + 4 * i as u32));
        let pretab = std::array::from_fn(|i| rdi(add(0x3cfac, l.dtree) + 4 * i as u32));
        let iskl = std::array::from_fn(|i| rd32(b, add(0x24178, l.dis) + 4 * i as u32));
        let iskr = std::array::from_fn(|i| rd32(b, add(0x241d8, l.dis) + 4 * i as u32));
        let cs = std::array::from_fn(|i| rdi(add(ALIAS_CS[i], l.dcode) + 2));
        let ca = std::array::from_fn(|i| rdi(add(ALIAS_CA[i], l.dcode) + 2));
        let ms = rd32(b, add(0x221c0, l.dcode) + 2);
        let t = Tables {
            img: img.bytes.clone(),
            pow43,
            gain,
            dw,
            imdct,
            ck,
            tree,
            cnt1,
            lin,
            slen1,
            slen2,
            pretab,
            iskl,
            iskr,
            cs,
            ca,
            ms,
        };
        let h = t.digest();
        if !TABLES_MD5.is_empty() && h != TABLES_MD5 {
            return Err(Error::Format(format!("{}: MP3 tables md5 {h}, expected {TABLES_MD5}", img.id.name())));
        }
        Ok(t)
    }

    /// Size in bytes of the Huffman tree at `root` (nodes are u16 pairs, links relative).
    fn tree_size(&self, root: u32) -> u32 {
        let mut max = root;
        let mut stack = vec![root];
        let mut seen = std::collections::HashSet::new();
        while let Some(n) = stack.pop() {
            if !seen.insert(n) || n as usize + 4 > self.img.len() {
                continue;
            }
            max = max.max(n);
            let h0 = rd16(&self.img, n);
            if h0 != 0 {
                stack.push(n + 4 * h0);
                stack.push(n + 4 * rd16(&self.img, n + 2));
            }
        }
        max + 4 - root
    }

    /// md5 of the constants, serialised as little-endian words in field order, followed by the
    /// bytes of every Huffman tree.
    pub fn digest(&self) -> String {
        let mut v: Vec<u8> = Vec::new();
        let mut w = |x: u32| v.extend_from_slice(&x.to_le_bytes());
        self.pow43.iter().for_each(|&x| w(x));
        self.gain[GAIN_NEG as usize..].iter().for_each(|&x| w(x));
        self.dw.iter().flatten().for_each(|&x| w(x as u32));
        for k in self.imdct.all().iter().chain(&self.ck[1..]) {
            w(k.c0 as u32);
            w(k.c1);
            w(k.c2);
        }
        self.lin.iter().for_each(|&x| w(x as u32));
        self.slen1.iter().chain(&self.slen2).chain(&self.pretab).for_each(|&x| w(x as u32));
        self.iskl.iter().chain(&self.iskr).for_each(|&x| w(x));
        self.cs.iter().chain(&self.ca).for_each(|&x| w(x as u32));
        w(self.ms);
        let mut roots: Vec<u32> = self.tree.iter().copied().filter(|&r| r != 0).collect();
        roots.extend(self.cnt1);
        roots.dedup();
        for r in roots {
            let n = self.tree_size(r);
            v.extend_from_slice(&self.img[r as usize..(r + n) as usize]);
        }
        md5::hex(&v)
    }
}

/// The decoder: one per stream, fed frame by frame.
pub struct Decoder {
    t: Tables,
    /// `true` (default): reading bit-buffer bytes the firmware never wrote (its heap contents)
    /// gives [`DecodeResult::Undefined`]; `false`: they read as 0 (a zeroed heap, as in the
    /// emulator harness).
    pub strict: bool,
    sri: usize,
    sr: i32,
    nch: usize,
    mode: u32,
    mode_ext: u32,
    mdb: usize,
    scfsi: [[bool; 4]; 2],
    p23: [[i32; 2]; 2],
    bv: [[usize; 2]; 2],
    gg: [[i32; 2]; 2],
    sfc: [[usize; 2]; 2],
    ws: [[bool; 2]; 2],
    bt: [[u32; 2]; 2],
    mixed: [[bool; 2]; 2],
    ts: [[[usize; 3]; 2]; 2],
    sbg: [[[i32; 3]; 2]; 2],
    r0c: [[usize; 2]; 2],
    r1c: [[usize; 2]; 2],
    pre: [[i32; 2]; 2],
    sfs: [[bool; 2]; 2],
    c1: [[usize; 2]; 2],
    sf_l: [[[i32; 22]; 2]; 2],
    sf_s: [[[[i32; 3]; 13]; 2]; 2],
    is: Box<[[[i16; 592]; 2]; 2]>,
    xr: Box<[[[i32; 592]; 2]; 2]>,
    res: Box<[u8; RES + RES_EXT]>,
    fill: u32,
    bp: usize,
    bit: u32,
    budget: i32,
    bb: Box<[u8; BITBUF]>,
    bbw: Box<[bool; BITBUF]>,
    undefined: bool,
    ov: Box<[[[i32; 18]; 32]; 2]>,
    hist: Box<[[[i32; 34]; 16]; 2]>,
    slot: [u32; 2],
    pcm: Vec<i16>,
}

#[inline]
fn mulsu(a: i32, b: u32) -> i32 {
    ((a as i64 * b as i64) >> 32) as i32
}
#[inline]
fn mulss(a: i32, b: i32) -> i32 {
    ((a as i64 * b as i64) >> 32) as i32
}

struct FrameBits<'a> {
    p: &'a [u8],
    pos: usize,
}
impl FrameBits<'_> {
    fn get(&mut self, n: u32) -> u32 {
        let mut v = 0;
        for _ in 0..n {
            let i = self.pos >> 3;
            let bit = self.p.get(i).map_or(0, |b| (b >> (7 - (self.pos & 7))) & 1);
            v = v << 1 | bit as u32;
            self.pos += 1;
        }
        v
    }
}

impl Decoder {
    /// A decoder using the tables of `music` (py_fw1, mi_fw1 or fup_sec1).
    pub fn new(music: &Image) -> Result<Decoder> {
        Ok(Decoder::with_tables(Tables::from_image(music)?))
    }

    pub fn with_tables(t: Tables) -> Decoder {
        Decoder {
            t,
            strict: true,
            sri: 0,
            sr: 44100,
            nch: 2,
            mode: 0,
            mode_ext: 0,
            mdb: 0,
            scfsi: [[false; 4]; 2],
            p23: [[0; 2]; 2],
            bv: [[0; 2]; 2],
            gg: [[0; 2]; 2],
            sfc: [[0; 2]; 2],
            ws: [[false; 2]; 2],
            bt: [[0; 2]; 2],
            mixed: [[false; 2]; 2],
            ts: [[[0; 3]; 2]; 2],
            sbg: [[[0; 3]; 2]; 2],
            r0c: [[0; 2]; 2],
            r1c: [[0; 2]; 2],
            pre: [[0; 2]; 2],
            sfs: [[false; 2]; 2],
            c1: [[0; 2]; 2],
            sf_l: [[[0; 22]; 2]; 2],
            sf_s: [[[[0; 3]; 13]; 2]; 2],
            is: Box::new([[[0; 592]; 2]; 2]),
            xr: Box::new([[[0; 592]; 2]; 2]),
            res: Box::new([0; RES + RES_EXT]),
            fill: 0,
            bp: 0,
            bit: 0,
            budget: 0,
            bb: Box::new([0; BITBUF]),
            bbw: Box::new([false; BITBUF]),
            undefined: false,
            ov: Box::new([[[0; 18]; 32]; 2]),
            hist: Box::new([[[0; 34]; 16]; 2]),
            slot: [0; 2],
            pcm: vec![0; 2304],
        }
    }

    /// The tables in use.
    pub fn tables(&self) -> &Tables {
        &self.t
    }

    /// Decode one frame (starting at its sync word). `Ok(Some(pcm))`: 1152 interleaved L/R
    /// samples (mono is duplicated into R, as the firmware does); also for
    /// [`DecodeResult::Silence`]. `Ok(None)`: the decoder writes no PCM for this frame
    /// (rejected header). `Err(Undefined)` for input outside the model.
    pub fn decode_frame(&mut self, frame: &[u8]) -> Result<Option<&[i16]>> {
        match self.decode_frame_status(frame).0 {
            DecodeResult::Ok | DecodeResult::Silence => Ok(Some(&self.pcm)),
            DecodeResult::BadHeader => Ok(None),
            DecodeResult::Unsupported => Err(Error::Undefined("MPEG-2/2.5 or mixed-block frame".into())),
            DecodeResult::Undefined => Err(Error::Undefined("frame reads memory the firmware never wrote".into())),
        }
    }

    /// The PCM of the last decoded frame (2304 values).
    pub fn pcm(&self) -> &[i16] {
        &self.pcm
    }

    /// Decode one frame; returns the result and the frame length in bytes as the firmware
    /// computes it (0 if the header was not accepted).
    pub fn decode_frame_status(&mut self, fr: &[u8]) -> (DecodeResult, usize) {
        let mut b = FrameBits { p: fr, pos: 0 };
        self.undefined = false;
        let sync = b.get(12);
        if sync != 0xfff {
            return (if sync == 0xffe { DecodeResult::Unsupported } else { DecodeResult::BadHeader }, 0);
        }
        let id = b.get(1);
        let layer = b.get(2);
        let prot = b.get(1);
        let bri = b.get(4) as usize;
        let sri = b.get(2) as usize;
        let pad = b.get(1) as i32;
        b.get(1);
        self.mode = b.get(2);
        self.mode_ext = b.get(2);
        b.get(4);
        if layer != 1 {
            return (DecodeResult::BadHeader, 0);
        }
        if id == 0 {
            return (DecodeResult::Unsupported, 0);
        }
        let (br, sr) = (BITRATE[bri], SRATE[sri]);
        if br == 999 || sr == 999 {
            return (DecodeResult::BadHeader, 0);
        }
        if prot == 0 {
            b.get(16);
        }
        self.nch = if self.mode == 3 { 1 } else { 2 };
        self.sri = sri;
        self.sr = sr;
        let bits = (2 * 576 * br as i64 / sr as i64) as i32 + 8 * pad;
        let fbytes = (bits >> 3) as usize;
        // side info (0x23b62)
        self.mdb = b.get(9) as usize;
        b.get(if self.nch == 1 { 5 } else { 3 });
        for ch in 0..self.nch {
            for k in 0..4 {
                self.scfsi[ch][k] = b.get(1) != 0;
            }
        }
        for gr in 0..2 {
            for ch in 0..self.nch {
                self.p23[gr][ch] = b.get(12) as i32;
                self.bv[gr][ch] = (b.get(9) as usize).min(288);
                self.gg[gr][ch] = b.get(8) as i32;
                self.sfc[gr][ch] = b.get(4) as usize;
                self.ws[gr][ch] = b.get(1) != 0;
                if self.ws[gr][ch] {
                    self.bt[gr][ch] = b.get(2);
                    self.mixed[gr][ch] = b.get(1) != 0;
                    self.ts[gr][ch] = [b.get(5) as usize, b.get(5) as usize, 0];
                    for w in 0..3 {
                        self.sbg[gr][ch][w] = b.get(3) as i32;
                    }
                    self.r0c[gr][ch] = if self.bt[gr][ch] == 2 && self.mixed[gr][ch] { 8 } else { 7 };
                    self.r1c[gr][ch] = 36;
                } else {
                    self.bt[gr][ch] = 0;
                    self.mixed[gr][ch] = false;
                    self.ts[gr][ch] = [b.get(5) as usize, b.get(5) as usize, b.get(5) as usize];
                    self.r0c[gr][ch] = b.get(4) as usize;
                    self.r1c[gr][ch] = b.get(3) as usize;
                }
                self.pre[gr][ch] = b.get(1) as i32;
                self.sfs[gr][ch] = b.get(1) != 0;
                self.c1[gr][ch] = b.get(1) as usize;
            }
        }
        // main data reservoir (0x22f88)
        let mut sil = if self.nch == 1 { 21 } else { 36 };
        if prot == 0 {
            sil += 2;
        }
        let n = fbytes as i64 - sil as i64;
        if n + self.mdb as i64 > RES as i64 {
            return (DecodeResult::BadHeader, fbytes);
        }
        if n < 0 || fbytes > fr.len() {
            return (DecodeResult::Undefined, fbytes);
        }
        let n = n as usize;
        self.res.copy_within(RES - self.mdb..RES, RES - n - self.mdb);
        self.res[RES - n..RES].copy_from_slice(&fr[sil..sil + n]);
        {
            let mut t = [0u32; 57];
            let mut c = 0;
            for s in 0..22 {
                t[s] = SFB_L[sri][s] as u32;
                c += t[s];
                t[22 + s] = c;
            }
            for s in 0..13 {
                t[44 + s] = SFB_S[sri][s] as u32;
            }
            for i in 0..RES_EXT {
                self.res[RES + i] = (t[i / 4] >> (8 * (i % 4))) as u8;
            }
        }
        self.bp = RES - n - self.mdb;
        self.bit = 0;
        let old = self.fill;
        self.fill = self.fill.wrapping_add(n as u32);
        if old < self.mdb as u32 {
            self.pcm.fill(0);
            return (DecodeResult::Silence, fbytes);
        }
        for gr in 0..2 {
            for ch in 0..self.nch {
                if self.ws[gr][ch] && self.bt[gr][ch] == 2 && self.mixed[gr][ch] {
                    return (DecodeResult::Unsupported, fbytes);
                }
                self.granule_ch(gr, ch);
            }
        }
        for gr in 0..2 {
            for ch in 0..self.nch {
                self.requant(gr, ch);
            }
        }
        if self.mode == 1 {
            for gr in 0..2 {
                match self.mode_ext {
                    1 => self.intensity(gr, false),
                    2 => {
                        let (l, r) = self.xr[gr].split_at_mut(1);
                        ms(&self.t, &mut l[0][..576], &mut r[0][..576]);
                    }
                    3 => self.intensity(gr, true),
                    _ => {}
                }
            }
        }
        for gr in 0..2 {
            for ch in 0..self.nch {
                if self.bt[gr][ch] == 2 {
                    self.reorder(gr, ch);
                } else {
                    alias(&self.t, &mut self.xr[gr][ch], 32);
                }
            }
        }
        for gr in 0..2 {
            for ch in 0..self.nch {
                self.imdct_synth(gr, ch);
            }
        }
        if self.nch == 1 {
            for i in 0..1152 {
                self.pcm[2 * i + 1] = self.pcm[2 * i];
            }
        }
        (if self.undefined { DecodeResult::Undefined } else { DecodeResult::Ok }, fbytes)
    }

    fn resb(&mut self, p: usize) -> u32 {
        match self.res.get(p) {
            Some(&v) => v as u32,
            None => {
                self.undefined = true;
                0
            }
        }
    }

    /// 0x23a0c
    fn getbits(&mut self, mut n: u32) -> u32 {
        let mut v = 0u32;
        self.budget -= n as i32;
        let mut left = 8 - self.bit;
        while n > 0 {
            let take = n.min(left);
            let b = self.resb(self.bp);
            v = v << take | ((b >> (left - take)) & ((1 << take) - 1));
            left -= take;
            n -= take;
            if left == 0 {
                left = 8;
                self.bp += 1;
            }
        }
        self.bit = 8 - left;
        v
    }

    /// 0x239d4
    fn getbit(&mut self) -> u8 {
        self.budget -= 1;
        let b = self.resb(self.bp);
        if self.bit == 7 {
            self.bit = 0;
            self.bp += 1;
            (b & 1) as u8
        } else {
            let v = (b >> (7 - self.bit)) & 1;
            self.bit += 1;
            v as u8
        }
    }

    fn bbget(&mut self, i: usize) -> u32 {
        if i >= BITBUF {
            self.undefined = true;
            return 0;
        }
        if !self.bbw[i] && self.strict {
            self.undefined = true;
        }
        self.bb[i] as u32
    }

    fn bbput(&mut self, i: usize, v: u8) {
        if i >= BITBUF {
            self.undefined = true;
            return;
        }
        self.bb[i] = v;
        self.bbw[i] = true;
    }

    /// 0x35024
    fn huff_pair(&mut self, p: &mut usize, mut node: u32) -> (i32, i32) {
        if node == 0 {
            return (0, 0);
        }
        loop {
            let h0 = rd16(&self.t.img, node);
            if h0 == 0 {
                let a = node as usize;
                return (self.t.img[a + 2] as i32, self.t.img[a + 3] as i32);
            }
            let bit = self.bbget(*p);
            *p += 1;
            node += 4 * if bit == 0 { h0 } else { rd16(&self.t.img, node + 2) };
        }
    }

    /// 0x21010
    fn huff_quad(&mut self, p: &mut usize, mut node: u32) -> [i32; 4] {
        loop {
            let h0 = rd16(&self.t.img, node);
            if h0 == 0 {
                let v = rd16(&self.t.img, node + 2) as i32;
                return [v >> 3 & 1, v >> 2 & 1, v >> 1 & 1, v & 1];
            }
            let bit = self.bbget(*p);
            *p += 1;
            node += 4 * if bit == 0 { h0 } else { rd16(&self.t.img, node + 2) };
        }
    }

    fn linval(&mut self, p: &mut usize, mut x: i32, lb: i32) -> i32 {
        if x == 15 && lb > 0 {
            let mut v = 0;
            for _ in 0..lb {
                v = (v << 1) + self.bbget(*p) as i32;
                *p += 1;
            }
            x = v + 15;
        }
        if x != 0 {
            let s = self.bbget(*p);
            *p += 1;
            if s != 0 {
                x = -x;
            }
        }
        x
    }

    /// 0x2d22e
    fn huff_region(&mut self, p: &mut usize, gr: usize, ch: usize, at: usize, tsel: usize, a: i64, b: i64) {
        let mut n = a.min(b);
        let lb = self.t.lin[tsel];
        let tree = self.t.tree[tsel];
        let mut o = at;
        while n > 0 {
            let (mut x, mut y) = self.huff_pair(p, tree);
            if x != 0 {
                x = self.linval(p, x, lb);
            }
            if y != 0 {
                y = self.linval(p, y, lb);
            }
            if o + 1 < 592 {
                self.is[gr][ch][o] = x as i16;
                self.is[gr][ch][o + 1] = y as i16;
            } else {
                self.undefined = true;
            }
            o += 2;
            n -= 2;
        }
    }

    /// 0x22398: scalefactors and Huffman data of one granule and channel.
    fn granule_ch(&mut self, gr: usize, ch: usize) {
        let slen1 = self.t.slen1[self.sfc[gr][ch]] as u32;
        let slen2 = self.t.slen2[self.sfc[gr][ch]] as u32;
        self.budget = self.p23[gr][ch];
        let short = self.ws[gr][ch] && self.bt[gr][ch] == 2;
        if short {
            for s in 0..12 {
                for w in 0..3 {
                    self.sf_s[gr][ch][s][w] = self.getbits(if s < 6 { slen1 } else { slen2 }) as i32;
                }
            }
            self.sf_s[gr][ch][12] = [0; 3];
        } else {
            const BAND: [usize; 5] = [0, 6, 11, 16, 21];
            for bnd in 0..4 {
                let sl = if bnd < 2 { slen1 } else { slen2 };
                for s in BAND[bnd]..BAND[bnd + 1] {
                    self.sf_l[gr][ch][s] =
                        if self.scfsi[ch][bnd] && gr > 0 { self.sf_l[0][ch][s] } else { self.getbits(sl) as i32 };
                }
            }
            self.sf_l[gr][ch][21] = 0;
        }
        let part3 = self.budget;
        let r8 = if self.bp & 1 != 0 { 8 } else { 0 };
        let r9 = if self.bit > 0 { 16 - r8 - self.bit as usize } else { r8 };
        let start = 16 - r9;
        for i in 0..r9 {
            let v = self.getbit();
            self.bbput(start + i, v);
        }
        let n = self.budget;
        if n > 7968 {
            return; // 0x22a2c: debug trap, the granule keeps its previous values
        }
        {
            let mut q = 16;
            let mut left = n;
            let mut hp = self.bp & !1;
            loop {
                let h = self.resb(hp) | self.resb(hp + 1) << 8;
                for k in 0..16 {
                    let sh = if k < 8 { 7 - k } else { 23 - k };
                    self.bbput(q + k, ((h >> sh) & 1) as u8);
                }
                q += 16;
                hp += 2;
                left -= 16;
                if left <= 0 {
                    break;
                }
            }
        }
        self.bp = (self.bp as i64 + (n >> 3) as i64) as usize;
        self.bit = (n & 7) as u32;
        self.budget -= n;
        let mut p = start;
        let end = start as i64 + part3 as i64; // may be below start: offsets compare signed
        let mut pos;
        if short {
            let r1 = if self.sr == 8000 { 72 } else { 36 };
            pos = 0;
            while pos < 2 * self.bv[gr][ch] {
                let tsel = self.ts[gr][ch][if pos < r1 { 0 } else { 1 }];
                let tree = self.t.tree[tsel];
                let lb = self.t.lin[tsel];
                let (mut x, mut y) = self.huff_pair(&mut p, tree);
                if x != 0 {
                    x = self.linval(&mut p, x, lb);
                }
                if y != 0 {
                    y = self.linval(&mut p, y, lb);
                }
                self.is[gr][ch][pos] = x as i16;
                self.is[gr][ch][pos + 1] = y as i16;
                pos += 2;
            }
        } else {
            let mut cum = [0usize; 22];
            let mut c = 0;
            for s in 0..22 {
                c += SFB_L[self.sri][s];
                cum[s] = c;
            }
            let a1 = self.r0c[gr][ch].min(21);
            let a2 = (self.r0c[gr][ch] + self.r1c[gr][ch] + 1).min(21);
            let (b1, b2, nb) = (cum[a1] as i64, cum[a2] as i64, 2 * self.bv[gr][ch] as i64);
            let ts = self.ts[gr][ch];
            self.huff_region(&mut p, gr, ch, 0, ts[0], b1, nb);
            self.huff_region(&mut p, gr, ch, b1 as usize, ts[1], b2 - b1, nb - b1);
            self.huff_region(&mut p, gr, ch, b2 as usize, ts[2], 576 - b2, nb - b2);
            pos = nb as usize;
        }
        if p as i64 != end && pos < 574 {
            loop {
                let mut q = self.huff_quad(&mut p, self.t.cnt1[self.c1[gr][ch]]);
                for v in q.iter_mut() {
                    if *v != 0 {
                        let s = self.bbget(p);
                        p += 1;
                        if s != 0 {
                            *v = -*v;
                        }
                    }
                }
                for k in 0..4 {
                    self.is[gr][ch][pos + k] = q[k] as i16;
                }
                pos += 4;
                if p as i64 >= end || pos >= 574 {
                    break;
                }
            }
        }
        for i in pos..576 {
            self.is[gr][ch][i] = 0;
        }
    }

    fn gainw(&mut self, i: i32) -> u32 {
        if i < -GAIN_NEG {
            self.undefined = true;
            return 0;
        }
        self.t.gain[(GAIN_NEG + i) as usize]
    }

    /// 0x24238 + 0x35058
    fn requant(&mut self, gr: usize, ch: usize) {
        let mult = if self.sfs[gr][ch] { 4 } else { 2 };
        let mut tmp = [0i16; 640];
        tmp[..576].copy_from_slice(&self.is[gr][ch][..576]);
        let mut pos = 0;
        let mut blocks: Vec<(usize, u32, usize)> = Vec::with_capacity(39);
        if self.bt[gr][ch] != 2 {
            for s in 0..22 {
                let w = SFB_L[self.sri][s];
                let i = self.gg[gr][ch] - mult * (self.sf_l[gr][ch][s] + self.pre[gr][ch] * self.t.pretab[s]);
                let m = self.gainw(i);
                blocks.push((pos, m, w));
                pos += w;
            }
        } else {
            for s in 0..13 {
                let w = SFB_S[self.sri][s];
                for win in 0..3 {
                    let i = self.gg[gr][ch] - mult * self.sf_s[gr][ch][s][win] - 8 * self.sbg[gr][ch][win];
                    let m = if i < 0 { 0 } else { self.gainw(i) };
                    blocks.push((pos, m, w));
                    pos += w;
                }
            }
        }
        let xr = &mut self.xr[gr][ch];
        for (pos, m, w) in blocks {
            for i in 0..16 * w.div_ceil(16) {
                xr[pos + i] = requant1(&self.t, tmp[pos + i] as i32, m);
            }
        }
    }

    /// 0x21ef6
    fn intensity(&mut self, gr: usize, msf: bool) {
        let sri = self.sri;
        let sf_l = self.sf_l[gr][1];
        let sf_s = self.sf_s[gr][1];
        let bt0 = self.bt[gr][0];
        let t = &self.t;
        let (lx, rx) = self.xr[gr].split_at_mut(1);
        let (l, r) = (&mut lx[0], &mut rx[0]);
        if bt0 != 2 {
            let mut end = 576;
            for s in (0..22).rev() {
                let w = SFB_L[sri][s];
                let ip = sf_l[s];
                if is_band(t, l, r, ip, end, w) {
                    if msf {
                        ms(t, &mut l[..end], &mut r[..end]);
                    }
                    return;
                }
                end -= w;
                if msf && ip == 7 {
                    ms(t, &mut l[end..end + w], &mut r[end..end + w]);
                }
            }
            return;
        }
        for win in (0..3usize).rev() {
            let mut end = 576usize;
            let mut s = 13usize;
            while s > 0 {
                s -= 1;
                let mut w = SFB_S[sri][s];
                let ip = sf_s[s][win];
                end -= (2 - win) * w;
                if is_band(t, l, r, ip, end, w) {
                    if !msf {
                        break;
                    }
                    ms(t, &mut l[end - w..end], &mut r[end - w..end]);
                    end -= (win + 1) * w;
                    while s > 0 {
                        s -= 1;
                        w = SFB_S[sri][s];
                        end -= w * (2 - win);
                        ms(t, &mut l[end - w..end], &mut r[end - w..end]);
                        end -= w * (win + 1);
                    }
                    break;
                }
                if msf && ip == 7 {
                    ms(t, &mut l[end - w..end], &mut r[end - w..end]);
                }
                end -= (win + 1) * w;
            }
        }
    }

    /// 0x2c52a (non-mixed short blocks)
    fn reorder(&mut self, gr: usize, ch: usize) {
        let mut tmp = [0i32; 576];
        tmp.copy_from_slice(&self.xr[gr][ch][..576]);
        let xr = &mut self.xr[gr][ch];
        let (mut src, mut f0) = (0, 0);
        for s in 0..13 {
            let w = SFB_S[self.sri][s];
            for win in 0..3 {
                for k in 0..w {
                    let idx = k + f0;
                    xr[(idx / 6) * 18 + idx % 6 + 6 * win] = tmp[src];
                    src += 1;
                }
            }
            f0 += w;
        }
    }

    /// 0x213b0 IMDCT + overlap, then per slot frequency inversion (0x2d2a6), dct32 (0x49c) and
    /// the window (0x316b4).
    fn imdct_synth(&mut self, gr: usize, ch: usize) {
        let mut out = [[0i32; 32]; 18];
        for sb in 0..32 {
            let x: &[i32; 18] = self.xr[gr][ch][18 * sb..18 * sb + 18].try_into().unwrap();
            let mut o = [0i32; 18];
            let mut v = [0i32; 18];
            let ov = &self.ov[ch][sb];
            match self.bt[gr][ch] {
                2 => imdct::imdct_short(x, ov, &mut o, &mut v, &self.t.imdct),
                bt => imdct::imdct_long(x, ov, &mut o, &mut v, bt, &self.t.imdct),
            }
            self.ov[ch][sb] = v;
            for k in 0..18 {
                out[k][sb] = o[k];
            }
        }
        for (s, slot) in out.iter().enumerate() {
            let mut x = [0i32; 32];
            for sb in 0..32 {
                x[sb] = if s & 1 != 0 && sb & 1 != 0 { slot[sb].wrapping_neg() } else { slot[sb] };
            }
            let mut row = [0i32; 34];
            dct32::dct32(&x, &mut row, &self.t.ck);
            let t = self.slot[ch];
            self.slot[ch] = t.wrapping_add(1);
            self.hist[ch][(t & 15) as usize] = row;
            let base = (gr * 18 + s) * 64 + ch;
            for k in 0..32 {
                let mut xs = [0i32; 16];
                for (n, xv) in xs.iter_mut().enumerate() {
                    let h = &self.hist[ch][(t.wrapping_sub(n as u32) & 15) as usize];
                    let r = if k < 16 {
                        Some(if n & 1 != 0 { 17 + k } else { k })
                    } else if k == 16 {
                        if n & 1 != 0 {
                            Some(33)
                        } else {
                            None
                        }
                    } else {
                        Some(if n & 1 != 0 { 49 - k } else { 32 - k })
                    };
                    *xv = r.map_or(0, |r| h[r]);
                }
                self.pcm[base + 2 * k] = synth_dot(&xs, &self.t.dw[k]);
            }
        }
    }
}

/// 0x35058 for one value
fn requant1(t: &Tables, x: i32, m: u32) -> i32 {
    let ax = x.unsigned_abs().min(8191) as usize;
    let tv = t.pow43[ax] as i64;
    let (th, tl) = (tv >> 16, tv & 0xffff);
    let (m5, m3) = ((m >> 16) as u16 as i16 as i64, (m & 0xffff) as i64);
    let p0 = ((th * m5) << 16) + th * m3 + tl * m5 + 2 * ((((m3 >> 1) * tl) + 0x8000) >> 16);
    let p = p0 as u32 as i32;
    let p3 = p >> 3;
    let l3 = (p3 as u32) & 0xffff;
    let h3 = p3 >> 16;
    let lo = (((l3 >> 1) + (l3 & 1)) | ((h3 as u32 & 1) << 15)) & 0xffff;
    let y = (((h3 >> 1) as u32) << 16 | lo) as i32;
    if x < 0 {
        y.wrapping_neg()
    } else {
        y
    }
}

/// 0x221b8
fn ms(t: &Tables, l: &mut [i32], r: &mut [i32]) {
    for (a, b) in l.iter_mut().zip(r.iter_mut()) {
        let (x, y) = (*a, *b);
        *a = mulsu(x.wrapping_add(y), t.ms);
        *b = mulsu(x.wrapping_sub(y), t.ms);
    }
}

/// 0x2c4a0
fn is_band(t: &Tables, l: &mut [i32], r: &mut [i32], ip: i32, end: usize, w: usize) -> bool {
    if r[end - w..end].iter().any(|&v| v != 0) {
        return true;
    }
    if ip == 7 || ip == 15 || ip == 23 || ip < 0 {
        return false;
    }
    let (kl, kr) = (t.iskl[ip as usize], t.iskr[ip as usize]);
    for i in end - w..end {
        let v = l[i];
        l[i] = mulsu(v, kl);
        r[i] = mulsu(v, kr);
    }
    false
}

/// 0x221ee
fn alias(t: &Tables, xr: &mut [i32], count: usize) {
    for i in 0..8 {
        for sb in 1..count {
            let (a, b) = (xr[18 * sb - 1 - i], xr[18 * sb + i]);
            xr[18 * sb - 1 - i] = (mulss(a, t.cs[i]).wrapping_sub(mulss(b, t.ca[i]))).wrapping_shl(1);
            xr[18 * sb + i] = (mulss(b, t.cs[i]).wrapping_add(mulss(a, t.ca[i]))).wrapping_shl(1);
        }
    }
}

/// 0x316b4 window dot product of one output sample
fn synth_dot(xs: &[i32; 16], ds: &[i32; 16]) -> i16 {
    let mut s: i64 = 0;
    for n in 0..16 {
        let (xh, xl, dh, dl) = ((xs[n] >> 16) as i64, (xs[n] & 0xffff) as i64, (ds[n] >> 16) as i64, (ds[n] & 0xffff) as i64);
        s += xh * dh + ((xh * dl + 0x8000) >> 16) + ((dh * xl + 0x8000) >> 16);
    }
    let v = ((s + 128) as u32 as i32) >> 8;
    v.clamp(-32768, 32767) as i16
}

/// A whole file as the player decodes it: frames from [`crate::mp3file`], fed through the
/// player's frame buffer (a frame cut short by the end of the file keeps the previous frame's
/// bytes behind it), one PCM block of 1152 stereo samples per frame.
pub struct Player<'a> {
    dec: Decoder,
    frames: crate::mp3file::Frames<'a>,
    data: &'a [u8],
    buf: Vec<u8>,
    /// Result of the last frame.
    pub last: DecodeResult,
    /// Index of the next frame.
    pub index: usize,
}

/// A decoded frame: its place in the file and its PCM (`None` for a rejected header).
pub type FrameOut<'a> = (crate::mp3file::FrameRef, Option<&'a [i16]>);

impl<'a> Player<'a> {
    pub fn new(music: &Image, file: &'a crate::mp3file::Mp3File<'a>) -> Result<Player<'a>> {
        Ok(Player {
            dec: Decoder::new(music)?,
            frames: file.frames(),
            data: file.data(),
            buf: vec![0; 1441 + 8],
            last: DecodeResult::Ok,
            index: 0,
        })
    }

    pub fn decoder_mut(&mut self) -> &mut Decoder {
        &mut self.dec
    }

    /// Decode the next frame: `Ok(None)` at the end; the PCM of rejected-header frames is
    /// `None` inside (the firmware writes none).
    pub fn next_frame(&mut self) -> Result<Option<FrameOut<'_>>> {
        let Some(f) = self.frames.next() else { return Ok(None) };
        self.buf[..f.avail].copy_from_slice(&self.data[f.offset..f.offset + f.avail]);
        let (r, _) = self.dec.decode_frame_status(&self.buf);
        self.last = r;
        self.index += 1;
        match r {
            DecodeResult::Ok | DecodeResult::Silence => Ok(Some((f, Some(self.dec.pcm())))),
            DecodeResult::BadHeader => Ok(Some((f, None))),
            DecodeResult::Unsupported => Err(Error::Undefined(format!("frame {} at {:#x}: mixed blocks", self.index - 1, f.offset))),
            DecodeResult::Undefined => Err(Error::Undefined(format!("frame {} at {:#x}: outside the model", self.index - 1, f.offset))),
        }
    }
}

