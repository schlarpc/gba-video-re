//! Generic helpers that are not VX-specific (candidates for hoisting into gbav-common).

/// Little-endian u16 at `off`, or `None` past the end.
pub fn le16(b: &[u8], off: usize) -> Option<u16> {
    b.get(off..off.checked_add(2)?)
        .map(|s| u16::from_le_bytes([s[0], s[1]]))
}

/// Little-endian u32 at `off`, or `None` past the end.
pub fn le32(b: &[u8], off: usize) -> Option<u32> {
    b.get(off..off.checked_add(4)?)
        .map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
}

/// Streaming MD5 (RFC 1321). `gbav_common::md5::digest` copies its input and rebuilds its
/// constant table per call, which dominates a whole-movie hash run; this one does neither.
#[derive(Clone)]
pub struct Md5 {
    h: [u32; 4],
    buf: [u8; 64],
    nbuf: usize,
    len: u64,
}

const S: [u32; 64] = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9,
    14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15,
    21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

#[rustfmt::skip]
const K: [u32; 64] = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
];

impl Default for Md5 {
    fn default() -> Self {
        Self::new()
    }
}

impl Md5 {
    pub fn new() -> Self {
        Md5 {
            h: [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476],
            buf: [0; 64],
            nbuf: 0,
            len: 0,
        }
    }

    fn block(h: &mut [u32; 4], chunk: &[u8]) {
        let mut m = [0u32; 16];
        for (i, w) in chunk.as_chunks::<4>().0.iter().enumerate() {
            m[i] = u32::from_le_bytes([w[0], w[1], w[2], w[3]]);
        }
        let (mut a, mut b, mut c, mut d) = (h[0], h[1], h[2], h[3]);
        for i in 0..64 {
            let (f, g) = match i / 16 {
                0 => ((b & c) | (!b & d), i),
                1 => ((d & b) | (!d & c), (5 * i + 1) % 16),
                2 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | !d), (7 * i) % 16),
            };
            let t = d;
            d = c;
            c = b;
            b = b.wrapping_add(
                a.wrapping_add(f)
                    .wrapping_add(K[i])
                    .wrapping_add(m[g])
                    .rotate_left(S[i]),
            );
            a = t;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
    }

    pub fn update(&mut self, mut data: &[u8]) {
        self.len = self.len.wrapping_add(data.len() as u64);
        if self.nbuf > 0 {
            let n = (64 - self.nbuf).min(data.len());
            self.buf[self.nbuf..self.nbuf + n].copy_from_slice(&data[..n]);
            self.nbuf += n;
            data = &data[n..];
            if self.nbuf < 64 {
                return;
            }
            let buf = self.buf;
            Self::block(&mut self.h, &buf);
            self.nbuf = 0;
        }
        let mut chunks = data.chunks_exact(64);
        for c in &mut chunks {
            Self::block(&mut self.h, c);
        }
        let rest = chunks.remainder();
        self.buf[..rest.len()].copy_from_slice(rest);
        self.nbuf = rest.len();
    }

    pub fn finish(mut self) -> [u8; 16] {
        let bits = self.len.wrapping_mul(8);
        let mut pad = [0u8; 72];
        pad[0] = 0x80;
        let n = if self.nbuf < 56 {
            56 - self.nbuf
        } else {
            120 - self.nbuf
        };
        let len = self.len;
        self.update(&pad[..n]);
        self.update(&bits.to_le_bytes());
        self.len = len;
        debug_assert_eq!(self.nbuf, 0);
        let mut out = [0u8; 16];
        for (i, v) in self.h.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&v.to_le_bytes());
        }
        out
    }

    pub fn digest(data: &[u8]) -> [u8; 16] {
        let mut m = Md5::new();
        m.update(data);
        m.finish()
    }
}

/// Lowercase hex string of `d`.
pub fn hex(d: &[u8]) -> String {
    d.iter().map(|b| format!("{b:02x}")).collect()
}

/// Hex MD5 of `data` (manifest form).
pub fn md5_hex(data: &[u8]) -> String {
    hex(&Md5::digest(data))
}

/// Hex MD5 of a u16 slice as little-endian bytes (the BGR555 framebuffer manifest form).
pub fn md5_hex_u16(px: &[u16]) -> String {
    let mut m = Md5::new();
    let mut tmp = [0u8; 512];
    for c in px.chunks(256) {
        for (i, v) in c.iter().enumerate() {
            tmp[2 * i..2 * i + 2].copy_from_slice(&v.to_le_bytes());
        }
        m.update(&tmp[..2 * c.len()]);
    }
    hex(&m.finish())
}

/// The GBA audio output stage of the VX player (IWRAM 0x03006a54..0x03006b54): 4x linear
/// interpolation of each sample (3p+c)/4, (p+c)/2, (p+3c)/4, c, then an 8-bit quantiser with
/// error feedback (noise shaping) for Direct Sound at 4x the sample rate. Exact 32-bit
/// wraparound; the interpolation sums are exact (as container/vxaudio.py computes them).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Interp4Shaper {
    pub e: i32,
    pub fb: i32,
    pub prev: i32,
}

impl Interp4Shaper {
    /// `out.len()` must be `4 * samples.len()`; bytes are signed 8-bit.
    pub fn run(&mut self, samples: &[i32], out: &mut [u8]) {
        assert_eq!(out.len(), 4 * samples.len());
        let (mut e, mut fb, mut prev) = (self.e, self.fb, self.prev);
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
        *self = Interp4Shaper { e, fb, prev };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn md5_matches_common() {
        let data: Vec<u8> = (0..1000u32).map(|i| (i * 7 + 3) as u8).collect();
        for n in [0, 1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 999, 1000] {
            assert_eq!(
                md5_hex(&data[..n]),
                gbav_common::md5::hex(&data[..n]),
                "len {n}"
            );
            // split updates
            let mut m = Md5::new();
            for c in data[..n].chunks(17) {
                m.update(c);
            }
            assert_eq!(hex(&m.finish()), gbav_common::md5::hex(&data[..n]));
        }
    }
}
