//! LZMA ("LZMA1", raw stream, no end marker) as the ADS players decode it: the cartridge's core
//! (IWRAM 0x03001C58 in SRMTHFG) hard-wires lc = 0, lp = 0, pb = 2 and reads the blob's params
//! word only to size its probability array, so this decoder does the same. Output goes into a
//! power-of-two ring (128 KiB for index streams, as on the cartridge).
//!
//! Written from Igor Pavlov's LZMA specification (public domain); behaviour on bad input follows
//! `reference/ads/lzma_dec.c`: [`LzmaError::InputOverrun`] when the range coder needs a byte past the
//! end of the blob (the symbol in progress is still finished, unnormalised),
//! [`LzmaError::BadDistance`] for a match before the start of the output or beyond the ring.

use gbav_common::Error;

/// Ring size of the index-stream decoder (EWRAM, mask 0x1FFFF).
pub const RING: usize = 0x20000;
/// Ring the reference uses for one-shot blobs (codebooks, text); never wraps for them.
pub const BLOB_RING: usize = 0x2000;

const LC: u32 = 0;
const LP: u32 = 0;
const PB: u32 = 2;

const NSTATES: usize = 12;
const POSB_MAX: usize = 4;
const P_ISMATCH: usize = 0;
const P_ISREP: usize = P_ISMATCH + (NSTATES << POSB_MAX);
const P_ISREPG0: usize = P_ISREP + NSTATES;
const P_ISREPG1: usize = P_ISREPG0 + NSTATES;
const P_ISREPG2: usize = P_ISREPG1 + NSTATES;
const P_ISREP0L: usize = P_ISREPG2 + NSTATES;
const P_POSSLOT: usize = P_ISREP0L + (NSTATES << POSB_MAX);
const P_SPECPOS: usize = P_POSSLOT + (4 << 6);
const P_ALIGN: usize = P_SPECPOS + 114;
const P_LEN: usize = P_ALIGN + 16;
const L_CHOICE: usize = 0;
const L_CHOICE2: usize = 1;
const L_LOW: usize = 2;
const L_MID: usize = L_LOW + (16 << 3);
const L_HIGH: usize = L_MID + (16 << 3);
const L_SIZE: usize = L_HIGH + 256;
const P_REPLEN: usize = P_LEN + L_SIZE;
const P_LITERAL: usize = P_REPLEN + L_SIZE;
/// probabilities for lc = 0, lp = 0 (0x736 + 0x300)
const NPROBS: usize = P_LITERAL + (0x300 << (LC + LP));

const TOP: u32 = 1 << 24;

/// Error classes, numbered like `lzma_dec.error` in the C reference.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LzmaError {
    /// 1: the range coder needs a byte past the end of the blob
    InputOverrun,
    /// 2: a match distance before the start of the output (or the end marker), or beyond the ring
    BadDistance,
}

impl LzmaError {
    pub fn code(self) -> i32 {
        match self {
            LzmaError::InputOverrun => 1,
            LzmaError::BadDistance => 2,
        }
    }
}

impl From<LzmaError> for Error {
    fn from(e: LzmaError) -> Error {
        Error::Undefined(match e {
            LzmaError::InputOverrun => "LZMA: read past the end of the blob".into(),
            LzmaError::BadDistance => "LZMA: match distance before the start of the output".into(),
        })
    }
}

/// An LZMA decoder over one raw stream (the bytes after a blob's 8-byte prefix).
#[derive(Clone)]
pub struct Lzma<B: AsRef<[u8]>> {
    input: B,
    ip: usize,
    range: u32,
    code: u32,
    ring: Vec<u8>,
    mask: u32,
    /// total bytes written
    pos: u32,
    state: usize,
    rep: [u32; 4],
    remain: u32,
    probs: Box<[u16; NPROBS]>,
    error: Option<LzmaError>,
}

