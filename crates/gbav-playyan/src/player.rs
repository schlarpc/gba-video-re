//! Whole-file decoding as the cartridge plays a file: what the GBA receives.
//!
//! * [`VideoPlayer`]: every video frame of an ASF (or MP4, with a video image that plays MP4)
//!   as the 240x160 frame of the DECODE_DATA_INDICATION, after the firmware's fit, colour
//!   conversion and brightness and the cartridge window's BGR555 conversion;
//! * [`thumbnail`]: the 60x40 VIDEO_INFO thumbnail (frame 0);
//! * [`AsfAudio`]: the G.726 track as headphone PCM (per ASF audio object, 16-bit mono at
//!   8 kHz, the samples the decoder hands to the output stage) and as the GBA's 8-bit speaker
//!   stream (1152-byte blocks: 576 bytes for ring A / right, 576 for ring B / left).
//!
//! The firmware decodes every frame and the frame records carry `elapsed_ms` = pts + 100 on the
//! original Play-Yan (pts + 0 on the Micro); which frames reach the GBA when the emulated chip
//! falls behind is a timing matter outside the codec (none were dropped in any capture).

use crate::asf::Asf;
use crate::firmware::{Firmware, Image};
use crate::gbapcm::GbaPcm;
use crate::{g726, m4v, pixel};
use gbav_common::{Error, Frame, Result};

/// Container of a media file, from its first bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Container {
    Asf,
    Mp4,
    Mp3,
}

pub fn detect(b: &[u8]) -> Option<Container> {
    if b.len() >= 16 && b[0..4] == [0x30, 0x26, 0xB2, 0x75] {
        return Some(Container::Asf);
    }
    if b.len() >= 8 && (&b[4..8] == b"ftyp" || &b[4..8] == b"moov" || &b[4..8] == b"mdat") {
        return Some(Container::Mp4);
    }
    if b.starts_with(b"ID3") || (b.len() >= 2 && b[0] == 0xFF && b[1] & 0xE0 == 0xE0) {
        return Some(Container::Mp3);
    }
    None
}

/// One frame as the GBA receives it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VideoFrame {
    /// Index among the decoded pictures.
    pub index: u32,
    /// Presentation time from the container, ms.
    pub pts_ms: u32,
    /// The firmware's RGB565 frame (what it writes to its memory), 240x160.
    pub rgb565: Vec<u16>,
    /// The frame as the GBA reads it through the cartridge window (BGR555), little-endian bytes.
    bytes: Vec<u8>,
}

impl VideoFrame {
    fn new(index: u32, pts_ms: u32, rgb565: Vec<u16>) -> VideoFrame {
        let bytes = rgb565.iter().flat_map(|&p| pixel::rgb565_to_bgr555(p).to_le_bytes()).collect();
        VideoFrame { index, pts_ms, rgb565, bytes }
    }
}

impl Frame for VideoFrame {
    /// The 0x12C00 bytes the GBA copies into VRAM (BGR555 LE).
    fn manifest_bytes(&self) -> &[u8] {
        &self.bytes
    }
    fn size(&self) -> (u32, u32) {
        (pixel::FRAME_W as u32, pixel::FRAME_H as u32)
    }
    fn bgr555_into(&self, out: &mut [u16]) {
        for (o, &p) in out.iter_mut().zip(&self.rgb565) {
            *o = pixel::rgb565_to_bgr555(p);
        }
    }
}

/// Video samples of a container: (pts_ms, bytes), plus the decoder configuration.
struct Samples {
    config: Vec<u8>,
    items: Vec<(u32, Vec<u8>)>,
}

fn asf_samples(asf: &Asf) -> Result<Samples> {
    let v = asf.video.as_ref().ok_or_else(|| Error::Format("ASF without a video stream".into()))?;
    if &v.fourcc != b"MP4S" && &v.fourcc != b"M4S2" && &v.fourcc != b"mp4s" {
        return Err(Error::Undefined(format!("ASF video codec {:?}", String::from_utf8_lossy(&v.fourcc))));
    }
    Ok(Samples { config: v.codec_data.clone(), items: asf.video_objects.iter().map(|o| (o.pts_ms, o.data.clone())).collect() })
}

