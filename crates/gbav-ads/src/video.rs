//! ADS video decoding: per chunk, the codebook blob A in one go (0x1800-byte slot), then the
//! index blob B decoded into the 128 KiB ring one index plane per frame, as `ads_video_next`.

use crate::archive::{AdsCart, Movie};
use crate::lzma::{self, Lzma, RING};
use gbav_common::{Error, Result, VideoDecoder};
use gbav_majesco::vq::{self, Row159, Tables, Vcb, H, W};
use gbav_majesco::{ArcSlice, VqFrame};
use std::sync::Arc;

/// The codebook slot the players decompress into.
pub const CB_SLOT: usize = 0x1800;

/// Decoder for one ADS movie. Owns a copy of the movie's video bytes.
pub struct AdsVideo {
    pub movie: Movie,
    data: ArcSlice,
    base: usize,
    tables: Tables,
    gains: [i32; 11],
    vcb: Box<Vcb>,
    fb: Vec<u16>,
    lz: Option<Lzma<ArcSlice>>,
    chunk: usize,
    /// frame within the chunk
    fin: u32,
    /// stream frame of the next frame
    frame: u32,
    plane: Vec<u8>,
}

impl AdsVideo {
    pub fn new(rom: &[u8], cart: &AdsCart, i: usize) -> Result<AdsVideo> {
        let movie = cart.movies.get(i).ok_or_else(|| Error::Invalid(format!("no movie {i}")))?.clone();
        let base = movie.v_off;
        let end = (movie.v_off + movie.v_len).max(movie.chunks.last().map_or(0, |c| c.b_off + c.b_len)).min(rom.len());
        let data = ArcSlice::new(Arc::from(&rom[base..end]));
        let mut v = AdsVideo {
            movie,
            data,
            base,
            tables: Tables::new(cart.dither, cart.gains[5], cart.gain_bias(i)),
            gains: cart.gains,
            vcb: Box::new([[0; 16]; 256]),
            fb: vec![0; W * H],
            lz: None,
            chunk: 0,
            fin: 0,
            frame: 0,
            plane: Vec::new(),
        };
        if !v.movie.chunks.is_empty() {
            v.seek_chunk(0)?;
        }
        Ok(v)
    }

    /// Player brightness level 0..10 (default 5) and gain bias; reconverts the current codebook.
    pub fn set_brightness(&mut self, level: usize, bias: i32) -> Result<()> {
        self.tables.set_gain(self.gains[level.min(10)], bias);
        if !self.movie.chunks.is_empty() {
            let cb = self.codebook(self.chunk)?;
            let c = &self.movie.chunks[self.chunk];
            vq::convert(&cb, c.word.mode, c.word.ncb as usize, &self.tables, &mut self.vcb)?;
        }
        Ok(())
    }

    fn blob(&self, off: usize, len: usize) -> ArcSlice {
        self.data.slice(off - self.base, off - self.base + len)
    }

    /// Chunk `ci`'s codebook, decompressed and unpredicted.
    pub fn codebook(&self, ci: usize) -> Result<Vec<u8>> {
        let c = &self.movie.chunks[ci];
        let a = self.blob(c.a_off, c.a_len);
        let mut cb = lzma::decode_blob(a.as_ref(), 6144).map_err(|e| ctx(ci, e))?;
        let ncb = c.word.ncb as usize;
        if cb.len() < c.word.mode.codebook_bytes(ncb).max(c.word.mode.unpredict_bytes(ncb)) {
            // the player would convert stale bytes of its codebook slot
            return Err(Error::Undefined(format!("chunk {ci}: codebook blob shorter than the converter reads")));
        }
        vq::unpredict(&mut cb, c.word.mode, ncb).map_err(|e| ctx(ci, e))?;
        Ok(cb)
    }

