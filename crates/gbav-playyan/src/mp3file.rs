//! MP3 files as the Play-Yan music firmware reads them (py_fw1 0x23da4 open / AUDIO_INFO,
//! 0x20ea4 + 0x21140 ID3v2, 0x21080 ID3v1, 0x20cec first-frame check and duration,
//! 0x1d4ec Xing / VBRI, 0x23e28 per-frame sync and read).
//!
//! * **Data range.** An ID3v2 tag (versions 2.2 to 2.4, sizes read as plain big-endian
//!   integers inside the tag, no extended header or unsynchronisation support) sets the start;
//!   if it cannot be walked (a frame larger than what remains, tag larger than the file) the
//!   file is treated as untagged from byte 0. A `TAG` block in the last 128 bytes ends the data.
//! * **Frames.** The reader looks for 12 set bits, then requires Layer III, bitrate index
//!   1..14, sample-rate index 0..2, emphasis != 2 and the MPEG-1 bit, scanning byte by byte (at
//!   most 144,000 bytes) otherwise. No second header is checked. The frame length is
//!   144000 * kbps / rate + padding; the last frame may be cut short by the end of the file.
//!   Reading stops once the previous frame started past the data end.
//! * **AUDIO_INFO** (the 0x8C-byte reply to 0x600): title and artist only from ID3v1 (30
//!   characters, trailing spaces removed) or ID3v2 `TIT2`/`TPE1` (`TT2`/`TP1`) frames with
//!   ISO-8859-1 text (64 characters max; other encodings give an empty string); the length in
//!   seconds from the first frame's bitrate over the data range, or from a Xing (frames flag)
//!   or VBRI header in the first frame: frames * 1152 / rate.

use gbav_common::{Error, Result};

const KBPS: [u32; 16] = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0];
const RATE: [u32; 4] = [44100, 48000, 32000, 0];

/// Tag type as the firmware records it (struct +8).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TagKind {
    None,
    Id3v1,
    Id3v2,
}

/// What the firmware extracts for AUDIO_INFO.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Id3 {
    pub kind: TagKind,
    /// The 65-byte title field (NUL-terminated as the GBA draws it).
    pub title: [u8; 65],
    /// The 65-byte artist field.
    pub artist: [u8; 65],
    /// Song length in seconds (block +0x88).
    pub seconds: u32,
}

impl Id3 {
    /// Title up to its first NUL.
    pub fn title_bytes(&self) -> &[u8] {
        cstr(&self.title)
    }
    pub fn artist_bytes(&self) -> &[u8] {
        cstr(&self.artist)
    }
    /// The 0x8C-byte AUDIO_INFO block: word 0 = 0 (status), title at +4, artist at +0x45, seconds
    /// at +0x88; title and artist only when a tag was found.
    pub fn block(&self) -> [u8; 0x8c] {
        let mut b = [0u8; 0x8c];
        if self.kind != TagKind::None {
            b[4..4 + 65].copy_from_slice(&self.title);
            b[0x45..0x45 + 65].copy_from_slice(&self.artist);
        }
        b[0x88..0x8c].copy_from_slice(&self.seconds.to_le_bytes());
        b
    }
}

fn cstr(b: &[u8]) -> &[u8] {
    &b[..b.iter().position(|&c| c == 0).unwrap_or(b.len())]
}

/// One frame as the player hands it to the decoder.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FrameRef {
    /// File offset of the sync word.
    pub offset: usize,
    /// Frame length from the header.
    pub len: usize,
    /// Bytes actually available (less than `len` for a frame cut by the end of the file).
    pub avail: usize,
    pub rate: u32,
}

#[derive(Debug, Clone)]
pub struct Mp3File<'a> {
    data: &'a [u8],
    /// First byte of audio data (after an ID3v2 tag).
    pub start: usize,
    /// Last byte of audio data (before an ID3v1 tag).
    pub end: usize,
    /// First frame: offset, length, sample rate, bitrate in kbit/s.
    pub first: FrameRef,
    pub first_kbps: u32,
    id3: Id3,
}

fn be(b: &[u8]) -> u32 {
    b.iter().fold(0u32, |v, &c| v << 8 | c as u32)
}

/// Header test of the frame reader (0x23ebc / 0x20db4). Returns (len, rate, kbps).
fn accept(w: u32) -> Option<(usize, u32, u32)> {
    if w & 0xfff0_0000 != 0xfff0_0000 {
        return None;
    }
    let layer = (w >> 17) & 3;
    let bri = ((w >> 12) & 15) as usize;
    let sri = ((w >> 10) & 3) as usize;
    if layer > 2 || bri == 0 || bri > 14 || sri > 2 || w & 3 == 2 || w & 0x80000 == 0 || layer != 1 {
        return None;
    }
    let (k, r) = (KBPS[bri], RATE[sri]);
    Some(((144000 * k / r) as usize + ((w >> 9) & 1) as usize, r, k))
}

/// Scan for an accepted header from `pos` (0x23e28): returns its offset.
fn sync(data: &[u8], pos: usize, limit: usize) -> Option<usize> {
    if pos + 4 > data.len() {
        return None;
    }
    let mut w = be(&data[pos..pos + 4]);
    let mut p = pos;
    let mut n = 0;
    loop {
        if accept(w).is_some() {
            return Some(p);
        }
        let next = *data.get(p + 4)?;
        w = w << 8 | next as u32;
        p += 1;
        n += 1;
        if n > limit {
            return None;
        }
    }
}

