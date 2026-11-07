//! Actimagine VX++ and VXGB video + VX audio decoder (Shrek, Shark Tale, Shrek 2,
//! Shrek + Shark Tale GBA Video carts).
//!
//! A bit-exact port of the reference C decoder (decoder/vxdec.c, residual.c,
//! vxgb_residual.c) and of container/vxaudio.py, verified against the emulator hash
//! manifests and differentially against the C decoder (see PORT-NOTES.md).
//!
//! * The codec is picked by the stream magic: `VX++` (Rev 6 player) or `VXGB` (Rev 5 player).
//! * The player's RAM images are located through the boot loader's program table
//!   ([`cart::PlayerLayout`]); every table the C code generated at build time (blit clamp
//!   table, VX++ coefficient VLC, VXGB CAVLC tables, dequant scales) is read from them at
//!   runtime ([`tables::Tables`]). The fill op lists are checked-in code structure, not ROM data
//!   ([`fill`]).
//! * Frame manifest bytes = the 0xF000-byte decoder slot (Y 256x160 stride 256, then
//!   interleaved U,V 256x80); the displayed picture is the player's mode-3 BGR555 blit
//!   ([`blit`], brightness 0).
//! * Audio blocks = one AFrame's 512 bytes of signed 8-bit 65536 Hz output; native PCM =
//!   its 128 samples at 16384 Hz.

pub mod audio;
pub mod bits;
pub mod blit;
pub mod cart;
mod cavlc;
mod fill;
mod fill_data;
mod residual;
pub mod tables;
pub mod util;
pub mod video;

use std::ops::Range;
use std::sync::Arc;

use gbav_common::{AudioDecoder, Codec, Error, Frame, Result, StreamInfo, VideoDecoder};

pub use cart::{find_streams, pick_stream, PlayerLayout, SeekEntry, StreamHeader, Variant};
pub use tables::Tables;
pub use video::{Core, Stats};

use audio::{AudioState, Codebook, OUT_BYTES, SAMPLES};
use blit::BlitLut;
use video::SLOTSZ;

/// GBA CPU clock (Hz); Timer 3 runs at clock/64 and a frame lasts 3 ticks.
pub const GBA_CLOCK: u64 = 1 << 24;
/// AFrame sample rate (Hz) before the 4x output stage.
pub const AUDIO_RATE: u32 = 16384;

/// The VX family codec.
pub struct Vx;

fn info_of(rom: &[u8], h: &StreamHeader) -> StreamInfo {
    StreamInfo {
        id: h.id(),
        title: None,
        width: h.width,
        height: h.height,
        fps_num: GBA_CLOCK,
        fps_den: 64 * 3 * h.timer.max(1) as u64,
        frames: h.frames,
        seek_points: h.seek_points(rom),
        audio_rate_hz: h.audio_range(rom.len()).map(|_| AUDIO_RATE),
    }
}

impl Codec for Vx {
    const FAMILY: &'static str = "vx";
    type Video = VxVideo<Vec<u8>>;
    type Audio = VxAudio<Vec<u8>>;

    fn streams(rom: &[u8]) -> Result<Vec<StreamInfo>> {
        let st = find_streams(rom);
        if st.is_empty() {
            return Err(Error::Format("no VX++ / VXGB stream".into()));
        }
        // the cart must carry a player we have tables for
        for h in &st {
            Tables::from_rom(rom, h.variant)?;
        }
        Ok(st.iter().map(|h| info_of(rom, h)).collect())
    }

    /// Copies the stream's video bytes; see [`VxVideo::open`] to decode from a borrowed or
    /// shared ROM instead.
    fn video(rom: &[u8], stream: &str) -> Result<Self::Video> {
        let h = pick_stream(rom, stream)?;
        let r = h.video_range(rom.len());
        let len = r.len();
        VxVideo::with_data(rom, h, rom[r].to_vec(), 0..len)
    }

    fn audio(rom: &[u8], stream: &str) -> Result<Self::Audio> {
        let h = pick_stream(rom, stream)?;
        let r = h.audio_range(rom.len()).ok_or_else(|| Error::Invalid(format!("stream {} has no audio", h.id())))?;
        let len = r.len();
        VxAudio::with_data(rom, h, rom[r].to_vec(), 0..len)
    }
}

