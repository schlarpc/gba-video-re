//! Structural cart classification: which engine packages the player, which codec family the
//! movie streams use, the player build id, and cheap stream locators.
//!
//! Everything here is decided from container structure, never from code bytes:
//!
//! 1. [`locate_player_image`] finds the player code image the way each engine stores it (the
//!    same search order and rules as the triage classifier `classify_carts.py`):
//!    - **SFCD archive** (Majesco ADS engine): an `SFCD` archive in the first 64 KiB whose
//!      `main.bin` member is the player's EWRAM program.
//!    - **Actimagine loader** (VX): `VX` at ROM 0x20200 and a program size table at 0x8e4 of
//!      three non-zero multiples of 0x1000 (at most 0x40000); image = ROM 0x200 .. 0xa00 + sizes.
//!    - **Hydrogen crt table**: a `{lma, 0x03000000, size}` init entry (the last one in the ROM)
//!      that sits at most 0x40 bytes before the IWRAM image it describes.
//!    - **ARM SDT regions** (FVMV): `0x03000000` followed by a ROM load address in the crt0
//!      literal pool at ROM 0xc0..0x400; the image ends at the largest pool word within 32 KiB.
//! 2. The family comes from the movie containers that engine uses:
//!    - SFCD archive: `*.mmstr` members holding a video resource whose codec nibble is 7 (LZMA)
//!      -> `ads`.
//!    - Actimagine loader: the stream header at ROM 0x20200 has magic `VX++` or `VXGB` -> `vx`.
//!    - Hydrogen crt table: Hydrogen movie containers (`u32 size << 4 | n`, typed entries);
//!      the video header's codec nibble is 5 (inflate) on every container -> `hydrogen`, or 7
//!      (LZMA) on every container -> `ads` (the Jimmy Neutron 1 / Noah proto hybrids).
//!    - ARM SDT regions: at least one `FVMV` stream header with a sane geometry -> `fvmv`.
//!
//!    Anything else (no engine, no containers, mixed codecs) is unknown: `family == None`.
//! 3. [`build_id`]: sha1 of the image with ROM-pointer words (0x08000000..0x09ffffff) and
//!    printable runs holding a Windows path (`X:\`) zeroed, first 12 hex digits. The same id
//!    means the same player code; it equals the triage classifier's id.

use std::fmt;
use std::ops::Range;

use crate::sha1;

/// Codec family: what decodes the movie streams.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Family {
    /// Actimagine VX (Mobiclip precursor); see [`VxVariant`] for the stream revision.
    Vx,
    /// FVMV (Pokemon carts).
    Fvmv,
    /// Majesco Hydrogen: VQ + inflate.
    Hydrogen,
    /// Majesco ADS: VQ + LZMA.
    Ads,
}

impl Family {
    /// Short name as used by the codec crates and in `nointro.csv`: "vx", "fvmv", "hydrogen", "ads".
    pub fn as_str(self) -> &'static str {
        match self {
            Family::Vx => "vx",
            Family::Fvmv => "fvmv",
            Family::Hydrogen => "hydrogen",
            Family::Ads => "ads",
        }
    }

    /// Inverse of [`Family::as_str`].
    pub fn parse(s: &str) -> Option<Family> {
        [Family::Vx, Family::Fvmv, Family::Hydrogen, Family::Ads]
            .into_iter()
            .find(|f| f.as_str() == s)
    }
}

impl fmt::Display for Family {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// VX stream revision, from the stream header magic.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum VxVariant {
    /// `VX++` (Rev 6 releases and Shrek + Shark Tale).
    VxPlusPlus,
    /// `VXGB` (the older Rev 5 releases).
    Vxgb,
}

