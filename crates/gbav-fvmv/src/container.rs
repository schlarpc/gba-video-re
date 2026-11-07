//! FVMV container: the 32-byte stream header and the frame records that follow it
//! (`docs/format-fvmv.md` section 2).

use gbav_common::{Error, Result};

/// Bytes of the stream header.
pub const HEADER_BYTES: usize = 0x20;
/// One scanline in CPU cycles; the header's frame period is given in scanlines.
pub const CYCLES_PER_LINE: u64 = 1232;
/// GBA CPU clock.
pub const GBA_CLOCK_HZ: u64 = 16_777_216;

fn rd32(p: &[u8], i: usize) -> u32 {
    u32::from_le_bytes([p[i], p[i + 1], p[i + 2], p[i + 3]])
}

/// The 32-byte stream header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Header {
    pub nframes: u32,
    pub width: u32,
    pub height: u32,
    /// Frame period in scanlines (1704 in all carts: 7.99171 fps).
    pub period: u32,
    /// Size of the record area after the header.
    pub data_size: u32,
    /// Size of the largest record (unused by the player).
    pub max_record: u32,
    /// Audio buffer parameter (the player's PCM buffer is twice this).
    pub audio_buf: u32,
}

impl Header {
    /// Parse a header at the start of `p`.
    pub fn parse(p: &[u8]) -> Result<Header> {
        if p.len() < HEADER_BYTES || &p[..4] != b"FVMV" {
            return Err(Error::Format("no FVMV header".into()));
        }
        Ok(Header {
            nframes: rd32(p, 4),
            width: rd32(p, 8),
            height: rd32(p, 12),
            period: rd32(p, 16),
            data_size: rd32(p, 20),
            max_record: rd32(p, 24),
            audio_buf: rd32(p, 28),
        })
    }

    /// Frame rate as a ratio: GBA clock / (period * 1232).
    pub fn fps(&self) -> (u64, u64) {
        (GBA_CLOCK_HZ, self.period as u64 * CYCLES_PER_LINE)
    }
}

/// One frame record. Offsets are relative to the stream header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Record {
    /// Record offset from the header.
    pub off: u32,
    /// Bytes after the 16-byte record header.
    pub size: u32,
    /// Offset (relative to this record) of the start of the previous segment, <= 0.
    pub back: i32,
    /// Offset (relative to this record) of the next seek point.
    pub fwd: u32,
    pub index: u32,
    /// Video payload bytes.
    pub vlen: u32,
    /// Audio payload bytes (a multiple of 40).
    pub alen: u32,
}

impl Record {
    /// Byte range of the video payload, relative to the header.
    pub fn video_range(&self) -> std::ops::Range<usize> {
        let s = self.off as usize + 0x14;
        s..s + self.vlen as usize
    }

    /// Byte range of the audio blocks, relative to the header.
    pub fn audio_range(&self) -> std::ops::Range<usize> {
        let s = self.off as usize + 0x18 + self.vlen as usize;
        s..s + self.alen as usize
    }
}

/// Walk the records of the stream whose header is at the start of `s`. Unlike the C
/// `fvmv_walk`, this also requires every audio payload to lie inside the record area.
pub fn walk(s: &[u8], h: &Header) -> Result<Vec<Record>> {
    let end = HEADER_BYTES + h.data_size as usize;
    if end > s.len() {
        return Err(Error::Format("FVMV record area runs past the ROM".into()));
    }
    let mut p = HEADER_BYTES;
    let mut recs = Vec::with_capacity(h.nframes as usize);
    while p + 0x18 <= end && recs.len() < h.nframes as usize {
        let vlen = rd32(s, p + 16);
        if p + 0x18 + vlen as usize > end {
            return Err(Error::Format(format!(
                "record {} video runs past the stream",
                recs.len()
            )));
        }
        let r = Record {
            off: p as u32,
            size: rd32(s, p),
            back: rd32(s, p + 4) as i32,
            fwd: rd32(s, p + 8),
            index: rd32(s, p + 12),
            vlen,
            alen: rd32(s, p + 0x14 + vlen as usize),
        };
        if r.audio_range().end > end {
            return Err(Error::Format(format!(
                "record {} audio runs past the stream",
                recs.len()
            )));
        }
        recs.push(r);
        p += 16 + r.size as usize;
    }
    if recs.len() != h.nframes as usize {
        return Err(Error::Format(format!(
            "record walk found {} of {} frames",
            recs.len(),
            h.nframes
        )));
    }
    Ok(recs)
}

/// Seek points: the union of all `back` and `fwd` targets, as frame indices, sorted.
pub fn seek_points(recs: &[Record]) -> Vec<u32> {
    let mut v: Vec<u32> = Vec::new();
    let find = |target: i64| -> Option<u32> {
        let t = u32::try_from(target).ok()?;
        recs.binary_search_by_key(&t, |r| r.off)
            .ok()
            .map(|i| i as u32)
    };
    for r in recs {
        for t in [r.off as i64 + r.back as i64, r.off as i64 + r.fwd as i64] {
            if let Some(i) = find(t) {
                v.push(i);
            }
        }
    }
    v.sort_unstable();
    v.dedup();
    v
}

/// Offsets of the FVMV streams in a ROM: `FVMV` at a 4-byte aligned offset, a 240x160
/// header and a record area that fits in the ROM (as the reference CLI finds them).
pub fn find_streams(rom: &[u8]) -> Vec<usize> {
    let mut out = Vec::new();
    let mut o = 0;
    while o + HEADER_BYTES <= rom.len() {
        if &rom[o..o + 4] == b"FVMV" {
            if let Ok(h) = Header::parse(&rom[o..]) {
                if h.width == 240
                    && h.height == 160
                    && o + HEADER_BYTES + h.data_size as usize <= rom.len()
                {
                    out.push(o);
                }
            }
        }
        o += 4;
    }
    out
}