impl<B: AsRef<[u8]>> Lzma<B> {
    /// Start a stream: `ring_size` must be a power of two.
    pub fn new(input: B, ring_size: usize) -> Lzma<B> {
        assert!(ring_size.is_power_of_two());
        let mut d = Lzma {
            input,
            ip: 0,
            range: 0xffff_ffff,
            code: 0,
            ring: vec![0; ring_size],
            mask: ring_size as u32 - 1,
            pos: 0,
            state: 0,
            rep: [0; 4],
            remain: 0,
            probs: Box::new([1024; NPROBS]),
            error: None,
        };
        for _ in 0..5 {
            let inp = d.input.as_ref();
            if d.ip >= inp.len() {
                d.error = Some(LzmaError::InputOverrun);
                break;
            }
            d.code = (d.code << 8) | inp[d.ip] as u32;
            d.ip += 1;
        }
        d
    }

    /// Restart on a new stream, keeping the ring's allocation (and, like the cartridge, its old
    /// contents, which no valid match can reach).
    pub fn restart(&mut self, input: B) {
        let ring = std::mem::take(&mut self.ring);
        let mut d = Lzma::new_with_ring(input, ring);
        std::mem::swap(self, &mut d);
    }

    fn new_with_ring(input: B, ring: Vec<u8>) -> Lzma<B> {
        let n = ring.len();
        let mut d = Lzma::new(input, 1);
        d.ring = ring;
        d.mask = n as u32 - 1;
        d
    }

    pub fn error(&self) -> Option<LzmaError> {
        self.error
    }
    /// Bytes written so far.
    pub fn pos(&self) -> u32 {
        self.pos
    }
    /// Input bytes consumed (5 at init, one per normalisation).
    pub fn consumed(&self) -> usize {
        self.ip
    }
    pub fn ring(&self) -> &[u8] {
        &self.ring
    }
    /// `n` bytes of output starting at stream offset `start`, read from the ring.
    pub fn read(&self, start: u32, n: usize, out: &mut Vec<u8>) {
        out.clear();
        out.extend((0..n).map(|i| self.ring[(start.wrapping_add(i as u32) & self.mask) as usize]));
    }

    #[inline]
    fn norm(&mut self) {
        if self.range < TOP {
            let inp = self.input.as_ref();
            if self.ip >= inp.len() {
                if self.error.is_none() {
                    self.error = Some(LzmaError::InputOverrun);
                }
                return;
            }
            self.range <<= 8;
            self.code = (self.code << 8) | inp[self.ip] as u32;
            self.ip += 1;
        }
    }

    #[inline]
    fn bit(&mut self, i: usize) -> u32 {
        let p = self.probs[i] as u32;
        let bound = (self.range >> 11).wrapping_mul(p);
        let b = if self.code < bound {
            self.range = bound;
            self.probs[i] = (p + ((2048 - p) >> 5)) as u16;
            0
        } else {
            self.range = self.range.wrapping_sub(bound);
            self.code = self.code.wrapping_sub(bound);
            self.probs[i] = (p - (p >> 5)) as u16;
            1
        };
        self.norm();
        b
    }

    fn direct(&mut self, n: u32) -> u32 {
        let mut v = 0;
        for _ in 0..n {
            self.range >>= 1;
            let b = (self.code >= self.range) as u32;
            if b != 0 {
                self.code = self.code.wrapping_sub(self.range);
            }
            v = (v << 1) | b;
            self.norm();
        }
        v
    }

    fn tree(&mut self, base: usize, bits: u32) -> u32 {
        let mut m = 1u32;
        for _ in 0..bits {
            m = (m << 1) | self.bit(base + m as usize);
        }
        m - (1 << bits)
    }

    fn rtree(&mut self, base: usize, bits: u32) -> u32 {
        let (mut m, mut v) = (1u32, 0u32);
        for i in 0..bits {
            let b = self.bit(base + m as usize);
            m = (m << 1) | b;
            v |= b << i;
        }
        v
    }

    fn len(&mut self, base: usize, ps: usize) -> u32 {
        if self.bit(base + L_CHOICE) == 0 {
            return self.tree(base + L_LOW + (ps << 3), 3);
        }
        if self.bit(base + L_CHOICE2) == 0 {
            return 8 + self.tree(base + L_MID + (ps << 3), 3);
        }
        16 + self.tree(base + L_HIGH, 8)
    }

