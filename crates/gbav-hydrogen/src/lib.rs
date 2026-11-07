//! Majesco Hydrogen engine: movie containers reached through `{ptr, size}` file slots, the
//! DEFLATE-variant compression (`hyCompressionManager::Inflate`), and the [`Codec`] for the 13
//! Hydrogen carts and the Sonic X Vol 2 prototype. The VQ and ADPCM layers are in
//! `gbav-majesco`; the two Hydrogen-packaged LZMA carts are decoded by `gbav-ads`, which reuses
//! [`container`] and [`engine`].
//!
//! Spec: `docs/hydrogen/format.md`; reference C: `reference/hydrogen/{hydec,hyinflate}.c`.

pub mod container;
pub mod engine;
pub mod inflate;
pub mod video;

use container::{find_containers, Container, VideoStream};
use engine::Player;
use gbav_common::{Chapter, Codec, Error, Result, Rom, StreamInfo};
use gbav_majesco::adpcm::CtabCoding;
use gbav_majesco::audio::{AudioTrack, TrackDecoder};
pub use video::HyVideo;

/// A Hydrogen cart as the decoder sees it.
#[derive(Debug, Clone)]
pub struct Cart {
    pub player: Player,
    pub containers: Vec<Container>,
    pub streams: Vec<VideoStream>,
}

impl Cart {
    /// Containers of any codec (5 = Inflate, 7 = LZMA).
    pub fn open_any(rom: &[u8]) -> Result<Cart> {
        let containers = find_containers(rom);
        if containers.is_empty() {
            return Err(Error::Format("no Hydrogen movie containers".into()));
        }
        let player = Player::detect(rom)?;
        let streams = containers
            .iter()
            .map(|c| VideoStream::parse(rom, c.video, c.video_len))
            .collect::<Result<Vec<_>>>()?;
        Ok(Cart { player, containers, streams })
    }

    /// A Hydrogen-Inflate cart: every stream has codec 5.
    pub fn open(rom: &[u8]) -> Result<Cart> {
        let c = Cart::open_any(rom)?;
        if let Some(s) = c.streams.iter().find(|s| s.codec != 5) {
            return Err(Error::Format(format!(
                "stream at {:#x} has codec {} (not Hydrogen Inflate; LZMA streams are gbav-ads)",
                s.offset, s.codec
            )));
        }
        Ok(c)
    }

    fn index(&self, id: &str) -> Result<usize> {
        id.parse::<usize>()
            .ok()
            .filter(|&i| i < self.streams.len())
            .ok_or_else(|| Error::Invalid(format!("no stream {id:?} (ids are 0..{})", self.streams.len())))
    }

    /// Gain bias for stream `i`: the boot-code bias applies after the logo (the first container).
    pub fn gain_bias(&self, i: usize) -> i32 {
        if i == 0 {
            0
        } else {
            self.player.bias_after_logo
        }
    }

    pub fn info(&self, i: usize) -> StreamInfo {
        let s = &self.streams[i];
        StreamInfo {
            id: i.to_string(),
            title: None,
            width: s.width,
            height: s.height,
            fps_num: gbav_majesco::FPS_NUM,
            fps_den: gbav_majesco::FPS_DEN,
            frames: s.frames,
            seek_points: s.seek_points(),
            chapters: s
                .chapters
                .iter()
                .filter_map(|c| {
                    let g = s.segments.get(c.segment as usize)?;
                    Some(Chapter { frame: g.frame, title: Some(c.name.clone()).filter(|t| !t.is_empty()) })
                })
                .collect(),
            audio_rate_hz: self.containers[i].audio.map(|_| gbav_majesco::adpcm::RATE),
        }
    }

    pub fn audio_track(&self, rom: &[u8], i: usize) -> Result<AudioTrack> {
        let (o, l) = self.containers[i].audio.ok_or_else(|| Error::Invalid(format!("stream {i} has no audio")))?;
        AudioTrack::parse(rom, o, l)
    }
}

/// The Hydrogen family codec. Stream ids are container indices in ROM order ("0" = the logo).
pub struct Hydrogen;

impl Codec for Hydrogen {
    const FAMILY: &'static str = "hydrogen";
    type Video = HyVideo;
    type Audio = TrackDecoder;

    fn streams(rom: &[u8]) -> Result<Vec<StreamInfo>> {
        let c = Cart::open(rom)?;
        Ok((0..c.streams.len()).map(|i| c.info(i)).collect())
    }

    fn video(rom: &Rom, stream: &str) -> Result<HyVideo> {
        let c = Cart::open(rom)?;
        let i = c.index(stream)?;
        HyVideo::new(rom, c.streams[i].clone(), &c.player, c.gain_bias(i))
    }

    fn audio(rom: &Rom, stream: &str) -> Result<TrackDecoder> {
        let c = Cart::open(rom)?;
        let i = c.index(stream)?;
        Ok(TrackDecoder::new(rom, c.audio_track(rom, i)?, CtabCoding::Flat, c.player.output))
    }
}
