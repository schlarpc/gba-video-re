//! The player's register-level video bit reader (IWRAM 0x030007a8 refill, 0x0300076c ue(v),
//! 0x0300081c se(v)); a port of reference/vx/decoder/bits.h.
//!
//! r1 = halfword read pointer (`pos`), r2 = count (`cnt`; valid bits in `buf` = cnt + 16),
//! r3 = MSB-first bit buffer (`buf`). The stream is little-endian u16 halfwords consumed
//! MSB-first. A refill (one halfword) happens only when an operation drives `cnt` below 0,
//! and every primitive does at most one refill, exactly like the ARM code, so the refill
//! phase (and with it what over-long codes decode to) is register-exact.

/// Saved reader registers (kept in the decoder between frames).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct BitState {
    pub pos: usize,
    pub buf: u32,
    pub cnt: i32,
}

/// A reader over the video elementary stream (header + 0x38). Reads past the end give 0.
pub struct Bits<'a> {
    pub s: &'a [u8],
    pub pos: usize,
    pub buf: u32,
    pub cnt: i32,
}

#[inline(always)]
fn hw(s: &[u8], pos: usize) -> u32 {
    match s.get(pos..pos + 2) {
        Some(b) => u32::from(b[0]) | u32::from(b[1]) << 8,
        None => 0,
    }
}

impl BitState {
    /// Position at an absolute bit offset as the player's seek (0x02000584) does:
    /// buf = (hw[i]<<16 | hw[i+1]) << skip, cnt = 16 - skip, pos -> hw[i+2].
    pub fn at(s: &[u8], bitoff: u32) -> BitState {
        let h = (bitoff >> 4) as usize * 2;
        let skip = bitoff & 15;
        BitState {
            buf: ((hw(s, h) << 16) | hw(s, h + 2)) << skip,
            cnt: 16 - skip as i32,
            pos: h + 4,
        }
    }

    /// The player's power-on init (player_run, no seek): buf = hw[0] << 16, cnt = 0, pos -> hw[1].
    /// Same bit position as `at(s, 0)` but a different refill phase.
    pub fn start(s: &[u8]) -> BitState {
        BitState {
            buf: hw(s, 0) << 16,
            cnt: 0,
            pos: 2,
        }
    }

    /// Bit position from the start of the stream.
    pub fn bitpos(&self) -> u32 {
        (self.pos as i64 * 8 - (self.cnt as i64 + 16)) as u32
    }
}

impl<'a> Bits<'a> {
    #[inline(always)]
    pub fn new(s: &'a [u8], st: BitState) -> Self {
        Bits {
            s,
            pos: st.pos,
            buf: st.buf,
            cnt: st.cnt,
        }
    }

    #[inline(always)]
    pub fn state(&self) -> BitState {
        BitState {
            pos: self.pos,
            buf: self.buf,
            cnt: self.cnt,
        }
    }

    pub fn bitpos(&self) -> u32 {
        self.state().bitpos()
    }

    /// 0x030007a8: called when cnt < 0.
    #[inline(always)]
    pub fn refill(&mut self) {
        let h = hw(self.s, self.pos);
        self.pos += 2;
        self.cnt += 16;
        let sh = 16 - self.cnt; // ARM lsl by register: >= 32 gives 0
        if (0..32).contains(&sh) {
            self.buf |= h << sh;
        }
    }

    /// Consume `n` bits already peeked (register shift: n >= 32 clears buf); one refill.
    #[inline(always)]
    pub fn skip(&mut self, n: u32) {
        self.buf = if n >= 32 { 0 } else { self.buf << n };
        self.cnt -= n as i32;
        if self.cnt < 0 {
            self.refill();
        }
    }

    /// ue(v): count leading zeros z (each shifted out), value = (1<<z) + next z bits - 1,
    /// cnt -= 2z+1, one refill.
    #[inline(always)]
    pub fn ue(&mut self) -> u32 {
        let z = self.buf.leading_zeros(); // 32 for an all-zero buffer, like the C loop's cap
        let b = if z >= 32 { 0 } else { self.buf << z };
        let b = b << 1; // the '1'
        let mut v = if z != 0 { b.wrapping_shr(32 - z) } else { 0 };
        // (1 << z) - 1 in exact arithmetic, so an all-zero buffer (z == 32, only reachable on
        // garbage) gives 0xffffffff. In bits.h this is `(1u << 32) - 1`, undefined in C; the
        // gcc -O2 reference build yields 0xffffffff too (found by the differential fuzz).
        v = v.wrapping_add(((1u64 << z) - 1) as u32);
        self.buf = if z >= 32 { 0 } else { b << z };
        self.cnt -= 2 * z as i32 + 1;
        if self.cnt < 0 {
            self.refill();
        }
        v
    }

    /// se(v): k = ue+1; 0, 1, -1, 2, -2, ...
    #[inline(always)]
    pub fn se(&mut self) -> i32 {
        let k = self.ue().wrapping_add(1);
        let mut v = k as i32;
        if k & 1 != 0 {
            v = 1i32.wrapping_sub(v);
        }
        v >> 1
    }
}