    /// Chunk `ci`'s VRAM codebook at the current brightness (for frames a late player shows with
    /// an old codebook, see the crate docs).
    pub fn segment_vcb(&self, ci: usize) -> Result<Box<Vcb>> {
        let cb = self.codebook(ci)?;
        let c = &self.movie.chunks[ci];
        let mut v: Box<Vcb> = Box::new([[0; 16]; 256]);
        vq::convert(&cb, c.word.mode, c.word.ncb as usize, &self.tables, &mut v)?;
        Ok(v)
    }

    fn start_chunk(&mut self) -> Result<()> {
        let ci = self.chunk;
        let cb = self.codebook(ci)?;
        let c = self.movie.chunks[ci].clone();
        vq::convert(&cb, c.word.mode, c.word.ncb as usize, &self.tables, &mut self.vcb).map_err(|e| ctx(ci, e))?;
        if c.b_len < 8 {
            return Err(Error::Undefined(format!("chunk {ci}: index blob shorter than its prefix")));
        }
        let b = self.blob(c.b_off + 8, c.b_len - 8);
        // the ring is reused from chunk to chunk, like the player's EWRAM ring
        match self.lz.take() {
            Some(mut l) => {
                l.restart(b);
                self.lz = Some(l);
            }
            None => self.lz = Some(Lzma::new(b, RING)),
        }
        self.fin = 0;
        Ok(())
    }

    fn seek_chunk(&mut self, ci: usize) -> Result<()> {
        if ci >= self.movie.chunks.len() {
            return Err(Error::Invalid(format!("chunk {ci} of {}", self.movie.chunks.len())));
        }
        self.chunk = ci;
        self.frame = self.movie.chunks[ci].first_frame;
        self.start_chunk()
    }

    /// Cold start at any frame: the containing chunk, then decode forward.
    pub fn seek_any(&mut self, frame: u32) -> Result<()> {
        self.seek_chunk(self.movie.chunk_of(frame))?;
        while self.frame < frame {
            if self.decode()?.is_none() {
                break;
            }
        }
        Ok(())
    }

    pub fn position(&self) -> u32 {
        self.frame
    }

    fn decode(&mut self) -> Result<Option<VqFrame>> {
        if self.movie.chunks.is_empty() {
            return Ok(None);
        }
        while self.fin >= self.movie.chunks[self.chunk].word.frames as u32 {
            if self.chunk + 1 >= self.movie.chunks.len() {
                return Ok(None);
            }
            self.chunk += 1;
            self.start_chunk()?;
        }
        let ci = self.chunk;
        let mode = self.movie.chunks[ci].word.mode;
        let g = mode.blocks();
        let lz = self.lz.as_mut().expect("chunk started");
        let start = lz.pos();
        lz.decode(g as u32).map_err(|e| ctx(ci, e.into()))?;
        lz.read(start, g, &mut self.plane);
        // ADS players keep the BG palette at zero: line 159 is black
        let lines = vq::blit(&self.plane, mode, &self.vcb, Row159::BLACK, &mut self.fb)?;
        let fr = VqFrame::new(self.frame, ci as u32, mode.id(), lines, &self.plane, &self.fb);
        self.fin += 1;
        self.frame += 1;
        Ok(Some(fr))
    }
}

fn ctx(ci: usize, e: Error) -> Error {
    match e {
        Error::Undefined(s) => Error::Undefined(format!("chunk {ci}: {s}")),
        o => o,
    }
}

impl VideoDecoder for AdsVideo {
    type Frame = VqFrame;

    /// `frame` must start a chunk.
    fn seek(&mut self, frame: u32) -> Result<()> {
        let ci = self.movie.chunk_of(frame);
        if self.movie.chunks.get(ci).is_none_or(|c| c.first_frame != frame) {
            return Err(Error::Invalid(format!("frame {frame} is not a chunk start")));
        }
        self.seek_chunk(ci)
    }

    fn next_frame(&mut self) -> Result<Option<VqFrame>> {
        self.decode()
    }
}