// ---------------------------------------------------------------- video

/// One decoded frame.
#[derive(Clone)]
pub struct VxFrame {
    /// Frame number in the stream.
    pub index: u32,
    /// The 0xF000-byte decoder slot (the manifest bytes).
    pub slot: Vec<u8>,
    pub width: u32,
    pub height: u32,
    lut: Arc<BlitLut>,
}

impl VxFrame {
    /// The BGR555 picture into `out` (`width * height` words).
    pub fn bgr555_into(&self, out: &mut [u16]) {
        self.lut.blit(&self.slot, out, self.width, self.height);
    }
    /// Y plane (`width x height`, stride 256 in the slot) as a packed buffer.
    pub fn luma(&self) -> Vec<u8> {
        (0..self.height as usize).flat_map(|y| self.slot[y * 256..y * 256 + self.width as usize].iter().copied()).collect()
    }
    /// U and V planes (`width/2 x height/2` each), de-interleaved.
    pub fn chroma(&self) -> (Vec<u8>, Vec<u8>) {
        let (w2, h2) = (self.width as usize / 2, self.height as usize / 2);
        let mut u = Vec::with_capacity(w2 * h2);
        let mut v = Vec::with_capacity(w2 * h2);
        for y in 0..h2 {
            for x in 0..w2 {
                u.push(self.slot[0xA000 + y * 256 + 2 * x]);
                v.push(self.slot[0xA000 + y * 256 + 2 * x + 1]);
            }
        }
        (u, v)
    }
}

impl Frame for VxFrame {
    fn manifest_bytes(&self) -> &[u8] {
        &self.slot
    }
    fn bgr555(&self) -> Vec<u16> {
        let mut out = vec![0u16; (self.width * self.height) as usize];
        self.bgr555_into(&mut out);
        out
    }
}

/// Video decoder for one stream. `S` holds the bytes the reader walks: either the whole ROM
/// ([`VxVideo::open`]) or just the video elementary stream (what [`Codec::video`] copies).
pub struct VxVideo<S: AsRef<[u8]> = Vec<u8>> {
    data: S,
    range: Range<usize>,
    header: StreamHeader,
    seek: Vec<SeekEntry>,
    core: Core,
    lut: Arc<BlitLut>,
    /// frame number the next decode produces
    next: u32,
    strict: bool,
}

impl<S: AsRef<[u8]>> VxVideo<S> {
    /// Decoder for stream `stream` (hex header offset or index) of `rom`, reading the video
    /// bytes straight from `rom` (pass `&[u8]`, `Arc<[u8]>`, `Vec<u8>`, ...).
    pub fn open(rom: S, stream: &str) -> Result<Self> {
        let r = rom.as_ref();
        let h = pick_stream(r, stream)?;
        let range = h.video_range(r.len());
        let (tables, seek) = (Tables::from_rom(r, h.variant)?, h.seek_table(r));
        Self::build(rom, h, range, tables, seek)
    }

    /// `data[range]` = the video elementary stream of `h`; tables come from `rom`.
    pub fn with_data(rom: &[u8], h: StreamHeader, data: S, range: Range<usize>) -> Result<Self> {
        let tables = Tables::from_rom(rom, h.variant)?;
        let seek = h.seek_table(rom);
        Self::build(data, h, range, tables, seek)
    }

    fn build(data: S, h: StreamHeader, range: Range<usize>, tables: Tables, seek: Vec<SeekEntry>) -> Result<Self> {
        let lut = Arc::new(BlitLut::new(&tables.iwram_lo, h.width, h.height));
        let core = Core::new(tables, h.width, h.height, h.qp, &data.as_ref()[range.clone()]);
        Ok(VxVideo { data, range, header: h, seek, core, lut, next: 0, strict: true })
    }

