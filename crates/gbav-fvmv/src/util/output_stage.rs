//! Actimagine's GBA audio output stage: 4x linear interpolation followed by 8-bit
//! quantisation with error feedback (noise shaping). FVMV (Pokemon, IWRAM 0x03001d18) and
//! VX audio (Shrek etc.) use the same algorithm; see `docs/format-fvmv.md` section 5.5.
//!
//! All arithmetic is 32-bit two's complement with wraparound and arithmetic right shifts,
//! as on the ARM7TDMI. The input is in 16-bit scale (one output step = 256).

/// Interpolator and noise-shaper state. All zero at power-on; the players never reset it.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct OutputStage {
    /// Last quantisation error, `(q << 8) - v`.
    pub err: i32,
    /// Error feedback subtracted before the next quantisation.
    pub fb: i32,
    /// Previous input sample, the left end of the interpolation.
    pub prev: i32,
}

impl OutputStage {
    pub const fn new() -> Self {
        OutputStage {
            err: 0,
            fb: 0,
            prev: 0,
        }
    }

    /// Quantise one sample to signed 8 bits and update the error feedback
    /// `fb = (e[n-1] + 2 e[n]) * 15/16`.
    #[inline]
    pub fn quantise(&mut self, v: i32) -> i8 {
        let q = (v.wrapping_add(0x80).wrapping_sub(self.fb) >> 8).clamp(-128, 127);
        let old = self.err;
        self.err = q.wrapping_mul(256).wrapping_sub(v);
        let f = old.wrapping_add(self.err.wrapping_mul(2));
        self.fb = f.wrapping_sub(f >> 4);
        q as i8
    }

    /// Interpolate `input` 4x and quantise: `out` receives `4 * input.len()` signed bytes.
    pub fn process(&mut self, input: &[i32], out: &mut [i8]) {
        assert!(out.len() >= 4 * input.len(), "output stage: out too short");
        for (&x, o) in input.iter().zip(out.chunks_exact_mut(4)) {
            let p = self.prev;
            o[0] = self.quantise(p.wrapping_mul(3).wrapping_add(x) >> 2);
            o[1] = self.quantise(p.wrapping_add(x) >> 1);
            o[2] = self.quantise(x.wrapping_mul(3).wrapping_add(p) >> 2);
            o[3] = self.quantise(x);
            self.prev = x;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn silence_stays_silent() {
        let mut s = OutputStage::new();
        let mut out = [1i8; 64];
        s.process(&[0; 16], &mut out);
        assert!(out.iter().all(|&b| b == 0));
        assert_eq!(s, OutputStage::new());
    }

    #[test]
    fn clamps_and_wraps() {
        let mut s = OutputStage::new();
        let mut out = [0i8; 8];
        s.process(&[i32::MAX, i32::MIN], &mut out);
        // (3*0 + MAX) >> 2 is far above the 8-bit range
        assert_eq!(out[0], 127);
    }
}
