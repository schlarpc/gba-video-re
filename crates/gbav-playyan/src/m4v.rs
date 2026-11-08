//! MPEG-4 Part 2 Simple Profile video, as the Play-Yan video firmware decodes it.
//!
//! The bitstream grammar is ISO/IEC 14496-2 (I- and P-VOPs, 1 or 4 motion vectors, half-pel
//! motion compensation with rounding control, unrestricted motion vectors, intra DC/AC
//! prediction, H.263 and MPEG quantisation, video packets with resync markers and header
//! extension). The arithmetic is the firmware's, where it differs from the standard
//! (docs/playyan/decoders.md has the evidence for each):
//!
//! * the IDCT is the firmware's own ([`crate::idct`]);
//! * intra levels are clipped to [-2048, 2047] before dequantisation and stored clipped for AC
//!   prediction; dequantised values are 16-bit (`vmul` low half, wrapping);
//! * MPEG quantisation: intra blocks are weighted with the non-intra matrix (a firmware bug);
//!   intra coefficients are not saturated to [-2048, 2047] (16-bit saturating multiplies, then
//!   a truncating /8) and mismatch control is computed on those values;
//! * motion compensation goes through the firmware's edge window ([`Decoder`] `fw_mc16`):
//!   vectors reaching more than 16 pixels outside the picture wrap inside a 32-pixel window, and
//!   4MV vectors are clamped to keep the macroblock origin within [-15, W-1] x [-15, H-1];
//! * 4MV chroma vectors use the sign-magnitude form of ISO table 7-9;
//! * AC prediction rescaling `(P * QPpred) // QPcur` is done with a reciprocal table (read from the
//!   firmware): a truncating 16-bit multiply-high, then +-1 when the remainder is at least
//!   ceil(QPcur / 2); a quotient that truncates to 0 is never rounded to +-1;
//! * DC prediction `(pred + scale/2) // scale` uses the same reciprocal table with a one-step
//!   correction (exact for every value a valid stream produces).

use crate::bits::BitReader;
use crate::firmware::Image;
use crate::idct::Idct;
use crate::m4v_tables as t;
use gbav_common::{Error, Result};

fn undef(s: &str) -> Error {
    Error::Undefined(format!("MPEG-4: {s}"))
}

// ------------------------------------------------------------------ VLC

#[derive(Clone)]
struct Vlc {
    bits: u32,
    /// (symbol, length); length 0 = invalid code.
    lut: Vec<(u16, u8)>,
}

impl Vlc {
    fn new<C: Copy + Into<u32>>(codes: &[(C, C)], skip: &[usize]) -> Vlc {
        let bits = codes.iter().map(|&(_, l)| l.into()).max().unwrap_or(1);
        let mut lut = vec![(0u16, 0u8); 1 << bits];
        for (sym, &(c, l)) in codes.iter().enumerate() {
            let (c, l): (u32, u32) = (c.into(), l.into());
            if l == 0 || skip.contains(&sym) {
                continue;
            }
            let sh = bits - l;
            for k in 0..(1u32 << sh) {
                lut[((c << sh) | k) as usize] = (sym as u16, l as u8);
            }
        }
        Vlc { bits, lut }
    }
    #[inline]
    fn read(&self, br: &mut BitReader) -> Result<usize> {
        let (s, l) = self.lut[br.peek(self.bits) as usize];
        if l == 0 {
            return Err(undef("invalid VLC code"));
        }
        br.skip(l as u32);
        Ok(s as usize)
    }
}

struct RlTable {
    vlc: Vlc,
    run: &'static [u8; 102],
    level: &'static [u8; 102],
    last_start: usize,
    max_level: [[u8; 64]; 2],
    max_run: [[u8; 64]; 2],
}

impl RlTable {
    fn new(codes: &[(u16, u16); 103], run: &'static [u8; 102], level: &'static [u8; 102], last_start: usize) -> RlTable {
        let mut max_level = [[0u8; 64]; 2];
        let mut max_run = [[0u8; 64]; 2];
        for i in 0..102 {
            let l = (i >= last_start) as usize;
            let (r, lv) = (run[i] as usize, level[i] as usize);
            max_level[l][r] = max_level[l][r].max(lv as u8);
            max_run[l][lv] = max_run[l][lv].max(r as u8);
        }
        RlTable { vlc: Vlc::new(codes, &[]), run, level, last_start, max_level, max_run }
    }
}

struct Tables {
    intra_mcbpc: Vlc,
    inter_mcbpc: Vlc,
    cbpy: Vlc,
    mv: Vlc,
    dc_lum: Vlc,
    dc_chrom: Vlc,
    intra: RlTable,
    inter: RlTable,
}

impl Tables {
    fn new() -> Tables {
        Tables {
            intra_mcbpc: Vlc::new(&t::INTRA_MCBPC, &[]),
            // FFmpeg order (bit 2 intra, bit 3 dquant, bit 4 4MV); 20 = stuffing; 21..27 are H.263+ only
            inter_mcbpc: Vlc::new(&t::INTER_MCBPC, &[21, 22, 23, 24, 25, 26, 27]),
            cbpy: Vlc::new(&t::CBPY, &[]),
            mv: Vlc::new(&t::MVTAB, &[]),
            dc_lum: Vlc::new(&t::DC_LUM, &[]),
            dc_chrom: Vlc::new(&t::DC_CHROM, &[]),
            intra: RlTable::new(&t::INTRA_VLC, &t::INTRA_RUN, &t::INTRA_LEVEL, 67),
            inter: RlTable::new(&t::INTER_VLC, &t::INTER_RUN, &t::INTER_LEVEL, 58),
        }
    }
}

// ------------------------------------------------------------------ headers

/// The video object layer header fields the decoder uses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Vol {
    pub verid: u32,
    pub width: u32,
    pub height: u32,
    pub time_resolution: u32,
    pub time_inc_bits: u32,
    pub fixed_vop_rate: bool,
    pub fixed_time_inc: u32,
    /// 0 = H.263 quantisation, 1 = MPEG quantisation.
    pub quant_type: u32,
    /// Natural (raster) order.
    pub intra_matrix: [u8; 64],
    pub inter_matrix: [u8; 64],
    pub resync_marker_disable: bool,
    pub data_partitioned: bool,
    pub reversible_vlc: bool,
}

fn read_matrix(br: &mut BitReader, m: &mut [u8; 64]) {
    let mut last = 0u8;
    let mut i = 0;
    while i < 64 {
        let v = br.get(8) as u8;
        if v == 0 {
            break;
        }
        last = v;
        m[t::ZIGZAG[i] as usize] = v;
        i += 1;
    }
    while i < 64 {
        m[t::ZIGZAG[i] as usize] = last;
        i += 1;
    }
}