    pub fn header(&self) -> &StreamHeader {
        &self.header
    }
    pub fn seek_table(&self) -> &[SeekEntry] {
        &self.seek
    }
    /// The decoder state (EWRAM image, counters).
    pub fn core(&self) -> &Core {
        &self.core
    }
    pub fn core_mut(&mut self) -> &mut Core {
        &mut self.core
    }
    pub fn stats(&self) -> &Stats {
        &self.core.stats
    }
    /// Frame number the next decode produces.
    pub fn position(&self) -> u32 {
        self.next
    }
    /// With `strict` (the default) a frame that raises a decoder error flag is returned as
    /// `Err(Undefined)`; otherwise decoding goes on like the C reference (which counts them).
    pub fn set_strict(&mut self, strict: bool) {
        self.strict = strict;
    }

    /// Cold decoder at seek entry `e` (player seek semantics on a freshly initialised player:
    /// frame 0 uses the power-on reader phase, other entries the seek routine's).
    fn cold_start(&mut self, e: SeekEntry) {
        let s = &self.data.as_ref()[self.range.clone()];
        self.core.reset(self.header.qp, s);
        if e.frame != 0 {
            self.core.bits = bits::BitState::at(s, e.video_bit);
        }
        self.next = e.frame;
    }

    /// Cold start at the last seek point at or before `frame`, then decode forward (without
    /// returning frames) up to `frame`; like `vxdec --start-frame`.
    pub fn seek_to(&mut self, frame: u32) -> Result<()> {
        if frame >= self.header.frames {
            return Err(Error::Invalid(format!("frame {frame} >= {}", self.header.frames)));
        }
        let e = self
            .seek
            .iter()
            .take_while(|e| e.frame <= frame && e.frame < self.header.frames)
            .last()
            .copied()
            .unwrap_or(SeekEntry { frame: 0, video_bit: 0, audio_byte: 0 });
        self.cold_start(e);
        while self.next < frame {
            self.decode_next()?;
        }
        Ok(())
    }

    /// Decode the next frame in place; `Ok(Some(n))` = frame n is now in [`Self::slot`].
    pub fn decode_next(&mut self) -> Result<Option<u32>> {
        if self.next >= self.header.frames {
            return Ok(None);
        }
        let err0 = self.core.stats.err;
        let s = &self.data.as_ref()[self.range.clone()];
        self.core.decode_frame(s);
        let n = self.next;
        self.next += 1;
        if self.strict && self.core.stats.err != err0 {
            return Err(Error::Undefined(format!(
                "frame {n}: {} decoder error flag(s) (stream bit {})",
                self.core.stats.err - err0,
                self.core.frame_bit
            )));
        }
        Ok(Some(n))
    }

    /// The slot of the frame just decoded.
    pub fn slot(&self) -> &[u8] {
        self.core.slot(self.core.dec_index.wrapping_sub(1))
    }

    /// BGR555 picture of the frame just decoded.
    pub fn bgr555_into(&self, out: &mut [u16]) {
        self.lut.blit(self.slot(), out, self.header.width, self.header.height);
    }

    /// The blit table for this stream's size.
    pub fn blit_lut(&self) -> &BlitLut {
        &self.lut
    }

    /// Bit position of the frame just decoded (relative to header + 0x38).
    pub fn frame_bit(&self) -> u32 {
        self.core.frame_bit
    }
}

impl<S: AsRef<[u8]>> VideoDecoder for VxVideo<S> {
    type Frame = VxFrame;

    fn seek(&mut self, frame: u32) -> Result<()> {
        let e = self
            .seek
            .iter()
            .find(|e| e.frame == frame && e.frame < self.header.frames)
            .copied()
            .ok_or_else(|| Error::Invalid(format!("frame {frame} is not a seek point")))?;
        self.cold_start(e);
        Ok(())
    }

    fn next_frame(&mut self) -> Result<Option<VxFrame>> {
        Ok(self.decode_next()?.map(|n| VxFrame {
            index: n,
            slot: self.slot().to_vec(),
            width: self.header.width,
            height: self.header.height,
            lut: self.lut.clone(),
        }))
    }
}

const _: () = assert!(SLOTSZ as usize == 0xF000);

// ---------------------------------------------------------------- audio

