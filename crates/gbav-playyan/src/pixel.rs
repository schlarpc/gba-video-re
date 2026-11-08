//! The video firmware's picture output stages, and the cartridge window's pixel conversion.
//!
//! * Frames (VIDEO_DECODE_START 240 x 160): the frame task (py_fw0 0x19368) fits the decoded
//!   picture into a 240x160 RGB565 image with 0x2749a mode 2 ([`Fitter`]: keep the width, crop or
//!   letterbox the height; a 240x176 source loses rows 0..7 and 168..175; other sizes go through
//!   the firmware's bilinear / box plane scalers), applies the brightness of VIDEO_MODE_SET
//!   (0x273aa -> 0x27bea, 256 = neutral) and sends it in a DECODE_DATA_INDICATION.
//! * Thumbnails (VIDEO_INFO 60 x 40, 0x112b2): frame 0 through the same fit into 60x40 (for
//!   240x176: the centred 240x160, then a 4:1 box filter). No brightness.
//! * Colour conversion 0x284a0, per pixel (chroma nearest-neighbour from 4:2:0):
//!   `Y' = (298 (Y - 16)) >> 8`, `lo = clamp(Y' + (408 (Cb - 128)) >> 8)`,
//!   `hi = clamp(Y' + (516 (Cr - 128)) >> 8)`, `g = clamp(Y' + sat16((-100 Cb') >> 8 + (-200 Cr') >> 8))`,
//!   `rgb565 = lo >> 3 | (g >> 2) << 5 | (hi & 0xF8) << 8` (>> is floor). The 408/516 multipliers sit
//!   on the swapped chroma planes compared with BT.601, a colour error of the firmware.
//! * The cartridge's pixel window WIN_R1 converts RGB565 to the GBA's BGR555 on reads:
//!   `p >> 11 | ((p >> 6) & 31) << 5 | (p & 31) << 10`. What the GBA receives (and hashes, for
//!   the Garage Games) is that.

use crate::firmware::Image;
use crate::m4v::Picture;
use gbav_common::{Error, Result};

pub const FRAME_W: usize = 240;
pub const FRAME_H: usize = 160;
pub const THUMB_W: usize = 60;
pub const THUMB_H: usize = 40;

/// VIDEO_MODE_SET brightness values the GBA sends (index 0..5); 256 is neutral.
pub const BRIGHTNESS_LEVELS: [u32; 6] = [0x101, 0x140, 0x180, 0x1E0, 0x240, 0x2C0];

#[inline]
fn fl8(a: i32, b: i32) -> i32 {
    (a * b) >> 8
}

/// Firmware 0x284a0 for one pixel: Y, first chroma plane (Cb), second (Cr) -> RGB565.
#[inline]
pub fn yuv_to_rgb565(y: u8, cb: u8, cr: u8) -> u16 {
    let yy = fl8(y as i32 - 16, 298);
    let u = cb as i32 - 128;
    let v = cr as i32 - 128;
    let g = (fl8(u, -100) + fl8(v, -200)).clamp(-32768, 32767);
    let lo = (yy + fl8(u, 408)).clamp(0, 255);
    let hi = (yy + fl8(v, 516)).clamp(0, 255);
    let gg = (yy + g).clamp(0, 255);
    ((lo >> 3) | ((gg >> 2) << 5) | ((hi & 0xF8) << 8)) as u16
}

/// The cartridge window's RGB565 -> BGR555 conversion.
#[inline]
pub fn rgb565_to_bgr555(p: u16) -> u16 {
    (p >> 11) | ((p >> 6) & 31) << 5 | (p & 31) << 10
}

/// Brightness `b` / 256 on an RGB565 pixel (0x27bea, op17 = (a*b)>>8 saturated to 8 bits):
/// each channel MSB-aligned in a byte, scaled, saturated, re-masked.
#[inline]
pub fn bright565(p: u16, b: u32) -> u16 {
    let p = p as u32;
    let r = ((((p >> 11) << 3) * b) >> 8).min(255) & 0xf8;
    let g = (((((p >> 5) & 63) << 2) * b) >> 8).min(255) & 0xfc;
    let bl = ((((p & 31) << 3) * b) >> 8).min(255) & 0xf8;
    ((r << 8) | (g << 3) | (bl >> 3)) as u16
}