impl Vol {
    /// Parse a VOL header starting right after its start code (00 00 01 2x).
    pub fn parse(br: &mut BitReader) -> Result<Vol> {
        br.skip(1); // random_accessible_vol
        br.skip(8); // video_object_type_indication
        let verid = if br.bit() {
            let v = br.get(4);
            br.skip(3);
            v
        } else {
            1
        };
        if br.get(4) == 15 {
            br.skip(16);
        }
        if br.bit() {
            // vol_control_parameters
            br.skip(3);
            if br.bit() {
                br.skip(79);
            }
        }
        if br.get(2) != 0 {
            return Err(undef("non-rectangular VOL shape"));
        }
        br.skip(1);
        let time_resolution = br.get(16);
        br.skip(1);
        let time_inc_bits = (32 - (time_resolution.max(2) - 1).leading_zeros()).max(1);
        let fixed_vop_rate = br.bit();
        let fixed_time_inc = if fixed_vop_rate { br.get(time_inc_bits) } else { 0 };
        br.skip(1);
        let width = br.get(13);
        br.skip(1);
        let height = br.get(13);
        br.skip(1);
        if br.bit() {
            return Err(undef("interlaced"));
        }
        br.skip(1); // obmc_disable
        let sprite = if verid == 1 { br.get(1) } else { br.get(2) };
        if sprite != 0 {
            return Err(undef("sprites"));
        }
        if br.bit() {
            return Err(undef("not_8_bit"));
        }
        let quant_type = br.get(1);
        let mut intra_matrix = t::DEFAULT_INTRA_MATRIX;
        let mut inter_matrix = t::DEFAULT_INTER_MATRIX;
        if quant_type == 1 {
            if br.bit() {
                read_matrix(br, &mut intra_matrix);
            }
            if br.bit() {
                read_matrix(br, &mut inter_matrix);
            }
        }
        if verid != 1 && br.bit() {
            return Err(undef("quarter_sample"));
        }
        if !br.bit() {
            return Err(undef("complexity estimation"));
        }
        let resync_marker_disable = br.bit();
        let data_partitioned = br.bit();
        let reversible_vlc = if data_partitioned { br.bit() } else { false };
        if data_partitioned {
            return Err(undef("data partitioning"));
        }
        if verid != 1 {
            if br.bit() {
                return Err(undef("newpred"));
            }
            br.skip(1); // reduced_resolution_vop_enable
        }
        if br.bit() {
            return Err(undef("scalability"));
        }
        if width == 0 || height == 0 || width > 1024 || height > 1024 {
            return Err(undef("picture size"));
        }
        Ok(Vol {
            verid,
            width,
            height,
            time_resolution,
            time_inc_bits,
            fixed_vop_rate,
            fixed_time_inc,
            quant_type,
            intra_matrix,
            inter_matrix,
            resync_marker_disable,
            data_partitioned,
            reversible_vlc,
        })
    }
}

/// Find the first VOL header in `data` (VOS/VO headers may precede it).
pub fn find_vol(data: &[u8]) -> Option<Result<Vol>> {
    let mut i = 0;
    while i + 4 <= data.len() {
        if data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1 && (0x20..=0x2f).contains(&data[i + 3]) {
            let mut br = BitReader::at(data, (i + 4) * 8);
            return Some(Vol::parse(&mut br));
        }
        i += 1;
    }
    None
}

// ------------------------------------------------------------------ pictures

/// A decoded 4:2:0 picture, MB-aligned dimensions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Picture {
    /// Displayed size (VOL width x height).
    pub width: u32,
    pub height: u32,
    /// Plane strides (MB-aligned width, half for chroma).
    pub stride: usize,
    pub rows: usize,
    pub y: Vec<u8>,
    /// Cb (ISO's U), Cr (V) in ISO order.
    pub cb: Vec<u8>,
    pub cr: Vec<u8>,
}

impl Picture {
    fn new(w: u32, h: u32) -> Picture {
        let stride = ((w as usize) + 15) & !15;
        let rows = ((h as usize) + 15) & !15;
        Picture {
            width: w,
            height: h,
            stride,
            rows,
            y: vec![0; stride * rows],
            cb: vec![128; stride * rows / 4],
            cr: vec![128; stride * rows / 4],
        }
    }
    /// Planar YUV 4:2:0 (I420) of the displayed area.
    pub fn i420(&self) -> Vec<u8> {
        let (w, h) = (self.width as usize, self.height as usize);
        let mut v = Vec::with_capacity(w * h * 3 / 2);
        for y in 0..h {
            v.extend_from_slice(&self.y[y * self.stride..y * self.stride + w]);
        }
        for p in [&self.cb, &self.cr] {
            for y in 0..h / 2 {
                v.extend_from_slice(&p[y * self.stride / 2..y * self.stride / 2 + w / 2]);
            }
        }
        v
    }
}

/// VOP coding type.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VopType {
    I,
    P,
}

/// What one media object produced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decoded {
    /// A coded VOP was decoded; [`Decoder::picture`] holds it.
    Frame(VopType),
    /// `vop_coded = 0`: the previous picture is shown again.
    NotCoded,
    /// Only headers (VOS/VOL/GOV/user data); no picture.
    Headers,
}

// ------------------------------------------------------------------ decoder

const NONE: u32 = u32::MAX;

#[derive(Clone, Copy, Default)]
struct MbInfo {
    slice: u32,
    intra: bool,
    qp: u8,
    mv: [(i32, i32); 4],
}

/// Per 8x8 block prediction state for intra DC/AC prediction.
#[derive(Clone, Copy)]
struct BlkPred {
    dc: i32,
    /// QF[0][1..7] (top row), QF[1..7][0] (left column): clipped quantised levels.
    row: [i16; 8],
    col: [i16; 8],
}

impl Default for BlkPred {
    fn default() -> Self {
        BlkPred { dc: 1024, row: [0; 8], col: [0; 8] }
    }
}