    #[inline]
    fn get(&self, dist: u32) -> u8 {
        self.ring[(self.pos.wrapping_sub(dist).wrapping_sub(1) & self.mask) as usize]
    }
    #[inline]
    fn put(&mut self, b: u8) {
        self.ring[(self.pos & self.mask) as usize] = b;
        self.pos = self.pos.wrapping_add(1);
    }

    fn copy_match(&mut self, n: &mut u32) {
        while self.remain > 0 && *n > 0 {
            let b = self.get(self.rep[0]);
            self.put(b);
            self.remain -= 1;
            *n -= 1;
        }
    }

    /// Decode exactly `n` more bytes into the ring (a match in progress is finished first and may
    /// be split across calls).
    pub fn decode(&mut self, mut n: u32) -> Result<(), LzmaError> {
        let pbm = (1u32 << PB) - 1;
        self.copy_match(&mut n);
        while n > 0 && self.error.is_none() {
            let ps = (self.pos & pbm) as usize;
            let st = self.state;
            if self.bit(P_ISMATCH + (st << POSB_MAX) + ps) == 0 {
                // literal (lc = lp = 0: one coder, no context from the previous byte)
                let lp = P_LITERAL;
                let mut sym = 1u32;
                if st >= 7 {
                    let mut mb = self.get(self.rep[0]) as u32;
                    loop {
                        let mbit = (mb >> 7) & 1;
                        mb <<= 1;
                        let b = self.bit(lp + (((1 + mbit) << 8) + sym) as usize);
                        sym = (sym << 1) | b;
                        if mbit != b || sym >= 0x100 {
                            break;
                        }
                    }
                }
                while sym < 0x100 {
                    sym = (sym << 1) | self.bit(lp + sym as usize);
                }
                self.put(sym as u8);
                self.state = if st < 4 { 0 } else if st < 10 { st - 3 } else { st - 6 };
                n -= 1;
                continue;
            }
            let len;
            if self.bit(P_ISREP + st) != 0 {
                if self.pos == 0 {
                    self.error = Some(LzmaError::BadDistance);
                    break;
                }
                if self.bit(P_ISREPG0 + st) == 0 {
                    if self.bit(P_ISREP0L + (st << POSB_MAX) + ps) == 0 {
                        // short rep
                        self.state = if st < 7 { 9 } else { 11 };
                        let b = self.get(self.rep[0]);
                        self.put(b);
                        n -= 1;
                        continue;
                    }
                } else {
                    let dist;
                    if self.bit(P_ISREPG1 + st) == 0 {
                        dist = self.rep[1];
                    } else {
                        if self.bit(P_ISREPG2 + st) == 0 {
                            dist = self.rep[2];
                        } else {
                            dist = self.rep[3];
                            self.rep[3] = self.rep[2];
                        }
                        self.rep[2] = self.rep[1];
                    }
                    self.rep[1] = self.rep[0];
                    self.rep[0] = dist;
                }
                len = self.len(P_REPLEN, ps);
                self.state = if st < 7 { 8 } else { 11 };
            } else {
                self.rep[3] = self.rep[2];
                self.rep[2] = self.rep[1];
                self.rep[1] = self.rep[0];
                len = self.len(P_LEN, ps);
                self.state = if st < 7 { 7 } else { 10 };
                let ls = len.min(3) as usize;
                let slot = self.tree(P_POSSLOT + (ls << 6), 6);
                let dist = if slot < 4 {
                    slot
                } else {
                    let nd = (slot >> 1) - 1;
                    let mut d = (2 | (slot & 1)) << nd;
                    if slot < 14 {
                        d = d.wrapping_add(self.rtree(P_SPECPOS + d as usize - slot as usize - 1, nd));
                    } else {
                        d = d.wrapping_add(self.direct(nd - 4) << 4);
                        d = d.wrapping_add(self.rtree(P_ALIGN, 4));
                    }
                    d
                };
                self.rep[0] = dist;
            }
            let len = len + 2;
            if self.rep[0] >= self.pos || self.rep[0] > self.mask {
                self.error = Some(LzmaError::BadDistance);
                break;
            }
            self.remain = len;
            self.copy_match(&mut n);
        }
        match self.error {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }
}

/// A blob: `u32 usize`, `u32 params` (bits 0-2 pb, 3-5 lp, 6-9 lc, 12-16 dictionary log2: 0x0001x002
/// on every blob; the cartridge ignores it), then the raw stream.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BlobHeader {
    pub usize: u32,
    pub params: u32,
}