/// The 240x160 RGB565 frame the firmware sends (before the cartridge window), at brightness `b`.
/// `f` carries the firmware's temp image and scratch from frame to frame (they only show in the
/// bars of letterboxed sizes).
pub fn frame_rgb565(f: &mut Fitter, pic: &Picture, brightness: u32) -> Result<Vec<u16>> {
    Ok(f.fit(pic, FRAME_W, FRAME_H)?.into_iter().map(|p| bright565(p, brightness)).collect())
}

/// The 240x160 frame as the GBA receives it (BGR555, through the WIN_R1 window).
pub fn frame_bgr555(f: &mut Fitter, pic: &Picture, brightness: u32) -> Result<Vec<u16>> {
    Ok(frame_rgb565(f, pic, brightness)?.into_iter().map(rgb565_to_bgr555).collect())
}

/// Thumbnail box filter reciprocals 65536 / n (0x2765c, u32 x 17).
#[derive(Clone)]
pub struct ThumbTables {
    recip: Vec<u32>,
}

const BOXRECIP_MD5: &str = "73a110145f176e9aafd2a188c7507782";

impl ThumbTables {
    pub fn from_firmware(img: &Image) -> Result<ThumbTables> {
        use crate::ImageId::*;
        let off = match img.id {
            PyFw0 => 0x2765c,
            MiFw0 => 0x3bcac,
            FupSec0 => 0x3c0cc,
            _ => return Err(Error::Invalid(format!("{} is not a video image", img.id.name()))),
        };
        Ok(ThumbTables { recip: img.u32_table(off, 17, BOXRECIP_MD5)? })
    }
}

/// The 60x40 RGB565 thumbnail the firmware returns for VIDEO_INFO (frame 0 of the file): the same
/// fit as the frames, into 60x40 (no strips, no brightness).
pub fn thumbnail_rgb565(pic: &Picture, tt: &ThumbTables) -> Result<Vec<u16>> {
    Fitter::new(tt).fit(pic, THUMB_W, THUMB_H)
}

/// The thumbnail as the GBA receives it (BGR555).
pub fn thumbnail_bgr555(pic: &Picture, tt: &ThumbTables) -> Result<Vec<u16>> {
    Ok(thumbnail_rgb565(pic, tt)?.into_iter().map(rgb565_to_bgr555).collect())
}

/// The GBA ROM's Garage Game hash over the 2400 thumbnail pixels (0x0C019410).
pub fn garage_hash(px: &[u16]) -> u32 {
    px.iter().fold(0u32, |h, &p| h.wrapping_add(p as u32).rotate_left(1))
}

// ---------------------------------------------------------------------------------------------
// The general fit (0x2749a mode 2 -> 0x27980 -> 0x2788e -> 0x276de and its passes and kernels),
// for pictures of any size. Modelled on the firmware's memory: one flat buffer holds the source
// picture (Y | Cb | Cr, MB-aligned, as the decoder stores it), the persistent temp image
// (gp+0x16b34) and the persistent 24 KiB scratch (gp+0x2a5d4); kernels write what the firmware's
// do, including one byte past the row (0x28e64 and the 16/8/4-row resamplers when the length is
// not a multiple of 16), the in-place vertical upscale and the row-pair order of the horizontal
// pass. Checked against the emulator (tools/playyan/video/fit_check.py) and the real firmware's frames.

const SCRATCH: usize = 24576;

struct Mem {
    m: Vec<u8>,
    /// 65536 / n, n = 0..16 (0x2765c)
    recip: [i64; 17],
}

