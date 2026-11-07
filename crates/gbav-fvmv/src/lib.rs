//! Actimagine FVMV video + GSM-style audio decoder (GBA Video *Pokemon* Volumes 1-4).
//!
//! A port of the reference C decoder (`fvmvdec.c`, `fvmvaudio.c`), bit-exact with the
//! cartridge on every frame and audio block of all eight episodes. The format is specified
//! in `docs/format-fvmv.md`.
//!
//! * [`video::Decoder`] models the player's whole 256 KiB EWRAM, because blocks at the
//!   picture edge read and write outside the picture (row-wrapping motion vectors, edge
//!   blending). Frame `n` is decoded into buffer `n & 1`.
//! * [`audio::AudioState`] is the GSM-style synthesis plus the 4x interpolation and
//!   noise-shaped 8-bit output stage ([`util::OutputStage`], shared with VX audio). Its
//!   state is zero at power-on and never reset, not even on a seek.
//! * [`Fvmv`] implements the [`gbav_common::Codec`] interface on top of them.
//!
//! Manifest bytes: the video manifest hashes the 240x160 u16 LE frame buffer with bit 15
//! kept ([`Frame::manifest_bytes`]); the audio manifest hashes the 256 int32 LE synthesis
//! samples ([`Audio::synth_bytes`]) and the 1024 signed 8-bit PCM bytes
//! ([`AudioDecoder::next_block`]).

pub mod audio;
pub mod container;
pub mod util;
pub mod video;

use std::sync::Arc;

use audio::{AudioState, BLOCK_BYTES, BLOCK_SAMPLES, OUT_BYTES};
use container::{Header, Record};
use gbav_common::{AudioDecoder, Codec, Error, Frame, Result, StreamInfo, VideoDecoder};
use video::{Decoder, FRAME_BYTES};

/// One FVMV stream, copied out of the ROM (header plus record area).
#[derive(Debug, Clone)]
pub struct Stream {
    data: Arc<[u8]>,
    /// Offset of the header in the ROM.
    pub rom_offset: usize,
    pub header: Header,
    pub records: Vec<Record>,
    /// Frames a cold decoder can start at, sorted.
    pub seek_points: Vec<u32>,
}

impl Stream {
    /// Parse the stream whose header is at `rom[offset..]`.
    pub fn parse(rom: &[u8], offset: usize) -> Result<Stream> {
        let s = rom.get(offset..).ok_or_else(|| Error::Invalid("offset past the ROM".into()))?;
        let header = Header::parse(s)?;
        let records = container::walk(s, &header)?;
        let seek_points = container::seek_points(&records);
        let data: Arc<[u8]> = s[..container::HEADER_BYTES + header.data_size as usize].into();
        Ok(Stream { data, rom_offset: offset, header, records, seek_points })
    }

    /// Open stream `id` ("0", "1", ...: the index among the ROM's FVMV streams).
    pub fn open(rom: &[u8], id: &str) -> Result<Stream> {
        let offs = container::find_streams(rom);
        if offs.is_empty() {
            return Err(Error::Format("no FVMV streams".into()));
        }
        let i: usize = id.parse().map_err(|_| Error::Invalid(format!("stream id {id:?}")))?;
        let o = *offs.get(i).ok_or_else(|| Error::Invalid(format!("no FVMV stream {id}")))?;
        Stream::parse(rom, o)
    }

    pub fn frames(&self) -> u32 {
        self.records.len() as u32
    }

    /// Video payload of frame `i`.
    pub fn video_payload(&self, i: u32) -> &[u8] {
        &self.data[self.records[i as usize].video_range()]
    }

    /// Audio blocks of frame `i` (a multiple of 40 bytes).
    pub fn audio_payload(&self, i: u32) -> &[u8] {
        &self.data[self.records[i as usize].audio_range()]
    }