/// 0x1d122 & co.: ISO-8859-1 text up to NUL, 64 bytes; `None` = field untouched.
fn text(field: &mut [u8; 65], data: &[u8]) {
    if data.len() <= 1 {
        return;
    }
    let enc = data[0];
    let mut o = 0;
    for &c in &data[1..] {
        if c == 0 {
            break;
        }
        if enc == 0 && o < 64 {
            field[o] = c;
            o += 1;
        }
    }
    field[o] = 0;
}

/// 0x21140; `Err(())` = the walk failed (the firmware then tries ID3v1 with start 0).
fn id3v2(d: &[u8], title: &mut [u8; 65], artist: &mut [u8; 65]) -> std::result::Result<usize, ()> {
    if d.len() < 10 || &d[..3] != b"ID3" {
        return Err(());
    }
    let mut r7 = d[6..10].iter().fold(0i64, |v, &c| v << 7 | (c & 127) as i64);
    if r7 >= d.len() as i64 {
        return Err(());
    }
    let ver = d[3];
    let mut pos = 10usize;
    let hl = if ver == 2 { 6 } else { 10 };
    if ver >= 2 {
        while r7 > 0 {
            let h = d.get(pos..pos + hl).ok_or(())?;
            pos += hl;
            let idl = if ver == 2 { 3 } else { 4 };
            if h[..idl].iter().all(|&c| c == 0) {
                break;
            }
            let size = if ver == 2 { be(&h[3..6]) } else { be(&h[4..8]) } as i64;
            if size > r7 {
                return Err(());
            }
            let body = d.get(pos..pos + size as usize).ok_or(())?;
            pos += size as usize;
            let id = &h[..idl];
            match (ver == 2, id) {
                (true, b"TT2") | (false, b"TIT2") => text(title, body),
                (true, b"TP1") | (false, b"TPE1") => text(artist, body),
                _ => {}
            }
            r7 -= size + hl as i64;
        }
    }
    Ok((pos as i64 + r7 - 10).max(0) as usize)
}

impl<'a> Mp3File<'a> {
    /// Open a file as the player does; `Err` where the player reports an error (no MPEG-1
    /// Layer III frame within reach, empty file).
    pub fn parse(data: &'a [u8]) -> Result<Mp3File<'a>> {
        if data.is_empty() {
            return Err(Error::Format("empty file".into()));
        }
        let mut title = [0u8; 65];
        let mut artist = [0u8; 65];
        let size = data.len();
        let has_v1 = size >= 128 && &data[size - 128..size - 125] == b"TAG";
        let (kind, start) = match id3v2(data, &mut title, &mut artist) {
            Ok(s) => (TagKind::Id3v2, s),
            Err(()) if has_v1 => {
                let t = &data[size - 128..];
                for (dst, off) in [(&mut title, 3usize), (&mut artist, 33)] {
                    let mut fill = 0u8;
                    for i in (0..30).rev() {
                        let c = t[off + i];
                        if c == 32 {
                            dst[i] = fill;
                        } else {
                            dst[i] = c;
                            fill = 32;
                        }
                    }
                }
                (TagKind::Id3v1, 0)
            }
            Err(()) => (TagKind::None, 0),
        };
        let end = if has_v1 { size - 129 } else { size - 1 };
        // 0x20cec: first frame within 1440 bytes of the start; layer/version errors end the open
        let p = sync(data, start, 1441).ok_or_else(|| Error::Format("no MPEG-1 Layer III frame".into()))?;
        let w = be(&data[p..p + 4]);
        let (len, rate, kbps) = accept(w).unwrap();
        let bits = ((end as i64 - start as i64) * 8 + 8) as i32;
        let mut seconds = ((bits / kbps as i32) / 1000) as u32;
        // 0x1d4ec: Xing / VBRI in the bytes after the first header
        let body = &data[(p + 4).min(size)..(p + len).min(size)];
        if body.len() >= 120 {
            for o in 0..=body.len() - 120 {
                if &body[o..o + 4] == b"Xing" {
                    let flags = be(&body[o + 4..o + 8]);
                    if flags & 1 != 0 {
                        seconds = be(&body[o + 8..o + 12]).wrapping_mul(1152) / rate;
                    }
                    break;
                }
            }
        }
        if body.len() >= 36 + 14 && &body[32..36] == b"VBRI" {
            let frames = be(&body[32 + 14..32 + 18]);
            if frames != 0 {
                seconds = frames.wrapping_mul(1152) / rate;
            }
        }
        Ok(Mp3File {
            data,
            start,
            end,
            first: FrameRef { offset: p, len, avail: len.min(size - p), rate },
            first_kbps: kbps,
            id3: Id3 { kind, title, artist, seconds },
        })
    }

    /// The AUDIO_INFO fields.
    pub fn id3(&self) -> &Id3 {
        &self.id3
    }

    /// The frames in playing order.
    pub fn frames(&self) -> Frames<'a> {
        Frames { data: self.data, pos: self.start, last: self.start, end: self.end, done: false }
    }

    pub fn data(&self) -> &'a [u8] {
        self.data
    }
}

/// Iterator over the frames the player reads (0x23e28).
#[derive(Debug, Clone)]
pub struct Frames<'a> {
    data: &'a [u8],
    pos: usize,
    last: usize,
    end: usize,
    done: bool,
}

impl Iterator for Frames<'_> {
    type Item = FrameRef;
    fn next(&mut self) -> Option<FrameRef> {
        if self.done || self.last > self.end {
            return None;
        }
        let Some(p) = sync(self.data, self.pos, 144000) else {
            self.done = true;
            return None;
        };
        let (len, rate, _) = accept(be(&self.data[p..p + 4])).unwrap();
        self.last = p;
        self.pos = p + len;
        Some(FrameRef { offset: p, len, avail: len.min(self.data.len() - p), rate })
    }
}