impl Mem {
    #[inline]
    fn rd(&self, a: i64) -> i64 {
        if a >= 0 && (a as usize) < self.m.len() {
            self.m[a as usize] as i64
        } else {
            0
        }
    }
    #[inline]
    fn wr(&mut self, a: i64, v: i64) {
        if a >= 0 && (a as usize) < self.m.len() {
            self.m[a as usize] = v as u8;
        }
    }
    fn memcpy(&mut self, d: i64, s: i64, n: i64) {
        for i in 0..n {
            let v = self.rd(s + i);
            self.wr(d + i, v);
        }
    }
    fn lerp_rows(&mut self, d: i64, a: i64, n: i64, st: i64, w: i64) {
        let w = w & 0xffff_ffff;
        if w <= 63 {
            return self.memcpy(d, a, n);
        }
        let w = w & 0xffff;
        // one byte too many unless the destination row ends 16-aligned
        let fast = (d + n).rem_euclid(16) == 0;
        let len = n + if fast { 0 } else { 1 };
        let vals: Vec<i64> = (0..len)
            .map(|i| {
                let (x, y) = (self.rd(a + i), self.rd(a + st + i));
                x + (((y - x) * w + 0x8000) >> 16)
            })
            .collect();
        for (i, v) in vals.into_iter().enumerate() {
            self.wr(d + i as i64, v);
        }
    }
    fn vbox(&mut self, d: i64, s: i64, nb: i64, mut st: i64, mut n: i64) {
        if n - 1 <= 1 {
            return self.lerp_rows(d, s, nb, st, (n - 1) << 15);
        }
        while n > 16 {
            st <<= 1;
            n >>= 1;
        }
        let r = self.recip[n as usize] & 0xffff;
        let vals: Vec<i64> = (0..nb).map(|i| ((0..n).map(|k| self.rd(s + k * st + i)).sum::<i64>() * r + 0x8000) >> 16).collect();
        for (i, v) in vals.into_iter().enumerate() {
            self.wr(d + i as i64, v);
        }
    }
    fn hbox(&mut self, d: i64, s: i64, nout: i64, step: i64) {
        let n = ((step + 0xffff) >> 16).min(16);
        let r = self.recip[n as usize] & 0xffff;
        let mut pos = 0i64;
        for j in 0..nout {
            let base = s + (pos >> 16);
            let sm: i64 = (0..n).map(|k| (self.rd(base + k) * r + 0x8000) >> 16).sum();
            self.wr(d + j, sm.min(255));
            pos += step;
        }
    }
    #[allow(clippy::too_many_arguments)]
    fn hres_rows(&mut self, d: i64, s: i64, n: i64, x0: i64, step: i64, dp: i64, sp: i64, rows: i64) {
        for r in 0..rows {
            // the partial last 16-byte group writes one byte too many unless the row ends
            // 16-aligned (buffers here sit at their firmware addresses mod 16)
            let nl = n + if (d + r * dp + n).rem_euclid(16) != 0 { 1 } else { 0 };
            let vals: Vec<i64> = (0..nl)
                .map(|j| {
                    let xx = x0 + j * step;
                    let (x, f) = (xx >> 16, xx & 0xffff);
                    let p0 = self.rd(s + r * sp + x);
                    let p1 = self.rd(s + r * sp + x + 1);
                    p0 + (((p1 - p0) * f + 0x8000) >> 16)
                })
                .collect();
            for (j, v) in vals.into_iter().enumerate() {
                self.wr(d + r * dp + j as i64, v);
            }
        }
    }
    fn hres_row1(&mut self, d: i64, s: i64, n: i64, x0: i64, step: i64) {
        let mut xx = x0;
        for j in 0..n {
            let (x, f) = (xx >> 16, xx & 0xffff);
            let p0 = self.rd(s + x);
            let p1 = self.rd(s + x + 1);
            self.wr(d + j, p0 + (((p1 - p0) * f) >> 16));
            xx += step;
        }
    }
    fn edge_fill(&mut self, d: i64, s: i64, n: i64, rows: i64, dp: i64, sp: i64) {
        for r in 0..rows {
            let v = self.rd(s + r * sp);
            for j in 0..n {
                self.wr(d + r * dp + j, v);
            }
        }
    }
}

fn tdiv(a: i64, b: i64) -> i64 {
    a / b // Rust integer division truncates toward zero, as the firmware's divs
}

/// 0x28844 (bilinear mode): horizontal pass.
#[allow(clippy::too_many_arguments)]
fn hpass(m: &mut Mem, mut dst: i64, mut dw: i64, rows: i64, dp: i64, mut src: i64, sw: i64, sp: i64) {
    let step = (sw << 16) / dw;
    if step > (2 << 16) {
        for r in 0..rows {
            m.hbox(dst + r * dp, src + r * sp, dw, step);
        }
        return;
    }
    let mut x0 = tdiv((sw - dw) << 15, dw);
    if dw > sw {
        let e = (sw + dw - 1) / (2 * sw);
        m.edge_fill(dst, src, e, rows, dp, sp);
        m.edge_fill(dst + dw - e, src + sw - 1, e, rows, dp, sp);
        dst += e;
        x0 += e * step;
        dw -= 2 * e;
    }
    let mut r = rows;
    if (dp | sp) & 8 == 0 {
        while r >= 16 {
            m.hres_rows(dst, src, dw, x0, step, dp, sp, 16);
            dst += 16 * dp;
            src += 16 * sp;
            r -= 16;
        }
        if r >= 8 {
            let r15 = r & 7;
            if r15 > 1 {
                m.hres_rows(dst, src, dw, x0, step, dp, sp, 8);
                dst += r15 * dp;
                src += r15 * sp;
                r -= r15;
            }
            m.hres_rows(dst, src, dw, x0, step, dp, sp, 8);
            dst += 8 * dp;
            src += 8 * sp;
            r -= 8;
        }
    } else {
        for (group, k) in [(32, 16), (16, 8), (8, 4)] {
            while r >= group {
                for par in 0..2 {
                    m.hres_rows(dst + par * dp, src + par * sp, dw, x0, step, 2 * dp, 2 * sp, k);
                }
                dst += group * dp;
                src += group * sp;
                r -= group;
                if group != 32 {
                    break;
                }
            }
        }
    }
    while r > 0 {
        m.hres_row1(dst, src, dw, x0, step);
        dst += dp;
        src += sp;
        r -= 1;
    }
}

