//! The type-2 audio resource shared by both Majesco engines and an owning [`AudioDecoder`] over it.
//!
//! ```text
//! u32 a, u32 b, u32 c      encoder leftovers (b bits 6-9 = codec 3 on Hydrogen)
//! u16 hdr_field            not read by the players
//! u16 nblocks
//! u16 block_words[nblocks] size of each coded block in 32-bit words
//! pad to 4, then the ADPCM payload (adpcm.rs), 16458 samples per block
//! ```

use crate::adpcm::{Adpcm, CtabCoding, Output, BLOCK};
use crate::{rd16, rd32, ArcSlice};
use gbav_common::{AudioDecoder, BlockKey, Error, Result, Rom};

/// Bytes after the payload handed to the decoder: the cartridge's word reader can run into them.
pub const TAIL: usize = 64;

/// A parsed type-2 resource.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioTrack {
    /// ROM offset of the resource payload (its first header word)
    pub entry: usize,
    pub entry_len: usize,
    pub header: [u32; 3],
    pub hdr_field: u16,
    pub nblocks: usize,
    pub block_words: Vec<u16>,
    /// ROM offset and length of the ADPCM payload
    pub payload: usize,
    pub payload_len: usize,
}

impl AudioTrack {
    /// Parse the resource at ROM offset `off`, `len` bytes.
    pub fn parse(rom: &[u8], off: usize, len: usize) -> Result<AudioTrack> {
        if len < 16 || off.checked_add(len).is_none_or(|e| e > rom.len()) {
            return Err(Error::Format(format!("audio resource at {off:#x}+{len:#x} out of range")));
        }
        let nblocks = rd16(rom, off + 14) as usize;
        let h = (16 + 2 * nblocks + 3) & !3;
        if h > len {
            return Err(Error::Format(format!("audio resource at {off:#x}: block table larger than the resource")));
        }
        let block_words = (0..nblocks).map(|i| rd16(rom, off + 16 + 2 * i)).collect();
        Ok(AudioTrack {
            entry: off,
            entry_len: len,
            header: [rd32(rom, off), rd32(rom, off + 4), rd32(rom, off + 8)],
            hdr_field: rd16(rom, off + 12),
            nblocks,
            block_words,
            payload: off + h,
            payload_len: len - h,
        })
    }
    /// Byte offset of block `i` in the payload.
    pub fn block_offset(&self, i: usize) -> usize {
        self.block_words[..i.min(self.nblocks)].iter().map(|&w| w as usize * 4).sum()
    }
    /// Total samples the blocks decode to (the last block is never played to its end).
    pub fn samples(&self) -> usize {
        self.nblocks * BLOCK
    }
    /// ROM range of the payload plus up to [`TAIL`] following ROM bytes, as the decoder should
    /// see it.
    pub fn payload_range(&self, rom_len: usize) -> std::ops::Range<usize> {
        self.payload.min(rom_len)..(self.payload + self.payload_len + TAIL).min(rom_len)
    }
    /// The payload plus up to [`TAIL`] following ROM bytes.
    pub fn payload_bytes<'r>(&self, rom: &'r [u8]) -> &'r [u8] {
        &rom[self.payload_range(rom.len())]
    }
}

/// Samples per video frame: 16384 Hz / (2^24 / (5 * 280896)) = 1371.5625 = 21945 / 16; a coded
/// block (16458 samples) lasts exactly 12 frames.
pub const FRAME_SAMPLES_X16: u64 = 21945;

/// Audio decoder for one track, reading the shared ROM. [`AudioDecoder::next_block`] yields one
/// coded block (16458 signed 8-bit samples), the granularity of the ADS and Sonic X proto
/// manifests; [`AudioDecoder::decode_samples`] / [`TrackDecoder::decode`] take arbitrary sample
/// counts (the Hydrogen manifests hash each of the player's calls, 1371/1372 samples split at its
/// ring wrap). [`AudioDecoder::manifest_part`] 0 is the s8 output of the last block or call as
/// bytes; [`AudioDecoder::pcm`] the same samples as `s8 << 8` (the decoder's native output is
/// 8-bit).
pub struct TrackDecoder {
    pub track: AudioTrack,
    adpcm: Adpcm<ArcSlice>,
    block: usize,
    in_block: usize,
    key: BlockKey,
    /// calls (decode_samples) since the start or the last seek
    calls: u64,
    scratch: Vec<i8>,
    bytes: Vec<u8>,
    pcm: Vec<i32>,
}

