//! The cartridge firmware images, read from the user's own files.
//!
//! Tables the codecs need that are not published standards (the firmware IDCT prescale table,
//! the MP3 requantiser and synthesis tables, the AAC cube-root and twiddle tables, ...) are read
//! from these images at run time, never embedded. A [`Firmware`] is built from any of:
//!
//! * the Play-Yan ROM (`Play-Yan (Japan)`, 1 MiB or a 4 MiB mirror): fw0 (video) at ROM 0x29710,
//!   fw1 (music) at ROM 0x5B258, stored raw;
//! * the Play-Yan Micro ROM: images A (video) and B (music) as NUL-terminated lists of GBA-BIOS
//!   LZ77 chunk pointers at ROM 0xE7898 / 0xE7D34;
//! * `playan.fup` (the v1.11 update): sections 0 (video) and 1 (music), 0x200-byte blocks each
//!   followed by a check byte;
//! * an already-extracted image file.
//!
//! Every image is identified by the md5 of its bytes; only these hashes are in the source.

use gbav_common::md5;
use gbav_common::{Error, Result};
use std::sync::Arc;

/// The firmware builds this crate knows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ImageId {
    /// Play-Yan video image (`vc01pp_asf`, Jan 2005): ASF (MPEG-4 + G.726).
    PyFw0,
    /// Play-Yan music image (`vc01pp_mp3`).
    PyFw1,
    /// Play-Yan Micro video image A (Aug 2005): ASF and MP4 (MPEG-4 + AAC).
    MiFw0,
    /// Play-Yan Micro music image B.
    MiFw1,
    /// `playan.fup` section 0: video v1.11 (Jul 2005), ASF and MP4.
    FupSec0,
    /// `playan.fup` section 1: music v1.11.
    FupSec1,
}

impl ImageId {
    pub const ALL: [ImageId; 6] =
        [ImageId::PyFw0, ImageId::PyFw1, ImageId::MiFw0, ImageId::MiFw1, ImageId::FupSec0, ImageId::FupSec1];
    /// md5 of the image as the chip receives it.
    pub fn md5(self) -> &'static str {
        match self {
            ImageId::PyFw0 => "19cee6751b736a086819dd352d57f99f",
            ImageId::PyFw1 => "904dbf90365f8ac1cdfb507f04d0a302",
            ImageId::MiFw0 => "09df03a96a904fb472ad733c03e33eb3",
            ImageId::MiFw1 => "390ce952b1c269930628f2ca27ae3511",
            ImageId::FupSec0 => "cdb23583f909d7920f4f37d6e9a91f2e",
            ImageId::FupSec1 => "3951130fb9c128bf3035852b51e2a732",
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            ImageId::PyFw0 => "py_fw0",
            ImageId::PyFw1 => "py_fw1",
            ImageId::MiFw0 => "mi_fw0",
            ImageId::MiFw1 => "mi_fw1",
            ImageId::FupSec0 => "fup_sec0",
            ImageId::FupSec1 => "fup_sec1",
        }
    }
    pub fn is_video(self) -> bool {
        matches!(self, ImageId::PyFw0 | ImageId::MiFw0 | ImageId::FupSec0)
    }
    /// Video images that decode MP4 (AAC); the original fw0 plays ASF only.
    pub fn has_mp4(self) -> bool {
        matches!(self, ImageId::MiFw0 | ImageId::FupSec0)
    }
    fn from_bytes(b: &[u8]) -> Option<ImageId> {
        let h = md5::hex(b);
        ImageId::ALL.into_iter().find(|i| i.md5() == h)
    }
}

/// One firmware image as loaded at VC address 0.
#[derive(Clone)]
pub struct Image {
    pub id: ImageId,
    pub bytes: Arc<[u8]>,
}

impl std::fmt::Debug for Image {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Image({}, {} bytes)", self.id.name(), self.bytes.len())
    }
}

impl Image {
    fn slice(&self, off: usize, len: usize) -> Result<&[u8]> {
        self.bytes
            .get(off..off + len)
            .ok_or_else(|| Error::Format(format!("{}: table at 0x{off:x} out of range", self.id.name())))
    }
    /// Raw bytes of a table, checked against the md5 recorded when the table was located.
    pub fn table(&self, off: usize, len: usize, md5_hex: &str) -> Result<&[u8]> {
        let s = self.slice(off, len)?;
        let h = md5::hex(s);
        if !md5_hex.is_empty() && h != md5_hex {
            return Err(Error::Format(format!(
                "{}: table at 0x{off:x} has md5 {h}, expected {md5_hex}",
                self.id.name()
            )));
        }
        Ok(s)
    }
    pub fn u16_table(&self, off: usize, n: usize, md5_hex: &str) -> Result<Vec<u16>> {
        Ok(self.table(off, 2 * n, md5_hex)?.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes([c[0], c[1]])).collect())
    }
    pub fn i16_table(&self, off: usize, n: usize, md5_hex: &str) -> Result<Vec<i16>> {
        Ok(self.u16_table(off, n, md5_hex)?.into_iter().map(|v| v as i16).collect())
    }
    pub fn u32_table(&self, off: usize, n: usize, md5_hex: &str) -> Result<Vec<u32>> {
        Ok(self
            .table(off, 4 * n, md5_hex)?
            .as_chunks::<4>().0.iter()
            .map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]]))
            .collect())
    }
    pub fn i32_table(&self, off: usize, n: usize, md5_hex: &str) -> Result<Vec<i32>> {
        Ok(self.u32_table(off, n, md5_hex)?.into_iter().map(|v| v as i32).collect())
    }
}

/// All images found in one or more user files.
#[derive(Clone, Debug, Default)]
pub struct Firmware {
    pub images: Vec<Image>,
}