impl VxVariant {
    /// The magic: "VX++" or "VXGB".
    pub fn as_str(self) -> &'static str {
        match self {
            VxVariant::VxPlusPlus => "VX++",
            VxVariant::Vxgb => "VXGB",
        }
    }

    fn from_magic(m: u32) -> Option<VxVariant> {
        match &m.to_le_bytes() {
            b"VX++" => Some(VxVariant::VxPlusPlus),
            b"VXGB" => Some(VxVariant::Vxgb),
            _ => None,
        }
    }
}

/// How the player is packaged and booted. Independent of the codec family.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Engine {
    ActimagineLoader,
    ArmSdtRegions,
    HydrogenCrtTable,
    SfcdArchive,
}

impl Engine {
    /// "Actimagine loader", "ARM SDT regions", "Hydrogen crt table" or "SFCD archive".
    pub fn as_str(self) -> &'static str {
        match self {
            Engine::ActimagineLoader => "Actimagine loader",
            Engine::ArmSdtRegions => "ARM SDT regions",
            Engine::HydrogenCrtTable => "Hydrogen crt table",
            Engine::SfcdArchive => "SFCD archive",
        }
    }

    /// Inverse of [`Engine::as_str`].
    pub fn parse(s: &str) -> Option<Engine> {
        [
            Engine::ActimagineLoader,
            Engine::ArmSdtRegions,
            Engine::HydrogenCrtTable,
            Engine::SfcdArchive,
        ]
        .into_iter()
        .find(|e| e.as_str() == s)
    }
}

impl fmt::Display for Engine {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Where the player code image is, and which engine stores it there.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlayerImage {
    pub engine: Engine,
    /// ROM byte range of the image (clamped to the ROM).
    pub range: Range<usize>,
}

/// The container kind a [`StreamLocator`] points at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamKind {
    /// A VX stream header (0x38 bytes) at a 0x200-aligned offset.
    Vx(VxVariant),
    /// An FVMV stream header (32 bytes) followed by its record area.
    Fvmv,
    /// A Hydrogen movie container `{size << 4 | n; entries}` (video + audio).
    HydrogenContainer,
    /// An SFCD archive member (`.mmstr` resource list) holding a video resource.
    Mmstr,
}

/// One movie stream found by container parsing. These are locators only; the codec crates
/// parse the streams fully and own the canonical stream ids.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamLocator {
    /// VX, FVMV and Hydrogen: header/container ROM offset in lower-case hex without `0x`
    /// (e.g. "20200"); SFCD: the member name without `.mmstr` (e.g. "movie_1").
    pub id: String,
    pub kind: StreamKind,
    /// ROM offset of the header / container / member.
    pub offset: usize,
    /// Bytes from `offset`, where the container states it (not for VX headers).
    pub size: Option<usize>,
    pub width: u32,
    pub height: u32,
    /// Frame count from the stream header.
    pub frames: u32,
}

/// Result of [`classify`]. `family == None` means unknown: no guess is made.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Classification {
    pub family: Option<Family>,
    /// Set when `family == Some(Family::Vx)`: the magic of the stream at ROM 0x20200.
    pub vx_variant: Option<VxVariant>,
    /// The located player image (absent when no engine matched).
    pub image: Option<PlayerImage>,
    /// 12 hex digits; absent when no image was found or it is empty.
    pub build_id: Option<String>,
    /// Movie streams, in ROM order (SFCD: archive order). Empty when the family is unknown.
    pub streams: Vec<StreamLocator>,
}

impl Classification {
    /// The engine that packages the player, if one was recognised.
    pub fn engine(&self) -> Option<Engine> {
        self.image.as_ref().map(|i| i.engine)
    }
}

// ---------------------------------------------------------------- byte helpers

fn u16_at(d: &[u8], o: usize) -> Option<u16> {
    d.get(o..o.checked_add(2)?)
        .map(|b| u16::from_le_bytes([b[0], b[1]]))
}

