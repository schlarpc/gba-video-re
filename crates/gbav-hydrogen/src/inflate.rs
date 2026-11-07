//! `hyCompressionManager::Inflate`, the DEFLATE variant of Majesco's Hydrogen library, including
//! its behaviour when the output is requested in pieces (one video frame per request).
//!
//! Port of `hydrogen/src/hyinflate.c` (spec: `hydrogen/docs/format-hydrogen.md` section 5).
//! Differences from RFC 1951:
//! - bits are read MSB first from 16-bit little-endian words; every field and every Huffman code
//!   is read most-significant bit first;
//! - a block header is a bare 2-bit type: 0 stored, 1 fixed, 2 dynamic, **3 end of stream**
//!   (no BFINAL bit);
//! - a stored block has a 16-bit LEN, no NLEN, and a byte layout set by the halfword reader and
//!   its eager refill (see [`Inflate::stored`]);
//! - the blob starts with a u32 little-endian uncompressed size.
//!
//! The request bookkeeping (`rem` = obj+0x38, `left` = obj+0x48, `state` = obj+0x3c, `sleft` =
//! obj+0x4c) is a literal transcription of the cartridge, quirks included. The quirks only show
//! with stored blocks, which no retail stream contains; [`Inflate::quirks`] counts them:
//! 1. a stored block that starts inside a request after another block ended inherits the
//!    remaining request (`rem` is not reset), so it produces up to twice what was asked;
//! 2. a 1-byte stored block whose byte comes from the bit buffer and exactly ends a request
//!    leaves the block counter at 0 in state 5: the next request reads a new stored header;
//! 3. a stored block with LEN 0 and 8 or more bits buffered underflows its counter: flagged
//!    [`InflateError::Undefined`] (the cartridge emits a byte and copies ~64 KiB);
//! 4. a stored copy entered with `rem` <= -2 has a negative size: the cartridge's memcpy runs off
//!    the end of memory; flagged [`InflateError::Undefined`].

use gbav_common::Error;

/// Error classes, numbered like `hyinf_stream.err` in the C reference.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InflateError {
    /// err 1: input the cartridge does not define (no code matches, a distance before the start
    /// of the output, output past the buffer, a code-length run past HLIT+HDIST, the stored-block
    /// LEN 0 / negative-copy cases, a missing size prefix)
    Undefined,
    /// err 2: a stored copy that would read past the end of the blob (the cartridge copies
    /// whatever ROM follows)
    PastBlob,
    /// err 3: an over-subscribed Huffman code-length set (the cartridge builds tables anyway, with
    /// implementation-defined results)
    Oversubscribed,
}

impl InflateError {
    /// The C reference's `err` value.
    pub fn code(self) -> i32 {
        match self {
            InflateError::Undefined => 1,
            InflateError::PastBlob => 2,
            InflateError::Oversubscribed => 3,
        }
    }
}

impl From<InflateError> for Error {
    fn from(e: InflateError) -> Error {
        Error::Undefined(match e {
            InflateError::Undefined => "inflate: input the cartridge does not define".into(),
            InflateError::PastBlob => "inflate: stored block copies past the end of the blob".into(),
            InflateError::Oversubscribed => "inflate: over-subscribed Huffman code set".into(),
        })
    }
}

/// Quirk counters (see the module docs); all zero on every retail stream.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Quirks {
    /// quirk 1: stored block entered with a non-zero carried request
    pub stored_inherits_request: u32,
    /// quirk 2: stored block left at counter 0 in state 5
    pub stored_reheader: u32,
}

const LBASE: [u16; 29] =
    [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
const LEXT: [u8; 29] = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
const DBASE: [u16; 30] = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
    8193, 12289, 16385, 24577,
];
const DEXT: [u8; 30] = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
const CLORD: [usize; 19] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

const FAST: u32 = 10;

/// Canonical Huffman decoder, MSB first, with a FAST-bit lookup table in front of the canonical
/// walk. Equivalent to the C `huff_dec` for complete and incomplete sets alike.
#[derive(Clone)]
struct Huff {
    count: [u16; 16],
    sym: [u16; 320],
    /// (symbol << 4 | length) for codes of at most FAST bits, 0 = not in the table
    fast: Vec<u16>,
}

impl Huff {
    fn empty() -> Huff {
        Huff { count: [0; 16], sym: [0; 320], fast: vec![0; 1 << FAST] }
    }