    pub fn info(&self, id: &str) -> StreamInfo {
        let (fps_num, fps_den) = self.header.fps();
        StreamInfo {
            id: id.to_string(),
            title: None,
            width: self.header.width,
            height: self.header.height,
            fps_num,
            fps_den,
            frames: self.frames(),
            seek_points: self.seek_points.clone(),
            audio_rate_hz: Some(audio::RATE_HZ),
        }
    }
}

/// A decoded frame: its index and a copy of the 16-bit frame buffer.
#[derive(Debug, Clone)]
pub struct FvmvFrame {
    pub index: u32,
    pub buffer: Vec<u8>,
}

impl Frame for FvmvFrame {
    fn manifest_bytes(&self) -> &[u8] {
        &self.buffer
    }

    fn bgr555(&self) -> Vec<u16> {
        self.buffer.chunks_exact(2).map(|p| u16::from_le_bytes([p[0], p[1]]) & 0x7fff).collect()
    }
}

/// Video decoder over one stream, starting from power-on state at frame 0.
pub struct Video {
    stream: Arc<Stream>,
    dec: Decoder,
    next: u32,
}

impl Video {
    pub fn new(stream: Arc<Stream>) -> Video {
        Video { stream, dec: Decoder::new(), next: 0 }
    }

    pub fn stream(&self) -> &Stream {
        &self.stream
    }

    /// The underlying decoder (EWRAM model, statistics).
    pub fn decoder(&self) -> &Decoder {
        &self.dec
    }

    /// Index of the next frame to decode.
    pub fn position(&self) -> u32 {
        self.next
    }

    /// Decode the next frame without copying it; read it with `decoder().last_frame()`.
    /// Returns the frame index, or `None` at the end.
    pub fn decode_next(&mut self) -> Result<Option<u32>> {
        let i = self.next;
        if i >= self.stream.frames() {
            return Ok(None);
        }
        if !self.dec.decode_frame(self.stream.video_payload(i)) {
            return Err(Error::Undefined(format!("frame {i}: a reader ran past the payload")));
        }
        self.next += 1;
        Ok(Some(i))
    }
}

impl VideoDecoder for Video {
    type Frame = FvmvFrame;

    /// Cold start at a seek point: zeroed EWRAM, buffer parity of the frame index. Seek
    /// points are true keyframes, so the output equals a decode from frame 0.
    fn seek(&mut self, frame: u32) -> Result<()> {
        if self.stream.seek_points.binary_search(&frame).is_err() {
            return Err(Error::Invalid(format!("frame {frame} is not a seek point")));
        }
        self.dec.reset(frame);
        self.next = frame;
        Ok(())
    }

    fn next_frame(&mut self) -> Result<Option<FvmvFrame>> {
        Ok(self.decode_next()?.map(|index| {
            debug_assert_eq!(self.dec.last_frame().len(), FRAME_BYTES);
            FvmvFrame { index, buffer: self.dec.last_frame().to_vec() }
        }))
    }
}

/// Audio decoder over one stream. Blocks are numbered (frame, block within the frame), as
/// in the audio manifest.
pub struct Audio {
    stream: Arc<Stream>,
    state: AudioState,
    frame: u32,
    block: u32,
    last: Option<(u32, u32)>,
    synth: [i32; BLOCK_SAMPLES],
    pcm: [i8; OUT_BYTES],
    pcm16: [i16; BLOCK_SAMPLES],
}

impl Audio {
    /// Start at frame 0 with the given decoder state (power-on: `AudioState::new()`).
    pub fn with_state(stream: Arc<Stream>, state: AudioState) -> Audio {
        Audio {
            stream,
            state,
            frame: 0,
            block: 0,
            last: None,
            synth: [0; BLOCK_SAMPLES],
            pcm: [0; OUT_BYTES],
            pcm16: [0; BLOCK_SAMPLES],
        }
    }

    pub fn new(stream: Arc<Stream>) -> Audio {
        Self::with_state(stream, AudioState::new())
    }