fn u32_at(d: &[u8], o: usize) -> Option<u32> {
    d.get(o..o.checked_add(4)?)
        .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

fn clamp(len: usize, start: usize, size: usize) -> Range<usize> {
    let s = start.min(len);
    s..start.saturating_add(size).min(len)
}

fn is_rom_ptr(w: u32) -> bool {
    (0x0800_0000..0x0a00_0000).contains(&w)
}

const IWRAM: u32 = 0x0300_0000;

// ---------------------------------------------------------------- SFCD archive

/// One SFCD archive member.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SfcdMember {
    pub name: String,
    /// ROM offset of the member data.
    pub offset: usize,
    pub size: usize,
}

/// Parse the ADS `SFCD` archive: magic in the first 64 KiB, then `u16 hdr_len, u16 w2,
/// u32 count`, then `count x {u32 size, u32 offset, name NUL, pad to 4}`. Member offsets are
/// relative to the data area at `SFCD + 8 + hdr_len`. Returns `None` without an archive, an
/// empty list when the count is outside 1..63 (as the reference classifier does).
pub fn sfcd_members(rom: &[u8]) -> Option<Vec<SfcdMember>> {
    let p = find(&rom[..rom.len().min(0x10000)], b"SFCD")?;
    let hdr = u16_at(rom, p + 4)? as usize;
    let cnt = u32_at(rom, p + 8)?;
    let mut out = Vec::new();
    if !(1..64).contains(&cnt) {
        return Some(out);
    }
    let data = p + 8 + hdr;
    let mut o = p + 12;
    for _ in 0..cnt {
        let (Some(size), Some(off)) = (u32_at(rom, o), u32_at(rom, o + 4)) else {
            break;
        };
        let Some(nl) = rom
            .get(o + 8..)
            .and_then(|r| r.iter().position(|&b| b == 0))
        else {
            break;
        };
        let e = o + 8 + nl;
        let name: String = rom[o + 8..e].iter().map(|&b| b as char).collect();
        o = (e + 4) & !3;
        out.push(SfcdMember {
            name,
            offset: data + off as usize,
            size: size as usize,
        });
    }
    Some(out)
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

// ---------------------------------------------------------------- player image

/// Find the player code image by the way each engine stores it (see the module docs).
/// Engines are tried in the order SFCD, Actimagine, Hydrogen, ARM SDT.
pub fn locate_player_image(rom: &[u8]) -> Option<PlayerImage> {
    let len = rom.len();
    if let Some(members) = sfcd_members(rom) {
        if let Some(m) = members
            .iter()
            .find(|m| m.name.eq_ignore_ascii_case("main.bin"))
        {
            return Some(PlayerImage {
                engine: Engine::SfcdArchive,
                range: clamp(len, m.offset, m.size),
            });
        }
    }
    if let (Some(a), Some(b), Some(c)) =
        (u32_at(rom, 0x8e4), u32_at(rom, 0x8e8), u32_at(rom, 0x8ec))
    {
        let ok = |s: u32| s > 0 && s <= 0x40000 && s & 0xfff == 0;
        if rom.get(0x20200..0x20202) == Some(b"VX") && ok(a) && ok(b) && ok(c) {
            let end = 0xa00 + (a + b + c) as usize;
            return Some(PlayerImage {
                engine: Engine::ActimagineLoader,
                range: clamp(len, 0x200, end - 0x200),
            });
        }
    }
    let nw = len / 4;
    let word =
        |i: usize| u32::from_le_bytes([rom[4 * i], rom[4 * i + 1], rom[4 * i + 2], rom[4 * i + 3]]);
    for i in (1..nw.saturating_sub(1)).rev() {
        if word(i) != IWRAM {
            continue;
        }
        let (lma, size) = (word(i - 1), word(i + 1));
        if is_rom_ptr(lma) && size > 0 && size <= 0x8000 {
            let o = (lma - 0x0800_0000) as usize;
            let gap = o as i64 - 4 * i as i64;
            if gap > 0 && gap <= 0x40 {
                return Some(PlayerImage {
                    engine: Engine::HydrogenCrtTable,
                    range: clamp(len, o, size as usize),
                });
            }
        }
    }
    if len >= 0x400 {
        let pool: Vec<u32> = (0xc0..0x400).step_by(4).map(|o| word(o / 4)).collect();
        for i in 0..pool.len() - 1 {
            let lma = pool[i + 1];
            if pool[i] == IWRAM && is_rom_ptr(lma) {
                if let Some(end) = pool
                    .iter()
                    .copied()
                    .filter(|&x| lma < x && x <= lma + 0x8000)
                    .max()
                {
                    let (o, e) = ((lma - 0x0800_0000) as usize, (end - 0x0800_0000) as usize);
                    return Some(PlayerImage {
                        engine: Engine::ArmSdtRegions,
                        range: clamp(len, o, e - o),
                    });
                }
            }
        }
    }
    None
}

/// Player build id of a code image: sha1 of the image (truncated to whole words) after zeroing
/// every maximal printable-ASCII run that contains a Windows path (`[A-Za-z]:\`) and then every
/// word in 0x08000000..0x09ffffff (ROM pointers the linker relocates per cart); the first 12
/// hex digits. `None` for an empty image.
pub fn build_id(image: &[u8]) -> Option<String> {
    let mut img = image[..image.len() / 4 * 4].to_vec();
    if img.is_empty() {
        return None;
    }
    let printable = |b: u8| (0x20..=0x7e).contains(&b);
    let mut s = 0;
    while s < img.len() {
        if !printable(img[s]) {
            s += 1;
            continue;
        }
        let e = s + img[s..]
            .iter()
            .position(|&b| !printable(b))
            .unwrap_or(img.len() - s);
        if img[s..e]
            .windows(3)
            .any(|w| w[0].is_ascii_alphabetic() && w[1] == b':' && w[2] == b'\\')
        {
            img[s..e].fill(0);
        }
        s = e;
    }
    for w in img.as_chunks_mut::<4>().0 {
        if is_rom_ptr(u32::from_le_bytes(*w)) {
            w.fill(0);
        }
    }
    Some(sha1::hex(&sha1::digest(&img))[..12].to_string())
}

// ---------------------------------------------------------------- streams per container kind

/// VX stream headers: every 0x200-aligned `VX++`/`VXGB` word with width 1..240, height 1..160
/// and a non-zero Timer-3 reload.
pub fn vx_streams(rom: &[u8]) -> Vec<StreamLocator> {
    let mut out = Vec::new();
    for h in (0..rom.len() / 0x200 * 0x200).step_by(0x200) {
        let Some(var) = u32_at(rom, h).and_then(VxVariant::from_magic) else {
            continue;
        };
        let f = |k| u32_at(rom, h + k).unwrap_or(0);
        let (n, w, ht, tm3) = (f(4), f(8), f(12), f(16));
        if (1..=240).contains(&w) && (1..=160).contains(&ht) && tm3 != 0 {
            out.push(StreamLocator {
                id: format!("{h:x}"),
                kind: StreamKind::Vx(var),
                offset: h,
                size: None,
                width: w,
                height: ht,
                frames: n,
            });
        }
    }
    out
}

/// FVMV stream headers: every 4-aligned `FVMV` with width 1..240, height 1..160 and a record
/// area (`header + 0x20 + u32 at +0x14`) that fits in the ROM.
pub fn fvmv_streams(rom: &[u8]) -> Vec<StreamLocator> {
    let magic = u32::from_le_bytes(*b"FVMV");
    let mut out = Vec::new();
    for h in (0..rom.len() / 4 * 4).step_by(4) {
        if u32_at(rom, h) != Some(magic) {
            continue;
        }
        let f = |k| u32_at(rom, h + k).unwrap_or(0);
        let (n, w, ht) = (f(4), f(8), f(12));
        let size = 0x20 + f(0x14) as usize;
        if (1..=240).contains(&w) && (1..=160).contains(&ht) && h + size <= rom.len() {
            out.push(StreamLocator {
                id: format!("{h:x}"),
                kind: StreamKind::Fvmv,
                offset: h,
                size: Some(size),
                width: w,
                height: ht,
                frames: n,
            });
        }
    }
    out
}

/// A Hydrogen movie container plus its video header's codec nibble (5 = inflate, 7 = LZMA).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HydrogenContainer {
    pub stream: StreamLocator,
    pub codec: u32,
}

