//! FVMV video: a bit-exact model of the player's IWRAM decoder (Vol 1 entry 0x03005fe8).
//!
//! The decoder works on GBA addresses inside a model of the whole 256 KiB EWRAM, because
//! blocks near the picture edge read and write outside the picture: motion vectors wrap
//! across the left/right edge into the neighbouring row, and FILL / 8x8-pattern blending
//! touches the row above and the column to the left. See `docs/format-fvmv.md` section 3.

/// Start of EWRAM on the GBA.
pub const EWRAM_BASE: u32 = 0x0200_0000;
/// Size of the modelled EWRAM.
pub const EWRAM_SIZE: usize = 0x40000;
/// GBA address of frame buffer 0 (the heap block starts after an 8-byte header).
pub const BUF0: u32 = 0x0200_0008;
/// Distance between the two frame buffers, and the size of one (240 x 160 x u16).
pub const BUF_STRIDE: u32 = 0x12c00;
pub const WIDTH: usize = 240;
pub const HEIGHT: usize = 160;
/// Bytes per picture row.
const STRIDE: u32 = 480;
/// Bytes in one frame buffer.
pub const FRAME_BYTES: usize = WIDTH * HEIGHT * 2;

/// Averaging mask: clears the LSB of G and B and bit 15 of both pixels of a pair.
const M32: u32 = 0x7bdf_7bdf;
/// The LSB of G and B of both pixels of a pair.
const L32: u32 = 0x0420_0420;

/// Block modes, as indices of [`Stats::mode_count`]'s inner array.
pub mod mode {
    pub const SKIP: usize = 0;
    pub const MV: usize = 1;
    pub const SPLIT_H: usize = 2;
    pub const SPLIT_V: usize = 3;
    pub const MV_OFFSET: usize = 4;
    pub const FILL: usize = 5;
    pub const PATTERN: usize = 6;
    pub const RAW: usize = 7;
    pub const NAMES: [&str; 8] = [
        "skip", "mv", "splitH", "splitV", "mv+off", "fill", "pattern", "raw",
    ];
}

/// Decoder statistics. The fields match `fvmv_dec` in the reference C decoder one for one,
/// so the differential tests compare them too.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Stats {
    /// `[size index][mode]`; size index = 4 * log2(w) + log2(h).
    pub mode_count: [[u64; 8]; 16],
    /// Accesses outside EWRAM (open bus on hardware; reads return 0, writes are lost).
    pub oob_reads: u64,
    pub oob_writes: u64,
    /// Accesses inside EWRAM but outside the current (and, for reads, reference) picture.
    pub offpic_reads: u64,
    pub offpic_writes: u64,
    /// Reads past the available payload bytes.
    pub err: u64,
    /// Frames whose readers ran past their substream.
    pub overrun: u64,
    /// Motion vectors whose source crosses the left/right edge.
    pub mv_xwrap: u64,
    /// Motion vectors whose source leaves the reference buffer.
    pub mv_yout: u64,
    /// `[below * 2 + (ref above out in memory)]`.
    pub mv_yout_kind: [u64; 4],
    /// FILL / 8x8 pattern blocks at column 0 / row 0.
    pub edge_x0: u64,
    pub edge_y0: u64,
    /// Frames whose substreams were not consumed exactly.
    pub inexact: u64,
}

/// Size index used by [`Stats::mode_count`].
pub fn size_index(w: u32, h: u32) -> usize {
    (w.trailing_zeros() * 4 + h.trailing_zeros()) as usize
}

/// The decoder: EWRAM model, frame counter (selects the output buffer) and statistics.
#[derive(Clone)]
pub struct Decoder {
    ewram: Box<[u8]>,
    /// Frames decoded so far. Frame `n` goes to buffer `n & 1`; set this when starting at a
    /// seek point to keep the player's buffer parity.
    pub frame_no: u32,
    pub stats: Stats,
}

impl Default for Decoder {
    fn default() -> Self {
        Self::new()
    }
}

impl Decoder {
    /// A decoder with zeroed EWRAM, as after power-on.
    pub fn new() -> Self {
        Decoder {
            ewram: vec![0u8; EWRAM_SIZE].into_boxed_slice(),
            frame_no: 0,
            stats: Stats::default(),
        }
    }

    /// Clear EWRAM and statistics, and set the frame counter.
    pub fn reset(&mut self, frame_no: u32) {
        self.ewram.fill(0);
        self.frame_no = frame_no;
        self.stats = Stats::default();
    }

