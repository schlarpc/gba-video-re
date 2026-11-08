//! `gbav` on Play-Yan media: a media file plus a firmware source (the user's Play-Yan or
//! Play-Yan Micro ROM, or `playan.fup`) instead of a cart.
//!
//!   gbav list   MEDIA --firmware FW...
//!   gbav decode MEDIA --firmware FW... --out DIR [--png|--ppm|--raw] [--wav] [--gba-audio]
//!               [--brightness N] [--volume N] [--start-frame N] [--count M] [--image NAME]
//!
//! MEDIA is ASF (MPEG-4 + G.726), MP4 (MPEG-4 + AAC; needs a Micro or playan.fup video image)
//! or MP3. `--raw` writes each frame as the GBA receives it (240x160 BGR555 LE, 0x12C00 bytes);
//! `--wav` the headphone PCM; `--gba-audio` the 8-bit speaker stream (gba_audio.raw: per block
//! 576 bytes ring A (right) then 576 bytes ring B (left), as DECODE_DATA_INDICATION carries it).

use crate::output::{bgr555_to_rgb8, png_rgb8, ppm_rgb8};
use crate::Args;
use gbav_common::{Error, Frame, Result};
use gbav_playyan::player::{self, Container, VideoPlayer};
use gbav_playyan::{Firmware, ImageId};
use std::io::Write;
use std::path::{Path, PathBuf};

pub fn wants(args: &[String]) -> bool {
    args.iter().any(|a| a == "--firmware")
}

fn firmware(a: &Args) -> Result<Firmware> {
    let paths: Vec<PathBuf> = a.opts.iter().filter(|(n, _)| n == "firmware").filter_map(|(_, v)| v.as_ref().map(PathBuf::from)).collect();
    if paths.is_empty() {
        return Err(Error::Invalid("--firmware FILE is required (Play-Yan ROM, Play-Yan Micro ROM or playan.fup)".into()));
    }
    Firmware::from_paths(&paths)
}

fn pick_image<'a>(fw: &'a Firmware, a: &Args, default: Option<&'a gbav_playyan::Image>) -> Result<&'a gbav_playyan::Image> {
    if let Some(n) = a.value("image") {
        let id = ImageId::ALL.into_iter().find(|i| i.name() == n).ok_or_else(|| Error::Invalid(format!("--image: unknown image {n}")))?;
        return fw.image(id).ok_or_else(|| Error::Invalid(format!("{n} is not in the firmware source")));
    }
    default.ok_or_else(|| Error::Invalid("the firmware source has no image for this file".into()))
}

fn media(a: &Args) -> Result<(PathBuf, Vec<u8>, Container)> {
    let p = match a.positional.as_slice() {
        [p] => PathBuf::from(p),
        _ => return Err(Error::Invalid("expected one media file".into())),
    };
    let b = std::fs::read(&p).map_err(|e| Error::Invalid(format!("{}: {e}", p.display())))?;
    let c = player::detect(&b).ok_or_else(|| Error::Format(format!("{}: not an ASF, MP4 or MP3 file", p.display())))?;
    Ok((p, b, c))
}

pub fn cmd_list(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["firmware", "image"])?;
    let fw = firmware(&a)?;
    let (p, b, c) = media(&a)?;
    println!("file      {}", p.display());
    println!("container {c:?}");
    println!("firmware  {}", fw.images.iter().map(|i| i.id.name()).collect::<Vec<_>>().join(" "));
    match c {
        Container::Asf | Container::Mp4 => {
            let img = pick_image(&fw, &a, player::video_image(&fw, c).ok())?;
            let vp = VideoPlayer::open(img, &b)?;
            if let Some(v) = vp.decoder().vol() {
                println!("video     MPEG-4 SP {}x{}, {} quantisation, {} samples (image {})", v.width, v.height,
                    if v.quant_type == 1 { "MPEG" } else { "H.263" }, vp.frames_in_container(), img.id.name());
            }
            if c == Container::Asf {
                let asf = gbav_playyan::asf::Asf::parse(&b)?;
                if let Some(au) = &asf.audio {
                    println!("audio     G.726 {} kbit/s, {} Hz, {} objects", au.g726_bits().unwrap_or(0) * 8, au.rate, asf.audio_objects.len());
                }
            } else {
                let m = gbav_playyan::mp4::Mp4::parse(&b)?;
                if let Some(t) = m.audio() {
                    if let gbav_playyan::mp4::Codec::Mp4a { channels, sample_rate, .. } = &t.codec {
                        println!("audio     AAC {} Hz, {} channels, {} frames", sample_rate, channels, t.samples.len());
                    }
                }
            }
            let t = player::thumbnail(img, &b)?;
            println!("thumbnail md5 {}  Garage hash {:08X}", gbav_common::md5::hex_u16(&t), gbav_playyan::pixel::garage_hash(&t));
        }
        Container::Mp3 => {
            let f = gbav_playyan::mp3file::Mp3File::parse(&b)?;
            let id = f.id3();
            println!("title     {}", String::from_utf8_lossy(id.title_bytes()));
            println!("artist    {}", String::from_utf8_lossy(id.artist_bytes()));
            println!("frames    {}", f.frames().count());
        }
    }
    Ok(true)
}

