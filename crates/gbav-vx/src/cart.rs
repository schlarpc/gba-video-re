//! Cartridge layout: the movie player's RAM images (through the boot loader's program table,
//! as `tools/vx/container/vxcart.py` does), the player build, and the VX++ / VXGB streams.

use gbav_common::bytes::le32;
use gbav_common::md5;
use gbav_common::{Error, Result};

/// The boot loader finds program i by summing the size table at ROM 0x8e4 from ROM 0xa00.
pub const PROG_TABLE: usize = 0x8e4;
pub const PROG_BASE: usize = 0xa00;
/// Program 2 is the movie player.
pub const PLAYER_PROG: usize = 2;

/// Video codec of a stream, from its header magic.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Variant {
    /// `VX++`: Rev 6 player (and Shrek + Shark Tale Rev 5). QP in a per-frame header,
    /// MPEG-4-style residual VLC.
    VxPP,
    /// `VXGB`: Rev 5 player (Shrek, Shark Tale, Shrek 2 Rev 5). No frame header, QP from the
    /// stream header, H.264 CAVLC residuals.
    Vxgb,
}

impl Variant {
    pub fn from_magic(m: &[u8]) -> Option<Variant> {
        match m {
            b"VX++" => Some(Variant::VxPP),
            b"VXGB" => Some(Variant::Vxgb),
            _ => None,
        }
    }
    pub fn magic(self) -> &'static str {
        match self {
            Variant::VxPP => "VX++",
            Variant::Vxgb => "VXGB",
        }
    }
}

/// ROM offsets of the player program and its RAM images.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlayerLayout {
    pub prog: usize,
    pub prog_size: usize,
    /// (ROM offset, size) of the image loaded at EWRAM 0x02000000.
    pub ewram: (usize, usize),
    /// (ROM offset, size) of the image loaded at IWRAM 0x03000000.
    pub iwram: (usize, usize),
    pub entry: u32,
    /// md5 of the program image (`prog_size` bytes), identifies the build.
    pub md5: [u8; 16],
}

/// Player builds seen so far, by md5 of the program image (ROM 0x8a00, 0x10000 bytes).
/// (sha1 in tools/vx/container/vxcart.py: 7324ebf3... / 33a2f3a7...)
const KNOWN_PLAYERS: [(&str, Variant); 2] = [
    ("7ea5c79032587f66c6b40b0feea97b4f", Variant::VxPP),
    ("9c75deb71bdadaa8f05a2d5a1dadf408", Variant::Vxgb),
];

impl PlayerLayout {
    /// Parse the program table and the player's 0x44-byte load header. Its sections are
    /// (dst, src_start, src_end) triples with src addresses linked at 0x02000000 = the
    /// program's ROM offset; the EWRAM section is (dst, size_end, entry) with src = prog.
    pub fn parse(rom: &[u8]) -> Result<PlayerLayout> {
        let bad = || Error::Format("cannot parse the player program table / load header".into());
        let mut prog = PROG_BASE;
        for i in 0..PLAYER_PROG {
            prog = prog
                .checked_add(le32(rom, PROG_TABLE + 4 * i).ok_or_else(bad)? as usize)
                .ok_or_else(bad)?;
        }
        let prog_size = le32(rom, PROG_TABLE + 4 * PLAYER_PROG).ok_or_else(bad)? as usize;
        let mut w = [0u32; 17];
        for (i, v) in w.iter_mut().enumerate() {
            *v = le32(rom, prog + 4 * i).ok_or_else(bad)?;
        }
        let (mut ew, mut iw) = (None, None);
        for k in (2..16).step_by(3) {
            let (dst, s0, s1) = (w[k], w[k + 1], w[k + 2]);
            if dst == 0x0200_0000 {
                ew = Some((prog, s0.checked_sub(0x0200_0000).ok_or_else(bad)? as usize));
            } else if s1 > s0 && dst >> 24 == 3 && iw.is_none() {
                let off = (prog as u64 + s0 as u64)
                    .checked_sub(0x0200_0000)
                    .ok_or_else(bad)? as usize;
                iw = Some((off, (s1 - s0) as usize));
            }
        }
        let (ewram, iwram) = (ew.ok_or_else(bad)?, iw.ok_or_else(bad)?);
        for (o, n) in [ewram, iwram, (prog, prog_size)] {
            if o.checked_add(n).is_none_or(|e| e > rom.len()) {
                return Err(bad());
            }
        }
        let md5 = md5::digest(&rom[prog..prog + prog_size]);
        Ok(PlayerLayout {
            prog,
            prog_size,
            ewram,
            iwram,
            entry: w[16],
            md5,
        })
    }