const PY_FW0: (usize, usize) = (0x029710, 0x31B48);
const PY_FW1: (usize, usize) = (0x05B258, 0x3F66C);
const MI_LISTS: [usize; 2] = [0x0E7898, 0x0E7D34];
const FUP_MAGIC: &[u8] = b"Nintendo Playan firmware update program";

/// GBA BIOS LZ77 (SWI 0x11, type 0x10) decoder.
pub fn lz77_decode(src: &[u8]) -> Result<Vec<u8>> {
    if src.len() < 4 || src[0] != 0x10 {
        return Err(Error::Format("not an LZ77 type 0x10 chunk".into()));
    }
    let size = src[1] as usize | (src[2] as usize) << 8 | (src[3] as usize) << 16;
    let mut out = Vec::with_capacity(size);
    let mut p = 4;
    let bad = || Error::Format("truncated LZ77 chunk".into());
    while out.len() < size {
        let flags = *src.get(p).ok_or_else(bad)?;
        p += 1;
        for bit in (0..8).rev() {
            if out.len() >= size {
                break;
            }
            if flags >> bit & 1 == 0 {
                out.push(*src.get(p).ok_or_else(bad)?);
                p += 1;
            } else {
                let b0 = *src.get(p).ok_or_else(bad)? as usize;
                let b1 = *src.get(p + 1).ok_or_else(bad)? as usize;
                p += 2;
                let n = (b0 >> 4) + 3;
                let d = ((b0 & 15) << 8 | b1) + 1;
                if d > out.len() {
                    return Err(Error::Format("LZ77 reference before start".into()));
                }
                for _ in 0..n {
                    let v = out[out.len() - d];
                    out.push(v);
                }
            }
        }
    }
    out.truncate(size);
    Ok(out)
}

fn micro_image(rom: &[u8], list: usize) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let mut p = list;
    loop {
        let ptr = u32::from_le_bytes(rom.get(p..p + 4)?.try_into().ok()?);
        if ptr == 0 {
            break;
        }
        let off = (ptr as usize).checked_sub(0x0C00_0000)?;
        out.extend(lz77_decode(rom.get(off..)?).ok()?);
        p += 4;
        if out.len() > 0x100000 {
            return None;
        }
    }
    Some(out)
}

fn fup_sections(f: &[u8]) -> Vec<Vec<u8>> {
    let mut v = Vec::new();
    if !f.starts_with(FUP_MAGIC) || f.len() < 0x40 {
        return v;
    }
    let n = f[0x3E] as usize;
    for e in 0..n {
        let b = 0x40 + 16 * e;
        let Some(ent) = f.get(b..b + 16) else { break };
        let off = u32::from_le_bytes(ent[0..4].try_into().unwrap()) as usize;
        let size = u32::from_le_bytes(ent[4..8].try_into().unwrap()) as usize;
        let mut out = Vec::with_capacity(size);
        let mut p = off;
        while out.len() < size {
            let take = (size - out.len()).min(0x200);
            let Some(blk) = f.get(p..p + take) else { break };
            out.extend_from_slice(blk);
            p += 0x201;
        }
        v.push(out);
    }
    v
}

impl Firmware {
    /// Images found in one file: a Play-Yan or Play-Yan Micro ROM, `playan.fup`, or a raw image.
    pub fn from_file_bytes(b: &[u8]) -> Firmware {
        let mut cands: Vec<Vec<u8>> = Vec::new();
        for (o, n) in [PY_FW0, PY_FW1] {
            if let Some(s) = b.get(o..o + n) {
                cands.push(s.to_vec());
            }
        }
        for l in MI_LISTS {
            if let Some(img) = micro_image(b, l) {
                cands.push(img);
            }
        }
        cands.extend(fup_sections(b));
        if b.len() <= 0x100000 {
            cands.push(b.to_vec());
        }
        let mut fw = Firmware::default();
        for c in cands {
            if let Some(id) = ImageId::from_bytes(&c) {
                fw.add(Image { id, bytes: c.into() });
            }
        }
        fw
    }
    /// Load and merge several files.
    pub fn from_paths<P: AsRef<std::path::Path>>(paths: &[P]) -> Result<Firmware> {
        let mut fw = Firmware::default();
        for p in paths {
            let p = p.as_ref();
            let b = std::fs::read(p).map_err(|e| Error::Invalid(format!("{}: {e}", p.display())))?;
            let f = Firmware::from_file_bytes(&b);
            if f.images.is_empty() {
                return Err(Error::Format(format!("{}: no known Play-Yan firmware image in it", p.display())));
            }
            for i in f.images {
                fw.add(i);
            }
        }
        Ok(fw)
    }
    pub fn add(&mut self, img: Image) {
        if self.image(img.id).is_none() {
            self.images.push(img);
        }
    }
    pub fn image(&self, id: ImageId) -> Option<&Image> {
        self.images.iter().find(|i| i.id == id)
    }
    /// The first image of `order` present.
    pub fn first(&self, order: &[ImageId]) -> Option<&Image> {
        order.iter().find_map(|&i| self.image(i))
    }
    /// The video image used by default: the original's fw0, else the Micro's, else the update's.
    pub fn video(&self) -> Option<&Image> {
        self.first(&[ImageId::PyFw0, ImageId::MiFw0, ImageId::FupSec0])
    }
    /// A video image that decodes MP4/AAC.
    pub fn video_mp4(&self) -> Option<&Image> {
        self.first(&[ImageId::MiFw0, ImageId::FupSec0])
    }
    pub fn music(&self) -> Option<&Image> {
        self.first(&[ImageId::PyFw1, ImageId::MiFw1, ImageId::FupSec1])
    }
}
