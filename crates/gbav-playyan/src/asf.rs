//! ASF container, as the Play-Yan video firmware uses it: an MPEG-4 Part 2 video stream
//! (`MP4S`, codec data = the VOL header) and a G.726 audio stream (`WAVE_FORMAT` 0x0045,
//! 8 kHz mono, 32 kbit/s in Nintendo's files).
//!
//! The parser walks the Header Object (File Properties, Stream Properties) and the fixed-size
//! data packets of the Data Object, and reassembles media objects from their payloads (single
//! or multiple payloads per packet, any length-type encoding, fragments by offset). Compressed
//! payloads (replicated-data length 1) are expanded into their sub-payloads. Media objects are
//! returned in packet order per stream, which is the order the firmware decodes them.

use gbav_common::{Error, Result};

const HEADER: [u8; 16] = guid(0x75B22630, 0x668E, 0x11CF, [0xA6, 0xD9, 0x00, 0xAA, 0x00, 0x62, 0xCE, 0x6C]);
const DATA: [u8; 16] = guid(0x75B22636, 0x668E, 0x11CF, [0xA6, 0xD9, 0x00, 0xAA, 0x00, 0x62, 0xCE, 0x6C]);
const FILE_PROPS: [u8; 16] = guid(0x8CABDCA1, 0xA947, 0x11CF, [0x8E, 0xE4, 0x00, 0xC0, 0x0C, 0x20, 0x53, 0x65]);
const STREAM_PROPS: [u8; 16] = guid(0xB7DC0791, 0xA9B7, 0x11CF, [0x8E, 0xE6, 0x00, 0xC0, 0x0C, 0x20, 0x53, 0x65]);
const VIDEO_MEDIA: [u8; 16] = guid(0xBC19EFC0, 0x5B4D, 0x11CF, [0xA8, 0xFD, 0x00, 0x80, 0x5F, 0x5C, 0x44, 0x2B]);
const AUDIO_MEDIA: [u8; 16] = guid(0xF8699E40, 0x5B4D, 0x11CF, [0xA8, 0xFD, 0x00, 0x80, 0x5F, 0x5C, 0x44, 0x2B]);

const fn guid(a: u32, b: u16, c: u16, d: [u8; 8]) -> [u8; 16] {
    let a = a.to_le_bytes();
    let b = b.to_le_bytes();
    let c = c.to_le_bytes();
    [a[0], a[1], a[2], a[3], b[0], b[1], c[0], c[1], d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7]]
}

fn fmt(s: &str) -> Error {
    Error::Format(format!("ASF: {s}"))
}

fn rd(b: &[u8], o: usize, n: usize) -> Result<u64> {
    let s = b.get(o..o + n).ok_or_else(|| fmt("truncated"))?;
    Ok(s.iter().rev().fold(0u64, |a, &x| a << 8 | x as u64))
}

/// Video stream properties.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VideoInfo {
    pub stream: u8,
    pub width: u32,
    pub height: u32,
    /// BITMAPINFOHEADER biCompression fourcc (`MP4S` in Nintendo's files).
    pub fourcc: [u8; 4],
    /// Codec-specific data after the 40-byte BITMAPINFOHEADER (biSize counts it too): the MPEG-4
    /// VOL header in Nintendo's files.
    pub codec_data: Vec<u8>,
}

/// Audio stream properties (WAVEFORMATEX).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioInfo {
    pub stream: u8,
    pub format_tag: u16,
    pub channels: u16,
    pub rate: u32,
    pub avg_bytes_per_sec: u32,
    pub block_align: u16,
    pub bits_per_sample: u16,
    pub extra: Vec<u8>,
}

impl AudioInfo {
    /// G.726 bits per sample, from the byte rate (Nintendo's files: 4000 B/s at 8 kHz = 4).
    pub fn g726_bits(&self) -> Option<u32> {
        if self.format_tag != 0x45 || self.rate == 0 {
            return None;
        }
        let b = (self.avg_bytes_per_sec as u64 * 8 / self.rate as u64) as u32;
        (2..=5).contains(&b).then_some(b)
    }
}

/// One reassembled media object.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaObject {
    pub stream: u8,
    pub number: u32,
    /// Presentation time in ms (from the replicated data), as the ASF file states it.
    pub pts_ms: u32,
    pub key: bool,
    pub data: Vec<u8>,
    /// Lengths of the ASF payloads (fragments) `data` was assembled from, in order.
    pub frags: Vec<usize>,
}

/// A parsed ASF file.
#[derive(Debug, Clone)]
pub struct Asf {
    pub video: Option<VideoInfo>,
    pub audio: Option<AudioInfo>,
    /// Play duration from the File Properties Object, in 100 ns units.
    pub play_duration: u64,
    pub preroll_ms: u64,
    pub packet_size: u32,
    /// Media objects of the video stream, in stream order.
    pub video_objects: Vec<MediaObject>,
    /// Media objects of the audio stream, in stream order.
    pub audio_objects: Vec<MediaObject>,
}