    /// The build this player image is (`None` for an unknown build).
    pub fn build(&self) -> Option<Variant> {
        let h = md5::to_hex(&self.md5);
        KNOWN_PLAYERS.iter().find(|(m, _)| *m == h).map(|(_, v)| *v)
    }

    /// ROM slice of the IWRAM image from IWRAM address `addr`, `n` bytes.
    pub fn iw<'r>(&self, rom: &'r [u8], addr: u32, n: usize) -> Result<&'r [u8]> {
        let rel = addr.wrapping_sub(0x0300_0000) as usize;
        if rel + n > self.iwram.1 {
            return Err(Error::Format(format!(
                "IWRAM 0x{addr:08x}+{n} outside the player image"
            )));
        }
        Ok(&rom[self.iwram.0 + rel..self.iwram.0 + rel + n])
    }

    /// ROM slice of the EWRAM image from EWRAM address `addr`, `n` bytes.
    pub fn ew<'r>(&self, rom: &'r [u8], addr: u32, n: usize) -> Result<&'r [u8]> {
        let rel = addr.wrapping_sub(0x0200_0000) as usize;
        if rel + n > self.ewram.1 {
            return Err(Error::Format(format!(
                "EWRAM 0x{addr:08x}+{n} outside the player image"
            )));
        }
        Ok(&rom[self.ewram.0 + rel..self.ewram.0 + rel + n])
    }
}

/// One seek-table entry: `{u32 frame, u32 video_bit, u32 audio_byte, u32 zero}`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SeekEntry {
    pub frame: u32,
    /// Bit offset from the first video halfword (header + 0x38).
    pub video_bit: u32,
    /// Byte offset into the audio stream (entry 0 = 3124, the codebook size).
    pub audio_byte: u32,
}

/// A parsed VX header (0x38 bytes, u32 LE; docs/vx/container.md section 2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamHeader {
    /// ROM offset of the header.
    pub offset: usize,
    pub variant: Variant,
    pub frames: u32,
    pub width: u32,
    pub height: u32,
    /// Timer 3 reload; one frame per 3 ticks of 2^24/64 Hz.
    pub timer: u32,
    /// +0x14: QP for the whole stream (VXGB); 0 in VX++.
    pub qp: u32,
    /// +0x18: nominal audio rate (16384); not read by the player.
    pub audio_rate: u32,
    /// +0x1c: audio stream offset from the header (0 = no audio).
    pub audio_off: u32,
    /// +0x20: chapter table offset from the header.
    pub chapter_off: u32,
    pub last_chapter: u32,
    /// +0x28: seek table offset from the header.
    pub seek_off: u32,
    /// +0x2c: seek entries including the sentinel.
    pub seek_count: u32,
}

impl StreamHeader {
    /// Parse the header at `off`; `None` unless the magic and dimensions are plausible
    /// (the same test as tools/vx/container/vxcart.py `find_streams`).
    pub fn parse(rom: &[u8], off: usize) -> Option<StreamHeader> {
        let variant = Variant::from_magic(rom.get(off..off + 4)?)?;
        let r = |i: usize| le32(rom, off + 4 * i);
        let (frames, width, height) = (r(1)?, r(2)?, r(3)?);
        if !(frames > 0
            && frames < 1 << 20
            && width > 0
            && width <= 240
            && height > 0
            && height <= 160)
            || (width | height) & 15 != 0
        {
            return None;
        }
        Some(StreamHeader {
            offset: off,
            variant,
            frames,
            width,
            height,
            timer: r(4)?,
            qp: r(5)?,
            audio_rate: r(6)?,
            audio_off: r(7)?,
            chapter_off: r(8)?,
            last_chapter: r(9)?,
            seek_off: r(10)?,
            seek_count: r(11)?,
        })
    }