struct StereoWav {
    f: std::fs::File,
    n: u32,
    ch: u16,
}
impl StereoWav {
    fn create(p: &Path, ch: u16) -> Result<StereoWav> {
        let mut f = std::fs::File::create(p).map_err(|e| Error::Invalid(format!("{}: {e}", p.display())))?;
        f.write_all(&[0u8; 44]).map_err(io)?;
        Ok(StereoWav { f, n: 0, ch })
    }
    fn write(&mut self, s: &[i16]) -> Result<()> {
        let b: Vec<u8> = s.iter().flat_map(|x| x.to_le_bytes()).collect();
        self.n += s.len() as u32;
        self.f.write_all(&b).map_err(io)
    }
    fn finish(mut self, rate: u32) -> Result<()> {
        use std::io::Seek;
        let data = self.n * 2;
        let mut h = Vec::new();
        h.extend_from_slice(b"RIFF");
        h.extend_from_slice(&(36 + data).to_le_bytes());
        h.extend_from_slice(b"WAVEfmt ");
        h.extend_from_slice(&16u32.to_le_bytes());
        h.extend_from_slice(&1u16.to_le_bytes());
        h.extend_from_slice(&self.ch.to_le_bytes());
        h.extend_from_slice(&rate.to_le_bytes());
        h.extend_from_slice(&(rate * 2 * self.ch as u32).to_le_bytes());
        h.extend_from_slice(&(2 * self.ch).to_le_bytes());
        h.extend_from_slice(&16u16.to_le_bytes());
        h.extend_from_slice(b"data");
        h.extend_from_slice(&data.to_le_bytes());
        self.f.seek(std::io::SeekFrom::Start(0)).map_err(io)?;
        self.f.write_all(&h).map_err(io)
    }
}

fn io(e: std::io::Error) -> Error {
    Error::Invalid(e.to_string())
}

pub fn cmd_decode(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["firmware", "image", "out", "start-frame", "count", "brightness", "volume"])?;
    let fw = firmware(&a)?;
    let (_, b, c) = media(&a)?;
    let out = PathBuf::from(a.value("out").ok_or_else(|| Error::Invalid("--out DIR is required".into()))?);
    std::fs::create_dir_all(&out).map_err(io)?;
    let volume = a.number("volume")?.unwrap_or(28) as i32;
    let fmt = match (a.flag("png"), a.flag("ppm"), a.flag("raw")) {
        (_, false, false) => "png",
        (false, true, false) => "ppm",
        (false, false, true) => "raw",
        _ => return Err(Error::Invalid("choose one of --png, --ppm, --raw".into())),
    };
    let mut nframes = 0u32;
    let mut nsamples = 0usize;
    if matches!(c, Container::Asf | Container::Mp4) {
        let img = pick_image(&fw, &a, player::video_image(&fw, c).ok())?;
        let mut vp = VideoPlayer::open(img, &b)?;
        vp.brightness = a.number("brightness")?.unwrap_or(256);
        let start = a.number("start-frame")?.unwrap_or(0);
        let count = a.number("count")?.unwrap_or(u32::MAX);
        let mut list = std::fs::File::create(out.join("frames.txt")).map_err(io)?;
        while let Some(f) = vp.next_frame()? {
            if f.index < start {
                continue;
            }
            if f.index - start >= count {
                break;
            }
            let name = out.join(format!("{:06}.{fmt}", f.index));
            let data = match fmt {
                "raw" => f.manifest_bytes().to_vec(),
                k => {
                    let rgb = bgr555_to_rgb8(&f.bgr555());
                    if k == "png" { png_rgb8(240, 160, &rgb) } else { ppm_rgb8(240, 160, &rgb) }
                }
            };
            std::fs::write(&name, data).map_err(io)?;
            writeln!(list, "{} {} {}", f.index, f.pts_ms, gbav_common::md5::hex(f.manifest_bytes())).map_err(io)?;
            nframes += 1;
        }
    }
    if a.flag("wav") || a.flag("gba-audio") {
        let mut gba = if a.flag("gba-audio") { Some(std::fs::File::create(out.join("gba_audio.raw")).map_err(io)?) } else { None };
        match c {
            Container::Asf => {
                let mut au = player::AsfAudio::open(&b, volume)?;
                let mut wav = if a.flag("wav") { Some(StereoWav::create(&out.join("audio.wav"), 1)?) } else { None };
                while let Some(pcm) = if wav.is_some() { au.next_payload()? } else { None } {
                    nsamples += pcm.len();
                    if let Some(w) = wav.as_mut() {
                        w.write(pcm)?;
                    }
                }
                if let Some(w) = wav {
                    w.finish(8000)?;
                }
                if let Some(g) = gba.as_mut() {
                    let mut au = player::AsfAudio::open(&b, volume)?;
                    while let Some(blk) = au.next_gba_block()? {
                        g.write_all(&blk).map_err(io)?;
                    }
                }
            }
            Container::Mp3 => {
                let img = pick_image(&fw, &a, fw.music())?;
                let f = gbav_playyan::mp3file::Mp3File::parse(&b)?;
                let mut pl = gbav_playyan::mp3::Player::new(img, &f)?;
                let mut wav = if a.flag("wav") { Some(StereoWav::create(&out.join("audio.wav"), 2)?) } else { None };
                let mut rate = 0;
                let mut gp: Option<gbav_playyan::gbapcm::GbaPcm> = None;
                while let Some((fr, pcm)) = pl.next_frame()? {
                    rate = fr.rate;
                    let Some(pcm) = pcm else { continue };
                    nsamples += pcm.len() / 2;
                    if let Some(w) = wav.as_mut() {
                        w.write(pcm)?;
                    }
                    if let Some(g) = gba.as_mut() {
                        let gp = gp.get_or_insert_with(|| gbav_playyan::gbapcm::GbaPcm::for_image(img.id, rate, volume, 0));
                        for (ra, rb) in gp.push(pcm) {
                            let blk: Vec<u8> = ra.iter().chain(rb.iter()).map(|&x| x as u8).collect();
                            g.write_all(&blk).map_err(io)?;
                        }
                    }
                }
                if let Some(w) = wav {
                    w.finish(rate)?;
                }
            }
            Container::Mp4 => {
                let img = pick_image(&fw, &a, fw.video_mp4())?;
                let mut au = player::Mp4Audio::open(img, &b, volume, 0)?;
                let rate = au.sample_rate();
                let mut wav = if a.flag("wav") { Some(StereoWav::create(&out.join("audio.wav"), 2)?) } else { None };
                while let Some(pcm) = au.next_frame()? {
                    nsamples += pcm.len() / 2;
                    if let Some(w) = wav.as_mut() {
                        w.write(pcm)?;
                    }
                }
                if let Some(w) = wav {
                    w.finish(rate)?;
                }
                if let Some(g) = gba.as_mut() {
                    let mut au = player::Mp4Audio::open(img, &b, volume, 0)?;
                    while let Some(blk) = au.next_gba_block()? {
                        g.write_all(&blk).map_err(io)?;
                    }
                }
            }
        }
    }
    println!("{nframes} frames, {nsamples} audio samples written to {}", out.display());
    Ok(true)
}

