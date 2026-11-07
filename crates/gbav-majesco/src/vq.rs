//! The Majesco VQ video layer shared by the Hydrogen and ADS players: segment header word,
//! codebook unprediction, YCbCr -> BGR555 conversion with the 4x4 ordered dither and the
//! brightness clamp, and the frame blit for all eight modes.
//!
//! Port of `reference/majesco/vq.c` (spec: `docs/majesco/vq.md`). Bit-exact with it for every
//! input it defines; inputs it leaves undefined (more than 256 codebook entries, a codebook
//! shorter than the converter reads) are rejected with [`Error::Undefined`].

use gbav_common::{Error, Result};

/// Screen size the blit writes (GBA mode 3).
pub const W: usize = 240;
pub const H: usize = 160;
/// VRAM codebook entry slots (halfwords). 4x4 and 3x3 use all 16 (3x3: 9 pixels + a zero).
pub const ENTRY_HW: usize = 16;
/// Largest codebook the converter can address: the blit indexes it with one byte.
pub const MAX_ENTRIES: usize = 256;

const BW: [u8; 8] = [4, 4, 4, 4, 3, 3, 4, 4];
const BH: [u8; 8] = [4, 4, 3, 3, 3, 3, 2, 2];
/// chroma pairs per entry as the unpredict step counts them (UnPredictChrominance)
const CPE_UNPRED: [u8; 8] = [1, 4, 1, 3, 1, 2, 1, 2];
/// luma byte j of a 4-wide entry -> pixel slot row*4+col (luma is stored as 2x2 quads)
const POS44: [u8; 16] = [0, 1, 4, 5, 2, 3, 6, 7, 8, 9, 12, 13, 10, 11, 14, 15];
const POS43: [u8; 12] = [0, 1, 4, 5, 2, 3, 6, 7, 8, 9, 10, 11];

/// Block geometry, bits 0-2 of a segment's first word. Only odd modes occur in the streams; the
/// even modes are decoded the way the cartridge would (misplaced chroma, see vq.md 1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Mode(u8);

impl Mode {
    pub fn new(m: u32) -> Mode {
        Mode((m & 7) as u8)
    }
    pub fn id(self) -> u8 {
        self.0
    }
    pub fn block_w(self) -> usize {
        BW[self.0 as usize] as usize
    }
    pub fn block_h(self) -> usize {
        BH[self.0 as usize] as usize
    }
    pub fn grid_w(self) -> usize {
        W / self.block_w()
    }
    pub fn grid_h(self) -> usize {
        H / self.block_h()
    }
    /// Index bytes per frame: 2400, 3180, 4240 or 4800.
    pub fn blocks(self) -> usize {
        self.grid_w() * self.grid_h()
    }
    /// Lines the block grid covers: 159 for the 3-row-block modes, else 160.
    pub fn grid_lines(self) -> usize {
        self.grid_h() * self.block_h()
    }
    /// Chroma pairs per entry as the converter reads them.
    pub fn chroma_per_entry(self) -> usize {
        if self.is_3x3() {
            2
        } else {
            self.block_h()
        }
    }
    fn is_3x3(self) -> bool {
        self.0 == 4 || self.0 == 5
    }
    /// VRAM entry stride in halfwords: 16, 12, 16 or 8.
    pub fn entry_stride(self) -> usize {
        if self.is_3x3() {
            16
        } else {
            self.block_w() * self.block_h()
        }
    }
    /// Codebook bytes the converter reads for `ncb` entries.
    pub fn codebook_bytes(self, ncb: usize) -> usize {
        ncb * (self.block_w() * self.block_h() + 2 * self.chroma_per_entry())
    }
    /// Codebook bytes the unpredict step touches (differs from [`Mode::codebook_bytes`] for
    /// the even modes only).
    pub fn unpredict_bytes(self, ncb: usize) -> usize {
        ncb * (self.block_w() * self.block_h() + 2 * CPE_UNPRED[self.0 as usize] as usize)
    }
}