    /// ROM range of the video elementary stream (header + 0x38 up to the audio stream, or
    /// to the end of the ROM when there is no audio), as vxdec_main.c computes it.
    pub fn video_range(&self, rom_len: usize) -> std::ops::Range<usize> {
        let start = (self.offset + 0x38).min(rom_len);
        let end = if self.audio_off != 0 {
            self.offset + self.audio_off as usize
        } else {
            rom_len
        };
        start..end.clamp(start, rom_len)
    }

    /// ROM range of the audio elementary stream (codebook + AFrames): audio offset up to the
    /// chapter table, as tools/vx/container/vxaudio.py's callers slice it.
    pub fn audio_range(&self, rom_len: usize) -> Option<std::ops::Range<usize>> {
        if self.audio_off == 0 {
            return None;
        }
        let s = (self.offset + self.audio_off as usize).min(rom_len);
        let e = if self.chapter_off > self.audio_off {
            self.offset + self.chapter_off as usize
        } else {
            rom_len
        };
        Some(s..e.clamp(s, rom_len))
    }

    /// The seek table (all `seek_count` entries, including the sentinel).
    pub fn seek_table(&self, rom: &[u8]) -> Vec<SeekEntry> {
        let base = self.offset + self.seek_off as usize;
        (0..self.seek_count as usize)
            .map_while(|i| {
                let e = base + 16 * i;
                Some(SeekEntry {
                    frame: le32(rom, e)?,
                    video_bit: le32(rom, e + 4)?,
                    audio_byte: le32(rom, e + 8)?,
                })
            })
            .collect()
    }

    /// Chapter start frames (`u32 count; u32 start_frame[count]` at header + 0x20); empty
    /// when the stream has no chapter table. Every chapter starts on a seek point.
    pub fn chapters(&self, rom: &[u8]) -> Vec<u32> {
        if self.chapter_off == 0 {
            return Vec::new();
        }
        let c = self.offset + self.chapter_off as usize;
        let n = le32(rom, c).unwrap_or(0).min(511) as usize;
        (0..n)
            .map_while(|i| le32(rom, c + 4 + 4 * i))
            .filter(|&f| f < self.frames)
            .collect()
    }

    /// Frames a cold decoder can start at (seek entries before the sentinel).
    pub fn seek_points(&self, rom: &[u8]) -> Vec<u32> {
        let mut v: Vec<u32> = self
            .seek_table(rom)
            .iter()
            .map(|e| e.frame)
            .filter(|&f| f < self.frames)
            .collect();
        v.dedup();
        v
    }

    /// Stream id as used on the command line and in manifest names: the header offset in hex.
    pub fn id(&self) -> String {
        format!("{:x}", self.offset)
    }
}

/// All VX++ / VXGB stream headers in a ROM: the magic on a 0x200 boundary (the cart reads
/// 512-byte sectors) with plausible dimensions.
pub fn find_streams(rom: &[u8]) -> Vec<StreamHeader> {
    (0..rom.len().saturating_sub(0x37))
        .step_by(0x200)
        .filter_map(|o| StreamHeader::parse(rom, o))
        .collect()
}

/// Resolve a stream id: a hex header offset ("20200", "0x1eef800") or an index ("1").
pub fn pick_stream(rom: &[u8], id: &str) -> Result<StreamHeader> {
    let st = find_streams(rom);
    if st.is_empty() {
        return Err(Error::Format("no VX++ / VXGB stream in this ROM".into()));
    }
    let t = id.trim();
    let hexs = t.strip_prefix("0x").unwrap_or(t);
    if let Some(h) = usize::from_str_radix(hexs, 16)
        .ok()
        .and_then(|o| st.iter().find(|h| h.offset == o))
    {
        return Ok(h.clone());
    }
    if !t.starts_with("0x") {
        if let Some(h) = t.parse::<usize>().ok().and_then(|i| st.get(i)) {
            return Ok(h.clone());
        }
    }
    Err(Error::Invalid(format!(
        "no stream '{id}' (streams: {})",
        st.iter().map(|h| h.id()).collect::<Vec<_>>().join(", ")
    )))
}
