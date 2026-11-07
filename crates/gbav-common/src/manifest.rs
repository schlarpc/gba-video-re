//! The hash manifests in `verify/` and whole-stream verification against them.
//!
//! Layout of `verify/`:
//!
//! * `carts.csv`: `family,short_id,sha1,nointro` for every cart with manifests;
//! * `known.csv`: `family,short_id,stream,kind,first,last,reason`, ranges of frames or audio
//!   blocks where the cart itself shows something other than the decoded stream (an emulated
//!   audio failure, a stale codebook); `last` empty = to the end;
//! * `<family>/<short_id>_<stream>.frame_md5.csv.gz`: one row per frame, first column the frame
//!   number, the first md5 column the hash of [`Frame::manifest_bytes`];
//! * `<family>/<stem>.audio_md5.csv.gz` (optional, large): one row per audio block, or per player
//!   call when the header has an `n` column (Hydrogen). The md5 columns hash
//!   [`AudioDecoder::manifest_part`] 0, 1, ... in order. A `frame` column, when present, is
//!   checked against [`BlockKey::frame`], the `block`/`call` column against [`BlockKey::index`];
//! * `<family>/<stem>.audio_seg_md5.csv.gz` (always): the same audio as about 30-second
//!   segments, `segment,first_block,blocks,<md5 columns>` (block units) or
//!   `segment,first_sample,samples,md5` (sample units, decoded with
//!   [`AudioDecoder::decode_samples`]). Each md5 is over the concatenated bytes of that part for
//!   the whole segment. Segments only cover blocks that matched the per-block manifest when
//!   they were made, so they never include a known cart-side difference.
//!
//! Verification uses the per-block audio manifest when it is present and the segment manifest
//! otherwise.

use crate::md5::{self, Md5};
use crate::{gzip, AudioDecoder, Error, Frame, Result, VideoDecoder};
use std::path::{Path, PathBuf};

/// The `verify/` directory: `$GBAV_VERIFY`, else the one in this source tree.
pub fn verify_dir() -> Option<PathBuf> {
    let cands = [
        std::env::var_os("GBAV_VERIFY").map(PathBuf::from),
        Some(Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("verify")),
    ];
    cands.into_iter().flatten().find(|p| p.join("carts.csv").is_file())
}

/// Split one CSV line (RFC 4180 quoting).
pub fn split_csv(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quoted = false;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' if quoted && chars.peek() == Some(&'"') => {
                cur.push('"');
                chars.next();
            }
            '"' => quoted = !quoted,
            ',' if !quoted => out.push(std::mem::take(&mut cur)),
            _ => cur.push(c),
        }
    }
    out.push(cur);
    out
}

fn read_csv(path: &Path) -> Result<Vec<Vec<String>>> {
    let text = std::fs::read_to_string(path).map_err(|e| Error::Invalid(format!("{}: {e}", path.display())))?;
    Ok(text.lines().skip(1).filter(|l| !l.is_empty()).map(split_csv).collect())
}

/// One row of `verify/carts.csv`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cart {
    pub family: String,
    pub short_id: String,
    pub sha1: String,
    pub nointro: String,
}

pub fn carts(dir: &Path) -> Result<Vec<Cart>> {
    Ok(read_csv(&dir.join("carts.csv"))?
        .into_iter()
        .map(|f| Cart {
            family: f[0].clone(),
            short_id: f[1].clone(),
            sha1: f[2].clone(),
            nointro: f.get(3).cloned().unwrap_or_default(),
        })
        .collect())
}

/// Frames or audio blocks the cart itself shows differently (`verify/known.csv`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Known {
    pub family: String,
    pub short_id: String,
    pub stream: String,
    /// "video" or "audio".
    pub kind: String,
    pub first: u64,
    pub last: Option<u64>,
    pub reason: String,
}

impl Known {
    pub fn contains(&self, i: u64) -> bool {
        i >= self.first && self.last.is_none_or(|l| i <= l)
    }
}