impl BlobHeader {
    pub fn parse(b: &[u8]) -> Option<BlobHeader> {
        (b.len() >= 8).then(|| BlobHeader {
            usize: u32::from_le_bytes([b[0], b[1], b[2], b[3]]),
            params: u32::from_le_bytes([b[4], b[5], b[6], b[7]]),
        })
    }
    /// The (lc, lp, pb) the params word names (the cartridge decodes as 0, 0, 2 regardless).
    pub fn named_params(&self) -> (u32, u32, u32) {
        ((self.params >> 6) & 15, (self.params >> 3) & 7, self.params & 7)
    }
}

/// Decode a whole blob of at most `cap` bytes in one call (codebooks, text resources).
pub fn decode_blob(blob: &[u8], cap: usize) -> Result<Vec<u8>, Error> {
    let h = BlobHeader::parse(blob).ok_or_else(|| Error::Undefined("LZMA blob shorter than its prefix".into()))?;
    if h.usize as usize > cap {
        return Err(Error::Undefined(format!("LZMA blob of {} bytes, buffer holds {cap}", h.usize)));
    }
    let mut d = Lzma::new(&blob[8..], BLOB_RING);
    d.decode(h.usize)?;
    let mut out = Vec::new();
    d.read(0, h.usize as usize, &mut out);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `xz --format=raw --lzma1=lc=0,lp=0,pb=2` of TEXT (with an end marker).
    const TEXT: &[u8] = b"abracadabra abracadabra abracadabra, the Majesco ring!";
    const RAW: [u8; 41] = [
        0x00, 0x30, 0x98, 0xe6, 0x3c, 0x0a, 0xd9, 0x9f, 0xae, 0x69, 0xe4, 0x44, 0xbd, 0x24, 0xb5, 0xc9, 0xe0, 0xa0,
        0x63, 0x43, 0x76, 0x10, 0xc6, 0x78, 0x1b, 0x13, 0x1f, 0xaa, 0xf3, 0x5f, 0x4c, 0x20, 0xbf, 0x80, 0xf8, 0xc3,
        0xff, 0xec, 0xe3, 0x80, 0x00,
    ];

    #[test]
    fn decodes_in_slices_and_flags_the_end_marker() {
        for step in [1u32, 3, 7, 54] {
            let mut d = Lzma::new(&RAW[..], RING);
            let mut done = 0;
            while done < TEXT.len() as u32 {
                let n = step.min(TEXT.len() as u32 - done);
                d.decode(n).unwrap();
                done += n;
            }
            let mut out = Vec::new();
            d.read(0, TEXT.len(), &mut out);
            assert_eq!(out, TEXT);
            // the cartridge streams have no end marker; an encoder's marker is a distance error
            assert_eq!(d.decode(1), Err(LzmaError::BadDistance));
        }
    }

    #[test]
    fn truncated_input_is_an_overrun() {
        let mut d = Lzma::new(&RAW[..20], RING);
        assert_eq!(d.decode(TEXT.len() as u32), Err(LzmaError::InputOverrun));
        assert_eq!(Lzma::new(&RAW[..3], RING).error(), Some(LzmaError::InputOverrun));
    }

    #[test]
    fn blob_prefix() {
        let mut blob = (TEXT.len() as u32).to_le_bytes().to_vec();
        blob.extend_from_slice(&0x0001_0002u32.to_le_bytes());
        blob.extend_from_slice(&RAW);
        assert_eq!(decode_blob(&blob, 0x1800).unwrap(), TEXT);
        assert!(decode_blob(&blob, 10).is_err());
        assert_eq!(BlobHeader::parse(&blob).unwrap().named_params(), (0, 0, 2));
    }
}
