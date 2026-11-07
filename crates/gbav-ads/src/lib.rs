//! Majesco ADS engine: the SFCD archive, `main.bin`, `.mmstr` resource lists, the `Settings` text
//! (`BaseBrightness`), and LZMA with the cartridge's fixed parameters decoded into the 128 KiB
//! ring; plus the ADS codec inside Hydrogen packaging (Jimmy Neutron 1 with its +51 gain bias
//! after the logo, the Yu-Gi-Oh! Noah's Final Threat proto). The VQ and ADPCM layers are in
//! `gbav-majesco`.
//!
//! Spec: `ads/docs/format-ads.md`; reference C: `ads/src/{ads,lzma_dec}.c`.
//!
//! Known cartridge behaviour this decoder does not reproduce: in the Noah proto's 738-frame
//! intro (`res3`) the player falls behind on a burst of 1-frame chunks and shows 41 frames with
//! the codebook of the chunk 8 earlier (its 8-slot codebook ring has not caught up). The decoder
//! produces the intended frames, like the C reference; [`video::AdsVideo::segment_vcb`] gives the
//! stale codebook to reproduce what the cartridge shows.

pub mod archive;
pub mod lzma;
pub mod video;

pub use archive::{AdsCart, Movie, Packaging};
use gbav_common::{Codec, Error, Result, StreamInfo};
use gbav_majesco::adpcm::{self, CtabCoding};
use gbav_majesco::audio::{AudioTrack, TrackDecoder};
pub use video::AdsVideo;

impl AdsCart {
    pub fn info(&self, i: usize) -> StreamInfo {
        let m = &self.movies[i];
        StreamInfo {
            id: m.id(),
            title: m.title.clone().filter(|t| !t.is_empty()),
            width: m.width,
            height: m.height,
            fps_num: gbav_majesco::FPS_NUM,
            fps_den: gbav_majesco::FPS_DEN,
            frames: m.total_frames(),
            seek_points: m.seek_points(),
            audio_rate_hz: m.audio.map(|_| adpcm::RATE),
        }
    }

    pub fn audio_track(&self, rom: &[u8], i: usize) -> Result<AudioTrack> {
        let (o, l) = self.movies[i].audio.ok_or_else(|| Error::Invalid(format!("movie {i} has no audio")))?;
        AudioTrack::parse(rom, o, l)
    }

    /// ADS players code context tables with the palette VLC; the Hydrogen engine uses the flat form.
    pub fn ctab_coding(&self) -> CtabCoding {
        match self.packaging {
            Packaging::Sfcd => CtabCoding::Vlc,
            Packaging::Hydrogen => CtabCoding::Flat,
        }
    }

    fn index(&self, id: &str) -> Result<usize> {
        self.find(id).ok_or_else(|| Error::Invalid(format!("no stream {id:?}")))
    }
}

/// The ADS family codec. Stream ids: SFCD member names with spaces as underscores
/// ("movie_1", "logo_majesco", "jingle"), or "res<N>" on the Hydrogen-packaged carts.
pub struct Ads;

impl Codec for Ads {
    const FAMILY: &'static str = "ads";
    type Video = AdsVideo;
    type Audio = TrackDecoder;

    fn streams(rom: &[u8]) -> Result<Vec<StreamInfo>> {
        let c = AdsCart::open(rom)?;
        Ok((0..c.movies.len()).map(|i| c.info(i)).collect())
    }

    fn video(rom: &[u8], stream: &str) -> Result<AdsVideo> {
        let c = AdsCart::open(rom)?;
        let i = c.index(stream)?;
        AdsVideo::new(rom, &c, i)
    }

    fn audio(rom: &[u8], stream: &str) -> Result<TrackDecoder> {
        let c = AdsCart::open(rom)?;
        let i = c.index(stream)?;
        let out = gbav_hydrogen::engine::adpcm_output_stage(rom);
        Ok(TrackDecoder::new(rom, c.audio_track(rom, i)?, c.ctab_coding(), out))
    }
}
