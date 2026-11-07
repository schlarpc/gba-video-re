//! FVMV audio: GSM 06.10-style RPE-LPC without long-term prediction (player IWRAM
//! 0x03001b88), followed by the Actimagine output stage ([`crate::util::OutputStage`],
//! 0x03001d18). See `docs/format-fvmv.md` section 5.
//!
//! The dequantisation tables are computed from the GSM 06.10 formulas; they equal the
//! cartridge's IWRAM tables entry for entry.

use crate::util::OutputStage;
use std::sync::OnceLock;

/// Bytes per coded block.
pub const BLOCK_BYTES: usize = 40;
/// Samples per block at 16384 Hz.
pub const BLOCK_SAMPLES: usize = 256;
/// Signed 8-bit output samples per block at 65536 Hz.
pub const OUT_BYTES: usize = 1024;
/// Synthesis sample rate.
pub const RATE_HZ: u32 = 16384;
/// Output (DirectSound) sample rate.
pub const OUT_RATE_HZ: u32 = 65536;

// GSM 06.10 tables (ETSI EN 300 961, sections 4.2.8, 5.2.8 and 5.2.15)
const LAR_B: [i32; 8] = [0, 0, 2048, -2560, 94, -1792, -341, -1144];
const LAR_MIC: [i32; 8] = [-32, -32, -16, -16, -8, -8, -4, -4];
const LAR_INVA: [i32; 8] = [13107, 13107, 13107, 13107, 19223, 17476, 31454, 29708];
const GSM_FAC: [i32; 8] = [18431, 20479, 22527, 24575, 26623, 28671, 30719, 32767];
/// Number of LARc levels per coefficient (6/6/5/5/4/4/3/3 bits).
const NLEV: [usize; 8] = [64, 64, 32, 32, 16, 16, 8, 8];

fn sat16(x: i32) -> i32 {
    x.clamp(-32768, 32767)
}

/// LARc -> reflection coefficient rp (Q15): GSM decoding of the coded LAR plus LAR -> rp.
fn lar_to_rp(i: usize, c: i32) -> i32 {
    let t = sat16((c + LAR_MIC[i]) * 1024);
    let t = sat16(t - LAR_B[i] * 2);
    let t = ((LAR_INVA[i] * t + 16384) >> 15) as i16 as i32; // GSM mult_r
    let larp = sat16(t + t);
    let mut a = larp.abs();
    a = if a < 11059 {
        a << 1
    } else if a < 20070 {
        a + 11059
    } else {
        (a >> 2) + 26112
    };
    if larp < 0 {
        -a
    } else {
        a
    }
}

/// xmaxc (6 bit) and pulse code (3 bit) -> amplitude: GSM APCM inverse quantisation.
fn pulse(xmaxc: i32, v: i32) -> i32 {
    let mut exp = if xmaxc > 15 { (xmaxc >> 3) - 1 } else { 0 };
    let mut mant = xmaxc - (exp << 3);
    if mant == 0 {
        exp = -4;
        mant = 7;
    } else {
        while mant <= 7 {
            mant = mant << 1 | 1;
            exp -= 1;
        }
        mant -= 8;
    }
    let t2 = 6 - exp;
    let t3 = if t2 > 0 { 1 << (t2 - 1) } else { 0 };
    let t = ((v << 1) - 7) * 4096;
    let t = (GSM_FAC[mant as usize] * t + 16384) >> 15;
    (t + t3) >> t2
}

struct Tables {
    rp: [[i32; 64]; 8],
    xm: [[i32; 8]; 64],
}

fn tables() -> &'static Tables {
    static T: OnceLock<Tables> = OnceLock::new();
    T.get_or_init(|| {
        let mut t = Tables {
            rp: [[0; 64]; 8],
            xm: [[0; 8]; 64],
        };
        for i in 0..8 {
            for c in 0..NLEV[i] {
                t.rp[i][c] = lar_to_rp(i, c as i32);
            }
        }
        for x in 0..64 {
            for v in 0..8 {
                t.xm[x][v] = pulse(x as i32, v as i32);
            }
        }
        t
    })
}

/// (a * b + 0x4000) >> 15 with a wrapping 32-bit multiply-accumulate, as the ARM `mla`.
#[inline]
fn madd_q15(a: i32, b: i32) -> i32 {
    a.wrapping_mul(b).wrapping_add(0x4000) >> 15
}

