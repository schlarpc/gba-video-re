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
//!   noise-shaped 8-bit output stage ([`gbav_common::output::OutputStage`], shared with VX audio). Its
//!   state is zero at power-on and never reset, not even on a seek.
//! * [`Fvmv`] implements the [`gbav_common::Codec`] interface on top of them.
//!
//! Manifest bytes: the video manifest hashes the 240x160 u16 LE frame buffer with bit 15
//! kept ([`Frame::manifest_bytes`]); the audio manifest hashes the 256 int32 LE synthesis
//! samples and the 1024 signed 8-bit PCM bytes ([`AudioDecoder::manifest_part`] 0 and 1).
//! Decoders share the ROM ([`gbav_common::Rom`]); no stream bytes are copied.

pub mod audio;
pub mod container;
pub mod video;

use std::sync::Arc;

use audio::{AudioState, BLOCK_BYTES, BLOCK_SAMPLES, OUT_BYTES};
use container::{Header, Record};
use gbav_common::bytes::ArcSlice;
use gbav_common::{AudioDecoder, BlockKey, Codec, Error, Frame, Result, Rom, StreamInfo, VideoDecoder};
use video::{Decoder, FRAME_BYTES};

/// One FVMV stream: its header, record table and seek points, and a view of the ROM bytes
/// from the header to the end of its record area (shared, not copied).
#[derive(Debug, Clone)]
pub struct Stream {
    data: ArcSlice,
    pub rom_offset: usize,
    pub header: Header,
    pub records: Vec<Record>,
    pub seek_points: Vec<u32>,
}

impl Stream {
    /// Parse the stream whose `FVMV` header is at ROM offset `offset`.
    pub fn parse(rom: &Rom, offset: usize) -> Result<Stream> {
        let s = rom
            .get(offset..)
            .ok_or_else(|| Error::Invalid("offset past the ROM".into()))?;
        let header = Header::parse(s)?;
        let records = container::walk(s, &header)?;
        let seek_points = container::seek_points(&records);
        let len = container::HEADER_BYTES + header.data_size as usize;
        let data = ArcSlice::range(rom.clone(), offset, offset + len);
        Ok(Stream {
            data,
            rom_offset: offset,
            header,
            records,
            seek_points,
        })
    }

    /// Stream `id` ("0", "1": index among the ROM's FVMV headers in ROM order).
    pub fn open(rom: &Rom, id: &str) -> Result<Stream> {
        let offs = container::find_streams(rom);
        if offs.is_empty() {
            return Err(Error::Format("no FVMV streams".into()));
        }
        let i: usize = id
            .parse()
            .map_err(|_| Error::Invalid(format!("stream id {id:?}")))?;
        let o = *offs
            .get(i)
            .ok_or_else(|| Error::Invalid(format!("no FVMV stream {id}")))?;
        Stream::parse(rom, o)
    }

    pub fn frames(&self) -> u32 {
        self.records.len() as u32
    }

    pub fn video_payload(&self, i: u32) -> &[u8] {
        &self.data[self.records[i as usize].video_range()]
    }

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
            chapters: Vec::new(),
            audio_rate_hz: Some(audio::RATE_HZ),
        }
    }
}

/// A decoded frame: the 240x160 u16 LE frame buffer, bit 15 included (the manifest bytes).
#[derive(Debug, Clone)]
pub struct FvmvFrame {
    pub index: u32,
    pub buffer: Vec<u8>,
}

impl Frame for FvmvFrame {
    fn manifest_bytes(&self) -> &[u8] {
        &self.buffer
    }

    fn size(&self) -> (u32, u32) {
        (240, 160)
    }

    fn bgr555_into(&self, out: &mut [u16]) {
        for (o, p) in out.iter_mut().zip(self.buffer.chunks_exact(2)) {
            *o = u16::from_le_bytes([p[0], p[1]]) & 0x7fff;
        }
    }
}

/// Video decoder for one stream.
pub struct Video {
    stream: Arc<Stream>,
    dec: Decoder,
    next: u32,
}

impl Video {
    pub fn new(stream: Arc<Stream>) -> Video {
        Video {
            stream,
            dec: Decoder::new(),
            next: 0,
        }
    }

    pub fn stream(&self) -> &Stream {
        &self.stream
    }

    pub fn decoder(&self) -> &Decoder {
        &self.dec
    }

    /// Frame number the next decode produces.
    pub fn position(&self) -> u32 {
        self.next
    }

    /// Decode the next frame in place (see [`Decoder::last_frame`]); `Ok(Some(n))` = frame n.
    pub fn decode_next(&mut self) -> Result<Option<u32>> {
        let i = self.next;
        if i >= self.stream.frames() {
            return Ok(None);
        }
        if !self.dec.decode_frame(self.stream.video_payload(i)) {
            return Err(Error::Undefined(format!(
                "frame {i}: a reader ran past the payload"
            )));
        }
        self.next += 1;
        Ok(Some(i))
    }
}

impl VideoDecoder for Video {
    type Frame = FvmvFrame;

    /// Cold start: zeroed EWRAM model, frame counter at the seek point (keeps the player's
    /// buffer parity). Every seek point is a true keyframe, so the result is exact.
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
            FvmvFrame {
                index,
                buffer: self.dec.last_frame().to_vec(),
            }
        }))
    }
}