impl TrackDecoder {
    pub fn new(rom: &Rom, track: AudioTrack, coding: CtabCoding, output: Output) -> TrackDecoder {
        let r = track.payload_range(rom.len());
        let data = ArcSlice::range(rom.clone(), r.start, r.end);
        TrackDecoder {
            track,
            adpcm: Adpcm::new(data, coding, output),
            block: 0,
            in_block: 0,
            key: BlockKey::default(),
            calls: 0,
            scratch: Vec::new(),
            bytes: Vec::new(),
            pcm: Vec::new(),
        }
    }

    /// Cold start at block `i` (every block of every stream is a restart block).
    pub fn seek_block(&mut self, i: usize) -> Result<()> {
        if i > self.track.nblocks {
            return Err(Error::Invalid(format!("audio block {i} of {}", self.track.nblocks)));
        }
        self.adpcm.seek(self.track.block_offset(i), i as u32);
        self.adpcm.clear_overrun();
        self.block = i;
        self.in_block = 0;
        self.calls = 0;
        Ok(())
    }

    /// Decode the next `out.len()` samples; returns how many were decoded (fewer only when the
    /// bit reader ran past the payload and its ROM tail, which the reference treats the same way).
    pub fn decode(&mut self, out: &mut [i8]) -> usize {
        let n = self.adpcm.decode(out);
        let t = self.in_block + n;
        self.block += t / BLOCK;
        self.in_block = t % BLOCK;
        n
    }

    /// The bit reader ran past the payload and its ROM tail.
    pub fn overrun(&self) -> bool {
        self.adpcm.overrun()
    }

    /// Current block index and sample offset within it.
    pub fn position(&self) -> (usize, usize) {
        (self.block, self.in_block)
    }

    /// The s8 samples of the last block or call.
    pub fn last_samples(&self) -> &[i8] {
        &self.scratch
    }

    /// Decode `n` samples into the part/PCM buffers; the number decoded.
    fn decode_unit(&mut self, n: usize) -> usize {
        let mut buf = std::mem::take(&mut self.scratch);
        buf.resize(n, 0);
        let got = self.decode(&mut buf);
        buf.truncate(got);
        self.bytes.clear();
        self.bytes.extend(buf.iter().map(|&s| s as u8));
        self.pcm.clear();
        self.pcm.extend(buf.iter().map(|&s| (s as i32) << 8));
        self.scratch = buf;
        got
    }
}

impl AudioDecoder for TrackDecoder {
    /// The rest of the current block (a whole block after a seek). `Ok(false)` after the last
    /// block. If the bit reader runs past the payload and its ROM tail (the last block of a track
    /// can end inside the padding; the cartridge never plays that far) the block is cut short, as
    /// the reference decoder does, and the track ends there.
    fn next_block(&mut self) -> Result<bool> {
        if self.block >= self.track.nblocks || self.adpcm.overrun() {
            return Ok(false);
        }
        let b = self.block;
        if self.decode_unit(BLOCK - self.in_block) == 0 {
            return Ok(false);
        }
        self.key = BlockKey { frame: None, index: b as u64 };
        Ok(true)
    }

    fn key(&self) -> BlockKey {
        self.key
    }

    fn manifest_part(&self, _i: usize) -> &[u8] {
        &self.bytes
    }

    fn pcm(&self) -> &[i32] {
        &self.pcm
    }

    /// One player call of `n` samples (the Hydrogen per-call manifests). `Ok(false)` if not all
    /// `n` samples can be decoded.
    fn decode_samples(&mut self, n: usize) -> Result<bool> {
        if self.adpcm.overrun() {
            return Ok(false);
        }
        let got = self.decode_unit(n);
        self.key = BlockKey { frame: None, index: self.calls };
        self.calls += 1;
        Ok(got == n)
    }

    /// Cold start at video frame `frame`: the block holding it (12 frames per block; every block
    /// is a restart block), then decode and discard up to the frame's first sample,
    /// floor(f * 1371.5625) as the players' sample clock counts.
    fn seek(&mut self, frame: u32) -> Result<()> {
        let f = frame as u64;
        let first = f * FRAME_SAMPLES_X16 / 16;
        let b = (first / BLOCK as u64) as usize;
        self.seek_block(b)?;
        let skip = (first - (b * BLOCK) as u64) as usize;
        if skip > 0 && self.decode_unit(skip) < skip {
            return Err(Error::Invalid(format!("frame {frame} past the audio")));
        }
        self.calls = 0;
        Ok(())
    }
}
