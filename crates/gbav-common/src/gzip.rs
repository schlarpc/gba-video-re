//! Standard DEFLATE (RFC 1951) decompression, gzip (RFC 1952) and zlib (RFC 1950) framing, and
//! the CRC-32 and Adler-32 checksums. Used to read the gzipped hash manifests in `verify/` and
//! to write PNG files, without a crates.io dependency.
//!
//! This is the textbook format, not the Hydrogen engine's DEFLATE variant (`gbav-hydrogen`).

use crate::{Error, Result};

/// CRC-32 (IEEE 802.3, reflected, as used by gzip and PNG) of `data`, continuing from `crc`
/// (start with 0).
pub fn crc32(crc: u32, data: &[u8]) -> u32 {
    static TABLE: std::sync::OnceLock<[u32; 256]> = std::sync::OnceLock::new();
    let t = TABLE.get_or_init(|| {
        let mut t = [0u32; 256];
        for (i, e) in t.iter_mut().enumerate() {
            let mut c = i as u32;
            for _ in 0..8 {
                c = if c & 1 != 0 { 0xEDB8_8320 ^ (c >> 1) } else { c >> 1 };
            }
            *e = c;
        }
        t
    });
    let mut c = !crc;
    for &b in data {
        c = t[((c ^ b as u32) & 0xff) as usize] ^ (c >> 8);
    }
    !c
}

/// Adler-32 of `data`, continuing from `adler` (start with 1).
pub fn adler32(adler: u32, data: &[u8]) -> u32 {
    let (mut a, mut b) = (adler & 0xffff, adler >> 16);
    for chunk in data.chunks(5552) {
        for &x in chunk {
            a += x as u32;
            b += a;
        }
        a %= 65521;
        b %= 65521;
    }
    (b << 16) | a
}

struct Bits<'a> {
    data: &'a [u8],
    pos: usize,
    bit: u32,
    nbits: u32,
}

impl<'a> Bits<'a> {
    fn need(&mut self, n: u32) -> Result<()> {
        while self.nbits < n {
            let byte = *self
                .data
                .get(self.pos)
                .ok_or_else(|| Error::Format("deflate: unexpected end of input".into()))?;
            self.pos += 1;
            self.bit |= (byte as u32) << self.nbits;
            self.nbits += 8;
        }
        Ok(())
    }
    fn get(&mut self, n: u32) -> Result<u32> {
        if n == 0 {
            return Ok(0);
        }
        self.need(n)?;
        let v = self.bit & ((1u32 << n) - 1);
        self.bit >>= n;
        self.nbits -= n;
        Ok(v)
    }
    fn align(&mut self) {
        let drop = self.nbits % 8;
        self.bit >>= drop;
        self.nbits -= drop;
    }
}

/// Canonical Huffman decoding table: counts per length and symbols sorted by code.
struct Huffman {
    count: [u16; 16],
    symbol: Vec<u16>,
}

impl Huffman {
    fn new(lengths: &[u8]) -> Result<Huffman> {
        let mut count = [0u16; 16];
        for &l in lengths {
            count[l as usize] += 1;
        }
        count[0] = 0;
        let mut left: i32 = 1;
        for &c in &count[1..] {
            left = (left << 1) - c as i32;
            if left < 0 {
                return Err(Error::Format("deflate: over-subscribed code".into()));
            }
        }
        let mut offs = [0u16; 16];
        for len in 1..15 {
            offs[len + 1] = offs[len] + count[len];
        }
        let mut symbol = vec![0u16; lengths.len()];
        for (s, &l) in lengths.iter().enumerate() {
            if l != 0 {
                symbol[offs[l as usize] as usize] = s as u16;
                offs[l as usize] += 1;
            }
        }
        Ok(Huffman { count, symbol })
    }

    fn decode(&self, br: &mut Bits) -> Result<u16> {
        let (mut code, mut first, mut index) = (0i32, 0i32, 0i32);
        for len in 1..16 {
            code |= br.get(1)? as i32;
            let count = self.count[len] as i32;
            if code - count < first {
                return Ok(self.symbol[(index + (code - first)) as usize]);
            }
            index += count;
            first += count;
            first <<= 1;
            code <<= 1;
        }
        Err(Error::Format("deflate: invalid Huffman code".into()))
    }
}