/// Audio decoder for one stream. A block is one 40-byte GSM block: 256 synthesis samples
/// at 16384 Hz and 1024 PCM bytes at 65536 Hz, keyed by (video frame, block in that frame).
pub struct Audio {
    stream: Arc<Stream>,
    state: AudioState,
    frame: u32,
    block: u32,
    last: Option<(u32, u32)>,
    synth: [i32; BLOCK_SAMPLES],
    pcm: [i8; OUT_BYTES],
    synth_bytes: [u8; 4 * BLOCK_SAMPLES],
    pcm_bytes: [u8; OUT_BYTES],
}

impl Audio {
    /// Audio from `state` (to carry the player's state from one episode into the next).
    pub fn with_state(stream: Arc<Stream>, state: AudioState) -> Audio {
        Audio {
            stream,
            state,
            frame: 0,
            block: 0,
            last: None,
            synth: [0; BLOCK_SAMPLES],
            pcm: [0; OUT_BYTES],
            synth_bytes: [0; 4 * BLOCK_SAMPLES],
            pcm_bytes: [0; OUT_BYTES],
        }
    }

    /// Audio from power-on state.
    pub fn new(stream: Arc<Stream>) -> Audio {
        Self::with_state(stream, AudioState::new())
    }

    /// Reposition at video frame `frame` without touching the state, as the player does.
    pub fn reposition(&mut self, frame: u32) -> Result<()> {
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

    /// Decode the next block; its (frame, block in frame), or `None` at the end.
    pub fn decode_next(&mut self) -> Option<(u32, u32)> {
        loop {
            if self.frame >= self.stream.frames() {
                return None;
            }
            let ab = self.stream.audio_payload(self.frame);
            let k = self.block as usize;
            if (k + 1) * BLOCK_BYTES <= ab.len() {
                let blk: &[u8; BLOCK_BYTES] = ab[k * BLOCK_BYTES..(k + 1) * BLOCK_BYTES]
                    .try_into()
                    .unwrap();
                self.state.decode_block(blk, &mut self.synth, &mut self.pcm);
                for (d, s) in self.synth_bytes.chunks_exact_mut(4).zip(self.synth.iter()) {
                    d.copy_from_slice(&s.to_le_bytes());
                }
                for (d, &s) in self.pcm_bytes.iter_mut().zip(self.pcm.iter()) {
                    *d = s as u8;
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

    pub fn last_position(&self) -> Option<(u32, u32)> {
        self.last
    }

    /// The exact int32 synthesis output of the last block.
    pub fn synth(&self) -> &[i32; BLOCK_SAMPLES] {
        &self.synth
    }

    /// The synthesis output as LE bytes (first audio manifest hash).
    pub fn synth_bytes(&self) -> &[u8] {
        &self.synth_bytes
    }

    /// The signed 8-bit PCM of the last block.
    pub fn pcm_s8(&self) -> &[i8; OUT_BYTES] {
        &self.pcm
    }

    /// The PCM as bytes (second audio manifest hash; what the cart plays).
    pub fn pcm_bytes(&self) -> &[u8] {
        &self.pcm_bytes
    }
}

impl AudioDecoder for Audio {
    fn next_block(&mut self) -> Result<bool> {
        Ok(self.decode_next().is_some())
    }

    fn key(&self) -> BlockKey {
        let (f, b) = self.last.unwrap_or((0, 0));
        BlockKey {
            frame: Some(f),
            index: b as u64,
        }
    }

    fn parts(&self) -> usize {
        2
    }

    fn manifest_part(&self, i: usize) -> &[u8] {
        match i {
            0 => &self.synth_bytes,
            _ => &self.pcm_bytes,
        }
    }

    fn pcm(&self) -> &[i32] {
        &self.synth
    }

    /// Cold start (power-on state) at seek point `frame`. The player itself keeps its audio
    /// state across seeks; use [`Audio::reposition`] for that behaviour.
    fn seek(&mut self, frame: u32) -> Result<()> {
        if self.stream.seek_points.binary_search(&frame).is_err() {
            return Err(Error::Invalid(format!("frame {frame} is not a seek point")));
        }
        self.state = AudioState::new();
        self.last = None;
        self.reposition(frame)
    }
}

/// The FVMV codec.
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
            .map(|(i, &o)| {
                let s = rom.get(o..).ok_or_else(|| Error::Invalid("offset past the ROM".into()))?;
                let header = Header::parse(s)?;
                let records = container::walk(s, &header)?;
                let st = Stream {
                    data: ArcSlice::new(Arc::from(&[][..])),
                    rom_offset: o,
                    seek_points: container::seek_points(&records),
                    header,
                    records,
                };
                Ok(st.info(&i.to_string()))
            })
            .collect()
    }

    fn video(rom: &Rom, stream: &str) -> Result<Video> {
        Ok(Video::new(Arc::new(Stream::open(rom, stream)?)))
    }

    fn audio(rom: &Rom, stream: &str) -> Result<Audio> {
        Ok(Audio::new(Arc::new(Stream::open(rom, stream)?)))
    }
}