/// Firmware tables the video decoder needs.
#[derive(Clone)]
pub struct FwTables {
    pub idct: Idct,
    /// round(65536 / q), q = 0..63 (q = 1: 65535): DC and AC prediction divisions.
    pub recip: [i32; 64],
    /// The 64 bytes that follow each scan table in the image (zigzag, alternate-horizontal,
    /// alternate-vertical): the firmware's coefficient loop (py_fw0 0x20484) does not bound the
    /// scan index of a run/level event, so an event past index 63 is stored at the raster
    /// position these bytes give (see `Decoder::read_coeffs`).
    pub scan_ext: [[u8; 64]; 3],
    /// Such a store past the sixth block buffer has no visible effect on mi_fw0 (63/63 frames of
    /// a crafted file whose events reach there) and fup_sec0 (same code and layout); on py_fw0
    /// no test reached it, so there it is `Error::Undefined`.
    pub spill_past_harmless: bool,
}

const RECIP_MD5: &str = "09e73bfc2bb7480d3e029a88375cc622";

impl FwTables {
    pub fn from_firmware(img: &Image) -> Result<FwTables> {
        use crate::ImageId::*;
        let off = match img.id {
            PyFw0 => 0x2c61c,
            MiFw0 => 0x41df0,
            FupSec0 => 0x42168,
            _ => return Err(Error::Invalid(format!("{} is not a video image", img.id.name()))),
        };
        let r = img.u16_table(off, 64, RECIP_MD5)?;
        let mut recip = [0i32; 64];
        for i in 0..64 {
            recip[i] = r[i] as i32;
        }
        // scan tables (ISO, checked) and the bytes after them
        let (scans, zz_ext_md5) = match img.id {
            PyFw0 => ([0x29a04, 0x29a7c, 0x29abc], "aaf404a8b77ea37ddc3f90edf920968a"),
            MiFw0 => ([0x3f164, 0x3f230, 0x3f270], "b54d9c4ec849b3ecb0164bf0c5c4b5d2"),
            _ => ([0x3f4dc, 0x3f5a8, 0x3f5e8], "a3c2b9be093373ae65e3fdf38c82f966"),
        };
        let iso: [&[u8; 64]; 3] = [&t::ZIGZAG, &t::ALT_HORIZ, &t::ALT_VERT];
        let ext_md5 = [zz_ext_md5, "5d27a7bac2e9f47d7e54affda59f6162", "f4317ffadf1d7feb9fb58cd4a403d2c3"];
        let mut scan_ext = [[0u8; 64]; 3];
        for k in 0..3 {
            if img.table(scans[k], 64, "")? != &iso[k][..] {
                return Err(Error::Format(format!("{}: scan table {k} at 0x{:x} is not the ISO one", img.id.name(), scans[k])));
            }
            scan_ext[k].copy_from_slice(img.table(scans[k] + 64, 64, ext_md5[k])?);
        }
        Ok(FwTables { idct: Idct::from_firmware(img)?, recip, scan_ext, spill_past_harmless: img.id != PyFw0 })
    }
}

/// The MPEG-4 decoder.
pub struct Decoder {
    tab: Tables,
    fw: FwTables,
    vol: Option<Vol>,
    mbw: usize,
    mbh: usize,
    cur: Picture,
    refp: Picture,
    mbs: Vec<MbInfo>,
    /// Luma blocks (2mbw x 2mbh), then Cb and Cr (mbw x mbh each).
    pred: [Vec<BlkPred>; 3],
    have_ref: bool,
    // VOP state
    qp: i32,
    dc_thr: i32,
    /// Coefficients stored into later blocks of the current macroblock by out-of-range scan
    /// indices (the firmware's six block buffers are contiguous).
    spill: [[i32; 64]; 6],
    /// The firmware's decode-error flag (bit reader +12): set by a coefficient run past
    /// index 63; the VOP stops after the current macroblock.
    fw_error: bool,
    rounding: i32,
    fcode: u32,
    slice: u32,
    /// Count of decoded VOPs.
    pub vops: u64,
}

#[inline(always)]
fn s16(x: i32) -> i32 {
    x as i16 as i32
}

fn sat16(x: i32) -> i32 {
    x.clamp(-32768, 32767)
}

fn sign(x: i32) -> i32 {
    (x > 0) as i32 - (x < 0) as i32
}

impl Decoder {
    /// A decoder for a stream whose decoder configuration (VOL) is `config` (may be empty when
    /// the VOL is in-band, as in Nintendo's ASF files).
    pub fn new(fw: &Image, config: &[u8]) -> Result<Decoder> {
        Self::with_tables(FwTables::from_firmware(fw)?, config)
    }

    pub fn with_tables(fw: FwTables, config: &[u8]) -> Result<Decoder> {
        let mut d = Decoder {
            tab: Tables::new(),
            fw,
            vol: None,
            mbw: 0,
            mbh: 0,
            cur: Picture::new(16, 16),
            refp: Picture::new(16, 16),
            mbs: Vec::new(),
            pred: [Vec::new(), Vec::new(), Vec::new()],
            have_ref: false,
            qp: 1,
            dc_thr: 0,
            spill: [[0; 64]; 6],
            fw_error: false,
            rounding: 0,
            fcode: 1,
            slice: 0,
            vops: 0,
        };
        if !config.is_empty() {
            d.decode(config)?;
        }
        Ok(d)
    }

    pub fn vol(&self) -> Option<&Vol> {
        self.vol.as_ref()
    }

    /// The last decoded (or repeated) picture.
    pub fn picture(&self) -> &Picture {
        &self.refp
    }

    fn set_vol(&mut self, vol: Vol) {
        if self.vol.as_ref().map(|v| (v.width, v.height)) != Some((vol.width, vol.height)) {
            self.cur = Picture::new(vol.width, vol.height);
            self.refp = Picture::new(vol.width, vol.height);
            self.mbw = self.cur.stride / 16;
            self.mbh = self.cur.rows / 16;
            self.mbs = vec![MbInfo::default(); self.mbw * self.mbh];
            self.pred = [
                vec![BlkPred::default(); 4 * self.mbw * self.mbh],
                vec![BlkPred::default(); self.mbw * self.mbh],
                vec![BlkPred::default(); self.mbw * self.mbh],
            ];
            self.have_ref = false;
        }
        self.vol = Some(vol);
    }

    /// Decode one media object (normally one VOP, possibly preceded by headers).
    pub fn decode(&mut self, data: &[u8]) -> Result<Decoded> {
        let mut res = Decoded::Headers;
        let mut i = 0;
        while i + 4 <= data.len() {
            if !(data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1) {
                i += 1;
                continue;
            }
            let code = data[i + 3];
            let mut br = BitReader::at(data, (i + 4) * 8);
            match code {
                0x20..=0x2f => {
                    let v = Vol::parse(&mut br)?;
                    self.set_vol(v);
                }
                0xb6 => {
                    res = self.vop(&mut br)?;
                    return Ok(res);
                }
                _ => {}
            }
            i += 4;
        }
        Ok(res)
    }

