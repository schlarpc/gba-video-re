//! Hydrogen movie containers and video stream headers (`hydrogen/docs/format-hydrogen.md`
//! sections 2-4). Shared with the two Hydrogen-packaged LZMA carts (`gbav-ads`).
//!
//! ```text
//! container:  u32 size << 4 | count;  count x { u32 type << 28 | len; u8 payload[len - 4] }
//!             type 1 = video stream, 2 = audio stream
//! video:      u32 w0 = 2 << 28 | frames << 8 | nchap
//!             u32 w1 = codec | fps << 4 | width << 10 | height << 20   (codec 5 Inflate, 7 LZMA)
//!             u16 nseg, u16 0
//!             nchap x { u16 first_segment; NUL-terminated name; pad to even }, pad to 4
//!             nseg x segment { u32 SegmentWord; u32 idx_words << 13 | cb_words; cb blob; idx blob }
//! ```

use gbav_common::{Error, Result};
use gbav_majesco::rd32;
use gbav_majesco::deobfuscate;
use gbav_majesco::vq::SegmentWord;

/// A movie container found in the ROM.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Container {
    /// ROM offset of the container word
    pub offset: usize,
    /// ROM offset / length of the type-1 entry payload
    pub video: usize,
    pub video_len: usize,
    /// type-2 entry payload, if present
    pub audio: Option<(usize, usize)>,
}

/// Scan every 4-byte-aligned offset for a container: entry 1 is type 1, the video header's first
/// word has kind 2, its width field is 240, the count is 1..7 and the entries tile the size.
/// Finds exactly the real containers on every Hydrogen-engine cart (`hy_find_containers`).
pub fn find_containers(rom: &[u8]) -> Vec<Container> {
    let mut out = Vec::new();
    let mut o = 0usize;
    while o + 16 <= rom.len() {
        let e1 = rd32(rom, o + 4);
        let v0 = rd32(rom, o + 8);
        let v1 = rd32(rom, o + 12);
        if e1 >> 28 == 1 && v0 >> 28 == 2 && (v1 >> 10) & 0x3ff == 240 {
            if let Some(c) = container_at(rom, o) {
                out.push(c);
            }
        }
        o += 4;
    }
    out
}

/// Parse a container at `o` with the scan's acceptance rule.
pub fn container_at(rom: &[u8], o: usize) -> Option<Container> {
    let w0 = rd32(rom, o);
    let (cnt, size) = (w0 & 0xf, (w0 >> 4) as usize);
    if cnt == 0 || cnt > 7 {
        return None;
    }
    let mut c = Container { offset: o, video: 0, video_len: 0, audio: None };
    let mut p = o;
    for _ in 0..cnt {
        if p + 8 > rom.len() {
            return None;
        }
        let w = rd32(rom, p + 4);
        let (t, l) = (w >> 28, (w & 0x0fff_ffff) as usize);
        if t == 1 && c.video == 0 {
            c.video = p + 8;
            c.video_len = l.checked_sub(4)?;
        }
        if t == 2 && c.audio.is_none() {
            c.audio = Some((p + 8, l.checked_sub(4)?));
        }
        p += 4 + l;
        if p > rom.len() {
            return None;
        }
    }
    if p - o + 4 != size || c.video == 0 {
        return None;
    }
    Some(c)
}

/// One segment: a run of frames sharing a codebook; every segment start is a seek point.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segment {
    /// ROM offset of the segment header
    pub offset: usize,
    /// first stream frame
    pub frame: u32,
    pub word: SegmentWord,
    pub cb_off: usize,
    pub cb_len: usize,
    pub idx_off: usize,
    pub idx_len: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chapter {
    pub segment: u16,
    pub name: String,
}

/// A parsed Hydrogen video stream header.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VideoStream {
    pub offset: usize,
    pub len: usize,
    pub w0: u32,
    pub w1: u32,
    /// frame count in w0 (equals the sum of the segment frame counts on every stream)
    pub total_frames: u32,
    /// 5 = Inflate (Hydrogen), 7 = LZMA (ADS codec in Hydrogen packaging)
    pub codec: u8,
    pub fps_nominal: u8,
    pub width: u32,
    pub height: u32,
    pub chapters: Vec<Chapter>,
    pub segments: Vec<Segment>,
    /// sum of the segment frame counts
    pub frames: u32,
}

impl VideoStream {
    /// Parse the type-1 payload at `off` (`hy_open`).
    pub fn parse(rom: &[u8], off: usize, len: usize) -> Result<VideoStream> {
        let bad = |what: &str| Error::Format(format!("video stream at {off:#x}: {what}"));
        if off + 12 > rom.len() {
            return Err(bad("header out of range"));
        }
        let (w0, w1) = (rd32(rom, off), rd32(rom, off + 4));
        if w0 >> 28 != 2 {
            return Err(bad("not a video stream (kind != 2)"));
        }
        let nchap = (w0 & 0xff) as usize;
        let nseg = gbav_majesco::rd16(rom, off + 8) as usize;
        let mut p = off + 12;
        let mut chapters = Vec::with_capacity(nchap);
        for _ in 0..nchap {
            if p + 2 > rom.len() {
                return Err(bad("chapter table out of range"));
            }
            let segment = gbav_majesco::rd16(rom, p);
            let q = p + 2;
            let z = rom[q..].iter().position(|&b| b == 0).ok_or_else(|| bad("unterminated chapter name"))?;
            chapters.push(Chapter { segment, name: deobfuscate(&rom[q..q + z]) });
            p = q + z + 1;
            if p & 1 != 0 {
                p += 1;
            }
        }
        // (p - off) rounded up to 4 relative to the stream start
        let mut q = off + ((p - off + 3) & !3);
        let mut segments = Vec::with_capacity(nseg);
        let mut frame = 0u32;
        for i in 0..nseg {
            if q + 8 > rom.len() {
                return Err(bad(&format!("segment {i} out of range")));
            }
            let (a, b) = (rd32(rom, q), rd32(rom, q + 4));
            let word = SegmentWord::parse(a);
            let cb_len = (b & 0x1fff) as usize * 4;
            let idx_len = (b >> 13) as usize * 4;
            let s = Segment { offset: q, frame, word, cb_off: q + 8, cb_len, idx_off: q + 8 + cb_len, idx_len };
            if s.idx_off + idx_len > rom.len() {
                return Err(bad(&format!("segment {i} blobs out of range")));
            }
            frame += word.frames as u32;
            q += 8 + cb_len + idx_len;
            segments.push(s);
        }
        Ok(VideoStream {
            offset: off,
            len,
            w0,
            w1,
            total_frames: (w0 >> 8) & 0xfffff,
            codec: (w1 & 0xf) as u8,
            fps_nominal: ((w1 >> 4) & 0x3f) as u8,
            width: (w1 >> 10) & 0x3ff,
            height: (w1 >> 20) & 0x3ff,
            chapters,
            segments,
            frames: frame,
        })
    }

    /// Segment holding stream frame `f`.
    pub fn segment_of(&self, f: u32) -> Option<usize> {
        if f >= self.frames {
            return None;
        }
        Some(self.segments.partition_point(|s| s.frame <= f) - 1)
    }

    /// Frames a cold decoder can start at: every segment start (segments with frames).
    pub fn seek_points(&self) -> Vec<u32> {
        let mut v: Vec<u32> = self.segments.iter().filter(|s| s.word.frames > 0).map(|s| s.frame).collect();
        v.dedup();
        v
    }

    /// One past the last byte of the segments.
    pub fn end(&self) -> usize {
        self.segments.last().map_or(self.offset, |s| s.idx_off + s.idx_len)
    }
}
