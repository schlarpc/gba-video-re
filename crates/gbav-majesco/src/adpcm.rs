//! Majesco ADPCM audio (ADS and Hydrogen players): adaptive 2-bit ADPCM with a 1/2/3-bit sample
//! VLC and a 64-entry context table per 16458-sample block.
//!
//! Port of `majesco-shared/src/adpcm.c` (spec: `majesco-shared/adpcm.md`), plus the output stage
//! of the Sonic X Vol 2 prototype, which stores `s/64` without the [-128, 127] clamp.

/// Samples per coded block (0x404A).
pub const BLOCK: usize = 16458;
/// Playback rate (Timer0 reload 0xFC00).
pub const RATE: u32 = 16384;

/// How a block header codes its 64 context-table entries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CtabCoding {
    /// ADS players: 7-entry palette VLC with an "11" escape.
    Vlc,
    /// Hydrogen engine (all Hydrogen carts, Jimmy Neutron, the Noah proto): escape form only.
    Flat,
}

/// The sample store of the player's decode loop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Output {
    /// Clamp `s/64` to [-128, 127] (every shipped player).
    Clip,
    /// Store the low 8 bits of `s/64` (Sonic X Vol 2 prototype, IWRAM 0x03002b94).
    Wrap,
}

const VLC: [u8; 8] = [0x01, 0x01, 0x01, 0x01, 0x12, 0x12, 0x23, 0x33];
const PAL: [u8; 7] = [0xd8, 0x72, 0xe4, 0x4e, 0x78, 0xd2, 0xe1];
const PFX: [(u8, u8); 12] =
    [(0, 2), (0, 2), (0, 2), (0, 2), (1, 3), (1, 3), (2, 3), (2, 3), (3, 4), (4, 4), (5, 4), (6, 4)];
const ESC: [u8; 4] = [0x0c, 0x0d, 0x08, 0x06];

/// Decoder state. Bits are read MSB first from little-endian 32-bit words; reading past the end
/// of the buffer (rounded down to whole words) yields zero bits and sets [`Adpcm::overrun`], and
/// [`Adpcm::decode`] stops at the next sample, as the reference does.
#[derive(Clone)]
pub struct Adpcm<B: AsRef<[u8]>> {
    buf: B,
    nbits: usize,
    pos: usize,
    overrun: bool,
    coding: CtabCoding,
    output: Output,
    step: i32,
    prev: i32,
    h0: i32,
    h1: i32,
    f0: i32,
    f1: i32,
    f2: i32,
    s1: bool,
    s2: bool,
    ctx: u8,
    tab: [u8; 64],
    left: u32,
    block: u32,
}

impl<B: AsRef<[u8]>> Adpcm<B> {
    /// `payload` = the first audio word (after the resource header and block-size table). The
    /// cartridge's reader can run up to a word past the payload into the next ROM bytes, so pass
    /// those too (both reference decoders pass 64 more bytes).
    pub fn new(payload: B, coding: CtabCoding, output: Output) -> Adpcm<B> {
        let nbits = (payload.as_ref().len() & !3) * 8;
        Adpcm {
            buf: payload,
            nbits,
            pos: 0,
            overrun: false,
            coding,
            output,
            step: 0,
            prev: 0,
            h0: 0,
            h1: 0,
            f0: 0,
            f1: 0,
            f2: 0,
            s1: false,
            s2: false,
            ctx: 0,
            tab: [0; 64],
            left: 0,
            block: 0,
        }
    }

    /// Move to the start of a block at byte offset `off` of the payload (4 x the sum of the earlier
    /// block sizes). Exact when that block starts with a restart header (every block does).
    pub fn seek(&mut self, off: usize, block_index: u32) {
        self.pos = off * 8;
        self.left = 0;
        self.block = block_index;
    }

    /// Clear the overrun flag (the C reference keeps it set until re-initialised; a cold start
    /// through [`crate::audio::TrackDecoder::seek_block`] clears it).
    pub fn clear_overrun(&mut self) {
        self.overrun = false;
    }

    /// The reader ran past the end of the buffer.
    pub fn overrun(&self) -> bool {
        self.overrun
    }
    /// Block headers read so far (plus the index given to [`Adpcm::seek`]).
    pub fn blocks_read(&self) -> u32 {
        self.block
    }
    /// Samples left in the current block.
    pub fn left_in_block(&self) -> u32 {
        self.left
    }
    /// Bit position in the payload.
    pub fn bit_pos(&self) -> usize {
        self.pos
    }

    #[inline]
    fn word(&self, w: usize) -> u32 {
        let b = &self.buf.as_ref()[w * 4..w * 4 + 4];
        u32::from_le_bytes([b[0], b[1], b[2], b[3]])
    }

    #[inline]
    fn peek(&mut self, n: u32) -> u32 {
        debug_assert!(n <= 25);
        if n == 0 {
            return 0;
        }
        let p = self.pos;
        if p + n as usize <= self.nbits {
            let w = p >> 5;
            let hi = self.word(w) as u64;
            let lo = if (w + 1) * 32 < self.nbits { self.word(w + 1) as u64 } else { 0 };
            let x = (hi << 32 | lo) << (p & 31);
            return (x >> (64 - n)) as u32;
        }
        // slow path near the end: bit by bit, zero past the end
        let mut v = 0u32;
        for i in 0..n as usize {
            let q = p + i;
            let bit = if q < self.nbits {
                (self.word(q >> 5) >> (31 - (q & 31))) & 1
            } else {
                self.overrun = true;
                0
            };
            v = v << 1 | bit;
        }
        v
    }

