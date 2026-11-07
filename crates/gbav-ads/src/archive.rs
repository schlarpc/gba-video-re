//! ADS packaging: the SFCD archive, `.mmstr` resource lists, the `Settings` text, and the
//! Hydrogen packaging of the two LZMA carts (`ads/docs/format-ads.md` sections 2-4).

use crate::lzma;
use gbav_common::{Error, Result};
use gbav_hydrogen::container::{find_containers, VideoStream};
use gbav_majesco::util::deobfuscate;
use gbav_majesco::vq::{find_dither, SegmentWord};
use gbav_majesco::{rd16, rd32};

/// One chunk (segment): a codebook blob A and an index blob B, both LZMA.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chunk {
    pub offset: usize,
    pub word: SegmentWord,
    pub first_frame: u32,
    pub a_off: usize,
    pub a_len: usize,
    pub b_off: usize,
    pub b_len: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chapter {
    pub chunk: u32,
    pub name: String,
}

/// How a cart packages its movies.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Packaging {
    /// SFCD archive of `.mmstr` members (the 19 ADS carts)
    Sfcd,
    /// Hydrogen movie containers (Jimmy Neutron 1, the Yu-Gi-Oh! Noah's Final Threat proto)
    Hydrogen,
}

/// A movie: one video resource and (usually) one audio resource.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Movie {
    /// SFCD member name without ".mmstr" ("movie_1", "logo majesco", "jingle"), or "res<N>"
    pub name: String,
    pub mm_off: usize,
    pub mm_len: usize,
    pub v_off: usize,
    pub v_len: usize,
    /// type-2 resource payload, if any
    pub audio: Option<(usize, usize)>,
    pub hdr0: u32,
    pub hdr1: u32,
    pub frames: u32,
    pub width: u32,
    pub height: u32,
    pub title: Option<String>,
    pub chapters: Vec<Chapter>,
    pub chunks: Vec<Chunk>,
}

impl Movie {
    /// The id used in manifests and on the command line: the name with spaces as underscores.
    pub fn id(&self) -> String {
        self.name.replace(' ', "_")
    }
    pub fn total_frames(&self) -> u32 {
        self.chunks.iter().map(|c| c.word.frames as u32).sum()
    }
    /// Chunk holding stream frame `f` (`ads_video_seek_frame`: the last chunk starting at or
    /// before it).
    pub fn chunk_of(&self, f: u32) -> usize {
        let mut i = 0;
        while i + 1 < self.chunks.len() && self.chunks[i + 1].first_frame <= f {
            i += 1;
        }
        i
    }
    pub fn seek_points(&self) -> Vec<u32> {
        let mut v: Vec<u32> = self.chunks.iter().filter(|c| c.word.frames > 0).map(|c| c.first_frame).collect();
        v.dedup();
        v
    }
}

/// An ADS-codec cart: its movies plus the player tables the decoder needs.
#[derive(Debug, Clone)]
pub struct AdsCart {
    pub packaging: Packaging,
    /// ROM offset of the SFCD archive
    pub sfcd: Option<usize>,
    pub movies: Vec<Movie>,
    /// the player's ordered-dither table
    pub dither: [u8; 16],
    /// the player's 11 brightness gains (level 5 = 256 is the default)
    pub gains: [i32; 11],
    /// `BaseBrightness` from menuresources' Settings text
    pub base_brightness: Option<i32>,
    /// gain bias from the Settings text: trunc(BaseBrightness * 10000 * 256 / 100000)
    pub settings_bias: i32,
    /// Hydrogen packaging: gain bias the boot code sets after the logo (Jimmy Neutron: 51)
    pub bias_after_logo: i32,
}

/// `find_tables`: the dither table (16 bytes before the block-geometry table, searched over the
/// whole ROM) and the 11 brightness gains (11 increasing s32 below 1024, the first in 1..255,
/// the sixth = 256, at a 4-byte-aligned offset).
fn find_tables(rom: &[u8]) -> Result<([u8; 16], [i32; 11])> {
    let d = find_dither(rom, rom.len()).ok_or_else(|| Error::Format("ADS player dither table not found".into()))?;
    let mut dither = [0u8; 16];
    dither.copy_from_slice(&rom[d..d + 16]);
    let mut i = 0;
    while i + 44 <= rom.len() {
        let g0 = rd32(rom, i);
        if rd32(rom, i + 20) == 256 && g0 != 0 && g0 < 256 {
            let inc = (1..11).all(|k| {
                let (a, b) = (rd32(rom, i + 4 * k - 4), rd32(rom, i + 4 * k));
                b > a && b < 1024
            });
            if inc {
                let gains = std::array::from_fn(|k| rd32(rom, i + 4 * k) as i32);
                return Ok((dither, gains));
            }
        }
        i += 4;
    }
    Err(Error::Format("ADS player brightness table not found".into()))
}

fn nul(rom: &[u8], from: usize, end: usize) -> Option<usize> {
    rom.get(from..end.min(rom.len()))?.iter().position(|&b| b == 0).map(|z| from + z)
}