/// Audio decoder for one stream: one block = one AFrame.
pub struct VxAudio<S: AsRef<[u8]> = Vec<u8>> {
    data: S,
    range: Range<usize>,
    seek: Vec<SeekEntry>,
    frames: u32,
    codebook: Codebook,
    state: AudioState,
    pos: usize,
    blocks: u64,
    samples: [i32; SAMPLES],
    pcm: [i16; SAMPLES],
}

impl<S: AsRef<[u8]>> VxAudio<S> {
    /// Audio of stream `stream` of `rom`, reading straight from `rom`.
    pub fn open(rom: S, stream: &str) -> Result<Self> {
        let r = rom.as_ref();
        let h = pick_stream(r, stream)?;
        let range = h.audio_range(r.len()).ok_or_else(|| Error::Invalid(format!("stream {} has no audio", h.id())))?;
        let seek = h.seek_table(r);
        Self::build(rom, &h, range, seek)
    }

    /// `data[range]` = the audio elementary stream (codebook + AFrames) of `h` in `rom`.
    pub fn with_data(rom: &[u8], h: StreamHeader, data: S, range: Range<usize>) -> Result<Self> {
        let seek = h.seek_table(rom);
        Self::build(data, &h, range, seek)
    }

    fn build(data: S, h: &StreamHeader, range: Range<usize>, seek: Vec<SeekEntry>) -> Result<Self> {
        let codebook = Codebook::parse(&data.as_ref()[range.clone()])
            .ok_or_else(|| Error::Format("audio stream shorter than its codebook".into()))?;
        Ok(VxAudio {
            data,
            range,
            seek,
            frames: h.frames,
            state: AudioState::new(codebook.clone()),
            codebook,
            pos: audio::CODEBOOK_SIZE,
            blocks: 0,
            samples: [0; SAMPLES],
            pcm: [0; SAMPLES],
        })
    }

    /// Byte offset of the next AFrame in the audio stream.
    pub fn position(&self) -> usize {
        self.pos
    }
    /// AFrames decoded since the start / last seek.
    pub fn blocks(&self) -> u64 {
        self.blocks
    }
    /// The exact 32-bit samples of the block just decoded (16384 Hz); `last_pcm` is these
    /// saturated to i16.
    pub fn last_samples(&self) -> &[i32; SAMPLES] {
        &self.samples
    }

    /// Cold start (fresh decoder state) at the AFrame of the seek entry for video `frame`.
    /// The cart does not reset its audio buffers on a seek, so after a seek the first
    /// AFrames can differ from a continuous decode until the intra AFrame's state settles.
    pub fn seek_frame(&mut self, frame: u32) -> Result<()> {
        let e = self
            .seek
            .iter()
            .find(|e| e.frame == frame && e.frame < self.frames)
            .copied()
            .ok_or_else(|| Error::Invalid(format!("frame {frame} is not a seek point")))?;
        self.state = AudioState::new(self.codebook.clone());
        self.pos = e.audio_byte as usize;
        self.blocks = 0;
        Ok(())
    }

    /// Decode the next AFrame into `out` (512 bytes, signed 8-bit). `false` at the end:
    /// past the stream, or at the zero padding after the last AFrame.
    pub fn next_into(&mut self, out: &mut [u8; OUT_BYTES]) -> bool {
        let a = &self.data.as_ref()[self.range.clone()];
        let p = self.pos;
        if p + 4 > a.len() || a[p..p + 4].iter().all(|&b| b == 0) {
            return false;
        }
        let mut fr = [0u8; 20];
        let n = (a.len() - p).min(20);
        fr[..n].copy_from_slice(&a[p..p + n]);
        self.pos += self.state.decode(&fr, &mut self.samples, out);
        for (d, s) in self.pcm.iter_mut().zip(self.samples.iter()) {
            *d = (*s).clamp(i16::MIN as i32, i16::MAX as i32) as i16;
        }
        self.blocks += 1;
        true
    }
}

impl<S: AsRef<[u8]>> AudioDecoder for VxAudio<S> {
    fn next_block(&mut self) -> Result<Option<Vec<u8>>> {
        let mut out = [0u8; OUT_BYTES];
        Ok(if self.next_into(&mut out) { Some(out.to_vec()) } else { None })
    }
    fn last_pcm(&self) -> &[i16] {
        &self.pcm
    }
}