    #[inline]
    fn get(&mut self, n: u32) -> u32 {
        let v = self.peek(n);
        self.pos += n as usize;
        v
    }

    fn block_header(&mut self) {
        if self.peek(1) != 0 {
            // continuation: keep step and predictor
            self.get(1);
        } else {
            let skip = ((32 - (self.pos & 31)) & 31) as u32;
            self.get(skip);
            self.get(1);
            let v = self.get(24);
            self.step = ((v << 8) as i32) >> 8;
            self.f2 = 0;
            self.prev = 1;
            self.f0 = 0;
            self.f1 = 0;
            self.h0 = 1;
            self.h1 = 1;
            self.s1 = false;
            self.s2 = false;
        }
        for i in 0..64 {
            if self.coding == CtabCoding::Vlc {
                let t = self.peek(4) as usize;
                if t < 12 {
                    self.get(PFX[t].1 as u32);
                    self.tab[i] = PAL[PFX[t].0 as usize];
                    continue;
                }
                self.get(2);
            }
            let x = self.get(2);
            let y = self.get(2);
            let sel = self.get(1);
            let m = (1u32 << x) | (1u32 << y);
            let lo = ESC[(m & 3) as usize];
            let hi = ESC[(m >> 2) as usize];
            let (p, q) = if sel != 0 { (hi >> 2, lo & 3) } else { (lo & 3, hi >> 2) };
            self.tab[i] = (x | y << 2) as u8 | p << 4 | q << 6;
        }
        self.left = BLOCK as u32;
        self.ctx = 0;
        self.block += 1;
    }

    #[inline]
    fn sample(&mut self) -> i8 {
        let e = VLC[self.peek(3) as usize];
        self.get((e & 15) as u32);
        let sym = (self.tab[self.ctx as usize] >> (e >> 3)) & 3;
        self.ctx = ((self.ctx << 2) & 0x3f) | sym;
        let st = self.step;
        let r = (st >> 8).wrapping_add(0x74).wrapping_add(if sym & 1 != 0 { 0xf9 } else { 0 });
        let sh = 14 - ((r >> 7) & 15);
        let base = (((r & 0x7f) | 0x80) << 7) as i32;
        // the shift amount is -1 for (r>>7)&15 == 15: ARM ASR by register 255 (and x86 SAR by 31)
        // gives 0 for this positive value
        let mut mag = if sh < 0 { 0 } else { base >> sh };
        if sym & 2 != 0 {
            mag = mag.wrapping_neg();
        }
        let l = st >> 6;
        let fp = if sym & 1 != 0 {
            if l > 0x12e0 {
                0x1400
            } else {
                l.wrapping_add(0x36e0i32.wrapping_sub(l) >> 5)
            }
        } else if l < 0x248 {
            0x220
        } else {
            l.wrapping_add(l.wrapping_add(0x2c0).wrapping_neg() >> 5)
        };
        self.step = st.wrapping_add(fp).wrapping_add(st.wrapping_neg() >> 6);
        let pf = self.prev.wrapping_mul(self.f2);
        let acc = (self.h1.wrapping_mul(self.f1 >> 2) >> 11)
            .wrapping_add(self.h0.wrapping_mul(self.f0 >> 2) >> 11)
            .wrapping_add(pf >> 11);
        let h = mag.wrapping_add(acc >> 1);
        self.h1 = self.h0;
        self.h0 = if h != 0 { h } else { 1 };
        let sb = mag.wrapping_add(pf >> 12);
        // truncating division by 64
        let mut o = h.wrapping_add(((h >> 31) as u32 >> 26) as i32) >> 6;
        if self.output == Output::Clip {
            o = o.clamp(-128, 127);
        }
        let f0 = self.f0.wrapping_sub(self.f0 >> 8);
        let mut f1 = self.f1.wrapping_sub(self.f1 >> 7);
        let mut r5 = f0;
        let mut r4 = f0;
        let sg = sb < 0;
        if sb != 0 {
            if sg == self.s1 {
                r5 = r4.wrapping_add(0xc0);
                r4 = r4.wrapping_neg();
            } else {
                r5 = r4.wrapping_sub(0xc0);
            }
            if (r4.wrapping_add(0x1fff) as u32) <= 0x3fff {
                f1 = f1.wrapping_add(r4 >> 5);
            } else if r4 < 0 {
                f1 = f1.wrapping_sub(0x100);
            } else {
                f1 = f1.wrapping_add(0xff);
            }
            f1 = f1.wrapping_add(if sg != self.s2 { -0x80 } else { 0x80 });
        }
        let lim = 0x3c00i32.wrapping_sub(f1);
        if r5 > lim {
            r5 = lim;
        }
        self.f0 = r5;
        self.f1 = f1;
        self.f2 = self.f2.wrapping_sub(self.f2 >> 8).wrapping_add(if (mag ^ self.prev) >= 0 { 0x20 } else { -0x20 });
        self.s2 = self.s1;
        self.s1 = sg;
        self.prev = mag;
        self.left -= 1;
        o as i8
    }

    /// Decode up to `out.len()` samples, reading block headers as needed. Returns the number
    /// decoded: fewer than asked only when the reader ran past the buffer (see [`Adpcm::overrun`]).
    pub fn decode(&mut self, out: &mut [i8]) -> usize {
        let mut k = 0;
        while k < out.len() {
            if self.left == 0 {
                self.block_header();
            }
            if self.overrun {
                break;
            }
            out[k] = self.sample();
            k += 1;
        }
        k
    }
}
