//! Actimagine VX audio: port of tools/vx/container/vxaudio.py, the model of the player's IWRAM
//! 0x03006a14 decoder (0x03007170 in the VXGB player, same code) and its callees, bit for bit
//! including 32-bit wraparound, the two 0x400-byte pulse/sample buffers and the 4x
//! interpolating, noise-shaped 8-bit output stage that feeds Direct Sound FIFO A at 65536 Hz.
//!
//! Codebook (first 3124 bytes of the audio stream):
//!   +0 int16 lpc_cb[3][64][8], +3072 int16 scale_mod[8], +3088 int32 lpc_base[8],
//!   +3120 int32 scale_initial.
//! AFrame: LE halfwords w1, w2, then 8/5/4/3 pulse words (mode = (w2>>12)&3); 128 samples at
//! 16384 Hz.

use gbav_common::output::OutputStage;

pub const CODEBOOK_SIZE: usize = 3124;
pub const PULSE_WORDS: [usize; 4] = [8, 5, 4, 3];
pub const PULSE_STRIDE: [usize; 4] = [3, 3, 4, 5];
pub const AFRAME_BYTES: [usize; 4] = [20, 14, 12, 10];
/// Samples per AFrame.
pub const SAMPLES: usize = 128;
/// Bytes of 8-bit output per AFrame (4x interpolation).
pub const OUT_BYTES: usize = 512;

fn vol(i: usize) -> i32 {
    (i as i32 + 1).min(8).min(128 - i as i32)
}

#[derive(Clone)]
pub struct Codebook {
    cb: [[[i16; 8]; 64]; 3],
    scale_mod: [i16; 8],
    lpc_base: [i32; 8],
    scale_initial: i32,
}

impl Codebook {
    pub fn parse(b: &[u8]) -> Option<Codebook> {
        if b.len() < CODEBOOK_SIZE {
            return None;
        }
        let h = |o: usize| i16::from_le_bytes([b[o], b[o + 1]]);
        let w = |o: usize| i32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]]);
        let mut cb = [[[0i16; 8]; 64]; 3];
        for (i, v) in cb.iter_mut().flatten().flatten().enumerate() {
            *v = h(2 * i);
        }
        let mut scale_mod = [0i16; 8];
        let mut lpc_base = [0i32; 8];
        for k in 0..8 {
            scale_mod[k] = h(3072 + 2 * k);
            lpc_base[k] = w(3088 + 4 * k);
        }
        Some(Codebook {
            cb,
            scale_mod,
            lpc_base,
            scale_initial: w(3120),
        })
    }
}

/// AFrame size in bytes for the frame starting at `b[off..]` (needs 4 bytes).
pub fn aframe_size(b: &[u8], off: usize) -> usize {
    let w2 = u16::from_le_bytes([b[off + 2], b[off + 3]]);
    AFRAME_BYTES[((w2 >> 12) & 3) as usize]
}

/// Decoder state; mirrors the player's VRAM 0x06013600..0x06013680, the two sample buffers
/// B[0]/B[1] (lo = 128 words, hi = 128 words) and the IWRAM 8-sample history at 0x0300660c.
#[derive(Clone)]
pub struct AudioState {
    c: Codebook,
    filt: [i32; 8],
    /// 0x06013620 / 0x06013640
    infl20: [i32; 8],
    infl40: [i32; 8],
    scale: i32,
    toggle: usize,
    /// the output stage (0x03006a54..0x03006b54)
    shaper: OutputStage,
    /// oldest first, hist[7] = newest
    hist: [i32; 8],
    b: [[i32; 256]; 2],
}

impl AudioState {
    pub fn new(c: Codebook) -> AudioState {
        AudioState {
            c,
            filt: [0; 8],
            infl20: [0; 8],
            infl40: [0; 8],
            scale: 0,
            toggle: 0,
            shaper: OutputStage::new(),
            hist: [0; 8],
            b: [[0; 256]; 2],
        }
    }

    /// 0x0300645c: filter update + reflection -> direct conversion.
    fn filter(&mut self, base: [i32; 8], i0: usize, i1: usize, i2: usize) -> [i32; 8] {
        let cb = &self.c.cb;
        let mut f = [0i32; 8];
        for k in 0..8 {
            f[k] = base[k]
                .wrapping_add(cb[0][i0][k] as i32)
                .wrapping_add(cb[1][i1][k] as i32)
                .wrapping_add(cb[2][i2][k] as i32);
        }
        self.filt = f;
        let mut psi = [0i32; 8];
        for i in 0..8 {
            let c = f[i];
            let old = psi;
            for j in 0..i {
                psi[j] = old[j].wrapping_add(old[i - 1 - j].wrapping_mul(c) >> 15);
            }
            psi[i] = c;
        }
        psi.map(|p| (p >> 1).wrapping_neg())
    }