/// `gbav verify --firmware FW... --manifests DIR MEDIA...`: every media file against the
/// manifests `<image>_<file stem>.*_md5.csv.gz` that exist for it in DIR.
pub fn cmd_verify(args: &[String]) -> Result<bool> {
    let a = Args::parse(args, &["firmware", "manifests", "volume", "prev-rate"])?;
    let prev_rate = a.number("prev-rate")?.unwrap_or(0);
    let fw = firmware(&a)?;
    let dir = PathBuf::from(a.value("manifests").ok_or_else(|| Error::Invalid("--manifests DIR is required".into()))?);
    let volume = a.number("volume")?.unwrap_or(28) as i32;
    let mut all = true;
    for m in &a.positional {
        let p = PathBuf::from(m);
        let b = std::fs::read(&p).map_err(|e| Error::Invalid(format!("{}: {e}", p.display())))?;
        let stem = gbav_playyan::verify::manifest_stem(&dir, &p, &b);
        let mut any = false;
        for img in &fw.images {
            let prefix = format!("{}_{}", img.id.name(), stem);
            if !std::fs::read_dir(&dir).map_err(io)?.flatten().any(|e| e.file_name().to_string_lossy().starts_with(&format!("{prefix}."))) {
                continue;
            }
            any = true;
            let (video, music) = if img.id.is_video() { (Some(img), fw.music()) } else { (fw.video(), Some(img)) };
            let r = gbav_playyan::verify::verify_with(video, music, &b, &dir, &prefix, volume, prev_rate)?;
            let show = |n: &str, c: Option<gbav_playyan::verify::Count>| {
                c.map(|c| format!(" {n} {}/{}{}", c.ok, c.total, c.first_bad.map(|i| format!(" (first bad {i})")).unwrap_or_default())).unwrap_or_default()
            };
            println!(
                "{}  {prefix}:{}{}{}{}",
                if r.passed() { "OK  " } else { "FAIL" },
                show("frames", r.frames),
                show("thumbs", r.thumbs),
                show("headphone", r.audio),
                show("gba8", r.audio8)
            );
            all &= r.passed();
        }
        if !any {
            println!("SKIP  {}: no manifests for it in {}", p.display(), dir.display());
        }
    }
    Ok(all)
}
