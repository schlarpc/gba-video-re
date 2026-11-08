//! The 8-bit stream for the GBA speaker (music image 0x3185e, used when no headphones are
//! plugged in): per stereo sample, volume `s * vol / 32` (truncating, clamped to 16 bits), rate
//! conversion to 16,384 Hz by nearest-neighbour decimation (0x21bc0: keep the sample when
//! `|(c << 14) / rate|` changes, c = 0..rate), then the high byte of each channel into
//! 576-sample blocks: ring A (right, played on the GBA's right speaker) and ring B (left)
//! (0x31888, then DECODE_DATA_INDICATION). For rates at or below 16,384 Hz the firmware's
//! duplicating branch (0x21bf6, shift 13 instead of 14) is modelled too, though MPEG-1 never
//! reaches it.
//!
//! State at stream start (0x33ff6): counters 0, and the "last position" is -1 when the
//! *previous* stream's rate was above 16,384 Hz, else 0 (the firmware tests the old rate before
//! storing the new one); 0 makes the first sample of the stream disappear. After power-on the
//! old rate is 0.

/// One 576-sample block: (ring A = right channel, ring B = left channel).
pub type Block = (Vec<i8>, Vec<i8>);

#[derive(Debug, Clone)]
pub struct GbaPcm {
    rate: i64,
    /// Rates above `cut` take the decimating branch with shift `dsh` (music images and py_fw0:
    /// 16,384 / 14; Micro and v1.11 video images: 8,192 / 13).
    cut: i64,
    dsh: u32,
    vol: i32,
    c: i64,
    last: i64,
    a: Vec<i8>,
    b: Vec<i8>,
}

impl GbaPcm {
    /// `src_rate`: the MP3's sample rate; `volume`: the VOLUME_SET value (0..56);
    /// first sample dropped unless the previous stream played above 16,384 Hz.
    pub fn new(src_rate: u32, volume: i32, prev_rate: u32) -> GbaPcm {
        GbaPcm {
            rate: src_rate as i64,
            cut: 16384,
            dsh: 14,
            vol: volume,
            c: 0,
            last: if prev_rate > 16384 { -1 } else { 0 },
            a: Vec::with_capacity(576),
            b: Vec::with_capacity(576),
        }
    }

    fn vol(&self, s: i16) -> i32 {
        let p = s as i32 * self.vol;
        ((p + if p < 0 { 31 } else { 0 }) >> 5).clamp(-32768, 32767)
    }

    fn emit(&mut self, l: i32, r: i32, out: &mut Vec<Block>) {
        self.a.push((r >> 8) as i8);
        self.b.push((l >> 8) as i8);
        if self.a.len() == 576 {
            out.push((std::mem::take(&mut self.a), std::mem::take(&mut self.b)));
            self.a.reserve(576);
            self.b.reserve(576);
        }
    }

    /// Push interleaved L/R samples; returns the blocks completed.
    pub fn push(&mut self, lr: &[i16]) -> Vec<Block> {
        let mut out = Vec::new();
        for s in lr.as_chunks::<2>().0 {
            let (l, r) = (self.vol(s[0]), self.vol(s[1]));
            let sh = if self.rate > self.cut { self.dsh } else { 13 };
            let v = ((self.c << sh) / self.rate).abs();
            self.c += 1;
            if self.c > self.rate {
                self.c = 0;
            }
            if self.rate > self.cut {
                if v != self.last {
                    self.last = v;
                    self.emit(l, r, &mut out);
                }
            } else {
                self.emit(l, r, &mut out);
                let dup = self.last + 1 != v;
                self.last = v;
                if dup {
                    self.emit(l, r, &mut out);
                }
            }
        }
        out
    }

    /// Samples waiting in the current (incomplete) block.
    pub fn pending(&self) -> usize {
        self.a.len()
    }
}

impl GbaPcm {
    /// The resampler as `image` starts a stream: the original images (py_fw0 0x1b57c, py_fw1
    /// 0x33ff6) test the *previous* stream's rate (`prev_rate`, 0 after loading the image); the
    /// Micro and v1.11 music images (mi_fw1 0x335b0, fup_sec1 0x3334c) store the new rate first
    /// and test that, so a stream above 16,384 Hz keeps its first sample. The Micro and v1.11
    /// VIDEO images (mi_fw0 reset 0x2ee9c, decimator 0x229e2, dispatch 0x2b114; fup_sec0 0x2f238,
    /// 0x22ed8) also test the new rate, but against 8,192 Hz, and decimate with a shift of 13: their
    /// speaker stream runs at 8,192 Hz (MP4/AAC at 44.1 kHz; the 8 kHz G.726 of ASF files takes
    /// the duplicating branch as before). Confirmed by the MP4 manifests (1057/1057 blocks).
    pub fn for_image(image: crate::ImageId, src_rate: u32, volume: i32, prev_rate: u32) -> GbaPcm {
        use crate::ImageId::*;
        match image {
            PyFw0 | PyFw1 => GbaPcm::new(src_rate, volume, prev_rate),
            MiFw1 | FupSec1 => GbaPcm::new(src_rate, volume, src_rate),
            MiFw0 | FupSec0 => {
                let mut g = GbaPcm::new(src_rate, volume, 0);
                g.cut = 8192;
                g.dsh = 13;
                g.last = if src_rate > 8192 { -1 } else { 0 };
                g
            }
        }
    }
}