fn mp4_samples(data: &[u8]) -> Result<Samples> {
    let m = crate::mp4::Mp4::parse(data)?;
    let v = m.video().ok_or_else(|| Error::Format("MP4 without an mp4v track".into()))?;
    let ts = v.timescale.max(1) as u64;
    let mut items = Vec::with_capacity(v.samples.len());
    for (i, s) in v.samples.iter().enumerate() {
        items.push(((s.dts * 1000 / ts) as u32, v.sample_data(data, i)?.to_vec()));
    }
    Ok(Samples { config: v.config().to_vec(), items })
}

/// The video of one file through the firmware's decoder and output stages.
pub struct VideoPlayer {
    dec: m4v::Decoder,
    fit: pixel::Fitter,
    samples: Samples,
    next: usize,
    index: u32,
    /// VIDEO_MODE_SET value (256 = neutral, the firmware's state at stream start).
    pub brightness: u32,
}

/// The video image a container needs (MP4: a Micro or v1.11 image).
pub fn video_image(fw: &Firmware, c: Container) -> Result<&Image> {
    match c {
        Container::Asf => fw.video(),
        Container::Mp4 => fw.video_mp4(),
        Container::Mp3 => None,
    }
    .ok_or_else(|| Error::Invalid(format!("no firmware image that plays {c:?} video in the given firmware source")))
}

impl VideoPlayer {
    pub fn open(img: &Image, media: &[u8]) -> Result<VideoPlayer> {
        let samples = match detect(media) {
            Some(Container::Asf) => asf_samples(&Asf::parse(media)?)?,
            Some(Container::Mp4) => mp4_samples(media)?,
            _ => return Err(Error::Format("not an ASF or MP4 file".into())),
        };
        let dec = m4v::Decoder::new(img, &samples.config)?;
        let fit = pixel::Fitter::new(&pixel::ThumbTables::from_firmware(img)?);
        Ok(VideoPlayer { dec, fit, samples, next: 0, index: 0, brightness: 256 })
    }
    pub fn frames_in_container(&self) -> usize {
        self.samples.items.len()
    }
    pub fn decoder(&self) -> &m4v::Decoder {
        &self.dec
    }
    /// The next frame; `Ok(None)` at the end.
    pub fn next_frame(&mut self) -> Result<Option<VideoFrame>> {
        while self.next < self.samples.items.len() {
            let (pts, data) = &self.samples.items[self.next];
            self.next += 1;
            match self.dec.decode(data)? {
                m4v::Decoded::Headers => continue,
                m4v::Decoded::Frame(_) | m4v::Decoded::NotCoded => {
                    let f = pixel::frame_rgb565(&mut self.fit, self.dec.picture(), self.brightness)?;
                    let i = self.index;
                    self.index += 1;
                    return Ok(Some(VideoFrame::new(i, *pts, f)));
                }
            }
        }
        Ok(None)
    }
}

/// The VIDEO_INFO thumbnail of a file (frame 0), BGR555 as the GBA receives it.
pub fn thumbnail(img: &Image, media: &[u8]) -> Result<Vec<u16>> {
    let tt = pixel::ThumbTables::from_firmware(img)?;
    let mut p = VideoPlayer::open(img, media)?;
    for (_, data) in std::mem::take(&mut p.samples.items) {
        if let m4v::Decoded::Frame(_) = p.dec.decode(&data)? {
            return pixel::thumbnail_bgr555(p.dec.picture(), &tt);
        }
    }
    Err(Error::Format("no picture in the file".into()))
}

/// The G.726 audio of an ASF file.
pub struct AsfAudio {
    dec: g726::Decoder,
    objects: Vec<Vec<u8>>,
    next: usize,
    pcm: Vec<i16>,
    /// The block the output stage plays (headphones) and converts (speaker), mono.
    block: Vec<i16>,
    gba: GbaPcm,
    blocks: std::collections::VecDeque<crate::gbapcm::Block>,
    /// The firmware's output ring (0x661c0 / 0x673c0 / 0x685c0 on py_fw0): three buffers of
    /// 1152 stereo words, zero at start; payload k decodes into buffer k % 3. It is followed by
    /// `SPILL_WORDS` words the firmware no longer uses once playing (they hold set-up data whose
    /// loss changes nothing), then live state: `None` = not written by us.
    ring: Vec<Option<i16>>,
    /// Set once the stream has left the part of the firmware's behaviour that is a function of
    /// the file alone (see `next_payload`).
    undefined: Option<String>,
}