/// 0x29014 (bilinear mode): vertical pass.
#[allow(clippy::too_many_arguments)]
fn vpass(m: &mut Mem, mut dst: i64, n: i64, rows: i64, dp: i64, src: i64, sp: i64, y0: i64, step: i64, last: i64) {
    if step >= (2 << 16) {
        let mut y = y0;
        for _ in 0..rows {
            let a = y >> 16;
            y += step;
            m.vbox(dst, src + a * sp, n, sp, (y >> 16) - a);
            dst += dp;
        }
        return;
    }
    let mut y = y0 + (step >> 1) - 0x8000;
    for _ in 0..rows {
        let r3 = y >> 16;
        if y <= 0 {
            m.memcpy(dst, src, n);
        } else if r3 >= last {
            m.memcpy(dst, src + r3 * sp, n);
        } else {
            m.lerp_rows(dst, src + r3 * sp, n, sp, y);
        }
        dst += dp;
        y += step;
    }
}

/// 0x276de: one plane.
#[allow(clippy::too_many_arguments)]
fn plane(m: &mut Mem, mut dst: i64, dw: i64, dh: i64, dp: i64, src: i64, sw: i64, sh: i64, sp: i64, scratch: i64) -> Result<()> {
    if dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0 {
        return Ok(());
    }
    if dw == sw && dh == sh {
        for r in 0..dh {
            m.memcpy(dst + r * dp, src + r * sp, sw);
        }
        return Ok(());
    }
    if dh >= sh {
        let off = (dh - sh) * dp;
        hpass(m, dst + off, dw, sh, dp, src, sw, sp);
        vpass(m, dst, dw, dh, dp, dst + off, dp, 0, tdiv(sh << 16, dh), sh - 1);
        return Ok(());
    }
    if sh == 2 * dh && sw == 2 * dw && (dh | sw | dp | sp | dst | src) & 15 == 0 {
        // 0x286e4 mode 1: 2x2 box (sum + 2) >> 2
        for r in 0..dh {
            for x in 0..dw {
                let a = src + 2 * r * sp + 2 * x;
                let v = (m.rd(a) + m.rd(a + 1) + m.rd(a + sp) + m.rd(a + sp + 1) + 2) >> 2;
                m.wr(dst + r * dp + x, v);
            }
        }
        return Ok(());
    }
    let pitch = (sw + 15) & !15;
    let chunk = if SCRATCH as i64 >= pitch * 64 {
        64
    } else if SCRATCH as i64 >= pitch * 32 {
        32
    } else {
        (SCRATCH as i64 / pitch).min(16)
    };
    let step = tdiv(sh << 16, dh);
    let (mut y0, mut done) = (0i64, 0i64);
    while done < dh {
        let c = chunk.min(dh - done);
        vpass(m, scratch, sw, c, pitch, src, sp, y0, step, sh - 1);
        hpass(m, dst, dw, c, dp, scratch, sw, pitch);
        y0 += step * c;
        dst += c * dp;
        done += c;
    }
    Ok(())
}

/// The firmware's fit of a picture into a `dw` x `dh` RGB565 image (frames 240x160, thumbnails
/// 60x40), with its persistent temp image and scratch.
#[derive(Clone)]
pub struct Fitter {
    recip: [i64; 17],
    temp: Vec<u8>,
    scratch: Vec<u8>,
}