/// First word of a segment (Hydrogen) or chunk (ADS).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SegmentWord {
    pub mode: Mode,
    /// bits 3-5; 6 on every stream, not read by the players
    pub mbits: u8,
    /// codebook entries (bits 6-15); 256 on every stream
    pub ncb: u16,
    /// frames in the segment (bits 16-31)
    pub frames: u16,
}

impl SegmentWord {
    pub fn parse(w: u32) -> SegmentWord {
        SegmentWord {
            mode: Mode::new(w),
            mbits: ((w >> 3) & 7) as u8,
            ncb: ((w >> 6) & 0x3ff) as u16,
            frames: (w >> 16) as u16,
        }
    }
}

/// The player's conversion tables: dither thresholds from the ROM and the brightness clamp.
#[derive(Clone)]
pub struct Tables {
    pub dither: [u8; 16],
    /// brightness gain (256 = identity, the players' default level)
    pub gain: i32,
    /// added to the gain (Settings `BaseBrightness`, Jimmy Neutron's boot code); usually 0
    pub gain_bias: i32,
    lut: [u8; 256],
    five: [[u8; 256]; 16],
}

impl std::fmt::Debug for Tables {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Tables")
            .field("dither", &self.dither)
            .field("gain", &self.gain)
            .field("gain_bias", &self.gain_bias)
            .finish()
    }
}

impl Tables {
    pub fn new(dither: [u8; 16], gain: i32, gain_bias: i32) -> Tables {
        let mut t = Tables { dither, gain, gain_bias, lut: [0; 256], five: [[0; 256]; 16] };
        t.rebuild();
        t
    }
    /// Change the gain (brightness level) and bias.
    pub fn set_gain(&mut self, gain: i32, gain_bias: i32) {
        self.gain = gain;
        self.gain_bias = gain_bias;
        self.rebuild();
    }
    fn rebuild(&mut self) {
        let g = self.gain.wrapping_add(self.gain_bias);
        for v in 0..256i32 {
            // mvq_init: (unsigned)(v * g) >> 8, saturated at 255
            let p = (v.wrapping_mul(g) as u32) >> 8;
            self.lut[v as usize] = if p > 255 { 255 } else { p as u8 };
        }
        for k in 0..16 {
            for v in 0..256usize {
                self.five[k][v] = if (v >> 3) >= 31 {
                    31
                } else {
                    ((v >> 3) + (self.dither[k] < (v & 7) as u8) as usize) as u8
                };
            }
        }
    }
    #[inline]
    fn clamp(&self, x: i32) -> usize {
        if x < 0 {
            0
        } else if x > 255 {
            self.lut[255] as usize
        } else {
            self.lut[x as usize] as usize
        }
    }
    #[inline]
    fn pix(&self, k: usize, y: i32, u: i32, v: i32) -> u16 {
        let f = &self.five[k];
        let r = f[self.clamp(y + 2 * v)] as u16;
        let g = f[self.clamp(y - (u >> 1) - v)] as u16;
        let b = f[self.clamp(y + 2 * u)] as u16;
        r | g << 5 | b << 10
    }
}

/// The VRAM codebook at 0x06012C00 as the cartridge holds it: 256 entries x 16 halfwords.
/// Entries the current segment does not convert keep their earlier contents.
pub type Vcb = [[u16; ENTRY_HW]; MAX_ENTRIES];

fn check_ncb(ncb: usize) -> Result<()> {
    if ncb > MAX_ENTRIES {
        return Err(Error::Undefined(format!(
            "{ncb} codebook entries: the 1-byte blit cannot address more than 256 (the player's 2-byte path is unusable)"
        )));
    }
    Ok(())
}

/// StreamBase::UnPredictLuminance / UnPredictChrominance: running byte sums over the luma plane,
/// then (restarting at 0) over both chroma planes.
pub fn unpredict(cb: &mut [u8], mode: Mode, ncb: usize) -> Result<()> {
    check_ncb(ncb)?;
    let nl = ncb * mode.block_w() * mode.block_h();
    let end = mode.unpredict_bytes(ncb);
    if cb.len() < end {
        return Err(Error::Undefined(format!("codebook of {} bytes, unpredict needs {end}", cb.len())));
    }
    let mut acc = 0u8;
    for b in &mut cb[..nl] {
        acc = acc.wrapping_add(*b);
        *b = acc;
    }
    acc = 0;
    for b in &mut cb[nl..end] {
        acc = acc.wrapping_add(*b);
        *b = acc;
    }
    Ok(())
}