    /// Err for an over-subscribed set.
    fn build(&mut self, len: &[u8]) -> Result<(), ()> {
        self.count = [0; 16];
        for &l in len {
            self.count[l as usize] += 1;
        }
        self.count[0] = 0;
        let mut left: i32 = 1;
        for l in 1..16 {
            left <<= 1;
            left -= self.count[l] as i32;
            if left < 0 {
                return Err(());
            }
        }
        let mut offs = [0u16; 16];
        for l in 1..15 {
            offs[l + 1] = offs[l] + self.count[l];
        }
        for (i, &l) in len.iter().enumerate() {
            if l != 0 {
                self.sym[offs[l as usize] as usize] = i as u16;
                offs[l as usize] += 1;
            }
        }
        // lookup table: canonical code values per length
        self.fast.iter_mut().for_each(|e| *e = 0);
        let (mut code, mut index) = (0u32, 0usize);
        for l in 1..=FAST as usize {
            for k in 0..self.count[l] as usize {
                let c = code + k as u32;
                let s = self.sym[index + k];
                let shift = FAST - l as u32;
                for j in 0..(1u32 << shift) {
                    self.fast[((c << shift) | j) as usize] = s << 4 | l as u16;
                }
            }
            index += self.count[l] as usize;
            code = (code + self.count[l] as u32) << 1;
        }
        Ok(())
    }
}

struct Fixed(Huff);

fn fixed_lit() -> &'static Huff {
    use std::sync::OnceLock;
    static F: OnceLock<Fixed> = OnceLock::new();
    &F.get_or_init(|| {
        let mut l = [0u8; 288];
        l[..144].fill(8);
        l[144..256].fill(9);
        l[256..280].fill(7);
        l[280..].fill(8);
        let mut h = Huff::empty();
        h.build(&l).unwrap();
        Fixed(h)
    })
    .0
}

/// One Inflate object, as `Inflate::Initialize` leaves it: a blob (u32 size + bit stream) and an
/// output buffer of `cap` bytes, decoded by [`Inflate::request`].
#[derive(Clone)]
pub struct Inflate<B: AsRef<[u8]>> {
    br: Bits<B>,
    out: Vec<u8>,
    o: usize,
    size: u32,
    rem: i32,
    left: i32,
    sleft: i32,
    state: u8,
    lit: Huff,
    dist: Huff,
    blocks: [u32; 4],
    err: Option<InflateError>,
    quirks: Quirks,
}

impl<B: AsRef<[u8]>> Inflate<B> {
    /// `Inflate::Initialize` on a blob, with an output buffer of `cap` bytes.
    pub fn new(blob: B, cap: usize) -> Inflate<B> {
        let b = blob.as_ref();
        let (size, short) = if b.len() >= 4 { (u32::from_le_bytes([b[0], b[1], b[2], b[3]]), false) } else { (0, true) };
        Inflate {
            br: Bits { blob, p: 4, buf: 0, n: 0 },
            out: vec![0; cap],
            o: 0,
            size,
            rem: 0,
            left: size as i32,
            sleft: 0,
            state: 0,
            lit: Huff::empty(),
            dist: Huff::empty(),
            blocks: [0; 4],
            err: if short { Some(InflateError::Undefined) } else { None },
            quirks: Quirks::default(),
        }
    }

    /// The u32 size prefix.
    pub fn size(&self) -> u32 {
        self.size
    }
    /// Bytes produced so far (may exceed the buffer after an error).
    pub fn produced(&self) -> usize {
        self.o
    }
    /// The output buffer (its first `min(produced, cap)` bytes are valid).
    pub fn output(&self) -> &[u8] {
        &self.out
    }
    pub fn into_output(self) -> Vec<u8> {
        self.out
    }
    /// Block headers seen per type: 0 stored, 1 fixed, 2 dynamic, 3 end.
    pub fn blocks(&self) -> [u32; 4] {
        self.blocks
    }
    /// Compressed bytes consumed so far, including the size prefix (counts eagerly loaded
    /// halfwords, like the C reference's `in_bytes`).
    pub fn consumed(&self) -> usize {
        self.br.p
    }
    pub fn error(&self) -> Option<InflateError> {
        self.err
    }
    pub fn quirks(&self) -> Quirks {
        self.quirks
    }

