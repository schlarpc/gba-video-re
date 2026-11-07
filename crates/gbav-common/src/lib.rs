//! Shared pieces for the GBA Video decoders: the decoder interface every codec crate
//! implements, and the helpers the codec crates share:
//!
//! * [`md5`] (streaming), [`gzip`] (DEFLATE / gzip reader, zlib stored writer);
//! * [`bytes`]: little-endian readers, [`bytes::ArcSlice`], the Majesco name alphabet;
//! * [`output`]: Actimagine's GBA audio output stage (4x interpolation + noise-shaped 8-bit);
//! * [`manifest`]: the hash manifests in `verify/` and generic whole-stream verification.

pub mod bytes;
pub mod gzip;
pub mod manifest;
pub mod md5;
pub mod output;

use std::fmt;
use std::sync::Arc;

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

/// A whole ROM image, shared between the decoders of a cart without copying.
pub type Rom = Arc<[u8]>;

/// A chapter (menu entry) of a stream.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chapter {
    /// First frame of the chapter; always a seek point.
    pub frame: u32,
    pub title: Option<String>,
}

/// One playable stream on a cart (a movie, an episode, a logo, an intro).
#[derive(Debug, Clone, PartialEq)]
pub struct StreamInfo {
    /// Stable id used on the command line and in manifest names, e.g. "20200", "movie_1", "3".
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
    /// Chapters, where the container has them (empty otherwise).
    pub chapters: Vec<Chapter>,
    /// Audio sample rate before the GBA output stage, if the stream has audio.
    pub audio_rate_hz: Option<u32>,
}

impl StreamInfo {
    pub fn fps(&self) -> f64 {
        self.fps_num as f64 / self.fps_den.max(1) as f64
    }
}

/// A decoded video frame.
pub trait Frame {
    /// Bytes exactly as the family's frame manifest hashes them (VX: the 0xF000-byte YUV slot;
    /// FVMV: the 16-bit frame buffer; Majesco: the framebuffer lines the blit writes).
    fn manifest_bytes(&self) -> &[u8];
    /// Picture size in pixels.
    fn size(&self) -> (u32, u32);
    /// The picture as the cart shows it, BGR555, row-major; `out.len() == width * height`.
    fn bgr555_into(&self, out: &mut [u16]);
    /// [`Frame::bgr555_into`] into a new buffer.
    fn bgr555(&self) -> Vec<u16> {
        let (w, h) = self.size();
        let mut v = vec![0u16; (w * h) as usize];
        self.bgr555_into(&mut v);
        v
    }
}

/// The per-codec video decoder.
pub trait VideoDecoder {
    type Frame: Frame;
    /// Cold-start the decoder at `frame`, which must be a seek point.
    fn seek(&mut self, frame: u32) -> Result<()>;
    /// Decode the next frame; `Ok(None)` at end of stream.
    fn next_frame(&mut self) -> Result<Option<Self::Frame>>;
}

/// Where an audio block sits in its manifest. Families whose audio manifest is keyed by video
/// frame and block within the frame (FVMV) set `frame`; the others count blocks only.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct BlockKey {
    pub frame: Option<u32>,
    /// Block (or call) index from the start of the stream.
    pub index: u64,
}

/// The per-codec audio decoder. A *block* is the unit of the family's per-block audio
/// manifest; [`AudioDecoder::manifest_part`] gives the bytes each md5 column of that manifest
/// hashes, in column order.
pub trait AudioDecoder {
    /// Decode the next block; `Ok(false)` at the end of the stream.
    fn next_block(&mut self) -> Result<bool>;
    /// Key of the block just decoded.
    fn key(&self) -> BlockKey;
    /// Number of hashed parts per block (md5 columns of the family's audio manifest).
    fn parts(&self) -> usize {
        1
    }
    /// Hashed part `i` of the block (or call) just decoded.
    fn manifest_part(&self, i: usize) -> &[u8];
    /// Native PCM of the block just decoded, before the GBA output stage, at
    /// [`StreamInfo::audio_rate_hz`]. Exact values: some codecs exceed 16 bits.
    fn pcm(&self) -> &[i32];
    /// Decode exactly `n` output samples as one unit, for manifests that hash the player's
    /// calls rather than coded blocks (Hydrogen). `Ok(false)` at the end. Part 0 is then
    /// the call's output.
    fn decode_samples(&mut self, n: usize) -> Result<bool> {
        let _ = n;
        Err(Error::Invalid("this codec's audio has no per-call decode".into()))
    }
    /// Cold-start the audio at the position of video seek point `frame`.
    fn seek(&mut self, frame: u32) -> Result<()>;
}

/// What a codec crate exposes to the CLI.
pub trait Codec {
    /// Short family name, as in `verify/<family>/`: "vx", "fvmv", "hydrogen", "ads".
    const FAMILY: &'static str;
    type Video: VideoDecoder;
    type Audio: AudioDecoder;
    /// Streams on this cart, or `Err(Format)` if the cart is not this family.
    fn streams(rom: &[u8]) -> Result<Vec<StreamInfo>>;
    /// Video decoder for `stream`. It keeps a reference to `rom`; no stream data is copied.
    fn video(rom: &Rom, stream: &str) -> Result<Self::Video>;
    /// Audio decoder for `stream`, from power-on state.
    fn audio(rom: &Rom, stream: &str) -> Result<Self::Audio>;
}