/// Stereo words per buffer of the G.726 output ring.
const RING_WORDS: usize = 1152;
const RING_BUFS: usize = 3;
/// Words after the ring that a payload may overrun into without effect (0x697c0..0x6a370 on
/// py_fw0: memory dumps show the firmware's live data from there on, and 16 kbit/s streams with
/// Nintendo-sized payloads, which overrun by 448 words, match the firmware throughout).
const SPILL_WORDS: usize = 748;

impl AsfAudio {
    /// `volume`: the VOLUME_SET value (0..56; the GBA's default level 18 sends 28).
    pub fn open(media: &[u8], volume: i32) -> Result<AsfAudio> {
        let asf = Asf::parse(media)?;
        let a = asf.audio.as_ref().ok_or_else(|| Error::Format("ASF without an audio stream".into()))?;
        let bits = a.g726_bits().ok_or_else(|| Error::Undefined(format!("ASF audio format 0x{:x}", a.format_tag)))?;
        let rate = a.rate;
        Ok(AsfAudio {
            dec: g726::Decoder::new(bits).unwrap(),
            // the firmware decodes ASF payload by payload, not whole media objects
            objects: asf
                .audio_objects
                .into_iter()
                .flat_map(|o| {
                    let mut at = 0;
                    o.frags.iter().map(|&l| { at += l; o.data[at - l..at].to_vec() }).collect::<Vec<_>>()
                })
                .collect(),
            next: 0,
            pcm: Vec::new(),
            block: Vec::new(),
            // the video image starts with the resampler's "last" at 0 (no >16 kHz stream before)
            gba: GbaPcm::new(rate, volume, 0),
            blocks: Default::default(),
            ring: (0..RING_BUFS * RING_WORDS + SPILL_WORDS).map(|i| (i < RING_BUFS * RING_WORDS).then_some(0)).collect(),
            undefined: None,
        })
    }
    /// Decode the next audio payload: the block the headphones play for it (mono, 8 kHz);
    /// `Ok(None)` at the end.
    ///
    /// The firmware's feed (set-param 0x1271a, payload loop 0x1234e): each ASF payload's even
    /// part is decoded, `n = (len & !1) * 8 / bits` codes, into the next buffer of a
    /// three-buffer ring, and the block handed on to the output stage is
    /// `2 * ceil(n * bits / 8)` stereo words long, whatever `n` is. At 32 kbit/s the two agree;
    /// at 40 kbit/s the block's tail is whatever the buffer held before; at 16 and 24 kbit/s
    /// the block is cut short. Nintendo's payloads are 400 bytes. A payload of more than 1152
    /// samples spills into the next buffer while the DMA may still be playing it, so its
    /// headphone output is `Error::Undefined` (the speaker stream, computed from the block, stays
    /// defined); a write or a block that reaches the firmware's live data after the ring ends
    /// both streams with `Error::Undefined`.
    pub fn next_payload(&mut self) -> Result<Option<&[i16]>> {
        if !self.feed()? {
            return Ok(None);
        }
        if self.pcm.len() > RING_WORDS {
            // the decoder writes past its buffer into the one the output DMA may still be
            // playing: what reaches the headphones depends on that timing
            return Err(Error::Undefined(format!(
                "G.726 payload {} of {} samples: headphone output depends on DMA timing",
                self.next - 1,
                self.pcm.len()
            )));
        }
        Ok(Some(&self.block))
    }