    fn fail(&mut self, e: InflateError) {
        if self.err.is_none() {
            self.err = Some(e);
        }
    }

    #[inline]
    fn getb(&mut self, k: u32) -> u32 {
        self.br.getb(k)
    }

    #[inline]
    fn huff_dec(&mut self, which: Table) -> u32 {
        let h: &Huff = match which {
            Table::Lit => &self.lit,
            Table::Dist => &self.dist,
            Table::Fixed => fixed_lit(),
        };
        match self.br.huff_dec(h) {
            Some(v) => v,
            None => {
                self.fail(InflateError::Undefined);
                0
            }
        }
    }

    #[inline]
    fn put(&mut self, v: u8) {
        if self.o < self.out.len() {
            self.out[self.o] = v;
        } else {
            self.fail(InflateError::Undefined);
        }
        self.o += 1;
    }

    fn copy(&mut self, len: u32, d: u32) {
        let d = d as usize;
        if d > self.o {
            self.fail(InflateError::Undefined);
            return;
        }
        let cap = self.out.len();
        if self.o + len as usize <= cap {
            // fast path: whole match inside the buffer
            let s = self.o - d;
            if d >= len as usize {
                self.out.copy_within(s..s + len as usize, self.o);
            } else {
                for i in 0..len as usize {
                    self.out[self.o + i] = self.out[s + i];
                }
            }
            self.o += len as usize;
            return;
        }
        for _ in 0..len {
            let src = self.o - d;
            let v = if src < cap { self.out[src] } else { 0 };
            self.put(v);
        }
    }

    fn dyn_header(&mut self) -> Result<(), ()> {
        if self.br.n < 14 {
            // the 14 header bits are taken from one top-up
            self.br.refill(self.br.n + 1);
        }
        let hlit = self.getb(5) as usize + 257;
        let hdist = self.getb(5) as usize + 1;
        let hclen = self.getb(4) as usize + 4;
        let mut cl = [0u8; 19];
        for &k in CLORD.iter().take(hclen) {
            cl[k] = self.getb(3) as u8;
        }
        let mut clh = Huff::empty();
        if clh.build(&cl).is_err() {
            self.fail(InflateError::Oversubscribed);
            return Err(());
        }
        let mut lens = [0u8; 320];
        let mut n = 0usize;
        // the cartridge loops until the count reaches exactly zero; a run that jumps past the end
        // never terminates there
        while n < hlit + hdist {
            let Some(c) = self.br.huff_dec(&clh) else {
                self.fail(InflateError::Undefined);
                return Err(());
            };
            if c < 16 {
                lens[n] = c as u8;
                n += 1;
                continue;
            }
            let (rep, v) = match c {
                16 => (self.getb(2) + 3, if n > 0 { lens[n - 1] } else { 0 }),
                17 => (self.getb(3) + 3, 0),
                _ => (self.getb(7) + 11, 0),
            };
            if n + rep as usize > hlit + hdist {
                return Err(());
            }
            for _ in 0..rep {
                lens[n] = v;
                n += 1;
            }
        }
        if self.lit.build(&lens[..hlit]).is_err() || self.dist.build(&lens[hlit..hlit + hdist]).is_err() {
            self.fail(InflateError::Oversubscribed);
            return Err(());
        }
        Ok(())
    }

    /// IWRAM 0x03002870 (dynamic) / 0x03000144 (fixed): decode symbols against `rem`.
    fn core(&mut self, fixed: bool) {
        let lt = if fixed { Table::Fixed } else { Table::Lit };
        if fixed && self.rem < 0 {
            return;
        }
        loop {
            let c = self.huff_dec(lt);
            if self.err.is_some() {
                return;
            }
            if c < 256 {
                self.put(c as u8);
                self.rem = self.rem.wrapping_sub(1);
                if !fixed && self.rem == 0 {
                    return; // dynamic: stop exactly at the request
                }
            } else if c == 256 {
                self.state = 0;
                return;
            } else {
                let c = (c - 257) as usize;
                if c >= 29 {
                    self.fail(InflateError::Undefined);
                    return;
                }
                let len = LBASE[c] as u32 + self.getb(LEXT[c] as u32);
                let dc = if fixed { self.getb(5) } else { self.huff_dec(Table::Dist) } as usize;
                if dc >= 30 {
                    self.fail(InflateError::Undefined);
                    return;
                }
                let d = DBASE[dc] as u32 + self.getb(DEXT[dc] as u32);
                self.rem = self.rem.wrapping_sub(len as i32);
                self.copy(len, d);
                if !fixed && self.rem <= 0 {
                    return; // dynamic: while (0 < rem)
                }
            }
            if fixed && self.rem < 0 {
                return; // fixed: while (rem >= 0)
            }
        }
    }