/// Parse an ADS-packaging video resource (`parse_video` with size_in_bytes = 0).
fn parse_video_ads(rom: &[u8], v_off: usize, v_len: usize) -> Option<(u32, u32, Option<String>, Vec<Chapter>, Vec<Chunk>)> {
    let end = v_off + v_len;
    let (hdr0, hdr1) = (rd32(rom, v_off), rd32(rom, v_off + 4));
    let nchap = hdr0 & 0xff;
    let nchunks = rd32(rom, v_off + 8);
    let mut p = v_off + 12;
    let z = nul(rom, p, end)?;
    let title = deobfuscate(&rom[p..z]);
    p += (z - p + 4) & !3;
    let mut chapters = Vec::new();
    for _ in 0..nchap {
        let ci = rd16(rom, p) as u32;
        let z = nul(rom, p + 2, end)?;
        chapters.push(Chapter { chunk: ci, name: deobfuscate(&rom[p + 2..z]) });
        p += 2 + ((z - (p + 2) + 4) & !3);
    }
    p = (p + 3) & !3;
    let mut chunks = Vec::new();
    let mut f = 0u32;
    for _ in 0..nchunks {
        if p + 8 > end {
            return None;
        }
        let (w0, w1) = (rd32(rom, p), rd32(rom, p + 4));
        let word = SegmentWord::parse(w0);
        let a_len = (w1 & 0x1fff) as usize * 4;
        let b_len = (w1 >> 13) as usize * 4;
        let c = Chunk { offset: p, word, first_frame: f, a_off: p + 8, a_len, b_off: p + 8 + a_len, b_len };
        f += word.frames as u32;
        p = c.b_off + b_len;
        if p > end || p > rom.len() {
            return None;
        }
        chunks.push(c);
    }
    Some((hdr0, hdr1, Some(title), chapters, chunks))
}

fn dims(hdr1: u32) -> (u32, u32) {
    if hdr1 & 0xfff == 0x307 {
        ((hdr1 >> 12) & 0x3ff, hdr1 >> 22) // layout B
    } else {
        ((hdr1 >> 10) & 0x3ff, (hdr1 >> 20) & 0x3ff) // layout A
    }
}

/// `add_mmstr` for an ADS `.mmstr` member: resource sizes in words.
fn add_mmstr(rom: &[u8], name: &str, off: usize, len: usize) -> Option<Movie> {
    let total = rd32(rom, off) as usize;
    if total > len || off + total > rom.len() {
        return None;
    }
    let (mut v, mut a) = (None, None);
    let mut p = off + 8;
    while p + 4 <= off + total {
        let w = rd32(rom, p);
        let (typ, n) = (w >> 28, (w & 0x0fff_ffff) as usize * 4);
        if typ == 1 && v.is_none() {
            v = Some((p + 4, n));
        }
        if typ == 2 && a.is_none() {
            a = Some((p + 4, n));
        }
        p += 4 + n;
    }
    let (v_off, v_len) = v?;
    let (hdr0, hdr1, title, chapters, chunks) = parse_video_ads(rom, v_off, v_len)?;
    let (width, height) = dims(hdr1);
    Some(Movie {
        name: name.to_owned(),
        mm_off: off,
        mm_len: total,
        v_off,
        v_len,
        audio: a,
        hdr0,
        hdr1,
        frames: (hdr0 >> 8) & 0xfffff,
        width,
        height,
        title,
        chapters,
        chunks,
    })
}

/// `read_settings`: the type-5 text resource in menuresources.mmstr; returns BaseBrightness.
fn read_settings(rom: &[u8], off: usize, len: usize) -> Option<i32> {
    let mut p = off + 8;
    while p + 4 <= off + len {
        let w = rd32(rom, p);
        let (typ, n) = (w >> 28, (w & 0x0fff_ffff) as usize * 4);
        if typ == 5 && n > 24 {
            let mut q = p + 8;
            while q + 8 < p + 4 + n && q < p + 64 {
                let (us, par) = (rd32(rom, q), rd32(rom, q + 4));
                if par & 0xffff_00c7 != 0x0001_0002 || us == 0 || us > 0x8000 {
                    q += 4;
                    continue;
                }
                let end = (p + 4 + n).min(rom.len());
                if let Ok(txt) = lzma::decode_blob(&rom[q..end], us as usize) {
                    let text = deobfuscate(&txt);
                    for line in text.split(['\n', '\0']) {
                        if let Some(rest) = line.strip_prefix("BaseBrightness") {
                            if let Some(v) = scan_int(rest) {
                                return Some(v);
                            }
                        }
                    }
                }
                break;
            }
        }
        p += 4 + n;
    }
    None
}

/// `sscanf(" %d")`: skip whitespace, optional sign, digits.
fn scan_int(s: &str) -> Option<i32> {
    let t = s.trim_start();
    let (neg, t) = match t.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    let digits: String = t.chars().take_while(|c| c.is_ascii_digit()).collect();
    let v: i64 = digits.parse().ok()?;
    Some(if neg { -v } else { v } as i32)
}