pub fn known(dir: &Path) -> Result<Vec<Known>> {
    let p = dir.join("known.csv");
    if !p.is_file() {
        return Ok(Vec::new());
    }
    read_csv(&p)?
        .into_iter()
        .map(|f| {
            let num = |s: &str| s.parse::<u64>().map_err(|_| Error::Format(format!("known.csv: bad number {s:?}")));
            Ok(Known {
                family: f[0].clone(),
                short_id: f[1].clone(),
                stream: f[2].clone(),
                kind: f[3].clone(),
                first: num(&f[4])?,
                last: if f[5].is_empty() { None } else { Some(num(&f[5])?) },
                reason: f.get(6).cloned().unwrap_or_default(),
            })
        })
        .collect()
}

/// Manifest file stem of a stream: `<short_id>_<stream id>`, spaces as underscores.
pub fn stem(short_id: &str, stream: &str) -> String {
    format!("{short_id}_{}", stream.replace(' ', "_"))
}

/// A decompressed manifest.
#[derive(Debug, Clone)]
pub struct Manifest {
    pub header: Vec<String>,
    pub rows: Vec<Vec<String>>,
}

impl Manifest {
    /// Read a gzipped CSV manifest; `Ok(None)` if the file does not exist.
    pub fn load(path: &Path) -> Result<Option<Manifest>> {
        let gz = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(Error::Invalid(format!("{}: {e}", path.display()))),
        };
        let text = String::from_utf8(gzip::gunzip(&gz)?)
            .map_err(|_| Error::Format(format!("{}: not UTF-8", path.display())))?;
        let mut lines = text.lines();
        let header = lines.next().unwrap_or("").split(',').map(str::to_owned).collect();
        let rows = lines.filter(|l| !l.is_empty()).map(|l| l.split(',').map(str::to_owned).collect()).collect();
        Ok(Some(Manifest { header, rows }))
    }

    pub fn col(&self, name: &str) -> Option<usize> {
        self.header.iter().position(|h| h == name)
    }

    /// Indices of the md5 columns, in order.
    pub fn md5_cols(&self) -> Vec<usize> {
        (0..self.header.len()).filter(|&i| self.header[i].contains("md5")).collect()
    }

    pub fn num(&self, row: usize, col: usize) -> Result<u64> {
        let s = &self.rows[row][col];
        s.parse().map_err(|_| Error::Format(format!("manifest: bad number {s:?} in column {}", self.header[col])))
    }

    /// gzip-compressed CSV text of this manifest (stored blocks; for small manifests).
    pub fn to_gzip(&self) -> Vec<u8> {
        let mut text = self.header.join(",");
        text.push('\n');
        for r in &self.rows {
            text.push_str(&r.join(","));
            text.push('\n');
        }
        gzip::gzip_stored(text.as_bytes())
    }
}

/// Paths of a stream's manifests.
#[derive(Debug, Clone)]
pub struct StreamManifests {
    pub frames: PathBuf,
    pub audio: PathBuf,
    pub audio_seg: PathBuf,
}

impl StreamManifests {
    pub fn new(dir: &Path, family: &str, short_id: &str, stream: &str) -> StreamManifests {
        let base = dir.join(family).join(stem(short_id, stream));
        let with = |ext: &str| PathBuf::from(format!("{}.{ext}", base.display()));
        StreamManifests {
            frames: with("frame_md5.csv.gz"),
            audio: with("audio_md5.csv.gz"),
            audio_seg: with("audio_seg_md5.csv.gz"),
        }
    }
}

/// Outcome of a check. `known_bad` counts mismatches inside a [`Known`] range (expected).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Check {
    /// Units compared (frames, blocks, calls or segments).
    pub total: u64,
    pub ok: u64,
    pub known_bad: u64,
    /// First unexpected mismatch.
    pub first_bad: Option<u64>,
    /// Units the decoder did not produce (stream ended early).
    pub missing: u64,
    /// "frames", "blocks", "calls" or "segments".
    pub unit: &'static str,
}