    /// IWRAM 0x03000490, stored block. If 8 or more bits are buffered after LEN, the top 8 bits
    /// are the first data byte; the rest of the buffer is dropped, whole bytes are copied in
    /// memory order, and an odd last byte of a piece is an 8-bit read (the high byte of the next
    /// halfword).
    fn stored(&mut self) {
        if self.sleft == 0 {
            self.sleft = self.getb(16) as i32;
            if self.br.n >= 8 {
                if self.sleft == 0 {
                    // quirk 3
                    self.fail(InflateError::Undefined);
                    return;
                }
                let b = (self.br.buf >> 24) as u8;
                self.put(b);
                self.br.buf <<= 8;
                self.br.n -= 8;
                self.sleft = self.sleft.wrapping_sub(1);
                self.rem = self.rem.wrapping_sub(1);
            }
            self.br.buf = 0;
            self.br.n = 0;
            if self.rem <= 0 {
                if self.sleft == 0 {
                    self.quirks.stored_reheader += 1; // quirk 2
                }
                return;
            }
        }
        let c = if self.rem >= self.sleft {
            self.sleft
        } else {
            (self.rem & !1).wrapping_add(if self.rem & 1 != 0 { 2 } else { 0 })
        };
        if c < 0 {
            // quirk 4: memcpy with a negative size
            self.fail(InflateError::Undefined);
            return;
        }
        let ev = (c & !1) as usize;
        if self.br.p + ev > self.br.blob.as_ref().len() {
            self.fail(InflateError::PastBlob);
            return;
        }
        for i in 0..ev {
            let v = self.br.blob.as_ref()[self.br.p + i];
            self.put(v);
        }
        self.br.p += ev;
        if c & 1 != 0 {
            let v = self.getb(8) as u8;
            self.put(v);
        }
        self.rem = self.rem.wrapping_sub(c);
        self.sleft = self.sleft.wrapping_sub(c);
        if self.sleft == 0 {
            self.state = 0;
        }
    }

    /// IWRAM 0x030005c8 `Inflate::UncompressBlock(n)`. Returns the output offset where this
    /// request's bytes start (the pointer the player hands to its blitter; it can be before the
    /// bytes produced by this call after a match overshoot, or negative after the cartridge's own
    /// bookkeeping wraps).
    pub fn request(&mut self, n: i64) -> Result<i64, InflateError> {
        if let Some(e) = self.err {
            return Err(e);
        }
        let mut k = self.left.wrapping_sub(self.rem);
        if (n as i32) < k {
            k = n as i32;
        }
        let mut go = k > 0;
        let ret = self.o as i64 + self.rem as i64;
        while go && self.err.is_none() {
            match self.state {
                0 => {
                    let t = self.getb(2);
                    self.blocks[t as usize] += 1;
                    self.state = [5, 2, 1, 7][t as usize];
                    if t == 0 && self.rem != 0 {
                        self.quirks.stored_inherits_request += 1; // quirk 1 (counted at entry)
                    }
                }
                1 | 3 => {
                    if self.state == 1 {
                        if self.dyn_header().is_err() {
                            self.fail(InflateError::Undefined);
                            break;
                        }
                        self.rem = 0;
                        self.state = 3;
                    }
                    k = self.rem.wrapping_add(k);
                    self.rem = k;
                    self.left = self.left.wrapping_sub(k);
                    self.core(false);
                    k = self.rem;
                    self.left = self.left.wrapping_add(k);
                    if self.state == 3 {
                        go = k > 0;
                    }
                }
                2 | 4 => {
                    if self.state == 2 {
                        self.rem = 0;
                        self.state = 4;
                    }
                    k = self.rem.wrapping_add(k);
                    self.rem = k;
                    self.left = self.left.wrapping_sub(k);
                    self.core(true);
                    k = self.rem;
                    self.left = self.left.wrapping_add(k);
                    if self.state == 4 {
                        go = k > 0;
                    }
                }
                5 => {
                    k = self.rem.wrapping_add(k);
                    self.rem = k;
                    self.left = self.left.wrapping_sub(k);
                    self.stored();
                    k = self.rem;
                    self.left = self.left.wrapping_add(k);
                    if self.state == 5 {
                        go = k > 0;
                    }
                }
                _ => {
                    // 7: end of stream (the cartridge asserts out == end and left == 0 here)
                    go = false;
                }
            }
        }
        match self.err {
            Some(e) => Err(e),
            None => Ok(ret),
        }
    }