    fn vop(&mut self, br: &mut BitReader) -> Result<Decoded> {
        let vol = self.vol.clone().ok_or_else(|| undef("VOP before VOL"))?;
        let ty = match br.get(2) {
            0 => VopType::I,
            1 => VopType::P,
            _ => return Err(undef("B or S VOP in a Simple Profile stream")),
        };
        while br.bit() {}
        br.skip(1);
        br.skip(vol.time_inc_bits);
        br.skip(1);
        if !br.bit() {
            return Ok(Decoded::NotCoded);
        }
        if ty == VopType::P {
            if !self.have_ref {
                return Err(undef("P-VOP without a reference"));
            }
            self.rounding = br.get(1) as i32;
        } else {
            self.rounding = 0;
        }
        self.dc_thr = t::DC_THRESHOLD[br.get(3) as usize] as i32;
        self.qp = br.get(5) as i32;
        if self.qp == 0 {
            return Err(undef("vop_quant 0"));
        }
        if ty == VopType::P {
            self.fcode = br.get(3);
            if self.fcode == 0 {
                return Err(undef("vop_fcode_forward 0"));
            }
        }
        for m in self.mbs.iter_mut() {
            m.slice = NONE;
        }
        self.slice = 0;
        let nmb = self.mbw * self.mbh;
        let mbnum_bits = usize::BITS - (nmb - 1).max(1).leading_zeros();
        let mut mb = 0usize;
        while mb < nmb {
            if !vol.resync_marker_disable && mb > 0 && self.resync_next(br, ty) {
                // video packet header: byte-aligning stuffing, resync marker
                let stuff = 8 - (br.pos() & 7) as u32;
                br.skip(stuff);
                let marker_len = if ty == VopType::I { 17 } else { 16 + self.fcode };
                br.skip(marker_len);
                let n = br.get(mbnum_bits) as usize;
                if n >= nmb || n < mb {
                    return Err(undef("bad macroblock_number"));
                }
                // macroblocks skipped by the packet header stay unreconstructed (error case)
                mb = n;
                self.qp = br.get(5) as i32;
                if self.qp == 0 {
                    return Err(undef("quant_scale 0"));
                }
                if br.bit() {
                    while br.bit() {}
                    br.skip(1);
                    br.skip(vol.time_inc_bits);
                    br.skip(1);
                    br.skip(2);
                    br.skip(3);
                    if ty == VopType::P {
                        br.skip(3);
                    }
                }
                self.slice += 1;
            }
            let (mx, my) = (mb % self.mbw, mb / self.mbw);
            let r = match ty {
                VopType::I => self.mb_intra_vop(br, mx, my),
                VopType::P => self.mb_p_vop(br, mx, my),
            };
            if let Err(Error::Undefined(e)) = r {
                return Err(Error::Undefined(format!("{e} (MB {mx},{my}, bit {})", br.pos())));
            }
            r?;
            if br.bits_left() < 0 {
                return Err(undef("bitstream overrun"));
            }
            mb += 1;
            if std::mem::take(&mut self.fw_error) {
                // 0x4a20: the firmware abandons the VOP; the remaining macroblocks keep what
                // the picture buffer held
                break;
            }
        }
        std::mem::swap(&mut self.cur, &mut self.refp);
        self.have_ref = true;
        self.vops += 1;
        Ok(Decoded::Frame(ty))
    }

    /// Is a (byte-aligning stuffing +) resync marker next?
    fn resync_next(&self, br: &BitReader, ty: VopType) -> bool {
        let p = br.pos();
        let n = 8 - (p & 7) as u32; // stuffing: 0 then n-1 ones (n = 1..8)
        let stuff = br.peek(n);
        if stuff != (1 << (n - 1)) - 1 {
            return false;
        }
        let mut q = br.clone();
        q.skip(n);
        let len = if ty == VopType::I { 17 } else { 16 + self.fcode };
        q.peek(len) == 1 && {
            // consume the stuffing only when the marker is there
            true
        }
    }

    fn mb_intra_vop(&mut self, br: &mut BitReader, mx: usize, my: usize) -> Result<()> {
        // stuffing is consumed before the resync check of the *next* MB in practice; handle here
        let c = loop {
            let c = self.tab.intra_mcbpc.read(br)?;
            if c != 8 {
                break c;
            }
        };
        let cbpc = (c & 3) as u32;
        let mb_type = 3 + (c >> 2);
        self.intra_mb(br, mx, my, cbpc, mb_type == 4)
    }

    /// dquant. The firmware does not clamp (0x64f2): a QP outside 1..31 sets its error flag and
    /// the macroblock is decoded with tables indexed out of range.
    fn update_qp(&mut self, br: &mut BitReader) -> Result<()> {
        const DQ: [i32; 4] = [-1, -2, 1, 2];
        self.qp += DQ[br.get(2) as usize];
        if !(1..=31).contains(&self.qp) {
            return Err(undef("dquant takes the QP outside 1..31"));
        }
        Ok(())
    }

    fn intra_mb(&mut self, br: &mut BitReader, mx: usize, my: usize, cbpc: u32, dq: bool) -> Result<()> {
        let ac_pred = br.bit();
        let cbpy = self.tab.cbpy.read(br)? as u32;
        if dq {
            self.update_qp(br)?;
        }
        let cbp = cbpy << 2 | cbpc;
        let mbi = my * self.mbw + mx;
        self.mbs[mbi] = MbInfo { slice: self.slice, intra: true, qp: self.qp as u8, mv: [(0, 0); 4] };
        // intra_dc_vlc_thr is compared with the QP after this macroblock's dquant (0x4b04);
        // above it the firmware reads no DC at all (see intra_block)
        let use_dc_vlc = self.qp < self.dc_thr;
        self.spill = [[0; 64]; 6];
        for n in 0..6 {
            let coded = cbp >> (5 - n) & 1 != 0;
            let mut blk = [0i16; 64];
            self.intra_block(br, mx, my, n, coded, ac_pred, use_dc_vlc, &mut blk)?;
            self.fw.idct.transform(&mut blk);
            self.put_block(mx, my, n, &blk, None);
        }
        Ok(())
    }

    /// Location of block `n` of MB (mx,my) in the prediction arrays: (component, index, bx, by).
    fn blk_loc(&self, mx: usize, my: usize, n: usize) -> (usize, usize, usize) {
        if n < 4 {
            let (bx, by) = (2 * mx + (n & 1), 2 * my + (n >> 1));
            (0, bx, by)
        } else {
            (n - 3, mx, my)
        }
    }