    /// The whole 256 KiB EWRAM image (index 0 = 0x02000000).
    pub fn ewram(&self) -> &[u8] {
        &self.ewram
    }

    pub fn ewram_mut(&mut self) -> &mut [u8] {
        &mut self.ewram
    }

    /// GBA address of frame buffer `sel` (0 or 1) in the player's layout.
    pub fn buffer_addr(sel: u32) -> u32 {
        BUF0 + (sel & 1) * BUF_STRIDE
    }

    /// The 240x160 u16 LE buffer of the most recently decoded frame (bit 15 included).
    pub fn last_frame(&self) -> &[u8] {
        let b = (Self::buffer_addr(self.frame_no.wrapping_sub(1)) - EWRAM_BASE) as usize;
        &self.ewram[b..b + FRAME_BYTES]
    }

    /// Decode one video payload into buffer `frame_no & 1`, with the other buffer as the
    /// reference, as the player does. Returns `false` if a reader ran past the payload.
    pub fn decode_frame(&mut self, payload: &[u8]) -> bool {
        let sel = self.frame_no & 1;
        let vlen = payload.len() as u32;
        self.decode_frame_at(
            payload,
            vlen,
            Self::buffer_addr(sel),
            Self::buffer_addr(sel ^ 1),
        )
    }

    /// Decode one video payload with explicit buffer addresses. `data` holds the payload
    /// (`vlen` bytes) followed by whatever bytes a runaway reader may read (`avail` =
    /// `data.len()`). Returns `false` if a reader ran past `data` (the C decoder's -1).
    pub fn decode_frame_at(&mut self, data: &[u8], vlen: u32, out: u32, refb: u32) -> bool {
        let avail = data.len();
        if vlen < 8 || avail < vlen as usize {
            self.stats.err += 1;
            return false;
        }
        let nbits = rd32(data, 0) as usize;
        let ncol = rd32(data, 4) as usize;
        let (mut col, mut mv) = (8 + nbits, 8 + nbits + ncol);
        if nbits + ncol + 8 > avail {
            col = avail;
            mv = avail;
        }
        let (nb_end, nc_end, nm_end) = (8 + nbits, mv, vlen as usize);
        let e0 = self.stats.err;
        let mut c = Ctx {
            ew: &mut self.ewram,
            st: &mut self.stats,
            data,
            bits: 8,
            col,
            mv,
            r8: 0x8000_0000,
            out,
            refb,
        };
        c.bit(); // the driver primes the bit register: the first bit is unused
        for by in 0..(HEIGHT as u32 / 8) {
            for bx in 0..(WIDTH as u32 / 8) {
                c.block(8, 8, by * 8 * STRIDE + bx * 16);
            }
        }
        let (b, cl, m) = (c.bits, c.col, c.mv);
        if b > nb_end || cl > nc_end || m > nm_end {
            self.stats.overrun += 1;
        }
        // bit and colour streams end exactly; the MV byte stream is padded to a word
        if b != nb_end || cl != nc_end || m > nm_end || nm_end - m > 3 || vlen & 3 != 0 {
            self.stats.inexact += 1;
        }
        self.frame_no = self.frame_no.wrapping_add(1);
        self.stats.err == e0
    }
}

#[inline]
fn rd32(p: &[u8], i: usize) -> u32 {
    u32::from_le_bytes([p[i], p[i + 1], p[i + 2], p[i + 3]])
}

struct Ctx<'a> {
    ew: &'a mut [u8],
    st: &'a mut Stats,
    /// payload plus any readable bytes after it; the reader positions below index into it
    /// and may run past its end (reads there return 0 and count as errors)
    data: &'a [u8],
    bits: usize,
    col: usize,
    mv: usize,
    /// bit register with the sentinel bit, as in the ARM code
    r8: u32,
    out: u32,
    refb: u32,
}

#[inline]
fn in_pic(a: u32, base: u32) -> bool {
    a >= base && a < base.wrapping_add(BUF_STRIDE)
}

#[inline]
fn ew_off(a: u32) -> Option<usize> {
    if a < EWRAM_BASE || a as u64 + 2 > EWRAM_BASE as u64 + EWRAM_SIZE as u64 {
        None
    } else {
        Some((a - EWRAM_BASE) as usize)
    }
}