struct Partial {
    stream: u8,
    number: u32,
    pts: u32,
    key: bool,
    size: usize,
    data: Vec<u8>,
    frags: Vec<usize>,
}

impl Asf {
    pub fn parse(b: &[u8]) -> Result<Asf> {
        if b.get(0..16) != Some(&HEADER[..]) {
            return Err(fmt("no ASF header object"));
        }
        let hsize = rd(b, 16, 8)? as usize;
        let nobj = rd(b, 24, 4)? as usize;
        let mut asf = Asf {
            video: None,
            audio: None,
            play_duration: 0,
            preroll_ms: 0,
            packet_size: 0,
            video_objects: Vec::new(),
            audio_objects: Vec::new(),
        };
        let mut p = 30;
        for _ in 0..nobj {
            let id = b.get(p..p + 16).ok_or_else(|| fmt("truncated header"))?;
            let sz = rd(b, p + 16, 8)? as usize;
            if sz < 24 || p + sz > hsize.max(b.len()) {
                return Err(fmt("bad header object size"));
            }
            let o = &b[p..p + sz];
            if id == FILE_PROPS {
                asf.play_duration = rd(o, 24 + 40, 8)?;
                asf.preroll_ms = rd(o, 24 + 56, 8)?;
                let minp = rd(o, 24 + 68, 4)? as u32;
                let maxp = rd(o, 24 + 72, 4)? as u32;
                if minp != maxp {
                    return Err(fmt("variable packet size"));
                }
                asf.packet_size = minp;
            } else if id == STREAM_PROPS {
                asf.stream_props(o)?;
            }
            p += sz;
        }
        p = hsize;
        if b.get(p..p + 16) != Some(&DATA[..]) {
            return Err(fmt("no data object after the header"));
        }
        let dsize = rd(b, p + 16, 8)? as usize;
        let npk = rd(b, p + 40, 8)? as usize;
        let end = if dsize >= 50 { (p + dsize).min(b.len()) } else { b.len() };
        p += 50;
        let ps = asf.packet_size as usize;
        if ps == 0 {
            return Err(fmt("packet size 0"));
        }
        let mut partial: Vec<Partial> = Vec::new();
        let mut k = 0;
        while p + ps <= end && (npk == 0 || k < npk) {
            asf.packet(&b[p..p + ps], &mut partial)?;
            p += ps;
            k += 1;
        }
        Ok(asf)
    }

    fn stream_props(&mut self, o: &[u8]) -> Result<()> {
        let ty = &o[24..40];
        let tsl = rd(o, 64, 4)? as usize;
        let flags = rd(o, 72, 2)? as u16;
        let ts = o.get(78..78 + tsl).ok_or_else(|| fmt("stream type data"))?;
        let stream = (flags & 0x7f) as u8;
        if ty == VIDEO_MEDIA {
            let width = rd(ts, 0, 4)? as u32;
            let height = rd(ts, 4, 4)? as u32;
            let fsize = rd(ts, 9, 2)? as usize;
            let bih = ts.get(11..11 + fsize).ok_or_else(|| fmt("video format data"))?;
            let mut fourcc = [0u8; 4];
            fourcc.copy_from_slice(bih.get(16..20).ok_or_else(|| fmt("BITMAPINFOHEADER"))?);
            self.video.get_or_insert(VideoInfo {
                stream,
                width,
                height,
                fourcc,
                codec_data: bih.get(40..).unwrap_or(&[]).to_vec(),
            });
        } else if ty == AUDIO_MEDIA {
            let cb = if ts.len() >= 18 { rd(ts, 16, 2)? as usize } else { 0 };
            self.audio.get_or_insert(AudioInfo {
                stream,
                format_tag: rd(ts, 0, 2)? as u16,
                channels: rd(ts, 2, 2)? as u16,
                rate: rd(ts, 4, 4)? as u32,
                avg_bytes_per_sec: rd(ts, 8, 4)? as u32,
                block_align: rd(ts, 12, 2)? as u16,
                bits_per_sample: rd(ts, 14, 2)? as u16,
                extra: ts.get(18..18 + cb).unwrap_or(&[]).to_vec(),
            });
        }
        Ok(())
    }

    fn deliver(&mut self, part: Partial) {
        let mo = MediaObject { stream: part.stream, number: part.number, pts_ms: part.pts, key: part.key, data: part.data, frags: part.frags };
        if self.video.as_ref().is_some_and(|v| v.stream == mo.stream) {
            self.video_objects.push(mo);
        } else if self.audio.as_ref().is_some_and(|a| a.stream == mo.stream) {
            self.audio_objects.push(mo);
        }
    }