    /// Move to the first block of `frame` without touching the decoder state, as the
    /// player does on a seek.
    pub fn seek(&mut self, frame: u32) -> Result<()> {
        if frame > self.stream.frames() {
            return Err(Error::Invalid(format!("frame {frame} past the end")));
        }
        self.frame = frame;
        self.block = 0;
        Ok(())
    }

    pub fn state(&self) -> &AudioState {
        &self.state
    }

    pub fn state_mut(&mut self) -> &mut AudioState {
        &mut self.state
    }

    /// Decode the next block without allocating; returns its (frame, block) position.
    pub fn decode_next(&mut self) -> Option<(u32, u32)> {
        loop {
            if self.frame >= self.stream.frames() {
                return None;
            }
            let ab = self.stream.audio_payload(self.frame);
            let k = self.block as usize;
            if (k + 1) * BLOCK_BYTES <= ab.len() {
                let blk: &[u8; BLOCK_BYTES] = ab[k * BLOCK_BYTES..(k + 1) * BLOCK_BYTES].try_into().unwrap();
                self.state.decode_block(blk, &mut self.synth, &mut self.pcm);
                for (d, &s) in self.pcm16.iter_mut().zip(self.synth.iter()) {
                    *d = s.clamp(-32768, 32767) as i16;
                }
                let pos = (self.frame, self.block);
                self.block += 1;
                self.last = Some(pos);
                return Some(pos);
            }
            self.frame += 1;
            self.block = 0;
        }
    }

    /// (frame, block) of the block decoded last.
    pub fn last_position(&self) -> Option<(u32, u32)> {
        self.last
    }

    /// The 256 int32 synthesis samples of the last block.
    pub fn synth(&self) -> &[i32; BLOCK_SAMPLES] {
        &self.synth
    }

    /// The synthesis samples as int32 LE bytes (the audio manifest's `md5_synth`).
    pub fn synth_bytes(&self) -> Vec<u8> {
        self.synth.iter().flat_map(|v| v.to_le_bytes()).collect()
    }

    /// The 1024 signed 8-bit samples at 65536 Hz of the last block (`md5_pcm`).
    pub fn pcm(&self) -> &[i8; OUT_BYTES] {
        &self.pcm
    }

    /// The PCM as bytes.
    pub fn pcm_bytes(&self) -> Vec<u8> {
        self.pcm.iter().map(|&b| b as u8).collect()
    }
}

impl AudioDecoder for Audio {
    /// The 1024 signed 8-bit PCM bytes (65536 Hz) the player writes to its DMA ring.
    fn next_block(&mut self) -> Result<Option<Vec<u8>>> {
        Ok(self.decode_next().map(|_| self.pcm_bytes()))
    }

    /// The 16384 Hz synthesis output saturated to 16 bits. (The cartridge never saturates:
    /// the exact values are in [`Audio::synth`].)
    fn last_pcm(&self) -> &[i16] {
        &self.pcm16
    }
}

/// The FVMV family.
pub struct Fvmv;

impl Codec for Fvmv {
    const FAMILY: &'static str = "fvmv";
    type Video = Video;
    type Audio = Audio;

    fn streams(rom: &[u8]) -> Result<Vec<StreamInfo>> {
        let offs = container::find_streams(rom);
        if offs.is_empty() {
            return Err(Error::Format("no FVMV streams".into()));
        }
        offs.iter()
            .enumerate()
            .map(|(i, &o)| Ok(Stream::parse(rom, o)?.info(&i.to_string())))
            .collect()
    }

    fn video(rom: &[u8], stream: &str) -> Result<Video> {
        Ok(Video::new(Arc::new(Stream::open(rom, stream)?)))
    }

    fn audio(rom: &[u8], stream: &str) -> Result<Audio> {
        Ok(Audio::new(Arc::new(Stream::open(rom, stream)?)))
    }
}