    /// Decode one payload into the ring and hand its block to the speaker stage.
    fn feed(&mut self) -> Result<bool> {
        if let Some(e) = &self.undefined {
            return Err(Error::Undefined(e.clone()));
        }
        let Some(o) = self.objects.get(self.next) else { return Ok(false) };
        let k = self.next;
        self.next += 1;
        self.pcm.clear();
        self.dec.decode_payload(&o[..o.len() & !1], &mut self.pcm);
        let n = self.pcm.len();
        let words = 2 * (n * self.dec.bits() as usize).div_ceil(8);
        let base = (k % RING_BUFS) * RING_WORDS;
        if base + n.max(words) > self.ring.len() {
            let e = format!("G.726 payload {k} overruns the output ring into the firmware's live data");
            self.undefined = Some(e.clone());
            return Err(Error::Undefined(e));
        }
        for (i, &v) in self.pcm.iter().enumerate() {
            self.ring[base + i] = Some(v);
        }
        self.block.clear();
        for i in 0..words {
            let Some(v) = self.ring[base + i] else {
                let e = format!("G.726 payload {k}: its output block reads memory after the ring that holds no sample");
                self.undefined = Some(e.clone());
                return Err(Error::Undefined(e));
            };
            self.block.push(v);
        }
        let stereo: Vec<i16> = self.block.iter().flat_map(|&v| [v, v]).collect();
        self.blocks.extend(self.gba.push(&stereo));
        Ok(true)
    }
    /// The next complete 8-bit GBA block (ring A then ring B, 576 bytes each), decoding more
    /// payloads as needed.
    pub fn next_gba_block(&mut self) -> Result<Option<Vec<u8>>> {
        while self.blocks.is_empty() {
            if !self.feed()? {
                return Ok(None);
            }
        }
        let (a, b) = self.blocks.pop_front().unwrap();
        Ok(Some(a.iter().chain(b.iter()).map(|&x| x as u8).collect()))
    }
}

/// The AAC audio of an MP4 file, through a video image that plays MP4 (mi_fw0 / fup_sec0).
pub struct Mp4Audio {
    dec: crate::aac::Decoder,
    samples: Vec<Vec<u8>>,
    next: usize,
    pcm: Vec<i16>,
    gba: GbaPcm,
    blocks: std::collections::VecDeque<crate::gbapcm::Block>,
    rate: u32,
}

impl Mp4Audio {
    /// `volume`: VOLUME_SET (0..56). `prev_rate`: the sample rate of the stream the same image
    /// played before (0 after loading the image): the speaker resampler drops the first sample
    /// unless it was above 16,384 Hz.
    pub fn open(img: &Image, media: &[u8], volume: i32, prev_rate: u32) -> Result<Mp4Audio> {
        let m = crate::mp4::Mp4::parse(media)?;
        let a = m.audio().ok_or_else(|| Error::Format("MP4 without an mp4a track".into()))?;
        let dec = crate::aac::Decoder::new(img, a.config())?;
        let rate = dec.sample_rate();
        let samples = (0..a.samples.len()).map(|i| a.sample_data(media, i).map(|s| s.to_vec())).collect::<Result<Vec<_>>>()?;
        Ok(Mp4Audio { dec, samples, next: 0, pcm: Vec::new(), gba: GbaPcm::for_image(img.id, rate, volume, prev_rate), blocks: Default::default(), rate })
    }
    pub fn sample_rate(&self) -> u32 {
        self.rate
    }
    /// Decode the next AAC frame: the headphone PCM the player queues (1024 interleaved stereo
    /// frames). The player queues the decoder's output buffer for every decoded frame, also the
    /// first two, for which the decoder reports 0 samples (manifest blocks 0 and 1);
    /// `Ok(None)` at the end.
    pub fn next_frame(&mut self) -> Result<Option<&[i16]>> {
        let Some(s) = self.samples.get(self.next) else { return Ok(None) };
        self.next += 1;
        self.dec.decode_frame(s)?;
        self.pcm.clear();
        self.pcm.extend_from_slice(self.dec.pcm_buffer());
        self.blocks.extend(self.gba.push(&self.pcm));
        Ok(Some(&self.pcm))
    }
    /// The next 8-bit GBA block (ring A then ring B, 576 bytes each).
    pub fn next_gba_block(&mut self) -> Result<Option<Vec<u8>>> {
        while self.blocks.is_empty() {
            if self.next_frame()?.is_none() {
                return Ok(None);
            }
        }
        let (a, b) = self.blocks.pop_front().unwrap();
        Ok(Some(a.iter().chain(b.iter()).map(|&x| x as u8).collect()))
    }
}