impl AdsCart {
    /// Open an ADS-codec cart: an SFCD archive in the first 2 MiB, or else Hydrogen containers
    /// whose video streams have codec 7 (LZMA).
    pub fn open(rom: &[u8]) -> Result<AdsCart> {
        let sfcd = (0..rom.len().min(0x20_0000).saturating_sub(3)).step_by(4).find(|&i| &rom[i..i + 4] == b"SFCD");
        let (dither, gains) = find_tables(rom)?;
        let mut cart = AdsCart {
            packaging: if sfcd.is_some() { Packaging::Sfcd } else { Packaging::Hydrogen },
            sfcd,
            movies: Vec::new(),
            dither,
            gains,
            base_brightness: None,
            settings_bias: 0,
            bias_after_logo: 0,
        };
        match sfcd {
            Some(s) => cart.open_sfcd(rom, s),
            None => cart.open_hydrogen(rom)?,
        }
        if cart.movies.is_empty() {
            return Err(Error::Format("no ADS movies".into()));
        }
        Ok(cart)
    }

    fn open_sfcd(&mut self, rom: &[u8], s: usize) {
        let hdr = rd16(rom, s + 4) as usize;
        let n = rd32(rom, s + 8) as usize;
        let data = s + 8 + hdr;
        let mut p = s + 12;
        for _ in 0..n {
            if p + 8 > rom.len() {
                break;
            }
            let (sz, off) = (rd32(rom, p) as usize, rd32(rom, p + 4) as usize);
            let nm = &rom[p + 8..(p + 8 + 64).min(rom.len())];
            let l = nm.iter().position(|&b| b == 0).unwrap_or(nm.len());
            let name = String::from_utf8_lossy(&nm[..l]).into_owned();
            p = (p + 8 + l + 4) & !3;
            if let Some(base) = name.strip_suffix(".mmstr").filter(|_| l > 6) {
                if base.eq_ignore_ascii_case("menuresources") {
                    if let Some(v) = read_settings(rom, data + off, sz) {
                        self.base_brightness = Some(v);
                        self.settings_bias = ((v as i64 * 10000 * 256) / 100000) as i32;
                    }
                } else if let Some(m) = add_mmstr(rom, base, data + off, sz) {
                    self.movies.push(m);
                }
            }
        }
    }

    fn open_hydrogen(&mut self, rom: &[u8]) -> Result<()> {
        for c in find_containers(rom) {
            let Ok(v) = VideoStream::parse(rom, c.video, c.video_len) else { continue };
            if v.codec != 7 {
                return Err(Error::Format(format!(
                    "Hydrogen container at {:#x} has codec {} (not LZMA; Inflate streams are gbav-hydrogen)",
                    c.offset, v.codec
                )));
            }
            let k = self.movies.len();
            let chunks = v
                .segments
                .iter()
                .map(|g| Chunk {
                    offset: g.offset,
                    word: g.word,
                    first_frame: g.frame,
                    a_off: g.cb_off,
                    a_len: g.cb_len,
                    b_off: g.idx_off,
                    b_len: g.idx_len,
                })
                .collect();
            // the ADS reference reads resource lengths as "bytes after the entry word", which
            // is 4 more than the Hydrogen reference's payload length
            let audio = c.audio.map(|(o, l)| (o, l + 4));
            self.movies.push(Movie {
                name: format!("res{k}"),
                mm_off: c.offset,
                mm_len: (rd32(rom, c.offset) >> 4) as usize,
                v_off: c.video,
                v_len: c.video_len + 4,
                audio,
                hdr0: v.w0,
                hdr1: v.w1,
                frames: v.total_frames,
                width: v.width,
                height: v.height,
                title: None,
                chapters: v.chapters.iter().map(|c| Chapter { chunk: c.segment as u32, name: c.name.clone() }).collect(),
                chunks,
            });
        }
        if let Ok(p) = gbav_hydrogen::engine::Player::detect(rom) {
            self.bias_after_logo = p.bias_after_logo;
        }
        Ok(())
    }

    /// Movie by id ("movie_1", "logo_majesco", "res3"), name, or index.
    pub fn find(&self, spec: &str) -> Option<usize> {
        if let Ok(i) = spec.parse::<usize>() {
            return (i < self.movies.len()).then_some(i);
        }
        self.movies.iter().position(|m| m.id() == spec || m.name == spec)
    }

    /// The gain bias the player uses for movie `i`: the Settings bias on SFCD carts; on the
    /// Hydrogen-packaged carts the boot-code bias for everything after the logo (movie 0).
    pub fn gain_bias(&self, i: usize) -> i32 {
        match self.packaging {
            Packaging::Sfcd => self.settings_bias,
            Packaging::Hydrogen => {
                if i == 0 {
                    0
                } else {
                    self.bias_after_logo
                }
            }
        }
    }
}
