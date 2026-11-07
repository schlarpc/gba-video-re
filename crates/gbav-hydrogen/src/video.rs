//! Hydrogen video decoding (codec 5): per segment, inflate + unpredict + convert the codebook in
//! one request, then request the index stream one frame at a time, as `StreamBase` does, and blit.

use crate::container::VideoStream;
use crate::engine::Player;
use crate::inflate::{decode_blob_counted, Inflate, InflateError};
use gbav_common::{Error, Result, VideoDecoder};
use gbav_majesco::vq::{self, Row159, Tables, Vcb, H, W};
use gbav_majesco::{ArcSlice, VqFrame};
use std::sync::Arc;

/// Per-segment numbers for statistics.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SegmentStats {
    pub cb_blocks: [u32; 4],
    pub idx_blocks: [u32; 4],
}

/// Decoder for one Hydrogen video stream. Owns a copy of the stream's bytes.
pub struct HyVideo {
    pub stream: VideoStream,
    data: ArcSlice,
    base: usize,
    tables: Tables,
    row159: Row159,
    vcb: Box<Vcb>,
    fb: Vec<u16>,
    cur_seg: Option<usize>,
    ix: Option<Inflate<ArcSlice>>,
    /// next frame to decode
    next: u32,
    pub last_stats: SegmentStats,
}

impl HyVideo {
    /// `rom` is only read here; the decoder copies the stream.
    pub fn new(rom: &[u8], stream: VideoStream, player: &Player, gain_bias: i32) -> Result<HyVideo> {
        if stream.codec != 5 {
            return Err(Error::Format(format!("video codec {} is not Hydrogen Inflate (5)", stream.codec)));
        }
        let base = stream.offset;
        let end = stream.end().max(base + stream.len).min(rom.len());
        let data = ArcSlice::new(Arc::from(&rom[base..end]));
        Ok(HyVideo {
            stream,
            data,
            base,
            tables: player.tables(gain_bias),
            row159: player.row159,
            vcb: Box::new([[0; 16]; 256]),
            fb: vec![0; W * H],
            cur_seg: None,
            ix: None,
            next: 0,
            last_stats: SegmentStats::default(),
        })
    }

    /// Brightness: gain (a value from the player's table) and bias.
    pub fn set_gain(&mut self, gain: i32, bias: i32) {
        self.tables.set_gain(gain, bias);
    }

    fn blob(&self, off: usize, len: usize) -> ArcSlice {
        self.data.slice(off - self.base, off - self.base + len)
    }

    fn load_segment(&mut self, si: usize) -> Result<()> {
        let g = self.stream.segments[si].clone();
        let cbb = self.blob(g.cb_off, g.cb_len);
        let (mut cb, blocks) = decode_blob_counted(cbb.as_ref()).map_err(|e| seg_err(si, "codebook", e))?;
        let ncb = g.word.ncb as usize;
        if g.word.mode.codebook_bytes(ncb) > cb.len() {
            return Err(Error::Undefined(format!("segment {si}: codebook smaller than the converter reads")));
        }
        // the unpredict step of an even mode can read past the codebook the converter uses;
        // the player's 0x1800-byte buffer holds stale bytes there: undefined
        vq::unpredict(&mut cb, g.word.mode, ncb).map_err(|e| ctx(si, e))?;
        vq::convert(&cb, g.word.mode, ncb, &self.tables, &mut self.vcb).map_err(|e| ctx(si, e))?;
        self.last_stats.cb_blocks = blocks;
        let ib = self.blob(g.idx_off, g.idx_len);
        let size = if g.idx_len >= 4 { gbav_majesco::rd32(ib.as_ref(), 0) as usize } else { 0 };
        self.ix = Some(Inflate::new(ib, size));
        self.cur_seg = Some(si);
        Ok(())
    }

    fn decode_frame(&mut self) -> Result<Option<VqFrame>> {
        let f = self.next;
        let Some(si) = self.stream.segment_of(f) else { return Ok(None) };
        if self.cur_seg != Some(si) {
            self.load_segment(si)?;
        }
        let g = &self.stream.segments[si];
        let mode = g.word.mode;
        let nb = mode.blocks();
        let ix = self.ix.as_mut().expect("segment loaded");
        // StreamBase state 3: Inflate::UncompressBlock(blocks per frame) once per displayed frame
        let off = ix.request(nb as i64).map_err(|e| seg_err(si, "index stream", e))?;
        if off < 0 || off as usize + nb > ix.produced().min(ix.output().len()) {
            return Err(Error::Undefined(format!(
                "segment {si} frame {f}: index bytes {off}..{} not produced by the stream (the player would blit stale memory)",
                off + nb as i64
            )));
        }
        let off = off as usize;
        self.last_stats.idx_blocks = ix.blocks();
        let idx = &ix.output()[off..off + nb];
        let lines = vq::blit(idx, mode, &self.vcb, self.row159, &mut self.fb)?;
        let fr = VqFrame::new(f, si as u32, mode.id(), lines, idx, &self.fb);
        self.next += 1;
        Ok(Some(fr))
    }

    /// Cold start at any frame: decode from the start of its segment and discard up to it.
    pub fn seek_any(&mut self, frame: u32) -> Result<()> {
        let si = self.stream.segment_of(frame).ok_or_else(|| Error::Invalid(format!("frame {frame} past the end")))?;
        self.seek(self.stream.segments[si].frame)?;
        while self.next < frame {
            self.decode_frame()?;
        }
        Ok(())
    }

    /// Next frame number.
    pub fn position(&self) -> u32 {
        self.next
    }
}

fn seg_err(si: usize, what: &str, e: InflateError) -> Error {
    Error::Undefined(format!("segment {si} {what}: {}", Error::from(e)))
}

fn ctx(si: usize, e: Error) -> Error {
    match e {
        Error::Undefined(s) => Error::Undefined(format!("segment {si}: {s}")),
        o => o,
    }
}

impl VideoDecoder for HyVideo {
    type Frame = VqFrame;

    /// `frame` must start a segment. The VRAM codebook and framebuffer keep what earlier frames
    /// left (as on the cartridge); a fresh decoder starts from zeros.
    fn seek(&mut self, frame: u32) -> Result<()> {
        let si = self.stream.segment_of(frame).ok_or_else(|| Error::Invalid(format!("frame {frame} past the end")))?;
        if self.stream.segments[si].frame != frame {
            return Err(Error::Invalid(format!("frame {frame} is not a segment start")));
        }
        self.cur_seg = None;
        self.ix = None;
        self.next = frame;
        Ok(())
    }

    fn next_frame(&mut self) -> Result<Option<VqFrame>> {
        self.decode_frame()
    }
}
