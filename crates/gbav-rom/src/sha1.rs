//! Minimal SHA-1 (FIPS 180-4), used to match ROMs against the No-Intro table and to compute
//! player build ids. Streaming: [`Sha1::update`] any number of times, then [`Sha1::finish`].
//! A `Sha1` can be cloned mid-stream to hash a prefix and the whole input in one pass.

/// Streaming SHA-1 state.
#[derive(Clone, Debug)]
pub struct Sha1 {
    h: [u32; 5],
    buf: [u8; 64],
    buf_len: usize,
    total: u64,
}

impl Default for Sha1 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha1 {
    pub fn new() -> Self {
        Sha1 {
            h: [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0],
            buf: [0; 64],
            buf_len: 0,
            total: 0,
        }
    }

    /// Feed more input.
    pub fn update(&mut self, mut data: &[u8]) {
        self.total = self.total.wrapping_add(data.len() as u64);
        if self.buf_len > 0 {
            let take = (64 - self.buf_len).min(data.len());
            self.buf[self.buf_len..self.buf_len + take].copy_from_slice(&data[..take]);
            self.buf_len += take;
            data = &data[take..];
            if self.buf_len < 64 {
                return;
            }
            let block = self.buf;
            compress(&mut self.h, &block);
            self.buf_len = 0;
        }
        let mut chunks = data.chunks_exact(64);
        for block in &mut chunks {
            compress(&mut self.h, block.try_into().expect("64-byte chunk"));
        }
        let rest = chunks.remainder();
        self.buf[..rest.len()].copy_from_slice(rest);
        self.buf_len = rest.len();
    }

    /// Finish and return the 20-byte digest.
    pub fn finish(mut self) -> [u8; 20] {
        let bits = self.total.wrapping_mul(8);
        let mut pad = [0u8; 72];
        pad[0] = 0x80;
        let pad_len = if self.buf_len < 56 { 56 - self.buf_len } else { 120 - self.buf_len };
        self.update(&pad[..pad_len]);
        self.update(&bits.to_be_bytes());
        debug_assert_eq!(self.buf_len, 0);
        let mut out = [0u8; 20];
        for (o, w) in out.chunks_exact_mut(4).zip(self.h) {
            o.copy_from_slice(&w.to_be_bytes());
        }
        out
    }
}

fn compress(h: &mut [u32; 5], block: &[u8; 64]) {
    let mut w = [0u32; 80];
    for (i, c) in block.chunks_exact(4).enumerate() {
        w[i] = u32::from_be_bytes([c[0], c[1], c[2], c[3]]);
    }
    for i in 16..80 {
        w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
    }
    let [mut a, mut b, mut c, mut d, mut e] = *h;
    for (i, &wi) in w.iter().enumerate() {
        let (f, k) = match i {
            0..=19 => ((b & c) | (!b & d), 0x5a827999),
            20..=39 => (b ^ c ^ d, 0x6ed9eba1),
            40..=59 => ((b & c) | (b & d) | (c & d), 0x8f1bbcdc),
            _ => (b ^ c ^ d, 0xca62c1d6),
        };
        let t = a
            .rotate_left(5)
            .wrapping_add(f)
            .wrapping_add(e)
            .wrapping_add(k)
            .wrapping_add(wi);
        e = d;
        d = c;
        c = b.rotate_left(30);
        b = a;
        a = t;
    }
    for (x, v) in h.iter_mut().zip([a, b, c, d, e]) {
        *x = x.wrapping_add(v);
    }
}

/// SHA-1 digest of `data`.
pub fn digest(data: &[u8]) -> [u8; 20] {
    let mut s = Sha1::new();
    s.update(data);
    s.finish()
}

/// Lower-case hex of a byte string.
pub fn hex(bytes: &[u8]) -> String {
    const D: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(D[(b >> 4) as usize] as char);
        s.push(D[(b & 15) as usize] as char);
    }
    s
}

/// SHA-1 of `data` as 40 lower-case hex digits.
pub fn hex_digest(data: &[u8]) -> String {
    hex(&digest(data))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Expected values from `printf ... | sha1sum` (coreutils).
    #[test]
    fn vectors() {
        assert_eq!(hex_digest(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(hex_digest(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(
            hex_digest(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
        assert_eq!(
            hex_digest(b"The quick brown fox jumps over the lazy dog"),
            "2fd4e1c67a2d28fced849ee1bb76e7391b93eb12"
        );
        // 1,000,000 x 'a'
        assert_eq!(hex_digest(&vec![b'a'; 1_000_000]), "34aa973cd4c4daa4f61eeb2bdbad27316534016f");
    }

    #[test]
    fn padding_boundaries_and_streaming() {
        // Every length around the 55/56/63/64-byte padding edges, hashed whole and in pieces.
        let data: Vec<u8> = (0..300u32).map(|i| (i * 7 + 3) as u8).collect();
        // From sha1sum over the same byte pattern.
        for (n, want) in [
            (55, "ddf57317ef34bfee3b6df83d359098930eb278bc"),
            (56, "a0d492bb0fc889d0eca3bc137066ab6f4f74f369"),
            (63, "c55856749bef509bdfe6bfebfc7bf4e793e82132"),
            (64, "bede92be29c3874e1b54ddc77988d606fc857a8e"),
            (119, "504e27376a6e0f0dba8295b85cb25dc4dfa17d23"),
            (120, "82134b02fb3f702491be9bed581eeab59334acb2"),
        ] {
            assert_eq!(hex_digest(&data[..n]), want, "len {n}");
        }
        for n in 0..data.len() {
            let whole = digest(&data[..n]);
            let mut s = Sha1::new();
            for piece in data[..n].chunks(13) {
                s.update(piece);
            }
            assert_eq!(s.finish(), whole, "len {n}");
        }
    }
}