impl Check {
    /// No unexpected mismatch and nothing missing.
    pub fn passed(&self) -> bool {
        self.ok + self.known_bad == self.total && self.missing == 0
    }
    fn bad(&mut self, i: u64, known: bool) {
        if known {
            self.known_bad += 1;
        } else if self.first_bad.is_none() {
            self.first_bad = Some(i);
        }
    }
}

impl std::fmt::Display for Check {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}/{} {}", self.ok, self.total, self.unit)?;
        if self.known_bad > 0 {
            write!(f, " (+{} known cart-side)", self.known_bad)?;
        }
        if let Some(b) = self.first_bad {
            write!(f, ", first bad {b}")?;
        }
        if self.missing > 0 {
            write!(f, ", {} not decoded", self.missing)?;
        }
        Ok(())
    }
}

/// Check every frame of `dec` (positioned at frame 0) against a frame manifest.
pub fn check_video<V: VideoDecoder>(dec: &mut V, m: &Manifest, known: &[&Known]) -> Result<Check> {
    let md5c = *m.md5_cols().first().ok_or_else(|| Error::Format("frame manifest without md5 column".into()))?;
    let mut want = std::collections::BTreeMap::new();
    for r in 0..m.rows.len() {
        if !m.rows[r][md5c].is_empty() {
            want.insert(m.num(r, 0)?, r);
        }
    }
    let mut c = Check { total: want.len() as u64, unit: "frames", ..Default::default() };
    let last = want.keys().next_back().copied();
    let mut k = 0u64;
    while last.is_some_and(|l| k <= l) {
        let Some(f) = dec.next_frame()? else { break };
        if let Some(&r) = want.get(&k) {
            if md5::hex(f.manifest_bytes()) == m.rows[r][md5c] {
                c.ok += 1;
            } else {
                c.bad(k, known.iter().any(|x| x.kind == "video" && x.contains(k)));
            }
        }
        k += 1;
    }
    c.missing = want.range(k..).count() as u64;
    Ok(c)
}

/// Check audio from power-on against a per-block (or per-call) manifest. With `on_row`, the
/// callback sees every unit (row index, matched in full, decoder) after it is decoded. With a
/// `samples` column only that prefix of a block is hashed (the cart decoded no further).
pub fn check_audio_blocks<A: AudioDecoder>(
    dec: &mut A,
    m: &Manifest,
    known: &[&Known],
    mut on_row: Option<&mut dyn FnMut(usize, bool, &A)>,
) -> Result<Check> {
    let md5c = m.md5_cols();
    let per_call = m.col("n");
    let frame_col = m.col("frame");
    let idx_col = m.col("block").or(m.col("call"));
    // `samples` (ADS, Sonic X 2 proto): the cart decoded only that many bytes of the last block
    let samples_col = if per_call.is_none() { m.col("samples") } else { None };
    let mut c = Check { total: m.rows.len() as u64, unit: if per_call.is_some() { "calls" } else { "blocks" }, ..Default::default() };
    for r in 0..m.rows.len() {
        let more = match per_call {
            Some(nc) => dec.decode_samples(m.num(r, nc)? as usize)?,
            None => dec.next_block()?,
        };
        if !more {
            c.missing = (m.rows.len() - r) as u64;
            break;
        }
        let key = dec.key();
        let mut ok = frame_col.is_none_or(|fc| key.frame.map(u64::from) == m.num(r, fc).ok());
        if per_call.is_none() {
            ok &= idx_col.is_none_or(|ic| Some(key.index) == m.num(r, ic).ok());
        }
        let mut whole = true;
        ok &= md5c.iter().enumerate().all(|(i, &col)| {
            let part = dec.manifest_part(i);
            let n = samples_col.and_then(|sc| m.num(r, sc).ok()).map_or(part.len(), |n| (n as usize).min(part.len()));
            whole &= n == part.len();
            md5::hex(&part[..n]) == m.rows[r][col]
        });
        if let Some(f) = on_row.as_mut() {
            // a block the cart decoded only in part can not go into a segment
            f(r, ok && whole, dec);
        }
        if ok {
            c.ok += 1;
        } else {
            c.bad(r as u64, known.iter().any(|x| x.kind == "audio" && x.contains(r as u64)));
        }
    }
    Ok(c)
}