impl Fitter {
    pub fn new(tt: &ThumbTables) -> Fitter {
        let mut recip = [0i64; 17];
        for (r, &v) in recip.iter_mut().zip(&tt.recip) {
            *r = v as i64;
        }
        Fitter { recip, temp: Vec::new(), scratch: Vec::new() }
    }

    /// 0x2749a(dst, src, 2): RGB565, row-major `dw` x `dh`.
    pub fn fit(&mut self, pic: &Picture, dw: usize, dh: usize) -> Result<Vec<u16>> {
        let (w, h) = (pic.width as i64, pic.height as i64);
        let (sp, hp) = (pic.stride as i64, pic.rows as i64);
        let (dwi, dhi) = (dw as i64, dh as i64);
        let tp = (dwi + 15) & !15;
        let big = dwi * dhi * 2 >= 28800;
        let th = if big { dhi / 2 } else { dhi };
        let tsz = (tp * ((th + 15) & !15) * 3 / 2) as usize;
        let srcsz = (sp * hp * 3 / 2) as usize;
        if self.temp.len() != tsz {
            self.temp = vec![0; tsz];
        }
        if self.scratch.len() != SCRATCH {
            self.scratch = vec![0; SCRATCH];
        }
        // [source picture][temp image][16 bytes after it][scratch]: the vertical upscale's last
        // chroma row reads one byte past the U plane (= V[0][0], left over from the previous strip
        // or frame) and past the V plane (gp+0x1dbb4, a variable that was 0 in every capture)
        const GUARD: usize = 16;
        let mut m = Mem { m: Vec::with_capacity(srcsz + tsz + GUARD + SCRATCH), recip: self.recip };
        m.m.extend_from_slice(&pic.y);
        m.m.extend_from_slice(&pic.cb);
        m.m.extend_from_slice(&pic.cr);
        m.m.extend_from_slice(&self.temp);
        m.m.extend_from_slice(&[0; GUARD]);
        m.m.extend_from_slice(&self.scratch);
        let (src, temp, scratch) = (0i64, srcsz as i64, (srcsz + tsz + GUARD) as i64);
        // 0x2749a mode 2 geometry
        let (dx, mut dy, mut fh) = (0i64, 0i64, dhi);
        let (sx, mut sy, sw, mut sh) = (0i64, 0i64, w, h);
        let h2 = tdiv(dwi * h, w);
        if h2 < dhi {
            dy = (dhi - h2) / 2;
            fh = h2;
        } else if h2 > dhi {
            let used = tdiv(dhi * h, h2);
            sy = (h - used) / 2;
            sh = used;
        }
        let strips = if big { 2 } else { 1 };
        let mut out = vec![0u16; dw * dh];
        let ty = ((th + 15) >> 4) * tp * 16;
        let tv = ty + ((th + 15) >> 4) * tp * 4;
        let syo = ((h + 15) >> 4) * sp * 16;
        let svo = syo + ((h + 15) >> 4) * sp * 4;
        for k in 0..strips {
            let (fdh, ssh, ssy) = (fh / strips, sh / strips, sy + k * (sh / strips));
            // 0x2788e: Y, Cb, Cr
            plane(&mut m, temp + dy * tp + dx, dwi, fdh, tp, src + ssy * sp + sx, sw, ssh, sp, scratch)?;
            let (ct, cs) = (tp >> 1, sp >> 1);
            plane(&mut m, temp + ty + (dy >> 1) * ct + (dx >> 1), dwi >> 1, fdh >> 1, ct, src + syo + (ssy >> 1) * cs + (sx >> 1), sw >> 1, ssh >> 1, cs, scratch)?;
            plane(&mut m, temp + tv + (dy >> 1) * ct + (dx >> 1), dwi >> 1, fdh >> 1, ct, src + svo + (ssy >> 1) * cs + (sx >> 1), sw >> 1, ssh >> 1, cs, scratch)?;
            // 0x27130: the whole temp image to RGB565, chroma nearest
            for r in 0..th {
                for x in 0..dwi {
                    let c = (r >> 1) * (tp >> 1) + (x >> 1);
                    let p = yuv_to_rgb565(m.rd(temp + r * tp + x) as u8, m.rd(temp + ty + c) as u8, m.rd(temp + tv + c) as u8);
                    out[((k * th + r) * dwi + x) as usize] = p;
                }
            }
        }
        self.temp.copy_from_slice(&m.m[temp as usize..temp as usize + tsz]);
        self.scratch.copy_from_slice(&m.m[scratch as usize..scratch as usize + SCRATCH]);
        Ok(out)
    }
}
