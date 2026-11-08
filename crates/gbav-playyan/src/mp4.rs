//! MP4 (ISO base media) demuxer for the Play-Yan Micro / `playan.fup` video firmware's files:
//! one `mp4v` (MPEG-4 Part 2) video track and one `mp4a` (AAC) audio track.
//!
//! The firmware (mi_fw0 0x22c00-0x24100, fourcc table at 0x46574) builds a box tree and looks up
//! `moov`/`trak`/`mdia`/`minf`/`stbl` children by type. It knows `ftyp mvhd tkhd mdhd hdlr dinf
//! edts elst udta mdat stsd stts ctts stss stsc stsz stco co64 esds`, the video sample entries
//! `mp4v avc1 s263` and the audio entries `mp4a samr` (`stz2` is not among them). This module
//! reads the same boxes; sample positions come from `stsc` + `stco`/`co64` + `stsz`, timing from
//! `stts` (+ `ctts` offsets, which the decoders do not need), sync samples from `stss` (all samples
//! are sync samples without it). Edit lists are parsed into [`Track::edits`] but not applied.

use gbav_common::{Error, Result};

/// One sample (an MPEG-4 VOP or an AAC raw_data_block).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sample {
    pub offset: u64,
    pub size: u32,
    /// Decode time in track timescale units.
    pub dts: u64,
    pub duration: u32,
    /// Composition offset (`ctts`), 0 without it.
    pub cts_offset: i32,
    pub sync: bool,
}

/// What a track carries, from its sample entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Codec {
    /// MPEG-4 Part 2 visual; `config` = the esds DecoderSpecificInfo (VOS/VO/VOL headers).
    Mp4v { width: u16, height: u16, config: Vec<u8> },
    /// MPEG-4 audio; `config` = the AudioSpecificConfig.
    Mp4a { channels: u16, sample_rate: u32, config: Vec<u8> },
    /// Another sample entry the firmware recognises (`avc1`, `s263`, `samr`) or an unknown one.
    Other([u8; 4]),
}

#[derive(Debug, Clone)]
pub struct Track {
    pub track_id: u32,
    /// `hdlr` handler type (`vide`, `soun`, ...).
    pub handler: [u8; 4],
    pub timescale: u32,
    pub duration: u64,
    pub codec: Codec,
    pub samples: Vec<Sample>,
    /// `elst` entries (segment duration, media time, rate) as stored.
    pub edits: Vec<(u64, i64, u32)>,
}

#[derive(Debug, Clone)]
pub struct Mp4 {
    /// Movie timescale and duration (`mvhd`).
    pub timescale: u32,
    pub duration: u64,
    pub tracks: Vec<Track>,
}

fn be16(d: &[u8], p: usize) -> Result<u16> {
    d.get(p..p + 2).map(|s| u16::from_be_bytes([s[0], s[1]])).ok_or_else(trunc)
}
fn be32(d: &[u8], p: usize) -> Result<u32> {
    d.get(p..p + 4).map(|s| u32::from_be_bytes([s[0], s[1], s[2], s[3]])).ok_or_else(trunc)
}
fn be64(d: &[u8], p: usize) -> Result<u64> {
    Ok((be32(d, p)? as u64) << 32 | be32(d, p + 4)? as u64)
}
fn trunc() -> Error {
    Error::Format("MP4: truncated box".into())
}

/// Child boxes of `d[start..end]`: (type, payload start, end).
fn boxes(d: &[u8], start: usize, end: usize) -> Result<Vec<([u8; 4], usize, usize)>> {
    let mut v = Vec::new();
    let mut p = start;
    while p + 8 <= end {
        let mut size = be32(d, p)? as u64;
        let typ: [u8; 4] = d[p + 4..p + 8].try_into().unwrap();
        let mut hdr = 8;
        if size == 1 {
            size = be64(d, p + 8)?;
            hdr = 16;
        } else if size == 0 {
            size = (end - p) as u64;
        }
        if size < hdr as u64 || p as u64 + size > end as u64 {
            return Err(Error::Format(format!("MP4: bad size for box {}", String::from_utf8_lossy(&typ))));
        }
        v.push((typ, p + hdr, p + size as usize));
        p += size as usize;
    }
    Ok(v)
}

fn child(d: &[u8], s: usize, e: usize, t: &[u8; 4]) -> Result<Option<(usize, usize)>> {
    Ok(boxes(d, s, e)?.into_iter().find(|b| &b.0 == t).map(|b| (b.1, b.2)))
}

/// Descriptor (tag, payload start, length) at `p` (ISO 14496-1 expandable size).
fn descriptor(d: &[u8], mut p: usize) -> Result<(u8, usize, usize)> {
    let tag = *d.get(p).ok_or_else(trunc)?;
    p += 1;
    let mut n = 0usize;
    for _ in 0..4 {
        let b = *d.get(p).ok_or_else(trunc)?;
        p += 1;
        n = n << 7 | (b & 0x7f) as usize;
        if b & 0x80 == 0 {
            break;
        }
    }
    Ok((tag, p, n))
}