    fn packet(&mut self, pk: &[u8], partial: &mut Vec<Partial>) -> Result<()> {
        let mut p = 0;
        let ec = pk[0];
        if ec & 0x80 != 0 {
            p = 1 + (ec & 0x0f) as usize;
        }
        let lt = *pk.get(p).ok_or_else(|| fmt("packet"))?;
        let pf = *pk.get(p + 1).ok_or_else(|| fmt("packet"))?;
        p += 2;
        let field = |p: &mut usize, t: u8| -> Result<u64> {
            let n = [0usize, 1, 2, 4][(t & 3) as usize];
            let v = if n == 0 { 0 } else { rd(pk, *p, n)? };
            *p += n;
            Ok(v)
        };
        let multiple = lt & 1 != 0;
        let plen = field(&mut p, lt >> 5)? as usize;
        let _seq = field(&mut p, lt >> 1)?;
        let pad = field(&mut p, lt >> 3)? as usize;
        let plen = if plen == 0 { pk.len() } else { plen.min(pk.len()) };
        p += 6; // send time, duration
        let end = plen.saturating_sub(pad);
        let (npay, plt) = if multiple {
            let f = *pk.get(p).ok_or_else(|| fmt("payload flags"))?;
            p += 1;
            ((f & 0x3f) as usize, f >> 6)
        } else {
            (1, 0)
        };
        for _ in 0..npay {
            let sn = *pk.get(p).ok_or_else(|| fmt("payload"))?;
            p += 1;
            let num = field(&mut p, pf >> 4)? as u32;
            let off = field(&mut p, pf >> 2)? as usize;
            let rl = field(&mut p, pf)? as usize;
            let len_after_rep = |p: usize, rl: usize| -> Result<usize> {
                let mut q = p + rl;
                let l = if multiple {
                    let n = [0usize, 1, 2, 4][plt as usize & 3];
                    let l = if n == 0 { 0 } else { rd(pk, q, n)? as usize };
                    q += n;
                    l
                } else {
                    end.saturating_sub(q)
                };
                Ok(q << 32 | l)
            };
            let stream = sn & 0x7f;
            let key = sn & 0x80 != 0;
            if rl == 1 {
                // compressed payload: off = presentation time, one byte of time delta, then
                // sub-payloads each prefixed by a length byte
                let _dt = *pk.get(p).ok_or_else(|| fmt("compressed payload"))?;
                let ql = len_after_rep(p, 1)?;
                let (mut q, l) = (ql >> 32, ql & 0xffff_ffff);
                let stop = (q + l).min(end);
                let mut n = num;
                while q < stop {
                    let sl = pk[q] as usize;
                    q += 1;
                    let d = pk.get(q..q + sl).ok_or_else(|| fmt("sub-payload"))?.to_vec();
                    q += sl;
                    self.deliver(Partial { stream, number: n, pts: off as u32, key, size: sl, frags: vec![d.len()], data: d });
                    n = n.wrapping_add(1);
                }
                p = stop;
                continue;
            }
            let (size, pts) = if rl >= 8 { (rd(pk, p, 4)? as usize, rd(pk, p + 4, 4)? as u32) } else { (0, 0) };
            let ql = len_after_rep(p, rl)?;
            let (q, l) = (ql >> 32, ql & 0xffff_ffff);
            let data = pk.get(q..q + l).ok_or_else(|| fmt("payload data"))?;
            p = q + l;
            // fragment assembly
            if let Some(i) = partial.iter().position(|x| x.stream == stream) {
                if partial[i].number != num || off != partial[i].data.len() {
                    // a new object before the previous one completed: deliver what we have
                    let old = partial.remove(i);
                    if off == 0 {
                        self.deliver(old);
                    }
                }
            }
            if off == 0 {
                partial.push(Partial { stream, number: num, pts, key, size: size.max(l), data: Vec::new(), frags: Vec::new() });
            }
            if let Some(i) = partial.iter().position(|x| x.stream == stream && x.number == num) {
                partial[i].data.extend_from_slice(data);
                partial[i].frags.push(data.len());
                if partial[i].data.len() >= partial[i].size {
                    let done = partial.remove(i);
                    self.deliver(done);
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sample_movie_structure() {
        let Some(d) = std::env::var_os("PY_MEDIA") else { return };
        let p = std::path::Path::new(&d).join("playan_support_sample_movie/sample_movie.asf");
        let Ok(b) = std::fs::read(p) else { return };
        let a = Asf::parse(&b).unwrap();
        let v = a.video.as_ref().unwrap();
        let au = a.audio.as_ref().unwrap();
        eprintln!("{:?} {}x{} cd={} audio {:?} vobj={} aobj={} pkt={}", std::str::from_utf8(&v.fourcc), v.width, v.height,
            v.codec_data.len(), au, a.video_objects.len(), a.audio_objects.len(), a.packet_size);
        eprintln!("first vobj {} {} {} len {}", a.video_objects[0].number, a.video_objects[0].pts_ms, a.video_objects[0].key, a.video_objects[0].data.len());
        assert_eq!(&v.fourcc, b"MP4S");
        assert_eq!(au.g726_bits(), Some(4));
    }
}