    /// Prediction state of the block at (comp, bx, by), if usable for intra prediction from
    /// the current macroblock (same VOP, same video packet, intra).
    fn pred_at(&self, comp: usize, bx: isize, by: isize) -> Option<BlkPred> {
        let (w, h) = if comp == 0 { (2 * self.mbw, 2 * self.mbh) } else { (self.mbw, self.mbh) };
        if bx < 0 || by < 0 || bx as usize >= w || by as usize >= h {
            return None;
        }
        let (bx, by) = (bx as usize, by as usize);
        let mb = if comp == 0 { (by / 2) * self.mbw + bx / 2 } else { by * self.mbw + bx };
        let m = &self.mbs[mb];
        if m.slice != self.slice || !m.intra {
            return None;
        }
        Some(self.pred[comp][by * w + bx])
    }

    fn mb_qp_at(&self, comp: usize, bx: usize, by: usize) -> i32 {
        let mb = if comp == 0 { (by / 2) * self.mbw + bx / 2 } else { by * self.mbw + bx };
        self.mbs[mb].qp as i32
    }

    /// Firmware 0x26f0e: a / b via the reciprocal table with a one-step upward correction.
    fn fw_div(&self, a: i32, b: i32) -> i32 {
        let mut q = (a * self.fw.recip[b as usize]) >> 16;
        if b * q + b <= a {
            q += 1;
        }
        q
    }

    /// Firmware AC prediction rescale (0x2589e / 0x25926): (p * qp) // qc.
    fn fw_ac_rescale(&self, p: i32, qp: i32, qc: i32) -> i32 {
        let a = s16(p * qp);
        let r = self.fw.recip[qc as usize];
        // vmulhdt.su: truncate toward zero
        let prod = a as i64 * r as i64;
        let mut q = (if prod < 0 { -((-prod) >> 16) } else { prod >> 16 }) as i32;
        q = s16(q);
        let rem = s16(a - s16(q * qc));
        if rem.abs() >= qc - (qc >> 1) {
            q += sign(q);
        }
        s16(q)
    }

    #[allow(clippy::too_many_arguments)]
    fn intra_block(
        &mut self,
        br: &mut BitReader,
        mx: usize,
        my: usize,
        n: usize,
        coded: bool,
        ac_pred: bool,
        use_dc_vlc: bool,
        blk: &mut [i16; 64],
    ) -> Result<()> {
        let (comp, bx, by) = self.blk_loc(mx, my, n);
        let (ibx, iby) = (bx as isize, by as isize);
        let pa = self.pred_at(comp, ibx - 1, iby);
        let pb = self.pred_at(comp, ibx - 1, iby - 1);
        let pc = self.pred_at(comp, ibx, iby - 1);
        let (a, b, c) = (pa.map_or(1024, |p| p.dc), pb.map_or(1024, |p| p.dc), pc.map_or(1024, |p| p.dc));
        // direction: true = predict from above (C), false = from the left (A)
        let top = (a - b).abs() < (b - c).abs();
        let pred_dc = if top { c } else { a };
        let scale = if comp == 0 { t::Y_DC_SCALE[self.qp as usize] } else { t::C_DC_SCALE[self.qp as usize] } as i32;

        // the block buffer: zero, plus anything an earlier block of this macroblock spilled
        let mut lv = self.spill[n];
        if use_dc_vlc {
            let size = if comp == 0 { self.tab.dc_lum.read(br)? } else { self.tab.dc_chrom.read(br)? } as u32;
            let mut d = 0i32;
            if size > 0 {
                let v = br.get(size) as i32;
                d = if v >> (size - 1) != 0 { v } else { v - (1 << size) + 1 };
                if size > 8 {
                    br.skip(1);
                }
            }
            lv[0] = d;
        }
        // Firmware deviation: when the DC is coded through the TCOEF table (QP >= threshold)
        // the firmware still reads the block's events from scan index 1 (0x54b4 always starts
        // there), so the DC event lands on index 1 and every later one a place further on;
        // the DC level stays whatever the buffer held (zero unless spilled into).
        let sk = if ac_pred {
            if top {
                1
            } else {
                2
            }
        } else {
            0
        };
        if coded {
            self.read_coeffs(br, true, 1, sk, n, &mut lv)?;
        }
        // AC prediction (quantised domain), into the level array (16-bit)
        if ac_pred {
            let qc = self.qp;
            if top {
                if let Some(p) = pc {
                    let qpp = self.mb_qp_at(comp, bx, by - 1);
                    let scaled = qpp != 0 && qpp != qc;
                    for j in 1..8 {
                        let v = if scaled { self.fw_ac_rescale(p.row[j] as i32, qpp, qc) } else { p.row[j] as i32 };
                        lv[j] = s16(lv[j] + v);
                    }
                }
            } else if let Some(p) = pa {
                let qpp = self.mb_qp_at(comp, bx - 1, by);
                let scaled = qpp != 0 && qpp != qc;
                for j in 1..8 {
                    let v = if scaled { self.fw_ac_rescale(p.col[j] as i32, qpp, qc) } else { p.col[j] as i32 };
                    lv[8 * j] = s16(lv[8 * j] + v);
                }
            }
        }
        // DC prediction
        lv[0] = s16(lv[0] + self.fw_div(pred_dc + (scale >> 1), scale));
        // clip levels to [-2048, 2047] (stored for AC prediction)
        let mut cl = [0i32; 64];
        for j in 0..64 {
            cl[j] = lv[j].clamp(-2048, 2047);
        }
        // dequantise
        let q = self.qp;
        let mut f = [0i32; 64];
        f[0] = s16(cl[0] * scale);
        let vol = self.vol.as_ref().unwrap();
        if vol.quant_type == 0 {
            let qadd = (q - 1) | 1;
            for j in 1..64 {
                let l = cl[j];
                f[j] = s16(l * 2 * q + sign(l) * qadd);
            }
        } else {
            for j in 1..64 {
                // firmware deviation: intra blocks are weighted with the *non-intra* matrix
                // (checked with default and custom matrices against the real firmware)
                let x = sat16(sat16(cl[j] * q) * vol.inter_matrix[j] as i32);
                f[j] = x / 8; // vmulhdt.su #8192: truncate toward zero
            }
            let sum: i32 = f.iter().sum();
            if sum & 1 == 0 {
                f[63] = s16(f[63] + if f[63] & 1 != 0 { -1 } else { 1 });
            }
        }
        // store prediction state
        // stored unclipped: the firmware keeps the level and its scale, so a rounded-up DC
        // above 2047 predicts as such (seen with DCs coded through the TCOEF table)
        let dcq = cl[0] * scale;
        let w = if comp == 0 { 2 * self.mbw } else { self.mbw };
        let mut st = BlkPred { dc: dcq, row: [0; 8], col: [0; 8] };
        for j in 1..8 {
            st.row[j] = cl[j] as i16;
            st.col[j] = cl[8 * j] as i16;
        }
        self.pred[comp][by * w + bx] = st;
        for j in 0..64 {
            blk[j] = f[j] as i16;
        }
        Ok(())
    }