const LBASE: [u16; 29] = [
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131,
    163, 195, 227, 258,
];
const LEXT: [u8; 29] = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
const DBASE: [u16; 30] = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049,
    3073, 4097, 6145, 8193, 12289, 16385, 24577,
];
const DEXT: [u8; 30] = [
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13,
];

fn codes(br: &mut Bits, out: &mut Vec<u8>, lit: &Huffman, dist: &Huffman) -> Result<()> {
    loop {
        let sym = lit.decode(br)? as usize;
        match sym {
            0..=255 => out.push(sym as u8),
            256 => return Ok(()),
            257..=285 => {
                let i = sym - 257;
                let len = LBASE[i] as usize + br.get(LEXT[i] as u32)? as usize;
                let d = dist.decode(br)? as usize;
                if d >= 30 {
                    return Err(Error::Format("deflate: bad distance symbol".into()));
                }
                let dist = DBASE[d] as usize + br.get(DEXT[d] as u32)? as usize;
                if dist > out.len() {
                    return Err(Error::Format("deflate: distance too far back".into()));
                }
                let start = out.len() - dist;
                for k in 0..len {
                    let b = out[start + k];
                    out.push(b);
                }
            }
            _ => return Err(Error::Format("deflate: bad length symbol".into())),
        }
    }
}

/// Decompress a raw DEFLATE stream. Returns the output and the number of input bytes used.
pub fn inflate(data: &[u8]) -> Result<(Vec<u8>, usize)> {
    let mut br = Bits { data, pos: 0, bit: 0, nbits: 0 };
    let mut out = Vec::new();
    let mut fixed: Option<(Huffman, Huffman)> = None;
    loop {
        let last = br.get(1)?;
        match br.get(2)? {
            0 => {
                br.align();
                let len = br.get(16)? as usize;
                let nlen = br.get(16)? as usize;
                if len != !nlen & 0xffff {
                    return Err(Error::Format("deflate: stored block length check".into()));
                }
                // the bit buffer is byte aligned and empty after reading 32 bits
                let end = br.pos + len;
                let src = data
                    .get(br.pos..end)
                    .ok_or_else(|| Error::Format("deflate: stored block past end".into()))?;
                out.extend_from_slice(src);
                br.pos = end;
            }
            1 => {
                if fixed.is_none() {
                    let mut l = [0u8; 288];
                    l[..144].fill(8);
                    l[144..256].fill(9);
                    l[256..280].fill(7);
                    l[280..].fill(8);
                    fixed = Some((Huffman::new(&l)?, Huffman::new(&[5u8; 30])?));
                }
                let (lit, dist) = fixed.as_ref().unwrap();
                codes(&mut br, &mut out, lit, dist)?;
            }
            2 => {
                let nlen = br.get(5)? as usize + 257;
                let ndist = br.get(5)? as usize + 1;
                let ncode = br.get(4)? as usize + 4;
                const ORDER: [usize; 19] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];
                let mut cl = [0u8; 19];
                for &o in ORDER.iter().take(ncode) {
                    cl[o] = br.get(3)? as u8;
                }
                let clh = Huffman::new(&cl)?;
                let mut lengths = vec![0u8; nlen + ndist];
                let mut i = 0;
                while i < nlen + ndist {
                    let sym = clh.decode(&mut br)?;
                    let (val, rep) = match sym {
                        0..=15 => (sym as u8, 1),
                        16 => {
                            if i == 0 {
                                return Err(Error::Format("deflate: repeat with no length".into()));
                            }
                            (lengths[i - 1], 3 + br.get(2)? as usize)
                        }
                        17 => (0, 3 + br.get(3)? as usize),
                        _ => (0, 11 + br.get(7)? as usize),
                    };
                    if i + rep > nlen + ndist {
                        return Err(Error::Format("deflate: too many code lengths".into()));
                    }
                    lengths[i..i + rep].fill(val);
                    i += rep;
                }
                let lit = Huffman::new(&lengths[..nlen])?;
                let dist = Huffman::new(&lengths[nlen..])?;
                codes(&mut br, &mut out, &lit, &dist)?;
            }
            _ => return Err(Error::Format("deflate: invalid block type".into())),
        }
        if last == 1 {
            return Ok((out, br.pos));
        }
    }
}