    /// 0x03006ba0: header fields, scale update, add pulses into `dst` (B index).
    fn pulses(&mut self, w1: u16, w2: u16, words: &[u16], dst: usize) -> (usize, usize, usize) {
        let smi = ((w1 >> 6) & 7) as usize;
        let start = ((w2 >> 14) & 3) as usize;
        let mode = ((w2 >> 12) & 3) as usize;
        self.scale = self.scale.wrapping_mul(self.c.scale_mod[smi] as i32) >> 13;
        let s = self.scale;
        let mut pos = 128 + start;
        let d = &mut self.b[dst];
        if mode == 0 {
            let (a, b) = (s.wrapping_mul(2), s.wrapping_mul(-7));
            let mut low: u32 = 0;
            for &w in &words[..8] {
                for sh in [13, 10, 7, 4, 1] {
                    let v = ((w >> sh) & 7) as i32;
                    d[pos] = d[pos].wrapping_add(a.wrapping_mul(v).wrapping_add(b));
                    pos += 3;
                }
                low = (low << 1) | (w & 1) as u32;
            }
            for v in [(low >> 5) & 7, (low >> 2) & 7] {
                d[pos] = d[pos].wrapping_add(a.wrapping_mul(v as i32).wrapping_add(b));
                pos += 3;
            }
        } else {
            let (a, b) = (s.wrapping_mul(2), s.wrapping_mul(-3));
            let st = PULSE_STRIDE[mode];
            for &w in &words[..PULSE_WORDS[mode]] {
                for sh in [14, 12, 10, 8, 6, 4, 2, 0] {
                    let v = ((w >> sh) & 3) as i32;
                    d[pos] = d[pos].wrapping_add(a.wrapping_mul(v).wrapping_add(b));
                    pos += st;
                }
            }
        }
        (
            (w1 & 0x3f) as usize,
            ((w2 >> 6) & 0x3f) as usize,
            (w2 & 0x3f) as usize,
        )
    }

    /// 0x0300662c: 8-tap all-pole synthesis of `n` samples from B[sb][si..] to B[db][di..].
    fn synth(&mut self, sb: usize, si: usize, db: usize, di: usize, infl: &[i32; 8], n: usize) {
        let mut h = self.hist;
        for k in 0..n {
            let mut acc = self.b[sb][si + k].wrapping_shl(14);
            for j in 0..8 {
                acc = acc.wrapping_add(infl[j].wrapping_mul(h[7 - j]));
            }
            let v = acc >> 14;
            h.copy_within(1..8, 0);
            h[7] = v;
            self.b[db][di + k] = v;
        }
        self.hist = h;
    }

    /// Decode the AFrame at `buf[off..]` (zero-padded to 20 bytes by the caller if short).
    /// Returns its size in bytes; the 128 samples go to `samples`, the 512 8-bit output
    /// bytes (as the cart writes them to the FIFO buffer) to `out`.
    pub fn decode(
        &mut self,
        fr: &[u8; 20],
        samples: &mut [i32; SAMPLES],
        out: &mut [u8; OUT_BYTES],
    ) -> usize {
        let hw = |i: usize| u16::from_le_bytes([fr[2 * i], fr[2 * i + 1]]);
        let (w1, w2) = (hw(0), hw(1));
        let mode = ((w2 >> 12) & 3) as usize;
        let n = AFRAME_BYTES[mode];
        let mut words = [0u16; 8];
        for (i, w) in words.iter_mut().take(PULSE_WORDS[mode]).enumerate() {
            *w = hw(2 + i);
        }
        let pfo = w1 >> 9;

        let outb = if pfo == 0x7f {
            // 0x03006c60: intra
            self.scale = self.c.scale_initial; // 0x030069c0
            self.toggle = 1;
            self.hist = [0; 8];
            self.shaper = OutputStage::new();
            self.b[1][128..].fill(0);
            let (i0, i1, i2) = self.pulses(w1, w2, &words, 1);
            let infl = self.filter(self.c.lpc_base, i0, i1, i2);
            self.infl40 = infl;
            self.synth(1, 128, 0, 0, &infl, 128);
            0
        } else {
            // 0x03006c9c: predicted
            let (cur, oth) = (self.toggle, self.toggle ^ 1);
            let hi: [i32; 128] = self.b[oth][128..].try_into().unwrap();
            self.b[cur][..128].copy_from_slice(&hi);
            if pfo == 0x7e {
                self.b[cur][128..].fill(0);
            } else {
                // 0x03006288
                let b = (0x7f - pfo) as usize;
                for i in 0..128 {
                    self.b[cur][128 + i] = self.b[oth][b + i].wrapping_mul(vol(i)) >> 4;
                }
            }
            let (i0, i1, i2) = self.pulses(w1, w2, &words, cur);
            let infl = self.filter(self.filt, i0, i1, i2);
            let prev = if self.toggle == 1 {
                let p = self.infl20;
                self.infl40 = infl;
                p
            } else {
                let p = self.infl40;
                self.infl20 = infl;
                p
            };
            // Python integers: the averages are exact (no 32-bit wrap)
            let avg = |a: &[i32; 8], b: &[i32; 8]| -> [i32; 8] {
                std::array::from_fn(|k| ((a[k] as i64 + b[k] as i64) >> 1) as i32)
            };
            let q1 = avg(&infl, &prev);
            let q0 = avg(&q1, &prev);
            let q2 = avg(&q1, &infl);
            for (blk, q) in [q0, q1, q2, infl].iter().enumerate() {
                self.synth(cur, 128 + 32 * blk, oth, 32 * blk, q, 32);
            }
            oth
        };
        self.toggle ^= 1;
        samples.copy_from_slice(&self.b[outb][..128]);
        self.shaper.process_u8(samples, out);
        n
    }
}