/// Check audio from power-on against a segment manifest.
pub fn check_audio_segments<A: AudioDecoder>(dec: &mut A, m: &Manifest, nparts: usize) -> Result<Check> {
    let md5c = m.md5_cols();
    let samples = m.col("first_sample").is_some();
    let mut c = Check { total: m.rows.len() as u64, unit: "segments", ..Default::default() };
    let mut pos = 0u64;
    for r in 0..m.rows.len() {
        let (first, count) = (m.num(r, 1)?, m.num(r, 2)?);
        let mut h: Vec<Md5> = (0..nparts.max(1)).map(|_| Md5::new()).collect();
        let mut complete = true;
        if samples {
            if first > pos && !dec.decode_samples((first - pos) as usize)? {
                complete = false;
            }
            if complete && dec.decode_samples(count as usize)? {
                h[0].update(dec.manifest_part(0));
            } else {
                complete = false;
            }
            pos = first + count;
        } else {
            while complete && pos < first {
                complete = dec.next_block()?;
                pos += 1;
            }
            for _ in 0..count {
                if !complete || !dec.next_block()? {
                    complete = false;
                    break;
                }
                for (i, m5) in h.iter_mut().enumerate() {
                    m5.update(dec.manifest_part(i));
                }
                pos += 1;
            }
        }
        if !complete {
            c.missing = (m.rows.len() - r) as u64;
            break;
        }
        let got: Vec<String> = h.into_iter().map(Md5::finalize_hex).collect();
        if md5c.iter().enumerate().all(|(i, &col)| got.get(i) == Some(&m.rows[r][col])) {
            c.ok += 1;
        } else {
            c.bad(r as u64, false);
        }
    }
    Ok(c)
}

/// Check a stream's audio with the best manifest available: per-block if present, else
/// segments. `make_dec` gives a decoder at power-on. `Ok(None)` if there is no audio manifest.
pub fn check_audio<A: AudioDecoder>(
    make_dec: impl Fn() -> Result<A>,
    paths: &StreamManifests,
    known: &[&Known],
) -> Result<Option<Check>> {
    if let Some(m) = Manifest::load(&paths.audio)? {
        return Ok(Some(check_audio_blocks(&mut make_dec()?, &m, known, None)?));
    }
    if let Some(m) = Manifest::load(&paths.audio_seg)? {
        let mut d = make_dec()?;
        let n = d.parts();
        return Ok(Some(check_audio_segments(&mut d, &m, n)?));
    }
    Ok(None)
}