/// DecoderSpecificInfo of an `esds` payload.
fn esds_config(d: &[u8], s: usize, e: usize) -> Result<Vec<u8>> {
    let (tag, mut p, _) = descriptor(d, s + 4)?;
    if tag != 3 {
        return Err(Error::Format("MP4: esds without ES_Descriptor".into()));
    }
    let flags = *d.get(p + 2).ok_or_else(trunc)?;
    p += 3;
    if flags & 0x80 != 0 {
        p += 2;
    }
    if flags & 0x40 != 0 {
        p += 1 + *d.get(p).ok_or_else(trunc)? as usize;
    }
    if flags & 0x20 != 0 {
        p += 2;
    }
    let (tag, p, _) = descriptor(d, p)?;
    if tag != 4 {
        return Err(Error::Format("MP4: esds without DecoderConfigDescriptor".into()));
    }
    let (tag, p, n) = descriptor(d, p + 13)?;
    if tag != 5 || p + n > e {
        return Ok(Vec::new());
    }
    Ok(d[p..p + n].to_vec())
}

fn parse_track(d: &[u8], s: usize, e: usize) -> Result<Track> {
    let mut t = Track {
        track_id: 0,
        handler: *b"    ",
        timescale: 0,
        duration: 0,
        codec: Codec::Other(*b"    "),
        samples: Vec::new(),
        edits: Vec::new(),
    };
    if let Some((a, _)) = child(d, s, e, b"tkhd")? {
        t.track_id = be32(d, a + if d[a] == 1 { 20 } else { 12 })?;
    }
    if let Some((a, b)) = child(d, s, e, b"edts")? {
        if let Some((a, _)) = child(d, a, b, b"elst")? {
            let v1 = d[a] == 1;
            let n = be32(d, a + 4)? as usize;
            let mut p = a + 8;
            for _ in 0..n {
                if v1 {
                    t.edits.push((be64(d, p)?, be64(d, p + 8)? as i64, be32(d, p + 16)?));
                    p += 20;
                } else {
                    t.edits.push((be32(d, p)? as u64, be32(d, p + 4)? as i32 as i64, be32(d, p + 8)?));
                    p += 12;
                }
            }
        }
    }
    let (ms, me) = child(d, s, e, b"mdia")?.ok_or_else(|| Error::Format("MP4: trak without mdia".into()))?;
    if let Some((a, _)) = child(d, ms, me, b"mdhd")? {
        if d[a] == 1 {
            t.timescale = be32(d, a + 20)?;
            t.duration = be64(d, a + 24)?;
        } else {
            t.timescale = be32(d, a + 12)?;
            t.duration = be32(d, a + 16)? as u64;
        }
    }
    if let Some((a, _)) = child(d, ms, me, b"hdlr")? {
        t.handler = d.get(a + 8..a + 12).ok_or_else(trunc)?.try_into().unwrap();
    }
    let (ns, ne) = child(d, ms, me, b"minf")?.ok_or_else(|| Error::Format("MP4: mdia without minf".into()))?;
    let (ss, se) = child(d, ns, ne, b"stbl")?.ok_or_else(|| Error::Format("MP4: minf without stbl".into()))?;
    let get = |typ: &[u8; 4]| child(d, ss, se, typ);
    if let Some((a, b)) = get(b"stsd")? {
        let entries = boxes(d, a + 8, b)?;
        if let Some(&(typ, p, q)) = entries.first() {
            t.codec = match &typ {
                b"mp4v" => {
                    let cfg = match child(d, p + 78, q, b"esds")? {
                        Some((x, y)) => esds_config(d, x, y)?,
                        None => Vec::new(),
                    };
                    Codec::Mp4v { width: be16(d, p + 24)?, height: be16(d, p + 26)?, config: cfg }
                }
                b"mp4a" => {
                    let cfg = match child(d, p + 28, q, b"esds")? {
                        Some((x, y)) => esds_config(d, x, y)?,
                        None => Vec::new(),
                    };
                    Codec::Mp4a { channels: be16(d, p + 16)?, sample_rate: be32(d, p + 24)? >> 16, config: cfg }
                }
                other => Codec::Other(*other),
            };
        }
    }
    // sample sizes
    let sizes: Vec<u32> = match get(b"stsz")? {
        Some((a, _)) => {
            let (ss0, n) = (be32(d, a + 4)?, be32(d, a + 8)? as usize);
            if ss0 != 0 {
                vec![ss0; n]
            } else {
                (0..n).map(|i| be32(d, a + 12 + 4 * i)).collect::<Result<_>>()?
            }
        }
        None => Vec::new(),
    };
    let chunks: Vec<u64> = if let Some((a, _)) = get(b"stco")? {
        (0..be32(d, a + 4)? as usize).map(|i| be32(d, a + 8 + 4 * i).map(|v| v as u64)).collect::<Result<_>>()?
    } else if let Some((a, _)) = get(b"co64")? {
        (0..be32(d, a + 4)? as usize).map(|i| be64(d, a + 8 + 8 * i)).collect::<Result<_>>()?
    } else {
        Vec::new()
    };
    let stsc: Vec<(u32, u32)> = match get(b"stsc")? {
        Some((a, _)) => (0..be32(d, a + 4)? as usize)
            .map(|i| Ok((be32(d, a + 8 + 12 * i)?, be32(d, a + 12 + 12 * i)?)))
            .collect::<Result<_>>()?,
        None => Vec::new(),
    };
    let mut stts: Vec<(u32, u32)> = Vec::new();
    if let Some((a, _)) = get(b"stts")? {
        for i in 0..be32(d, a + 4)? as usize {
            stts.push((be32(d, a + 8 + 8 * i)?, be32(d, a + 12 + 8 * i)?));
        }
    }
    let mut ctts: Vec<(u32, i32)> = Vec::new();
    if let Some((a, _)) = get(b"ctts")? {
        for i in 0..be32(d, a + 4)? as usize {
            ctts.push((be32(d, a + 8 + 8 * i)?, be32(d, a + 12 + 8 * i)? as i32));
        }
    }
    let sync: Option<Vec<u32>> = match get(b"stss")? {
        Some((a, _)) => Some((0..be32(d, a + 4)? as usize).map(|i| be32(d, a + 8 + 4 * i)).collect::<Result<_>>()?),
        None => None,
    };
    // walk chunks
    let mut si = 0usize;
    for (ci, &co) in chunks.iter().enumerate() {
        let mut per = 0u32;
        for &(first, n) in &stsc {
            if ci as u32 + 1 >= first {
                per = n;
            }
        }
        let mut off = co;
        for _ in 0..per {
            if si >= sizes.len() {
                break;
            }
            t.samples.push(Sample { offset: off, size: sizes[si], dts: 0, duration: 0, cts_offset: 0, sync: true });
            off += sizes[si] as u64;
            si += 1;
        }
    }
    let mut k = 0usize;
    let mut dts = 0u64;
    for &(n, delta) in &stts {
        for _ in 0..n {
            if let Some(s) = t.samples.get_mut(k) {
                s.dts = dts;
                s.duration = delta;
            }
            dts += delta as u64;
            k += 1;
        }
    }
    let mut k = 0usize;
    for &(n, off) in &ctts {
        for _ in 0..n {
            if let Some(s) = t.samples.get_mut(k) {
                s.cts_offset = off;
            }
            k += 1;
        }
    }
    if let Some(sy) = sync {
        for s in t.samples.iter_mut() {
            s.sync = false;
        }
        for n in sy {
            if let Some(s) = t.samples.get_mut((n as usize).wrapping_sub(1)) {
                s.sync = true;
            }
        }
    }
    Ok(t)
}

