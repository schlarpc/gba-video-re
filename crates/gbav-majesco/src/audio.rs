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
use crate::{rd16, rd32};
use gbav_common::{AudioDecoder, Error, Result};

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
    /// The payload plus up to [`TAIL`] following ROM bytes, as the decoder should see it.
    pub fn payload_bytes<'r>(&self, rom: &'r [u8]) -> &'r [u8] {
        let end = (self.payload + self.payload_len + TAIL).min(rom.len());
        &rom[self.payload..end]
    }
}

/// Owning audio decoder for one track: [`AudioDecoder::next_block`] yields one coded block
/// (16458 signed 8-bit samples as bytes), the granularity of the ADS and Sonic X proto manifests.
/// [`TrackDecoder::decode`] takes arbitrary sample counts (the Hydrogen manifests hash each of the
/// player's calls, 1371/1372 samples split at its ring wrap).
pub struct TrackDecoder {
    pub track: AudioTrack,
    adpcm: Adpcm<Vec<u8>>,
    block: usize,
    in_block: usize,
    last: Vec<i16>,
}

impl TrackDecoder {
    pub fn new(rom: &[u8], track: AudioTrack, coding: CtabCoding, output: Output) -> TrackDecoder {
        let data = track.payload_bytes(rom).to_vec();
        TrackDecoder { track, adpcm: Adpcm::new(data, coding, output), block: 0, in_block: 0, last: Vec::new() }
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
        Ok(())
    }

    /// Decode the next `out.len()` samples; returns how many were decoded (fewer only when the
    /// bit reader ran past the payload and its ROM tail, which the reference treats the same way).
    pub fn decode(&mut self, out: &mut [i8]) -> usize {
        let n = self.adpcm.decode(out);
        let t = self.in_block + n;
        self.block += t / BLOCK;
        self.in_block = t % BLOCK;
        self.last = out[..n].iter().map(|&s| (s as i16) << 8).collect();
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
}

impl AudioDecoder for TrackDecoder {
    /// The rest of the current block (a whole block after a seek), as signed 8-bit samples.
    /// `Ok(None)` after the last block. If the bit reader runs past the payload and its ROM tail
    /// (the last block of a track can end inside the padding; the cartridge never plays that far)
    /// the block is returned cut short, as the reference decoder does, and the track ends there.
    fn next_block(&mut self) -> Result<Option<Vec<u8>>> {
        if self.block >= self.track.nblocks || self.adpcm.overrun() {
            return Ok(None);
        }
        let mut buf = vec![0i8; BLOCK - self.in_block];
        let n = self.decode(&mut buf);
        if n == 0 {
            return Ok(None);
        }
        buf.truncate(n);
        Ok(Some(buf.into_iter().map(|s| s as u8).collect()))
    }
    /// The samples of the last call scaled to 16 bits (`s8 << 8`); the decoder's native output is
    /// 8-bit.
    fn last_pcm(&self) -> &[i16] {
        &self.last
    }
}