    /// After the whole blob has been produced: does it end the way the player expects (end of
    /// the last Huffman block, then block type 3)?
    pub fn check_end(&mut self) -> bool {
        if self.err.is_some() || self.o != self.size as usize {
            return false;
        }
        if self.state == 3 || self.state == 4 {
            let c = self.huff_dec(if self.state == 3 { Table::Lit } else { Table::Fixed });
            if self.err.is_some() || c != 256 {
                return false;
            }
            self.state = 0;
        }
        if self.state != 0 {
            return self.state == 7;
        }
        let t = self.getb(2);
        self.blocks[t as usize] += 1;
        t == 3
    }
}

#[derive(Clone, Copy)]
enum Table {
    Lit,
    Dist,
    Fixed,
}

/// The halfword bit reader.
#[derive(Clone)]
struct Bits<B: AsRef<[u8]>> {
    blob: B,
    /// halfword read pointer (byte offset into `blob`; may run past the end: reads give zero)
    p: usize,
    buf: u32,
    n: u32,
}

impl<B: AsRef<[u8]>> Bits<B> {
    #[inline]
    fn refill(&mut self, need: u32) {
        while self.n < need {
            // the cartridge tops up eagerly and may load a halfword past the end of the blob;
            // those bits are never consumed by a valid stream
            let b = self.blob.as_ref();
            let w = if self.p + 2 <= b.len() {
                b[self.p] as u32 | (b[self.p + 1] as u32) << 8
            } else {
                0
            };
            self.p += 2;
            self.buf |= w << (16 - self.n);
            self.n += 16;
        }
    }

    #[inline]
    fn getb(&mut self, k: u32) -> u32 {
        if k == 0 {
            return 0;
        }
        self.refill(k);
        let v = self.buf >> (32 - k);
        self.buf <<= k;
        self.n -= k;
        v
    }

    /// The cartridge tops the buffer up with ONE halfword if fewer than 15 bits are buffered
    /// (all three table decoders do this), then looks the code up in the buffered bits. The eager
    /// refill does not change the symbols, but it decides how many bits are buffered when a
    /// stored block starts. None: no code matches (an incomplete set).
    #[inline]
    fn huff_dec(&mut self, h: &Huff) -> Option<u32> {
        if self.n < 15 {
            self.refill(self.n + 1);
        }
        let e = h.fast[(self.buf >> (32 - FAST)) as usize];
        if e != 0 {
            let l = (e & 15) as u32;
            self.buf <<= l;
            self.n -= l;
            return Some((e >> 4) as u32);
        }
        let (mut code, mut first, mut index) = (0i32, 0i32, 0i32);
        for l in 1..16u32 {
            code |= ((self.buf >> (32 - l)) & 1) as i32;
            let c = h.count[l as usize] as i32;
            if code - c < first {
                let s = h.sym[(index + (code - first)) as usize] as u32;
                self.buf <<= l;
                self.n -= l;
                return Some(s);
            }
            index += c;
            first += c;
            first <<= 1;
            code <<= 1;
        }
        None
    }
}

/// Decode a whole blob in one request (the player decompresses a codebook this way). Fails if the
/// stream errs or produces fewer than `size` bytes.
pub fn decode_blob(blob: &[u8]) -> Result<Vec<u8>, InflateError> {
    decode_blob_counted(blob).map(|(v, _)| v)
}

/// [`decode_blob`] plus the block-type counts.
pub fn decode_blob_counted(blob: &[u8]) -> Result<(Vec<u8>, [u32; 4]), InflateError> {
    let mut s = Inflate::new(blob, 0);
    if let Some(e) = s.err {
        return Err(e);
    }
    let size = s.size;
    s.out = vec![0; size as usize];
    s.request(size as i64)?;
    if s.o < size as usize {
        return Err(InflateError::Undefined);
    }
    let b = s.blocks;
    Ok((s.out, b))
}