    /// TCOEF run/level decoding into `lv` (raster positions via scan `sk`: 0 zigzag,
    /// 1 alternate-horizontal, 2 alternate-vertical), from scan index `start`, for block `n`.
    ///
    /// The firmware's loop (py_fw0 0x20484, escapes in 0x54b4) checks the index only after
    /// storing: a table event at index 64 or more is stored at the raster position read from the
    /// bytes after the scan table (`FwTables::scan_ext`), which can name a later block of the
    /// macroblock (the six 64-coefficient buffers are contiguous); a non-last table event at
    /// index 63 or more, or an escape past 63 (not stored), sets the decode-error flag and ends
    /// the block. Valid streams never get there except through the TCOEF-coded DC (intra_block).
    fn read_coeffs(&mut self, br: &mut BitReader, intra: bool, start: usize, sk: usize, n: usize, lv: &mut [i32; 64]) -> Result<()> {
        let scan: &[u8; 64] = [&t::ZIGZAG, &t::ALT_HORIZ, &t::ALT_VERT][sk];
        let mut i = start as i32 - 1;
        loop {
            let rl = if intra { &self.tab.intra } else { &self.tab.inter };
            let idx = rl.vlc.read(br)?;
            let (last, run, level, esc);
            if idx == 102 {
                esc = true;
                if !br.bit() {
                    let k = rl.vlc.read(br)?;
                    if k == 102 {
                        return Err(undef("escape in escape"));
                    }
                    let l = (k >= rl.last_start) as usize;
                    run = rl.run[k] as i32;
                    let lv0 = rl.level[k] as i32 + rl.max_level[l][run as usize] as i32;
                    last = l == 1;
                    level = if br.bit() { -lv0 } else { lv0 };
                } else if !br.bit() {
                    let k = rl.vlc.read(br)?;
                    if k == 102 {
                        return Err(undef("escape in escape"));
                    }
                    let l = (k >= rl.last_start) as usize;
                    let lv0 = rl.level[k] as i32;
                    run = rl.run[k] as i32 + rl.max_run[l][lv0 as usize] as i32 + 1;
                    last = l == 1;
                    level = if br.bit() { -lv0 } else { lv0 };
                } else {
                    last = br.bit();
                    run = br.get(6) as i32;
                    br.skip(1);
                    let v = br.get(12) as i32;
                    level = if v & 0x800 != 0 { v - 4096 } else { v };
                    br.skip(1);
                }
            } else {
                esc = false;
                last = idx >= rl.last_start;
                run = rl.run[idx] as i32;
                let l0 = rl.level[idx] as i32;
                level = if br.bit() { -l0 } else { l0 };
            }
            i += run + 1;
            if i > 63 {
                if !intra {
                    return Err(undef("coefficient index beyond 63"));
                }
                if esc {
                    self.fw_error = true;
                    return Ok(());
                }
                let pos = self.fw.scan_ext[sk][(i - 64) as usize] as usize;
                let m = n + pos / 64;
                if m >= 6 {
                    if !self.fw.spill_past_harmless {
                        return Err(undef("coefficient stored past the macroblock's block buffers"));
                    }
                } else if m == n {
                    lv[pos] = s16(level);
                } else {
                    self.spill[m][pos % 64] = s16(level);
                }
            } else {
                lv[scan[i as usize] as usize] = s16(level);
            }
            if last {
                return Ok(());
            }
            if i >= 63 && !esc {
                self.fw_error = true;
                return Ok(());
            }
        }
    }

    /// Write a reconstructed 8x8 block: intra (pred = None) clamps the IDCT output; inter adds
    /// the prediction first.
    fn put_block(&mut self, mx: usize, my: usize, n: usize, res: &[i16; 64], pred: Option<&[u8; 64]>) {
        let (plane, stride, x0, y0) = match n {
            0..=3 => (&mut self.cur.y, self.cur.stride, mx * 16 + (n & 1) * 8, my * 16 + (n >> 1) * 8),
            4 => (&mut self.cur.cb, self.cur.stride / 2, mx * 8, my * 8),
            _ => (&mut self.cur.cr, self.cur.stride / 2, mx * 8, my * 8),
        };
        for y in 0..8 {
            for x in 0..8 {
                let mut v = res[8 * y + x] as i32;
                if let Some(p) = pred {
                    v = s16(v + p[8 * y + x] as i32);
                }
                plane[(y0 + y) * stride + x0 + x] = v.clamp(0, 255) as u8;
            }
        }
    }

    // ---------------------------------------------------------------- P-VOP

    fn mb_p_vop(&mut self, br: &mut BitReader, mx: usize, my: usize) -> Result<()> {
        let mbi = my * self.mbw + mx;
        let c = loop {
            if br.bit() {
                // not coded: copy from the reference with MV 0
                self.mbs[mbi] = MbInfo { slice: self.slice, intra: false, qp: self.qp as u8, mv: [(0, 0); 4] };
                self.inter_recon(mx, my, &[(0, 0); 4], false, 0, None)?;
                return Ok(());
            }
            let c = self.tab.inter_mcbpc.read(br)?;
            if c != 20 {
                break c;
            }
        };
        // FFmpeg's index order of the H.263 MCBPC table: bit 2 = intra, bit 3 = dquant, bit 4 = 4MV
        let cbpc = (c & 3) as u32;
        let (intra, dq, four) = (c & 4 != 0, c & 8 != 0, c & 16 != 0);
        if intra {
            return self.intra_mb(br, mx, my, cbpc, dq);
        }
        let cbpy = self.tab.cbpy.read(br)? as u32 ^ 15;
        if dq {
            self.update_qp(br)?;
        }
        let cbp = cbpy << 2 | cbpc;
        self.mbs[mbi] = MbInfo { slice: self.slice, intra: false, qp: self.qp as u8, mv: [(0, 0); 4] };
        let mut mv = [(0i32, 0i32); 4];
        if four {
            for k in 0..4 {
                let (px, py) = self.mv_pred(mx, my, k);
                let x = self.read_mv(br, px)?;
                let y = self.read_mv(br, py)?;
                mv[k] = (x, y);
                self.mbs[mbi].mv[k] = (x, y);
            }
        } else {
            let (px, py) = self.mv_pred(mx, my, 0);
            let x = self.read_mv(br, px)?;
            let y = self.read_mv(br, py)?;
            mv = [(x, y); 4];
            self.mbs[mbi].mv = mv;
        }
        self.inter_recon(mx, my, &mv, four, cbp, Some(br))
    }