/// Decompress a gzip file (all members, concatenated), checking each member's CRC-32 and size.
pub fn gunzip(data: &[u8]) -> Result<Vec<u8>> {
    let bad = |m: &str| Error::Format(format!("gzip: {m}"));
    let mut out = Vec::new();
    let mut p = 0;
    while p < data.len() {
        let h = data.get(p..p + 10).ok_or_else(|| bad("truncated header"))?;
        if h[0] != 0x1f || h[1] != 0x8b || h[2] != 8 {
            return Err(bad("not a gzip deflate stream"));
        }
        let flg = h[3];
        p += 10;
        if flg & 4 != 0 {
            let xlen = u16::from_le_bytes([data[p], data[p + 1]]) as usize;
            p += 2 + xlen;
        }
        for bit in [8u8, 16] {
            if flg & bit != 0 {
                while *data.get(p).ok_or_else(|| bad("truncated name"))? != 0 {
                    p += 1;
                }
                p += 1;
            }
        }
        if flg & 2 != 0 {
            p += 2;
        }
        let (member, used) = inflate(data.get(p..).ok_or_else(|| bad("truncated"))?)?;
        p += used;
        let t = data.get(p..p + 8).ok_or_else(|| bad("truncated trailer"))?;
        let crc = u32::from_le_bytes([t[0], t[1], t[2], t[3]]);
        let isize = u32::from_le_bytes([t[4], t[5], t[6], t[7]]);
        if crc != crc32(0, &member) || isize != member.len() as u32 {
            return Err(bad("CRC or size mismatch"));
        }
        p += 8;
        out.extend_from_slice(&member);
    }
    Ok(out)
}

/// Wrap `data` in a zlib stream made of stored (uncompressed) DEFLATE blocks. Enough for PNG.
pub fn zlib_stored(data: &[u8]) -> Vec<u8> {
    let mut z = vec![0x78, 0x01];
    let mut chunks = data.chunks(0xffff).peekable();
    if chunks.peek().is_none() {
        z.extend_from_slice(&[1, 0, 0, 0xff, 0xff]);
    }
    while let Some(c) = chunks.next() {
        z.push(chunks.peek().is_none() as u8);
        let n = c.len() as u16;
        z.extend_from_slice(&n.to_le_bytes());
        z.extend_from_slice(&(!n).to_le_bytes());
        z.extend_from_slice(c);
    }
    z.extend_from_slice(&adler32(1, data).to_be_bytes());
    z
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksums() {
        assert_eq!(crc32(0, b"123456789"), 0xCBF4_3926);
        assert_eq!(adler32(1, b"Wikipedia"), 0x11E6_0398);
    }

    #[test]
    fn gzip_fixed_and_stored() {
        // `printf 'hello hello hello\n' | gzip -n`: one fixed-Huffman block with a match
        let gz = [
            0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0xcb, 0x48, 0xcd, 0xc9,
            0xc9, 0x57, 0xc8, 0x40, 0x90, 0x5c, 0x00, 0x3b, 0x7c, 0x8a, 0xdf, 0x12, 0x00, 0x00,
            0x00,
        ];
        assert_eq!(gunzip(&gz).unwrap(), b"hello hello hello\n");
        // stored blocks round trip through zlib_stored
        let data: Vec<u8> = (0..200_000u32).map(|i| (i * 7 % 251) as u8).collect();
        let z = zlib_stored(&data);
        let (back, used) = inflate(&z[2..]).unwrap();
        assert_eq!(back, data);
        assert_eq!(&z[2 + used..], &adler32(1, &data).to_be_bytes());
    }

    #[test]
    fn gzip_dynamic_manifest() {
        // a real manifest from verify/ (dynamic Huffman blocks); md5 and size from zcat
        let gz = include_bytes!("../../../verify/vx/shrek-sharktale-rev5_3dd3600.frame_md5.csv.gz");
        let csv = gunzip(gz).unwrap();
        assert_eq!(csv.len(), 41435);
        assert_eq!(crate::md5::hex(&csv), "944c124ceb43e06650c5de933078ab90");
    }
}