impl Mp4 {
    /// Parse the `moov` of a whole file.
    pub fn parse(d: &[u8]) -> Result<Mp4> {
        let top = boxes(d, 0, d.len())?;
        let (ms, me) = top
            .iter()
            .find(|b| &b.0 == b"moov")
            .map(|b| (b.1, b.2))
            .ok_or_else(|| Error::Format("MP4: no moov box".into()))?;
        let mut m = Mp4 { timescale: 0, duration: 0, tracks: Vec::new() };
        for (typ, s, e) in boxes(d, ms, me)? {
            match &typ {
                b"mvhd" => {
                    if d[s] == 1 {
                        m.timescale = be32(d, s + 20)?;
                        m.duration = be64(d, s + 24)?;
                    } else {
                        m.timescale = be32(d, s + 12)?;
                        m.duration = be32(d, s + 16)? as u64;
                    }
                }
                b"trak" => m.tracks.push(parse_track(d, s, e)?),
                _ => {}
            }
        }
        Ok(m)
    }
    /// The first `mp4v` track.
    pub fn video(&self) -> Option<&Track> {
        self.tracks.iter().find(|t| matches!(t.codec, Codec::Mp4v { .. }))
    }
    /// The first `mp4a` track.
    pub fn audio(&self) -> Option<&Track> {
        self.tracks.iter().find(|t| matches!(t.codec, Codec::Mp4a { .. }))
    }
}

impl Track {
    /// The codec configuration (VOL headers or AudioSpecificConfig).
    pub fn config(&self) -> &[u8] {
        match &self.codec {
            Codec::Mp4v { config, .. } | Codec::Mp4a { config, .. } => config,
            Codec::Other(_) => &[],
        }
    }
    /// Bytes of sample `i` in `file`.
    pub fn sample_data<'a>(&self, file: &'a [u8], i: usize) -> Result<&'a [u8]> {
        let s = self.samples.get(i).ok_or_else(|| Error::Invalid(format!("MP4: no sample {i}")))?;
        file.get(s.offset as usize..s.offset as usize + s.size as usize)
            .ok_or_else(|| Error::Format(format!("MP4: sample {i} beyond the end of the file")))
    }
}