    fn read_mv(&self, br: &mut BitReader, pred: i32) -> Result<i32> {
        let code = self.tab.mv.read(br)? as i32;
        if code == 0 {
            return Ok(pred);
        }
        let neg = br.bit();
        let shift = self.fcode - 1;
        let mut val = code;
        if shift > 0 {
            val = ((val - 1) << shift) | br.get(shift) as i32;
            val += 1;
        }
        if neg {
            val = -val;
        }
        val += pred;
        // modulo: sign-extend to 5 + fcode bits
        let b = 5 + self.fcode;
        Ok((val << (32 - b)) >> (32 - b))
    }

    /// Candidate motion vector of block `blk` (0..3) of MB (mx,my) relative block grid offset.
    fn mv_cand(&self, mx: usize, my: usize, bx: isize, by: isize) -> Option<(i32, i32)> {
        // bx, by: luma 8x8 block coordinates
        if bx < 0 || by < 0 || bx as usize >= 2 * self.mbw || by as usize >= 2 * self.mbh {
            return None;
        }
        let (bx, by) = (bx as usize, by as usize);
        let mb = (by / 2) * self.mbw + bx / 2;
        let cur = my * self.mbw + mx;
        let m = &self.mbs[mb];
        if mb > cur || m.slice != self.slice {
            return None;
        }
        Some(m.mv[(by & 1) * 2 + (bx & 1)])
    }

    fn mv_pred(&self, mx: usize, my: usize, k: usize) -> (i32, i32) {
        const OFF: [isize; 4] = [2, 1, 1, -1];
        let bx = (2 * mx + (k & 1)) as isize;
        let by = (2 * my + (k >> 1)) as isize;
        let cands = [
            self.mv_cand(mx, my, bx - 1, by),
            self.mv_cand(mx, my, bx, by - 1),
            self.mv_cand(mx, my, bx + OFF[k], by - 1),
        ];
        let valid = cands.iter().filter(|c| c.is_some()).count();
        match valid {
            0 => (0, 0),
            1 => cands.iter().flatten().next().copied().unwrap(),
            _ => {
                let v: Vec<(i32, i32)> = cands.iter().map(|c| c.unwrap_or((0, 0))).collect();
                let med = |a: i32, b: i32, c: i32| a.max(b).min(a.min(b).max(c));
                (med(v[0].0, v[1].0, v[2].0), med(v[0].1, v[1].1, v[2].1))
            }
        }
    }

    /// The firmware's motion compensation wrapper (0x38a8): the 16x16 luma and 8x8 Cb, Cr
    /// predictions for a macroblock at (x, y) with a half-pel motion vector.
    ///
    /// The reference picture is addressed as the firmware's frame buffer: Y (stride x rows), then
    /// Cb and Cr, contiguous. A block whose half-pel position lies in `[0, 2W-32] x [0, 2H-32]`
    /// is interpolated straight from that buffer (a half-pel read at the last column takes the
    /// next row's first pixel). Any other block goes through a 32x32 luma / 16x16 chroma window:
    /// per axis, `[-16, 16)` with edge replication when the position is negative, `[W-16, W+16)`
    /// with replication when it is beyond `2W-32`, else the raw buffer from the position rounded
    /// down to 16 (chroma 8); the block is then read at offset `(mv & 31) / 2` inside the window.
    /// For vectors within 16 pixels outside the picture this equals the ISO edge extension;
    /// further out the window wraps (a firmware deviation; tools/playyan/video/mc_oracle.py).
    fn fw_mc16(refp: &Picture, x: i32, y: i32, mvx: i32, mvy: i32, rc: i32, ly: &mut [u8; 256], cb: &mut [u8; 64], cr: &mut [u8; 64]) {
        let (w, h) = (refp.stride as i32, refp.rows as i32);
        let (cw, ch) = (w / 2, h / 2);
        let ysz = (w * h) as usize;
        let csz = ysz / 4;
        let rd = |a: i32| -> i32 {
            if a < 0 {
                return 0;
            }
            let a = a as usize;
            if a < ysz {
                refp.y[a] as i32
            } else if a < ysz + csz {
                refp.cb[a - ysz] as i32
            } else if a < ysz + 2 * csz {
                refp.cr[a - ysz - csz] as i32
            } else {
                0
            }
        };
        let interp = |get: &dyn Fn(i32, i32) -> i32, ox: i32, oy: i32, fx: i32, fy: i32, n: usize, out: &mut [u8]| {
            for j in 0..n as i32 {
                for i in 0..n as i32 {
                    let a = get(ox + i, oy + j);
                    let v = match (fx, fy) {
                        (0, 0) => a,
                        (1, 0) => (a + get(ox + i + 1, oy + j) + 1 - rc) >> 1,
                        (0, 1) => (a + get(ox + i, oy + j + 1) + 1 - rc) >> 1,
                        _ => (a + get(ox + i + 1, oy + j) + get(ox + i, oy + j + 1) + get(ox + i + 1, oy + j + 1) + 2 - rc) >> 2,
                    };
                    out[(j * n as i32 + i) as usize] = v as u8;
                }
            }
        };
        let cmv = |m: i32| (m >> 1) | (m & 1);
        let (px, py) = (2 * x + mvx, 2 * y + mvy);
        let ub = ysz as i32;
        let vb = (ysz + csz) as i32;
        if (0..=2 * w - 32).contains(&px) && (0..=2 * h - 32).contains(&py) {
            interp(&|i, j| rd(j * w + i), x + (mvx >> 1), y + (mvy >> 1), mvx & 1, mvy & 1, 16, ly);
            let (cx, cy) = (cmv(mvx), cmv(mvy));
            interp(&|i, j| rd(ub + j * cw + i), x / 2 + (cx >> 1), y / 2 + (cy >> 1), cx & 1, cy & 1, 8, cb);
            interp(&|i, j| rd(vb + j * cw + i), x / 2 + (cx >> 1), y / 2 + (cy >> 1), cx & 1, cy & 1, 8, cr);
            return;
        }
        // window axes: source coordinate of window index k
        let axis = |p: i32, org: i32, mv: i32, size: i32, n: i32, lim: i32| -> (i32, i32, bool) {
            let hh = n / 2;
            if p < 0 {
                (-hh, size, true)
            } else if p > lim {
                (size - hh, size, true)
            } else {
                let o = if n == 32 { org + (mv >> 1) } else { (org + (mv >> 1)) >> 1 };
                (o & !(hh - 1), size, false)
            }
        };
        let src = |k: i32, ax: (i32, i32, bool)| -> i32 {
            let c = ax.0 + k;
            if ax.2 {
                c.clamp(0, ax.1 - 1)
            } else {
                c
            }
        };
        let (axl, ayl) = (axis(px, x, mvx, w, 32, 2 * w - 32), axis(py, y, mvy, h, 32, 2 * h - 32));
        let (m5x, m5y) = (mvx & 31, mvy & 31);
        interp(&|i, j| rd(src(j, ayl) * w + src(i, axl)), m5x >> 1, m5y >> 1, m5x & 1, m5y & 1, 16, ly);
        let (axc, ayc) = (axis(px, x, mvx, cw, 16, 2 * w - 32), axis(py, y, mvy, ch, 16, 2 * h - 32));
        let (cx, cy) = (cmv(m5x), cmv(m5y));
        interp(&|i, j| rd(ub + src(j, ayc) * cw + src(i, axc)), cx >> 1, cy >> 1, cx & 1, cy & 1, 8, cb);
        interp(&|i, j| rd(vb + src(j, ayc) * cw + src(i, axc)), cx >> 1, cy >> 1, cx & 1, cy & 1, 8, cr);
    }