/// Convert an unpredicted codebook to dithered BGR555 entries in the cartridge's VRAM layout.
/// Writes entries 0..ncb only (and only the halfwords the cartridge writes).
pub fn convert(cb: &[u8], mode: Mode, ncb: usize, t: &Tables, out: &mut Vcb) -> Result<()> {
    check_ncb(ncb)?;
    let need = mode.codebook_bytes(ncb);
    if cb.len() < need {
        return Err(Error::Undefined(format!("codebook of {} bytes, converter reads {need}", cb.len())));
    }
    let (bw, bh) = (mode.block_w(), mode.block_h());
    let nl = ncb * bw * bh;
    if mode.is_3x3() {
        let (u, v) = (nl, nl + 2 * ncb);
        for e in 0..ncb {
            for s in 0..9 {
                let c = if s < 6 { 0 } else { 1 };
                let k = (s / 3) * 4 + s % 3;
                out[e][s] = t.pix(k, cb[e * 9 + s] as i32, cb[u + 2 * e + c] as i8 as i32, cb[v + 2 * e + c] as i8 as i32);
            }
            // the 3x3 blitter packs two blocks per word and relies on this zero
            out[e][9] = 0;
        }
        return Ok(());
    }
    // generic 4-wide path, also used for the even modes (as on the cartridge)
    let pos: &[u8] = if bh == 3 { &POS43 } else { &POS44 };
    let (u0, v0) = (nl, nl + (nl >> 2));
    for e in 0..ncb {
        for g in 0..bh {
            let u = cb[u0 + e * bh + g] as i8 as i32;
            let v = cb[v0 + e * bh + g] as i8 as i32;
            for j in 0..4 {
                let k = pos[g * 4 + j] as usize;
                out[e][k] = t.pix(k, cb[(e * bh + g) * 4 + j] as i32, u, v);
            }
        }
    }
    Ok(())
}

/// What the blitter does with line 159 in the 3-row-block modes (2..5), which the 53-row grid
/// does not cover.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Row159 {
    /// Shipped players: copy BG palette halfwords 0..23 ten times across the line (the palette is
    /// all zero during playback, so the line is black).
    Palette([u16; 24]),
    /// Sonic X Vol 2 prototype: line 159 is never written (keeps whatever VRAM held).
    Untouched,
}

impl Row159 {
    /// The shipped players' behaviour with the playback palette (all zero).
    pub const BLACK: Row159 = Row159::Palette([0; 24]);
}

/// Rasterise one frame of 1-byte indices into a 240x160 framebuffer. Returns the number of lines
/// the blit wrote, from the top (159 when line 159 is left untouched, else 160): the hash
/// manifests cover exactly those lines.
pub fn blit(idx: &[u8], mode: Mode, cb: &Vcb, row159: Row159, fb: &mut [u16]) -> Result<usize> {
    let (bw, bh, gw, gh) = (mode.block_w(), mode.block_h(), mode.grid_w(), mode.grid_h());
    if idx.len() < gw * gh {
        return Err(Error::Undefined(format!("index plane of {} bytes, mode {} needs {}", idx.len(), mode.0, gw * gh)));
    }
    if fb.len() < W * H {
        return Err(Error::Invalid("framebuffer smaller than 240x160".into()));
    }
    for by in 0..gh {
        let row = &idx[by * gw..(by + 1) * gw];
        for r in 0..bh {
            let line = &mut fb[(by * bh + r) * W..(by * bh + r + 1) * W];
            for (bx, &i) in row.iter().enumerate() {
                line[bx * bw..bx * bw + bw].copy_from_slice(&cb[i as usize][r * bw..r * bw + bw]);
            }
        }
    }
    let covered = gh * bh;
    match row159 {
        Row159::Palette(pal) => {
            for y in covered..H {
                for x in 0..W {
                    fb[y * W + x] = pal[x % 24];
                }
            }
            Ok(H)
        }
        Row159::Untouched => Ok(covered),
    }
}

