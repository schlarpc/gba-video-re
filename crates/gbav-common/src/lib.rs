//! Shared pieces for the GBA Video decoders: the decoder interface every codec crate
//! implements, plus helpers (md5, gzip/DEFLATE, GBA output stages) shared by the codec crates.

pub mod gzip;
pub mod md5;

use std::fmt;

/// Errors a decoder can report. Codec crates map their own conditions onto these.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// The ROM is not one this crate handles, or a structure in it is malformed.
    Format(String),
    /// The bitstream hit a condition the cart's own code does not define (the reference C
    /// decoders flag these too); decoding stops.
    Undefined(String),
    /// A stream id, frame or option that does not exist.
    Invalid(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Format(s) => write!(f, "format error: {s}"),
            Error::Undefined(s) => write!(f, "undefined input: {s}"),
            Error::Invalid(s) => write!(f, "invalid request: {s}"),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

/// One playable stream on a cart (a movie, an episode, a logo, an intro).
#[derive(Debug, Clone, PartialEq)]
pub struct StreamInfo {
    /// Stable id used on the command line and in manifest names, e.g. "20200", "movie_1", "ep103".
    pub id: String,
    /// Human title if the cart provides one.
    pub title: Option<String>,
    pub width: u32,
    pub height: u32,
    /// Exact frame rate as a ratio of integers (e.g. GBA clock / cycles per frame).
    pub fps_num: u64,
    pub fps_den: u64,
    pub frames: u32,
    /// Frame indices that a cold-start decoder can begin at.
    pub seek_points: Vec<u32>,
    /// Audio sample rate before the GBA output stage, if the stream has audio.
    pub audio_rate_hz: Option<u32>,
}

/// A decoded video frame in the codec's native output plus the cart's displayed form.
pub trait Frame {
    /// Bytes exactly as the family's hash manifest defines them (see the crate docs: VX hashes
    /// the 0xF000-byte YUV ring slot, FVMV the 16-bit frame buffer, Majesco the framebuffer
    /// lines the blit writes).
    fn manifest_bytes(&self) -> &[u8];
    /// The picture as the cart shows it: BGR555 words, `width * height`, row-major.
    fn bgr555(&self) -> Vec<u16>;
}

/// The per-codec decoder interface the `gbav` CLI drives.
pub trait VideoDecoder {
    type Frame: Frame;
    /// Start a cold decoder at `frame`, which must be a seek point.
    fn seek(&mut self, frame: u32) -> Result<()>;
    /// Decode the next frame; `Ok(None)` at end of stream.
    fn next_frame(&mut self) -> Result<Option<Self::Frame>>;
}

/// Audio decoder interface. `block` granularity matches the family's audio manifest.
pub trait AudioDecoder {
    /// Decode the next audio block as the family's manifest defines it; `Ok(None)` at end.
    fn next_block(&mut self) -> Result<Option<Vec<u8>>>;
    /// Native PCM (signed 16-bit, mono) for the block just decoded, before the GBA output stage.
    fn last_pcm(&self) -> &[i16];
}

/// What a codec crate exposes to the CLI and to `gbav-rom`.
pub trait Codec {
    /// Short family name: "vx", "fvmv", "hydrogen", "ads".
    const FAMILY: &'static str;
    type Video: VideoDecoder;
    type Audio: AudioDecoder;
    /// Streams on this cart, or `Err(Format)` if the cart is not this family.
    fn streams(rom: &[u8]) -> Result<Vec<StreamInfo>>;
    fn video(rom: &[u8], stream: &str) -> Result<Self::Video>;
    fn audio(rom: &[u8], stream: &str) -> Result<Self::Audio>;
}