/// Build the segment manifest of a stream from its per-block manifest: decode from power-on,
/// check every unit against `m`, and cut the matching units into segments of about
/// `seconds` of audio at `rate` Hz. A unit that does not match ends the current segment and is
/// left out, and so is every unit in a `known` audio range. Units are blocks, or samples for a
/// per-call manifest (header column `n`).
pub fn make_segments<A: AudioDecoder>(
    dec: &mut A,
    m: &Manifest,
    known: &[&Known],
    rate: u32,
    seconds: u32,
) -> Result<(Manifest, Check)> {
    let per_call = m.col("n").is_some();
    let names: Vec<String> = m.md5_cols().iter().map(|&i| m.header[i].clone()).collect();
    let nparts = names.len();
    let target = rate as u64 * seconds as u64;
    struct Seg {
        first: u64,
        count: u64,
        samples: u64,
        h: Vec<Md5>,
    }
    let mut segs: Vec<(u64, u64, Vec<String>)> = Vec::new();
    let mut cur: Option<Seg> = None;
    let mut unit_pos = 0u64; // blocks decoded, or samples decoded
    let close = |cur: &mut Option<Seg>, segs: &mut Vec<(u64, u64, Vec<String>)>| {
        if let Some(s) = cur.take() {
            if s.count > 0 {
                segs.push((s.first, s.count, s.h.into_iter().map(Md5::finalize_hex).collect()));
            }
        }
    };
    let check = {
    let mut cb = |r: usize, ok: bool, d: &A| {
        let ok = ok && !known.iter().any(|x| x.kind == "audio" && x.contains(r as u64));
        let len = if per_call { d.manifest_part(0).len() as u64 } else { 1 };
        let samples = d.pcm().len() as u64;
        if ok {
            let s = cur.get_or_insert_with(|| Seg {
                first: unit_pos,
                count: 0,
                samples: 0,
                h: (0..nparts).map(|_| Md5::new()).collect(),
            });
            for (i, m5) in s.h.iter_mut().enumerate() {
                m5.update(d.manifest_part(i));
            }
            s.count += len;
            s.samples += if per_call { len } else { samples };
            if s.samples >= target {
                close(&mut cur, &mut segs);
            }
        } else {
            close(&mut cur, &mut segs);
        }
        unit_pos += len;
    };
    check_audio_blocks(dec, m, known, Some(&mut cb))?
    };
    close(&mut cur, &mut segs);
    let mut header = vec!["segment".to_string()];
    if per_call {
        header.extend(["first_sample".into(), "samples".into(), names[0].clone()]);
    } else {
        header.extend(["first_block".into(), "blocks".into()]);
        header.extend(names);
    }
    let rows = segs
        .into_iter()
        .enumerate()
        .map(|(i, (first, count, h))| {
            let mut r = vec![i.to_string(), first.to_string(), count.to_string()];
            r.extend(h.into_iter().take(if per_call { 1 } else { nparts }));
            r
        })
        .collect();
    Ok((Manifest { header, rows }, check))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::BlockKey;

    #[test]
    fn csv_quoting() {
        assert_eq!(split_csv(r#"a,"b, c","d ""e""",f"#), ["a", "b, c", r#"d "e""#, "f"]);
        assert_eq!(stem("dbgt", "logo majesco"), "dbgt_logo_majesco");
    }

    #[test]
    fn repo_manifests() {
        let Some(dir) = verify_dir() else { return };
        assert_eq!(carts(&dir).unwrap().len(), 46);
        let m = Manifest::load(&dir.join("vx/shrek-sharktale-rev5_3dd3600.frame_md5.csv.gz")).unwrap().unwrap();
        assert_eq!(m.rows.len(), 595);
        assert_eq!(m.md5_cols(), [1, 2]);
    }

    /// Blocks of 4 samples; block k's single part is `[k; 4]` and its PCM `[k; 4]`.
    struct Mock {
        k: u64,
        n: u64,
        part: Vec<u8>,
        pcm: Vec<i32>,
        pos: u64, // samples decoded
    }
    impl Mock {
        fn new(n: u64) -> Mock {
            Mock { k: 0, n, part: vec![], pcm: vec![], pos: 0 }
        }
        fn sample(i: u64) -> u8 {
            (i / 4) as u8
        }
    }
    impl AudioDecoder for Mock {
        fn next_block(&mut self) -> Result<bool> {
            if self.k >= self.n {
                return Ok(false);
            }
            self.part = (0..4).map(|j| Mock::sample(self.pos + j)).collect();
            self.pcm = self.part.iter().map(|&b| b as i32).collect();
            self.k += 1;
            self.pos += 4;
            Ok(true)
        }
        fn key(&self) -> BlockKey {
            BlockKey { frame: None, index: self.k - 1 }
        }
        fn manifest_part(&self, _i: usize) -> &[u8] {
            &self.part
        }
        fn pcm(&self) -> &[i32] {
            &self.pcm
        }
        fn decode_samples(&mut self, n: usize) -> Result<bool> {
            if self.pos + n as u64 > self.n * 4 {
                return Ok(false);
            }
            self.part = (0..n as u64).map(|j| Mock::sample(self.pos + j)).collect();
            self.pcm = self.part.iter().map(|&b| b as i32).collect();
            self.pos += n as u64;
            self.k += 1;
            Ok(true)
        }
        fn seek(&mut self, _f: u32) -> Result<()> {
            unreachable!()
        }
    }

    fn block_manifest(n: u64, bad: &[u64]) -> Manifest {
        Manifest {
            header: vec!["block".into(), "md5".into()],
            rows: (0..n)
                .map(|k| vec![k.to_string(), if bad.contains(&k) { "x".repeat(32) } else { md5::hex(&[k as u8; 4]) }])
                .collect(),
        }
    }

    #[test]
    fn segments_round_trip_and_skip_mismatches() {
        let m = block_manifest(40, &[13]);
        // 4 samples per block at "rate" 1 Hz, 10 s segments = 3 blocks (12 samples)
        let (seg, c) = make_segments(&mut Mock::new(40), &m, &[], 1, 10).unwrap();
        assert_eq!((c.ok, c.total, c.first_bad), (39, 40, Some(13)));
        assert_eq!(seg.header, ["segment", "first_block", "blocks", "md5"]);
        let firsts: Vec<u64> = (0..seg.rows.len()).map(|r| seg.num(r, 1).unwrap()).collect();
        assert_eq!(&firsts[..6], &[0, 3, 6, 9, 12, 14]); // block 13 left out
        let c = check_audio_segments(&mut Mock::new(40), &seg, 1).unwrap();
        assert!(c.passed(), "{c}");
        // a decoder that differs in block 20 fails exactly one segment
        let mut seg2 = seg.clone();
        let r = seg2.rows.iter().position(|r| r[1] == "20" || (r[1].parse::<u64>().unwrap() <= 20 && r[1].parse::<u64>().unwrap() + 3 > 20)).unwrap();
        seg2.rows[r][3] = "0".repeat(32);
        let c = check_audio_segments(&mut Mock::new(40), &seg2, 1).unwrap();
        assert_eq!((c.ok + 1, c.first_bad), (c.total, Some(r as u64)));
    }

    #[test]
    fn per_call_segments() {
        // calls of 3 and 5 samples over 40 blocks x 4 samples = 160 samples
        let mut rows = Vec::new();
        let (mut pos, mut i) = (0u64, 0);
        while pos + 5 <= 160 {
            let n = if i % 2 == 0 { 3 } else { 5 };
            let bytes: Vec<u8> = (0..n).map(|j| Mock::sample(pos + j)).collect();
            rows.push(vec![i.to_string(), n.to_string(), md5::hex(&bytes)]);
            pos += n;
            i += 1;
        }
        let m = Manifest { header: vec!["call".into(), "n".into(), "md5".into()], rows };
        let c = check_audio_blocks(&mut Mock::new(40), &m, &[], None).unwrap();
        assert!(c.passed(), "{c}");
        let (seg, _) = make_segments(&mut Mock::new(40), &m, &[], 1, 20).unwrap();
        assert_eq!(seg.header[1], "first_sample");
        let c = check_audio_segments(&mut Mock::new(40), &seg, 1).unwrap();
        assert!(c.passed() && c.total >= 7, "{c}");
        // gzip round trip of the generated manifest
        let dir = std::env::temp_dir().join(format!("gbav-seg-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("x.csv.gz");
        std::fs::write(&p, seg.to_gzip()).unwrap();
        let back = Manifest::load(&p).unwrap().unwrap();
        assert_eq!((back.header, back.rows), (seg.header, seg.rows));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