/// Hydrogen movie containers: at a 4-aligned `o`, `u32 size << 4 | n` (n 1..7) then n entries
/// `{u32 type << 28 | len; data}`; entry 1 is type 1 (video) whose first word has stream kind 2
/// in its top nibble and width 240 in bits 10..19 of its second word; the entry chain must end
/// exactly at `o + size`.
pub fn hydrogen_containers(rom: &[u8]) -> Vec<HydrogenContainer> {
    let nw = rom.len() / 4;
    let word =
        |i: usize| u32::from_le_bytes([rom[4 * i], rom[4 * i + 1], rom[4 * i + 2], rom[4 * i + 3]]);
    let mut out = Vec::new();
    for i in 0..nw.saturating_sub(3) {
        if word(i + 1) >> 28 != 1 || word(i + 2) >> 28 != 2 || (word(i + 3) >> 10) & 0x3ff != 240 {
            continue;
        }
        let o = 4 * i;
        let w0 = word(i);
        let (n, size) = (w0 & 0xf, (w0 >> 4) as usize);
        if !(1..8).contains(&n) {
            continue;
        }
        let mut p = o;
        let mut video = None;
        let mut ok = true;
        for _ in 0..n {
            let Some(w) = u32_at(rom, p + 4) else {
                ok = false;
                break;
            };
            if w >> 28 == 1 && video.is_none() {
                video = Some(p + 8);
            }
            p += 4 + (w & 0x0fff_ffff) as usize;
            if p > rom.len() {
                ok = false;
                break;
            }
        }
        let Some(v) = video else { continue };
        if !ok || p - o + 4 != size {
            continue;
        }
        let (Some(v0), Some(v1)) = (u32_at(rom, v), u32_at(rom, v + 4)) else {
            continue;
        };
        out.push(HydrogenContainer {
            stream: StreamLocator {
                id: format!("{o:x}"),
                kind: StreamKind::HydrogenContainer,
                offset: o,
                size: Some(size),
                width: (v1 >> 10) & 0x3ff,
                height: (v1 >> 20) & 0x3ff,
                frames: (v0 >> 8) & 0xfffff,
            },
            codec: v1 & 0xf,
        });
    }
    out
}

