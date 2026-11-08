//! Verification against the Play-Yan hash manifests made from the real firmware running in the
//! full-system emulator (`<image>_<file>.<kind>_md5.csv.gz`):
//!
//! * `frame_md5`: `frame,gba_cycle,bgr555_md5` -- every 240x160 frame the GBA received;
//! * `thumb_md5`: `thumb,bgr555_md5` -- the 60x40 VIDEO_INFO thumbnails (all of frame 0);
//! * `audio_md5`: `block,pcm_frame,frames,rate,md5` -- headphone PCM, 16-bit stereo interleaved,
//!   `frames` frames from stream frame `pcm_frame` (counted from 2: the two words the codec
//!   receives before the stream);
//! * `audio8_md5`: `block,bytes,gba_cycle,md5` -- the GBA's 8-bit blocks (ring A then ring B);
//!   blocks of other sizes than 1152 bytes are OPERATION_SOUND clicks and are skipped.

use crate::firmware::Image;
use crate::player::{self, Container, VideoPlayer};
use gbav_common::{md5, Error, Frame, Result};
use std::path::Path;

/// Counts for one manifest kind.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Count {
    pub ok: usize,
    pub total: usize,
    /// First failing index, if any.
    pub first_bad: Option<usize>,
}

impl Count {
    fn add(&mut self, i: usize, ok: bool) {
        self.total += 1;
        if ok {
            self.ok += 1;
        } else if self.first_bad.is_none() {
            self.first_bad = Some(i);
        }
    }
}

#[derive(Debug, Default, Clone)]
pub struct Report {
    pub frames: Option<Count>,
    pub thumbs: Option<Count>,
    pub audio: Option<Count>,
    pub audio8: Option<Count>,
}

impl Report {
    pub fn passed(&self) -> bool {
        [self.frames, self.thumbs, self.audio, self.audio8].iter().flatten().all(|c| c.ok == c.total)
    }
}

fn csv(path: &Path) -> Result<Option<Vec<Vec<String>>>> {
    let Ok(b) = std::fs::read(path) else { return Ok(None) };
    let t = gbav_common::gzip::gunzip(&b)?;
    let s = String::from_utf8_lossy(&t);
    Ok(Some(s.lines().skip(1).filter(|l| !l.is_empty()).map(|l| l.split(',').map(str::to_string).collect()).collect()))
}

/// Check one media file (decoded with `img` for video/MP4 audio and `music` for MP3) against
/// the manifests `<dir>/<prefix>.*_md5.csv.gz`.
pub fn verify(img: Option<&Image>, music: Option<&Image>, media: &[u8], dir: &Path, prefix: &str, volume: i32) -> Result<Report> {
    verify_with(img, music, media, dir, prefix, volume, 0)
}