/// Synthesis state: the lattice and the de-emphasis memory. Zero at power-on and never
/// reset by the player, not even between episodes or on a seek.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Synth {
    /// Lattice state (IWRAM 0x03001b60).
    pub v: [i32; 9],
    /// De-emphasis state (0x03001b5c).
    pub msr: i32,
}

impl Synth {
    pub const fn new() -> Self {
        Synth { v: [0; 9], msr: 0 }
    }

    /// Decode one 40-byte block into 256 int32 samples (2 x the de-emphasised output; no
    /// saturation, so values can exceed 16 bits).
    pub fn decode(&mut self, blk: &[u8; BLOCK_BYTES], out: &mut [i32; BLOCK_SAMPLES]) {
        let t = tables();
        let mut w = [0u32; 10];
        for (i, wi) in w.iter_mut().enumerate() {
            *wi = u32::from_le_bytes(blk[4 * i..4 * i + 4].try_into().unwrap());
        }
        let larc = [
            w[0] >> 26,
            (w[0] >> 20) & 63,
            (w[0] >> 15) & 31,
            (w[0] >> 10) & 31,
            (w[0] >> 6) & 15,
            (w[3] & 1) << 3 | (w[5] & 1) << 2 | (w[7] & 1) << 1 | (w[9] & 1),
            (w[0] >> 3) & 7,
            w[0] & 7,
        ];
        let mut rp = [0i32; 8];
        for i in 0..8 {
            rp[i] = t.rp[i][larc[i] as usize];
        }
        // excitation: 4 sub-blocks of 64 samples, 21 pulses at stride 3 from grid offset 0..3
        out.fill(0);
        for k in 0..4 {
            let xmaxc = ((w[1] >> (8 + 6 * k)) & 63) as usize;
            let grid = ((w[1] >> (2 * k)) & 3) as usize;
            let (a, b) = (w[2 + 2 * k], w[3 + 2 * k]);
            let e = &mut out[64 * k..64 * k + 64];
            for i in 0..10 {
                e[grid + 3 * i] = t.xm[xmaxc][((a >> (29 - 3 * i)) & 7) as usize];
                e[grid + 3 * (10 + i)] = t.xm[xmaxc][((b >> (29 - 3 * i)) & 7) as usize];
            }
            e[grid + 60] = t.xm[xmaxc][((a & 3) << 1 | ((b >> 1) & 1)) as usize];
        }
        // short-term synthesis lattice (GSM 4.3.2 without saturation) and de-emphasis
        for o in out.iter_mut() {
            let mut s = *o;
            for k in (0..8).rev() {
                s = s.wrapping_sub(madd_q15(rp[k], self.v[k]));
                self.v[k + 1] = self.v[k].wrapping_add(madd_q15(rp[k], s));
            }
            self.v[0] = s;
            self.msr = s.wrapping_add(madd_q15(self.msr, 28180));
            *o = self.msr.wrapping_mul(2);
        }
    }
}

/// Complete audio state: synthesis plus output stage.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct AudioState {
    pub synth: Synth,
    pub output: OutputStage,
}

impl AudioState {
    pub const fn new() -> Self {
        AudioState {
            synth: Synth::new(),
            output: OutputStage::new(),
        }
    }

    /// Decode one block: the int32 synthesis output (first audio manifest hash) and the
    /// 1024 signed 8-bit PCM bytes the player plays (second hash).
    pub fn decode_block(
        &mut self,
        blk: &[u8; BLOCK_BYTES],
        synth: &mut [i32; BLOCK_SAMPLES],
        pcm: &mut [i8; OUT_BYTES],
    ) {
        self.synth.decode(blk, synth);
        self.output.process(synth, pcm);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gsm_table_spot_checks() {
        // GSM 06.10: LARc at the zero point decodes to rp = 0 for the symmetric coefficients
        assert_eq!(lar_to_rp(0, 32), 0);
        assert_eq!(lar_to_rp(1, 32), 0);
        // APCM: xmaxc = 0 gives the smallest amplitudes, symmetric around zero
        assert_eq!(pulse(0, 0), -pulse(0, 7));
        // largest xmaxc, largest code: 32767 * 28672 >> 15 = 28671
        assert_eq!(pulse(63, 7), 28671);
    }
}