/// SFCD `.mmstr` members that hold a video resource, with that resource's codec nibble.
/// A `.mmstr` is `u32 total, u32 count_word`, then resources `{u32 type << 28 | words; data}`.
/// The video header's second word has the codec in bits 0..3 and width 240 either in bits
/// 10..19 (layout A) or 12..21 (layout B).
pub fn mmstr_streams(rom: &[u8], members: &[SfcdMember]) -> Vec<(StreamLocator, u32)> {
    let mut out = Vec::new();
    for m in members {
        let Some(id) = m.name.strip_suffix(".mmstr") else {
            continue;
        };
        let end = m.offset.saturating_add(m.size).min(rom.len());
        let Some(total) = u32_at(rom, m.offset) else {
            continue;
        };
        let stop = m.offset.saturating_add(total as usize).min(end);
        let mut o = m.offset + 8;
        while o < stop {
            let Some(w) = u32_at(rom, o) else { break };
            if w >> 28 == 1 {
                let (Some(v0), Some(v1)) = (u32_at(rom, o + 4), u32_at(rom, o + 8)) else {
                    break;
                };
                let (width, height) = if (v1 >> 10) & 0x3ff == 240 {
                    ((v1 >> 10) & 0x3ff, (v1 >> 20) & 0x3ff)
                } else {
                    ((v1 >> 12) & 0x3ff, (v1 >> 22) & 0x3ff)
                };
                let loc = StreamLocator {
                    id: id.to_string(),
                    kind: StreamKind::Mmstr,
                    offset: m.offset,
                    size: Some(m.size),
                    width,
                    height,
                    frames: (v0 >> 8) & 0xfffff,
                };
                out.push((loc, v1 & 0xf));
                break;
            }
            o += 4 + 4 * (w & 0x0fff_ffff) as usize;
        }
    }
    out
}

