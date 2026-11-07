//! Actimagine's GBA audio output stage, shared by the VX players (IWRAM 0x03006a54) and the
//! FVMV player (IWRAM 0x03001d18): 4x linear interpolation of each sample, `(3p+x)/4`,
//! `(p+x)/2`, `(3x+p)/4`, `x`, then an 8-bit quantiser with error feedback (noise shaping) for
//! Direct Sound at four times the sample rate.
//!
//! All arithmetic is 32-bit two's complement with wraparound and arithmetic right shifts, as on
//! the ARM7TDMI. The input is in 16-bit scale (one output step = 256).

/// Interpolator and noise-shaper state. All zero at power-on.
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
        OutputStage { err: 0, fb: 0, prev: 0 }
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

    /// [`OutputStage::process`] writing the signed bytes as `u8` (the manifests' byte form).
    pub fn process_u8(&mut self, input: &[i32], out: &mut [u8]) {
        assert!(out.len() >= 4 * input.len(), "output stage: out too short");
        for (&x, o) in input.iter().zip(out.chunks_exact_mut(4)) {
            let mut q = [0i8; 4];
            self.process(&[x], &mut q);
            for (d, s) in o.iter_mut().zip(q) {
                *d = s as u8;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The VX lane's formulation (gbav-vx `Interp4Shaper`, from container/vxaudio.py):
    /// interpolation sums in exact (64-bit) arithmetic, error feedback written as
    /// `fb = e + 2 ne; fb -= fb >> 4`.
    fn vx_interp4_shaper(st: &mut (i32, i32, i32), samples: &[i32], out: &mut [u8]) {
        let (mut e, mut fb, mut prev) = *st;
        let mut k = 0;
        for &cur in samples {
            let (p, c) = (prev as i64, cur as i64);
            for x in [(3 * p + c) >> 2, (c + p) >> 1, (3 * c + p) >> 2, c] {
                let x = x as i32;
                let q = (x.wrapping_add(0x80).wrapping_sub(fb) >> 8).clamp(-128, 127);
                let ne = (q << 8).wrapping_sub(x);
                fb = e.wrapping_add(ne.wrapping_mul(2));
                fb = fb.wrapping_sub(fb >> 4);
                e = ne;
                out[k] = q as u8;
                k += 1;
            }
            prev = cur;
        }
        *st = (e, fb, prev);
    }

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
    }

    /// The VX and FVMV formulations are the same algorithm: identical output and state on any
    /// input whose interpolation sums fit in 32 bits (|x| < 2^29). VX samples stay within
    /// +-2^17 and FVMV's within the 16-bit range plus synthesis headroom, so both families'
    /// real input is covered; beyond 2^29 only the 32-bit form matches the ARM.
    #[test]
    fn vx_and_fvmv_output_stages_agree() {
        let mut r = Rng(0x9e3779b97f4a7c15);
        for run in 0..2000 {
            let bits = [8, 12, 16, 18, 24, 29][run % 6];
            let n = 1 + (r.next() % 300) as usize;
            let input: Vec<i32> =
                (0..n).map(|_| ((r.next() % (1u64 << bits)) as i64 - (1i64 << (bits - 1))) as i32).collect();
            let start = OutputStage {
                err: (r.next() % 512) as i32 - 256,
                fb: (r.next() % 1024) as i32 - 512,
                prev: input[0] / 2,
            };
            let mut a = start;
            let mut out_a = vec![0u8; 4 * n];
            a.process_u8(&input, &mut out_a);
            let mut b = (start.err, start.fb, start.prev);
            let mut out_b = vec![0u8; 4 * n];
            vx_interp4_shaper(&mut b, &input, &mut out_b);
            assert_eq!(out_a, out_b, "run {run}");
            assert_eq!((a.err, a.fb, a.prev), b, "run {run}");
        }
    }

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
        assert_eq!(out[0], 127);
    }
}