/// [`verify`] with the speaker resampler's previous-stream rate (`prev_rate`, see
/// [`crate::gbapcm::GbaPcm::new`]).
pub fn verify_with(img: Option<&Image>, music: Option<&Image>, media: &[u8], dir: &Path, prefix: &str, volume: i32, prev_rate: u32) -> Result<Report> {
    let c = player::detect(media).ok_or_else(|| Error::Format("not an ASF, MP4 or MP3 file".into()))?;
    let mut rep = Report::default();
    let p = |k: &str| dir.join(format!("{prefix}.{k}_md5.csv.gz"));
    if matches!(c, Container::Asf | Container::Mp4) {
        let img = img.ok_or_else(|| Error::Invalid("no video image for this file".into()))?;
        if let Some(rows) = csv(&p("frame"))? {
            let mut vp = VideoPlayer::open(img, media)?;
            let mut cnt = Count::default();
            for (i, r) in rows.iter().enumerate() {
                let ok = match vp.next_frame()? {
                    Some(f) => md5::hex(f.manifest_bytes()) == r[2],
                    None => false,
                };
                cnt.add(i, ok);
            }
            rep.frames = Some(cnt);
        }
        if let Some(rows) = csv(&p("thumb"))? {
            let t = player::thumbnail(img, media)?;
            let h = md5::hex_u16(&t);
            let mut cnt = Count::default();
            for (i, r) in rows.iter().enumerate() {
                cnt.add(i, h == r[1]);
            }
            rep.thumbs = Some(cnt);
        }
    }
    // headphone PCM and GBA blocks as two flat streams
    let (mut hp, mut gba): (Vec<i16>, Vec<u8>) = (Vec::new(), Vec::new());
    let want_audio = p("audio").is_file() || p("audio8").is_file();
    if want_audio {
        match c {
            Container::Asf => {
                let mut a = player::AsfAudio::open(media, volume)?;
                while let Some(pcm) = a.next_payload()? {
                    hp.extend(pcm.iter().flat_map(|&s| [s, s]));
                }
                let mut a = player::AsfAudio::open(media, volume)?;
                while let Some(b) = a.next_gba_block()? {
                    gba.extend(b);
                }
            }
            Container::Mp4 => {
                let img = img.ok_or_else(|| Error::Invalid("no MP4 video image".into()))?;
                let mut a = player::Mp4Audio::open(img, media, volume, 0)?;
                while let Some(pcm) = a.next_frame()? {
                    hp.extend_from_slice(pcm);
                }
                let mut a = player::Mp4Audio::open(img, media, volume, prev_rate)?;
                while let Some(b) = a.next_gba_block()? {
                    gba.extend(b);
                }
            }
            Container::Mp3 => {
                let music = music.ok_or_else(|| Error::Invalid("no music image".into()))?;
                let f = crate::mp3file::Mp3File::parse(media)?;
                let mut pl = crate::mp3::Player::new(music, &f)?;
                let mut gp: Option<crate::gbapcm::GbaPcm> = None;
                while let Some((fr, pcm)) = pl.next_frame()? {
                    let Some(pcm) = pcm else { continue };
                    hp.extend_from_slice(pcm);
                    let g = gp.get_or_insert_with(|| crate::gbapcm::GbaPcm::for_image(music.id, fr.rate, volume, prev_rate));
                    for (ra, rb) in g.push(pcm) {
                        gba.extend(ra.iter().chain(rb.iter()).map(|&x| x as u8));
                    }
                }
            }
        }
    }
    if let Some(rows) = csv(&p("audio"))? {
        let bytes: Vec<u8> = hp.iter().flat_map(|s| s.to_le_bytes()).collect();
        let mut cnt = Count::default();
        for (i, r) in rows.iter().enumerate() {
            let (st, n): (usize, usize) = (r[1].parse().unwrap_or(0), r[2].parse().unwrap_or(0));
            let (a, b) = (st.saturating_sub(2) * 4, (st.saturating_sub(2) + n) * 4);
            cnt.add(i, bytes.get(a..b).is_some_and(|s| md5::hex(s) == r[4]));
        }
        rep.audio = Some(cnt);
    }
    if let Some(rows) = csv(&p("audio8"))? {
        let mut cnt = Count::default();
        let mut k = 0;
        for (i, r) in rows.iter().enumerate() {
            if r[1] != "1152" {
                continue;
            }
            cnt.add(i, gba.get(k * 1152..(k + 1) * 1152).is_some_and(|s| md5::hex(s) == r[3]));
            k += 1;
        }
        rep.audio8 = Some(cnt);
    }
    Ok(rep)
}

/// The manifest stem for a media file: the stem listed for its md5 in `DIR/media.csv`
/// (columns `stem,file,md5,...`), else the file's own stem. Two of Nintendo's sample files differ
/// only in letter case, so their manifests are named by source instead (`_direct`, `_zip`).
pub fn manifest_stem(dir: &Path, path: &Path, bytes: &[u8]) -> String {
    let own = path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let Ok(csv) = std::fs::read_to_string(dir.join("media.csv")) else { return own };
    let md5 = gbav_common::md5::hex(bytes);
    csv.lines()
        .skip(1)
        .find_map(|l| {
            let mut c = l.split(',');
            let (stem, _, m) = (c.next()?, c.next()?, c.next()?);
            (m == md5).then(|| stem.to_string())
        })
        .unwrap_or(own)
}