/// The player's 16-byte ordered-dither table: the 16 bytes right before its block-geometry table
/// `{4,4, 4,4, 4,3, 4,3, 3,3, 3,3, 4,2, 4,2}`. Returns the ROM offset of the dither table.
pub fn find_dither(data: &[u8], limit: usize) -> Option<usize> {
    const DIMS: [u8; 16] = [4, 4, 4, 4, 4, 3, 4, 3, 3, 3, 3, 3, 4, 2, 4, 2];
    let lim = limit.min(data.len());
    let mut o = 16;
    while o + 16 <= lim {
        if data[o] == 4 && data[o..o + 16] == DIMS {
            return Some(o - 16);
        }
        o += 1;
    }
    None
}

/// The player's brightness gains: the s32 table right before the dither table, read backwards
/// while the values decrease and stay positive (11 levels 128..384 in the shipped players, 8
/// levels 230..409 in the Sonic X Vol 2 prototype). Returns (gains, default level), where the
/// default level is the one with gain 256.
pub fn gains_before(data: &[u8], dither_off: usize) -> Option<(Vec<i32>, usize)> {
    let mut v = Vec::new();
    let mut o = dither_off;
    while o >= 4 && v.len() < 16 {
        let g = crate::rd32(data, o - 4) as i32;
        if g <= 0 || g >= 1024 || v.last().is_some_and(|&l: &i32| g >= l) {
            break;
        }
        v.push(g);
        o -= 4;
    }
    v.reverse();
    let d = v.iter().position(|&g| g == 256)?;
    Some((v, d))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn geometry() {
        let b: Vec<usize> = (0..8).map(|m| Mode::new(m).blocks()).collect();
        assert_eq!(b, [2400, 2400, 3180, 3180, 4240, 4240, 4800, 4800]);
        let l: Vec<usize> = (0..8).map(|m| Mode::new(m).grid_lines()).collect();
        assert_eq!(l, [160, 160, 159, 159, 159, 159, 160, 160]);
        assert_eq!(Mode::new(5).codebook_bytes(256), 3328);
        assert_eq!(Mode::new(1).codebook_bytes(256), 6144);
        assert_eq!(Mode::new(3).codebook_bytes(256), 4608);
        assert_eq!(Mode::new(7).codebook_bytes(256), 3072);
        let w = SegmentWord::parse(0x0020_4035);
        assert_eq!((w.mode.id(), w.mbits, w.ncb, w.frames), (5, 6, 256, 32));
    }

    #[test]
    fn identity_gain_and_dither() {
        let t = Tables::new([0, 4, 1, 5, 6, 2, 7, 3, 1, 5, 0, 4, 7, 3, 6, 2], 256, 0);
        assert!((0..256).all(|v| t.lut[v] as usize == v));
        // grey stays grey; 8-bit 255 -> 31
        assert_eq!(t.pix(0, 255, 0, 0), 0x7fff);
        assert_eq!(t.pix(0, 0, 0, 0), 0);
        let b = Tables::new([0; 16], 307, 0);
        assert_eq!(b.lut[255], 255);
        assert_eq!(b.lut[100], 119);
    }

    #[test]
    fn row159() {
        let cb: Box<Vcb> = Box::new([[0x1234; 16]; 256]);
        let idx = vec![0u8; 4240];
        let mut fb = vec![0xffffu16; W * H];
        assert_eq!(blit(&idx, Mode::new(5), &cb, Row159::Untouched, &mut fb).unwrap(), 159);
        assert!(fb[159 * W..].iter().all(|&p| p == 0xffff));
        assert_eq!(blit(&idx, Mode::new(5), &cb, Row159::BLACK, &mut fb).unwrap(), 160);
        assert!(fb[159 * W..].iter().all(|&p| p == 0));
        assert!(convert(&[0; 10], Mode::new(5), 257, &Tables::new([0; 16], 256, 0), &mut [[0; 16]; 256]).is_err());
    }
}