impl Ctx<'_> {
    // ---- memory model (GBA addresses) ----
    #[inline]
    fn m16(&mut self, a: u32) -> u32 {
        let Some(o) = ew_off(a) else {
            self.st.oob_reads += 1;
            return 0;
        };
        if !in_pic(a, self.out) && !in_pic(a, self.refb) {
            self.st.offpic_reads += 1;
        }
        u16::from_le_bytes([self.ew[o], self.ew[o + 1]]) as u32
    }

    #[inline]
    fn w16(&mut self, a: u32, v: u32) {
        let Some(o) = ew_off(a) else {
            self.st.oob_writes += 1;
            return;
        };
        if !in_pic(a, self.out) {
            self.st.offpic_writes += 1;
        }
        self.ew[o..o + 2].copy_from_slice(&(v as u16).to_le_bytes());
    }

    /// 32-bit access of a pixel pair. The ARM code issues word accesses only at aligned
    /// addresses; an unaligned source is read as two halfwords, which gives the same value.
    #[inline]
    fn m32(&mut self, a: u32) -> u32 {
        self.m16(a) | self.m16(a.wrapping_add(2)) << 16
    }

    #[inline]
    fn w32(&mut self, a: u32, v: u32) {
        self.w16(a, v & 0xffff);
        self.w16(a.wrapping_add(2), v >> 16);
    }

    // ---- streams ----
    #[inline]
    fn bit(&mut self) -> bool {
        let mut carry = self.r8 >> 31;
        self.r8 <<= 1;
        if self.r8 == 0 {
            // sentinel shifted out: refill (adcs shifts in carry = 1)
            let w = if self.bits + 4 <= self.data.len() {
                rd32(self.data, self.bits)
            } else {
                self.st.err += 1;
                0
            };
            self.bits += 4;
            carry = w >> 31;
            self.r8 = (w << 1) | 1;
        }
        carry != 0
    }

    #[inline]
    fn colour(&mut self) -> u32 {
        if self.col + 2 > self.data.len() {
            self.st.err += 1;
            self.col += 2;
            return 0;
        }
        let v = u16::from_le_bytes([self.data[self.col], self.data[self.col + 1]]) as u32;
        self.col += 2;
        v
    }

    /// Next motion vector as a byte offset: 480*dy + 2*dx, dx and dy in -8..7.
    #[inline]
    fn mvoff(&mut self) -> i32 {
        let i = if self.mv < self.data.len() {
            self.data[self.mv] as i32
        } else {
            self.st.err += 1;
            0
        };
        self.mv += 1;
        let (dx, dy) = ((i & 15) - 8, (i >> 4) - 8);
        dy * STRIDE as i32 + dx * 2
    }

    // ---- block operations ----
    /// Copy w x h pixels row by row (each row read completely, then written).
    fn copy_blk(&mut self, dst: u32, src: u32, w: u32, h: u32) {
        let mut row = [0u32; 8];
        for y in 0..h {
            for x in 0..w {
                row[x as usize] = self.m16(src.wrapping_add(y * STRIDE + 2 * x));
            }
            for x in 0..w {
                self.w16(dst.wrapping_add(y * STRIDE + 2 * x), row[x as usize]);
            }
        }
    }

    /// Copy + colour offset: one 32-bit add of (c | c << 16) per pixel pair, so a carry out
    /// of the left pixel reaches the right one; 1-pixel-wide blocks add to the halfword.
    fn copy_off_blk(&mut self, dst: u32, src: u32, w: u32, h: u32, col: u32) {
        let k = col | col << 16;
        for y in 0..h {
            let (s, d) = (src.wrapping_add(y * STRIDE), dst.wrapping_add(y * STRIDE));
            if w == 1 {
                let v = self.m16(s).wrapping_add(k) & 0xffff;
                self.w16(d, v);
                continue;
            }
            let mut row = [0u32; 4];
            for x in 0..w / 2 {
                row[x as usize] = self.m32(s.wrapping_add(4 * x)).wrapping_add(k);
            }
            for x in 0..w / 2 {
                self.w32(d.wrapping_add(4 * x), row[x as usize]);
            }
        }
    }

    /// One edge-blending step (the ARM sequence at e.g. 0x03002844). Writes the edge pixel
    /// `a_e`, the neighbour `a_n` and the pixel one further inside `a_i`.
    fn blend3(&mut self, r0: u32, r1: u32, a_n: u32, a_e: u32, a_i: u32, odd: bool) {
        let mut r5 = self.m16(a_n);
        let r2 = r5 & L32;
        r5 &= M32;
        let mut r6 = (r0.wrapping_add(r5) >> 1) | if odd { r1 } else { r2 };
        self.w16(a_e, r6 & 0xffff);
        r6 &= M32;
        let r7 = (r6.wrapping_add(r5) >> 1) | if odd { r2 } else { r1 };
        self.w16(a_n, r7 & 0xffff);
        let r7 = (r6.wrapping_add(r0) >> 1) | if odd { r2 } else { r1 };
        self.w16(a_i, r7 & 0xffff);
    }

    fn fill_blk(&mut self, dst: u32, w: u32, h: u32, col: u32) {
        let k = col | col << 16;
        if w == 1 || h == 1 {
            for y in 0..h {
                for x in 0..w {
                    self.w16(dst.wrapping_add(y * STRIDE + 2 * x), col);
                }
            }
            return;
        }
        for y in 2..h {
            for x in 2..w {
                self.w16(dst.wrapping_add(y * STRIDE + 2 * x), col);
            }
        }
        let (r0, r1) = (k & M32, k & L32);
        for x in 0..w {
            let e = dst.wrapping_add(2 * x);
            self.blend3(
                r0,
                r1,
                e.wrapping_sub(STRIDE),
                e,
                e.wrapping_add(STRIDE),
                x & 1 != 0,
            );
        }
        for y in 0..h {
            let e = dst.wrapping_add(y * STRIDE);
            self.blend3(r0, r1, e.wrapping_sub(2), e, e.wrapping_add(2), y & 1 != 0);
        }
    }

    /// 2-colour pattern palette: c0, c1, (2c0+c1)/3, (c0+2c1)/3 per 5-bit channel, rounded.
    fn palette(&mut self) -> ([u32; 4], u32) {
        let c0 = self.colour();
        let c1 = self.colour();
        let lo = self.colour();
        let hi = self.colour();
        let mut pal = [c0, c1, 0, 0];
        for s in [10, 5, 0] {
            let (a, b) = ((c0 >> s) & 31, (c1 >> s) & 31);
            pal[2] = (pal[2] << 5) | ((2 * a + b + 1) / 3);
            pal[3] = (pal[3] << 5) | ((a + 2 * b + 1) / 3);
        }
        (pal, lo | hi << 16)
    }

    fn pat4(&mut self, dst: u32) {
        let (pal, idx) = self.palette();
        for k in 0..16u32 {
            let a = dst.wrapping_add((k >> 2) * STRIDE + 2 * (k & 3));
            self.w16(a, pal[((idx >> (2 * k)) & 3) as usize]);
        }
    }

    fn pat8(&mut self, dst: u32) {
        let (pal, idx) = self.palette();
        // odd rows: palette pixels at odd columns, even columns averaged with the left one
        for r in 0..4u32 {
            let row = dst.wrapping_add((2 * r + 1) * STRIDE);
            let mut left = self.m16(row.wrapping_sub(2)) & M32;
            for k in 0..4u32 {
                let v = pal[((idx >> (2 * (4 * r + k))) & 3) as usize];
                self.w16(row.wrapping_add(4 * k + 2), v);
                self.w16(
                    row.wrapping_add(4 * k),
                    (left.wrapping_add(v & M32) >> 1) | (v & L32),
                );
                left = v & M32;
            }
        }
        // even rows: average of the rows above and below, two pixels at a time
        for k in 0..4u32 {
            let a = dst.wrapping_add(4 * k);
            let at = |n: i32| a.wrapping_add((n * STRIDE as i32) as u32);
            let mut r0 = self.m32(at(7));
            let mut r6 = r0 & L32;
            r0 &= M32;
            let mut r1 = self.m32(at(5));
            let mut r7 = r1 & L32;
            r1 &= M32;
            self.w32(at(6), (r0.wrapping_add(r1) >> 1) | r6);
            r0 = self.m32(at(3));
            r6 = r0 & L32;
            r0 &= M32;
            self.w32(at(4), (r1.wrapping_add(r0) >> 1) | r7);
            r1 = self.m32(at(1));
            r7 = r1 & L32;
            r1 &= M32;
            self.w32(at(2), (r0.wrapping_add(r1) >> 1) | r6);
            r0 = self.m32(at(-1)) & M32;
            self.w32(a, (r1.wrapping_add(r0) >> 1) | r7);
        }
    }

    /// Edge statistics: does a source rectangle leave the reference picture?
    fn mv_check(&mut self, pos: u32, o: i32, w: u32, h: u32) {
        // o = 480*dy + 2*dx with dx, dy in -8..7: dx follows uniquely from o mod 480
        let dx = ((o % 480) + 480 + 16) % 480 / 2 - 8;
        let x = ((pos % STRIDE) / 2) as i32;
        if x + dx < 0 || x + dx + w as i32 > WIDTH as i32 {
            self.st.mv_xwrap += 1;
        }
        let src = self.refb.wrapping_add(pos).wrapping_add(o as u32);
        let end = src.wrapping_add((h - 1) * STRIDE + 2 * w);
        let rend = self.refb.wrapping_add(BUF_STRIDE);
        if src < self.refb || end > rend {
            self.st.mv_yout += 1;
            let below = (end > rend) as usize;
            let ref1 = (self.refb > self.out) as usize;
            self.st.mv_yout_kind[below * 2 + ref1] += 1;
        }
    }

    /// The recursive block grammar (one function per size in the ARM code).
    fn block(&mut self, w: u32, h: u32, pos: u32) {
        let si = size_index(w, h);
        let dst = self.out.wrapping_add(pos);
        if !self.bit() {
            if !self.bit() {
                self.st.mode_count[si][mode::SKIP] += 1;
                self.copy_blk(dst, self.refb.wrapping_add(pos), w, h);
            } else {
                self.st.mode_count[si][mode::MV] += 1;
                let o = self.mvoff();
                self.mv_check(pos, o, w, h);
                self.copy_blk(
                    dst,
                    self.refb.wrapping_add(pos).wrapping_add(o as u32),
                    w,
                    h,
                );
            }
            return;
        }
        let (can_h, can_v) = (h > 1, w > 1);
        if (can_h || can_v) && w * h > 2 && !self.bit() {
            // 2x1 and 1x2 have no split
            let vert = if can_h && can_v { self.bit() } else { !can_h };
            if !vert {
                self.st.mode_count[si][mode::SPLIT_H] += 1;
                self.block(w, h / 2, pos);
                self.block(w, h / 2, pos + (h / 2) * STRIDE);
            } else {
                self.st.mode_count[si][mode::SPLIT_V] += 1;
                self.block(w / 2, h, pos);
                self.block(w / 2, h, pos + w); // w/2 pixels = w bytes
            }
            return;
        }
        let fill = if w >= 4 && h >= 4 {
            if self.bit() {
                false
            } else if !self.bit() {
                true
            } else {
                self.st.mode_count[si][mode::PATTERN] += 1;
                if w == 8 && h == 8 {
                    if self.bit() {
                        self.edge_stats(pos);
                        self.pat8(dst);
                    } else {
                        self.pat4(dst);
                        self.pat4(dst.wrapping_add(8));
                        self.pat4(dst.wrapping_add(4 * STRIDE));
                        self.pat4(dst.wrapping_add(4 * STRIDE + 8));
                    }
                } else if w == 8 {
                    self.pat4(dst);
                    self.pat4(dst.wrapping_add(8));
                } else if h == 8 {
                    self.pat4(dst);
                    self.pat4(dst.wrapping_add(4 * STRIDE));
                } else {
                    self.pat4(dst);
                }
                return;
            }
        } else if !self.bit() {
            false
        } else if w * h == 2 && self.bit() {
            // 2x1 / 1x2: two raw pixels
            self.st.mode_count[si][mode::RAW] += 1;
            let a = self.colour();
            self.w16(dst, a);
            let b = self.colour();
            self.w16(dst.wrapping_add(if w == 2 { 2 } else { STRIDE }), b);
            return;
        } else {
            true
        };
        if fill {
            self.st.mode_count[si][mode::FILL] += 1;
            if w > 1 && h > 1 {
                self.edge_stats(pos);
            }
            let c = self.colour();
            self.fill_blk(dst, w, h, c);
        } else {
            self.st.mode_count[si][mode::MV_OFFSET] += 1;
            let o = self.mvoff();
            self.mv_check(pos, o, w, h);
            let k = self.colour();
            self.copy_off_blk(
                dst,
                self.refb.wrapping_add(pos).wrapping_add(o as u32),
                w,
                h,
                k,
            );
        }
    }

    #[inline]
    fn edge_stats(&mut self, pos: u32) {
        if pos.is_multiple_of(STRIDE) {
            self.st.edge_x0 += 1;
        }
        if pos < STRIDE {
            self.st.edge_y0 += 1;
        }
    }
}