    fn inter_recon(&mut self, mx: usize, my: usize, mv: &[(i32, i32); 4], four: bool, cbp: u32, br: Option<&mut BitReader>) -> Result<()> {
        let rc = self.rounding;
        let (x, y) = ((mx * 16) as i32, (my * 16) as i32);
        let mut preds = [[0u8; 64]; 6];
        let (mut ly, mut cb, mut cr) = ([0u8; 256], [0u8; 64], [0u8; 64]);
        if !four {
            Self::fw_mc16(&self.refp, x, y, mv[0].0, mv[0].1, rc, &mut ly, &mut cb, &mut cr);
            for k in 0..4 {
                let (bx, by) = ((k & 1) * 8, (k >> 1) * 8);
                for j in 0..8 {
                    preds[k][j * 8..j * 8 + 8].copy_from_slice(&ly[(by + j) * 16 + bx..(by + j) * 16 + bx + 8]);
                }
            }
            preds[4] = cb;
            preds[5] = cr;
        } else {
            // 0x5d9e: each 8x8 block is the matching quadrant of a 16x16 prediction made at the
            // MB origin with that block's vector, clamped by 0x3e4c so that the origin + vector
            // stays in [-30, 2W-2] x [-30, 2H-2] half-pels
            let (w2, h2) = (2 * self.refp.stride as i32 - 2, 2 * self.refp.rows as i32 - 2);
            for k in 0..4 {
                let mut vx = mv[k].0;
                let mut vy = mv[k].1;
                if 2 * x + vx < -30 {
                    vx = -30 - 2 * x;
                } else if 2 * x + vx > w2 {
                    vx = w2 - 2 * x;
                }
                if 2 * y + vy < -30 {
                    vy = -30 - 2 * y;
                } else if 2 * y + vy > h2 {
                    vy = h2 - 2 * y;
                }
                Self::fw_mc16(&self.refp, x, y, vx, vy, rc, &mut ly, &mut cb, &mut cr);
                let (bx, by) = ((k & 1) * 8, (k >> 1) * 8);
                for j in 0..8 {
                    preds[k][j * 8..j * 8 + 8].copy_from_slice(&ly[(by + j) * 16 + bx..(by + j) * 16 + bx + 8]);
                }
            }
            // chroma from the sum of the unclamped vectors: 0x5edc..0x5f20 rounds it with
            // sign(s) * (T[|s| & 15] + 2 (|s| >> 4)) (T = the table at 0x2df00, ISO 14496-2
            // table 7-9) and passes 4 (c >> 1) + (c & 1) so that 0x38a8's own chroma rounding
            // gives back c
            const RT: [i32; 16] = [0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2];
            let r = |s: i32| (if s < 0 { -1 } else { 1 }) * (RT[(s.abs() & 15) as usize] + 2 * (s.abs() >> 4));
            let (cx, cy) = (r(mv.iter().map(|m| m.0).sum()), r(mv.iter().map(|m| m.1).sum()));
            let up = |c: i32| 4 * (c >> 1) + (c & 1);
            Self::fw_mc16(&self.refp, x, y, up(cx), up(cy), rc, &mut ly, &mut cb, &mut cr);
            preds[4] = cb;
            preds[5] = cr;
        }
        let mut br = br;
        let quant_type = self.vol.as_ref().unwrap().quant_type;
        for n in 0..6 {
            let mut res = [0i16; 64];
            if cbp >> (5 - n) & 1 != 0 {
                let b = br.as_deref_mut().ok_or_else(|| undef("coded block without bitstream"))?;
                let mut lv = [0i32; 64];
                self.read_coeffs(b, false, 0, 0, 0, &mut lv)?;
                let q = self.qp;
                let mut f = [0i32; 64];
                if quant_type == 0 {
                    let qadd = (q - 1) | 1;
                    for j in 0..64 {
                        let l = s16(lv[j]);
                        f[j] = s16(l * 2 * q + sign(l) * qadd);
                    }
                } else {
                    let m = &self.vol.as_ref().unwrap().inter_matrix;
                    for j in 0..64 {
                        let l = s16(lv[j]);
                        let x = sat16(l * 2) + sign(l);
                        let x = sat16(sat16(x * q) * m[j] as i32);
                        f[j] = (x / 16).clamp(-2048, 2047);
                    }
                    let sum: i32 = f.iter().sum();
                    if sum & 1 == 0 {
                        f[63] += if f[63] & 1 != 0 { -1 } else { 1 };
                    }
                }
                for j in 0..64 {
                    res[j] = f[j] as i16;
                }
                self.fw.idct.transform(&mut res);
            }
            self.put_block(mx, my, n, &res, Some(&preds[n]));
        }
        Ok(())
    }
}