// ---------------------------------------------------------------- classify

/// Classify a cart: engine, family (or unknown), build id and stream locators.
pub fn classify(rom: &[u8]) -> Classification {
    let image = locate_player_image(rom);
    let build_id = image.as_ref().and_then(|i| build_id(&rom[i.range.clone()]));
    let mut c = Classification {
        family: None,
        vx_variant: None,
        image,
        build_id,
        streams: Vec::new(),
    };
    let Some(engine) = c.engine() else { return c };
    match engine {
        Engine::SfcdArchive => {
            let members = sfcd_members(rom).unwrap_or_default();
            let s = mmstr_streams(rom, &members);
            if !s.is_empty() && s.iter().all(|(_, codec)| *codec == 7) {
                c.family = Some(Family::Ads);
                c.streams = s.into_iter().map(|(l, _)| l).collect();
            }
        }
        Engine::ActimagineLoader => {
            if let Some(v) = u32_at(rom, 0x20200).and_then(VxVariant::from_magic) {
                let s = vx_streams(rom);
                if s.iter().any(|l| l.offset == 0x20200) {
                    c.family = Some(Family::Vx);
                    c.vx_variant = Some(v);
                    c.streams = s;
                }
            }
        }
        Engine::HydrogenCrtTable => {
            let s = hydrogen_containers(rom);
            let fam = match s.first().map(|h| h.codec) {
                Some(5) => Some(Family::Hydrogen),
                Some(7) => Some(Family::Ads),
                _ => None,
            };
            if fam.is_some() && s.iter().all(|h| h.codec == s[0].codec) {
                c.family = fam;
                c.streams = s.into_iter().map(|h| h.stream).collect();
            }
        }
        Engine::ArmSdtRegions => {
            let s = fvmv_streams(rom);
            if !s.is_empty() {
                c.family = Some(Family::Fvmv);
                c.streams = s;
            }
        }
    }
    c
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put(d: &mut [u8], o: usize, w: u32) {
        d[o..o + 4].copy_from_slice(&w.to_le_bytes());
    }

    #[test]
    fn build_id_masks_pointers_and_paths() {
        let mut a = vec![0u8; 64];
        put(&mut a, 0, 0xe3a0_0001);
        put(&mut a, 4, 0x0800_1234);
        a[8..24].copy_from_slice(b"x C:\\src\\a.cpp\0\0");
        let mut b = a.clone();
        put(&mut b, 4, 0x09ff_fffc);
        b[8..24].copy_from_slice(b"yyD:\\other\\b.c\0\0");
        assert_eq!(build_id(&a), build_id(&b));
        // A non-pointer word or a printable run without a path is hashed as is.
        let mut c = a.clone();
        put(&mut c, 4, 0x0a00_0000);
        assert_ne!(build_id(&a), build_id(&c));
        let mut d = vec![0u8; 64];
        d[8..12].copy_from_slice(b"abcd");
        let mut e = d.clone();
        e[8..12].copy_from_slice(b"abce");
        assert_ne!(build_id(&d), build_id(&e));
        // Trailing partial word ignored; empty image has no id.
        let mut f = a.clone();
        f.push(0x55);
        assert_eq!(build_id(&a), build_id(&f));
        assert_eq!(build_id(&[1, 2, 3]), None);
        // A run that is entirely a path hashes as zeros.
        let g = build_id(&b"zz C:\\path\\x.c"[..12]).unwrap();
        assert_eq!(g, sha1::hex(&sha1::digest(&[0u8; 12]))[..12]);
    }

    /// Synthetic Hydrogen-engine cart: crt init entry before the IWRAM image, two containers.
    fn hydrogen_rom(codecs: &[u32]) -> Vec<u8> {
        let mut r = vec![0xffu8; 0x4000];
        let mut o = 0x100;
        for &codec in codecs {
            // container: head, then entries {u32 type << 28 | payload bytes; payload}: video
            // (12-byte stream header + 4), audio (4)
            let vlen = 4 + 16;
            let alen = 4 + 8;
            let size = 4 + vlen + alen;
            put(&mut r, o, (size as u32) << 4 | 2);
            put(&mut r, o + 4, 1 << 28 | (vlen - 4) as u32);
            put(&mut r, o + 8, 2 << 28 | 100 << 8 | 6);
            put(&mut r, o + 12, 160 << 20 | 240 << 10 | 12 << 4 | codec);
            put(&mut r, o + 4 + vlen, 2 << 28 | (alen - 4) as u32);
            o += size + 0x40;
        }
        put(&mut r, 0x3000, 0x0800_3010);
        put(&mut r, 0x3004, IWRAM);
        put(&mut r, 0x3008, 0x100);
        r
    }

    #[test]
    fn hydrogen_engine_and_codec_nibble() {
        let c = classify(&hydrogen_rom(&[5, 5]));
        assert_eq!(c.engine(), Some(Engine::HydrogenCrtTable));
        assert_eq!(c.image.unwrap().range, 0x3010..0x3110);
        assert_eq!(c.family, Some(Family::Hydrogen));
        assert_eq!(c.streams.len(), 2);
        assert_eq!(
            (
                c.streams[0].id.as_str(),
                c.streams[0].frames,
                c.streams[0].width
            ),
            ("100", 100, 240)
        );
        assert_eq!(classify(&hydrogen_rom(&[7, 7])).family, Some(Family::Ads));
        // Mixed or unknown codecs: engine known, family unknown, no streams.
        let m = classify(&hydrogen_rom(&[5, 7]));
        assert_eq!(
            (m.engine(), m.family, m.streams.len()),
            (Some(Engine::HydrogenCrtTable), None, 0)
        );
        assert_eq!(classify(&hydrogen_rom(&[3])).family, None);
        assert_eq!(classify(&hydrogen_rom(&[])).family, None);
    }

    #[test]
    fn hydrogen_container_chain_must_tile() {
        let mut r = hydrogen_rom(&[5]);
        put(&mut r, 0x100, (37u32) << 4 | 2); // wrong size
        assert!(hydrogen_containers(&r).is_empty());
    }

    fn sfcd_rom(codec: u32) -> Vec<u8> {
        let mut r = vec![0u8; 0x2000];
        let p = 0x200;
        r[p..p + 4].copy_from_slice(b"SFCD");
        let mut names = Vec::new();
        for (name, off, size) in [
            ("main.bin", 0x0u32, 0x100u32),
            ("movie_1.mmstr", 0x100, 0x40),
        ] {
            let mut e = Vec::new();
            e.extend_from_slice(&size.to_le_bytes());
            e.extend_from_slice(&off.to_le_bytes());
            e.extend_from_slice(name.as_bytes());
            e.push(0);
            while e.len() % 4 != 0 {
                e.push(0);
            }
            names.extend(e);
        }
        let hdr = 4 + names.len();
        r[p + 4..p + 6].copy_from_slice(&(hdr as u16).to_le_bytes());
        put(&mut r, p + 8, 2);
        r[p + 12..p + 12 + names.len()].copy_from_slice(&names);
        let data = p + 8 + hdr;
        r[data..data + 0x100].fill(0x11);
        let m = data + 0x100;
        put(&mut r, m, 0x40);
        put(&mut r, m + 4, 2);
        put(&mut r, m + 8, 2 << 28 | 1); // text resource, 1 word
        put(&mut r, m + 16, 1 << 28 | 4); // video resource, 4 words
        put(&mut r, m + 20, 2 << 28 | 50 << 8);
        put(&mut r, m + 24, 160 << 22 | 240 << 12 | 0x30 << 4 | codec); // layout B
        r
    }

    #[test]
    fn sfcd_archive() {
        let r = sfcd_rom(7);
        let ms = sfcd_members(&r).unwrap();
        assert_eq!(
            ms.iter().map(|m| m.name.as_str()).collect::<Vec<_>>(),
            ["main.bin", "movie_1.mmstr"]
        );
        let c = classify(&r);
        assert_eq!(c.engine(), Some(Engine::SfcdArchive));
        assert_eq!(c.image.as_ref().unwrap().range.len(), 0x100);
        assert_eq!(c.family, Some(Family::Ads));
        assert_eq!(c.streams.len(), 1);
        let s = &c.streams[0];
        assert_eq!(
            (s.id.as_str(), s.width, s.height, s.frames),
            ("movie_1", 240, 160, 50)
        );
        assert_eq!(classify(&sfcd_rom(5)).family, None);
    }

    #[test]
    fn vx_and_fvmv() {
        let mut r = vec![0u8; 0x40000];
        for (i, s) in [0x1000u32, 0x2000, 0x3000].into_iter().enumerate() {
            put(&mut r, 0x8e4 + 4 * i, s);
        }
        r[0x20200..0x20204].copy_from_slice(b"VXGB");
        put(&mut r, 0x20204, 1000);
        put(&mut r, 0x20208, 240);
        put(&mut r, 0x2020c, 160);
        put(&mut r, 0x20210, 0x30c3);
        let c = classify(&r);
        assert_eq!(c.engine(), Some(Engine::ActimagineLoader));
        assert_eq!(c.image.as_ref().unwrap().range, 0x200..0x6a00);
        assert_eq!(
            (c.family, c.vx_variant),
            (Some(Family::Vx), Some(VxVariant::Vxgb))
        );
        assert_eq!(c.streams[0].id, "20200");
        r[0x20200..0x20204].copy_from_slice(b"VXZZ");
        assert_eq!(classify(&r).family, None);

        let mut f = vec![0u8; 0x10000];
        put(&mut f, 0xc0, IWRAM);
        put(&mut f, 0xc4, 0x0800_2000);
        put(&mut f, 0xc8, 0x0800_2800);
        f[0x4000..0x4004].copy_from_slice(b"FVMV");
        put(&mut f, 0x4004, 10);
        put(&mut f, 0x4008, 240);
        put(&mut f, 0x400c, 160);
        put(&mut f, 0x4014, 0x100);
        let c = classify(&f);
        assert_eq!(c.engine(), Some(Engine::ArmSdtRegions));
        assert_eq!(c.image.as_ref().unwrap().range, 0x2000..0x2800);
        assert_eq!(c.family, Some(Family::Fvmv));
        assert_eq!(c.streams[0].size, Some(0x120));
        put(&mut f, 0x4014, 0x10000); // record area past the end
        assert_eq!(classify(&f).family, None);
    }

    #[test]
    fn garbage_is_unknown() {
        for len in [0usize, 3, 0xc4, 0x401, 0x9000] {
            let r: Vec<u8> = (0..len).map(|i| (i * 31 % 251) as u8).collect();
            let c = classify(&r);
            assert_eq!(c.family, None, "len {len}");
            assert!(c.streams.is_empty());
        }
    }
}
