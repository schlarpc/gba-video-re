//! VX++ / VXGB video decoder core: an exact model of the player's hand-written ARM decoder
//! (IWRAM 0x03000520 and callees). Port of reference/vx/decoder/vxdec.c; spec docs/vx/video.md, docs/vx/vxgb.md.
//!
//! Memory model: the decoder works inside a 256 KiB image of GBA EWRAM at the player's own
//! addresses (frame slots, MV array, VXGB nonzero-count grids). Reference reads are
//! address-wrapped like EWRAM's mirroring, so reads outside a frame (bad MVs, intra edge
//! reads) return exactly what the player would read.

use crate::bits::{BitState, Bits};
use crate::cart::Variant;
use crate::cavlc::{vxgb_residual, NZC_STRIDE, NZY_STRIDE};
use crate::fill::{Family, FillIndex, FillOp, MAX_OPS};
use crate::residual::vx_residual;
use crate::tables::Tables;

pub const EWSIZE: usize = 0x40000;
pub const EWMASK: u32 = 0x3FFFF;
/// One decoder frame slot: Y 256x160 (stride 256) then interleaved U,V 256x80.
pub const SLOTSZ: u32 = 0xF000;
pub const CHROMA: u32 = 0xA000;
const MVSTRIDE: u32 = 0x24;
const NZY_START: u32 = 0x3EB8A;
const NZC_START: u32 = 0x3F5D3;

/// Where each player build keeps its decoder state in EWRAM.
#[derive(Debug, Clone, Copy)]
pub struct Layout {
    /// frame slot 0 (slot i at slot0 + i*0xF000)
    pub slot0: u32,
    /// MV-array entry of MB (0,0)
    pub mvstart: u32,
    /// regions player_run zeroes (MV array; VXGB also both nonzero-count grids)
    pub zero: &'static [(u32, u32)],
}

impl Layout {
    pub fn of(v: Variant) -> Layout {
        match v {
            // player_run 0x03006df4: slots at 0x020032c8, MV array 0x0203f2c8
            Variant::VxPP => Layout {
                slot0: 0x32C8,
                mvstart: 0x3F314,
                zero: &[(0x3F2C8, 0x318)],
            },
            // Rev 5 player: slots 0x02002b48, nnz grids 0x0203eb48 / 0x0203f5b1, MV array 0x0203f866
            Variant::Vxgb => Layout {
                slot0: 0x2B48,
                mvstart: 0x3F8B2,
                zero: &[(0x3EB48, 0xA69), (0x3F5B1, 0x2B5), (0x3F866, 0x318)],
            },
        }
    }
}

/// Counters for conditions the reference decoder also flags.
#[derive(Debug, Clone, Default)]
pub struct Stats {
    /// Bitstream values the ARM code does not define (mode out of range, impossible residual).
    pub err: u64,
    /// Motion vectors whose source block leaves the picture.
    pub oob: u64,
    /// Bilinear corner values outside 0..255.
    pub fill_ovf: u64,
    /// Intra blocks in picture row 0 (may read row -1).
    pub edge_intra: u64,
    /// Mode histogram per block width (16, 8, 4, 2) and mode 0..11.
    pub modes: [[u64; 12]; 4],
}

/// The decoder state: EWRAM image, bit reader registers, reference ring and QP.
#[derive(Clone)]
pub struct Core {
    pub ew: Box<[u8; EWSIZE]>,
    pub tables: Tables,
    pub lay: Layout,
    pub width: u32,
    pub height: u32,
    /// player ctx+0x28: frame n goes to slot n&3, refs (n-1..n-3)&3
    pub dec_index: u32,
    pub bits: BitState,
    refy: [u32; 3],
    refc: [u32; 3],
    outy: u32,
    outc: u32,
    yoff: u32,
    uvoff: u32,
    mvp: u32,
    nzy: u32,
    nzc: u32,
    /// VX++ dequant scales (IWRAM 0x03005770..0x0300577b)
    qscale: [i32; 3],
    /// VXGB dequant scales from the header QP, index (y&1)*4 + x
    gscale: [i32; 8],
    /// Bit position of the current frame's first bit.
    pub frame_bit: u32,
    /// The VX++ frame header ue (0 = keep QP).
    pub hdr_q: u32,
    pub stats: Stats,
    /// Optional per-MB start bit positions of the last frame.
    pub mb_bits: Option<Vec<u32>>,
    /// Optional block trace (w, h, yoff, mode, bit position after the mode code), the
    /// equivalent of vxdec's `--trace` "blk" lines.
    pub trace: Option<Vec<[u32; 5]>>,
}

#[inline(always)]
fn clip255(v: i32) -> u8 {
    v.clamp(0, 255) as u8
}

#[inline(always)]
fn median3(a: i32, b: i32, c: i32) -> i32 {
    let (mut mn, mut mx) = if a < b { (a, b) } else { (b, a) };
    if mn > c {
        mn = c;
    }
    if mx < c {
        mx = c;
    }
    a.wrapping_add(b)
        .wrapping_add(c)
        .wrapping_sub(mn)
        .wrapping_sub(mx)
}

#[inline(always)]
fn mv_unpack(v: i16) -> (i32, i32) {
    let lx = v as u16 as u8 as i8;
    (lx as i32, (v >> 8) as i32 + (lx < 0) as i32)
}

impl Core {
    /// Player state after player_run: Y planes 0, chroma 0x80, MV array (and VXGB grids) 0,
    /// decode index 3 (frame 0 goes to slot 3), EWRAM below slot 0 = the player's image.
    /// `stream` is only used to prime the bit reader at its power-on phase.
    pub fn new(tables: Tables, width: u32, height: u32, qp: u32, stream: &[u8]) -> Core {
        let lay = Layout::of(tables.variant);
        let mut c = Core {
            ew: vec![0u8; EWSIZE].into_boxed_slice().try_into().unwrap(),
            tables,
            lay,
            width,
            height,
            dec_index: 3,
            bits: BitState::start(stream),
            refy: [0; 3],
            refc: [0; 3],
            outy: 0,
            outc: 0,
            yoff: 0,
            uvoff: 0,
            mvp: 0,
            nzy: 0,
            nzc: 0,
            qscale: [0; 3],
            gscale: [0; 8],
            frame_bit: 0,
            hdr_q: 0,
            stats: Stats::default(),
            mb_bits: None,
            trace: None,
        };
        c.reset(qp, stream);
        c
    }

    /// Back to the power-on state (keeps tables, stats and options).
    pub fn reset(&mut self, qp: u32, stream: &[u8]) {
        let img = &self.tables.ewram_image;
        self.ew.fill(0);
        self.ew[..img.len()].copy_from_slice(img);
        for i in 0..4 {
            let s = (self.lay.slot0 + i * SLOTSZ) as usize;
            self.ew[s..s + CHROMA as usize].fill(0);
            self.ew[s + CHROMA as usize..s + SLOTSZ as usize].fill(0x80);
        }
        for &(a, n) in self.lay.zero {
            self.ew[a as usize..(a + n) as usize].fill(0);
        }
        self.dec_index = 3;
        self.qscale = [0; 3];
        if self.tables.variant == Variant::Vxgb {
            let deq = &self.tables.vxgb.as_ref().unwrap().deq;
            for i in 0..8 {
                self.gscale[i] =
                    (deq[(qp % 6) as usize * 8 + i] as u32).wrapping_shl(qp / 6) as i32;
            }
        }
        self.bits = BitState::start(stream);
    }

    /// The 0xF000-byte slot holding decode index `n`.
    pub fn slot(&self, n: u32) -> &[u8] {
        let s = (self.lay.slot0 + (n & 3) * SLOTSZ) as usize;
        &self.ew[s..s + SLOTSZ as usize]
    }

    // ------------------------------------------------------------ memory
    #[inline(always)]
    fn rd8(&self, a: u32) -> u8 {
        self.ew[(a & EWMASK) as usize]
    }
    #[inline(always)]
    fn wr8(&mut self, a: u32, v: u8) {
        self.ew[(a & EWMASK) as usize] = v;
    }
    #[inline(always)]
    fn rd16s(&self, a: u32) -> i16 {
        (self.rd8(a) as u16 | (self.rd8(a.wrapping_add(1)) as u16) << 8) as i16
    }
    #[inline(always)]
    fn wr16(&mut self, a: u32, v: u32) {
        self.wr8(a, v as u8);
        self.wr8(a.wrapping_add(1), (v >> 8) as u8);
    }

    /// Copy `w` bytes from `s` to `t` exactly as a forward byte loop over wrapped addresses.
    #[inline(always)]
    fn copy_row(&mut self, s: u32, t: u32, w: u32) {
        let (s, t) = (s & EWMASK, t & EWMASK);
        let (su, tu, wu) = (s as usize, t as usize, w as usize);
        if su + wu <= EWSIZE && tu + wu <= EWSIZE && (su + wu <= tu || tu + wu <= su) {
            self.ew.copy_within(su..su + wu, tu);
        } else {
            for x in 0..w {
                let v = self.rd8(s + x);
                self.wr8(t + x, v);
            }
        }
    }

    fn note_mv(&mut self, w: u32, h: u32, mx: i32, my: i32) {
        let bx = ((self.yoff & 0xff) as i32).wrapping_add(mx);
        let by = ((self.yoff >> 8) as i32).wrapping_add(my);
        if bx < 0
            || by < 0
            || bx.wrapping_add(w as i32) > self.width as i32
            || by.wrapping_add(h as i32) > self.height as i32
        {
            self.stats.oob += 1;
        }
    }

    // ------------------------------------------------------------ predictors
    /// MC copy, integer pel (0x03001578 / 0x03002910 / 0x03003b00 / 0x03004964). Chroma uses
    /// floor(mv/2); the MV (mx + my*256) is stored in the MB's MV-array entry.
    fn mc_copy(&mut self, r: usize, w: u32, h: u32, mx: i32, my: i32) {
        let (ry, rc) = (self.refy[r], self.refc[r]);
        let lo = mx.wrapping_add(my.wrapping_mul(256)) as u32;
        let co = (mx & !1).wrapping_add((my & !1).wrapping_mul(128)) as u32;
        self.wr16(self.mvp, lo);
        self.note_mv(w, h, mx, my);
        for y in 0..h {
            let s = ry
                .wrapping_add(self.yoff)
                .wrapping_add(lo)
                .wrapping_add(y * 256);
            let t = self.outy + self.yoff + y * 256;
            self.copy_row(s, t, w);
        }
        for y in 0..h / 2 {
            let s = rc
                .wrapping_add(self.uvoff)
                .wrapping_add(co)
                .wrapping_add(y * 256);
            let t = self.outc + self.uvoff + y * 256;
            self.copy_row(s, t, w);
        }
    }

    /// Mode 5: absolute MV from ref0 + DC offsets (0x03001854 ...). pixel = clip(ref + 2*d).
    /// The MV array is not written.
    fn mc_dcoff(&mut self, bs: &mut Bits, w: u32, h: u32) {
        let mx = bs.se();
        let my = bs.se();
        let (dy, du, dv) = (bs.se(), bs.se(), bs.se());
        let (ry, rc) = (self.refy[0], self.refc[0]);
        let lo = mx.wrapping_add(my.wrapping_mul(256)) as u32;
        let co = (mx & !1).wrapping_add((my & !1).wrapping_mul(128)) as u32;
        self.note_mv(w, h, mx, my);
        let (dy2, du2, dv2) = (dy.wrapping_mul(2), du.wrapping_mul(2), dv.wrapping_mul(2));
        for y in 0..h {
            let s = ry
                .wrapping_add(self.yoff)
                .wrapping_add(lo)
                .wrapping_add(y * 256);
            let t = self.outy + self.yoff + y * 256;
            for x in 0..w {
                let v = clip255((self.rd8(s.wrapping_add(x)) as i32).wrapping_add(dy2));
                self.wr8(t + x, v);
            }
        }
        for y in 0..h / 2 {
            let s = rc
                .wrapping_add(self.uvoff)
                .wrapping_add(co)
                .wrapping_add(y * 256);
            let t = self.outc + self.uvoff + y * 256;
            for x in (0..w).step_by(2) {
                let u = clip255((self.rd8(s.wrapping_add(x)) as i32).wrapping_add(du2));
                self.wr8(t + x, u);
                let v = clip255((self.rd8(s.wrapping_add(x + 1)) as i32).wrapping_add(dv2));
                self.wr8(t + x + 1, v);
            }
        }
    }

    /// Execute a fill op list; `yb` / `cb` = luma / chroma block address, `v` = corner value.
    fn run_fill(&mut self, bs: &mut Bits, ops: &[FillOp], yb: u32, cb: u32, v: i32) {
        let mut val = [0i32; MAX_OPS];
        for (i, o) in ops.iter().enumerate() {
            let b = if o.plane != 0 { cb } else { yb };
            let a = match o.aref {
                2 => v,
                1 => val[o.a as usize],
                _ => self.rd8(b.wrapping_add(o.a as i32 as u32)) as i32,
            };
            let c = match o.bref {
                2 => v,
                1 => val[o.b as usize],
                _ => self.rd8(b.wrapping_add(o.b as i32 as u32)) as i32,
            };
            let r = if o.kind == 1 {
                let r =
                    (a.wrapping_add(c).wrapping_add(1) >> 1).wrapping_add(bs.se().wrapping_mul(2));
                if !(0..=255).contains(&r) {
                    self.stats.fill_ovf += 1;
                }
                r
            } else {
                a.wrapping_add(c) >> 1
            };
            val[i] = r;
            self.wr8(b.wrapping_add(o.dst as i32 as u32), r as u8);
        }
    }

    /// Mode 4 "bilinear" intra: corner = ((T[w-1] + L[h-1] + 1) >> 1) + 2*se per plane, then
    /// recursive midpoint averaging. Luma, then U, then V.
    fn intra_bilinear(&mut self, bs: &mut Bits, w: u32, h: u32) {
        let ops = FillIndex::get().ops(Family::Mode4, w as usize, h as usize);
        self.run_fill(bs, ops, self.outy + self.yoff, self.outc + self.uvoff, 0);
    }

    /// Intra 16x16-style whole-block luma predictors, generic WxH (0x03000884).
    fn ipred_luma(&mut self, bs: &mut Bits, mode: u32, w: u32, h: u32) {
        let b = self.outy + self.yoff;
        let off = self.yoff;
        match mode {
            0 => {
                // V 0x030009b8
                for y in 0..h {
                    for x in 0..w {
                        let v = self.rd8(b.wrapping_sub(256) + x);
                        self.wr8(b + y * 256 + x, v);
                    }
                }
            }
            1 => {
                // H 0x03000a04
                for y in 0..h {
                    let l = self.rd8((b + y * 256).wrapping_sub(1));
                    for x in 0..w {
                        self.wr8(b + y * 256 + x, l);
                    }
                }
            }
            2 => {
                // DC 0x03000a54; availability from the frame position only
                const LG: [u32; 5] = [0, 2, 3, 0, 4];

                let dc: u32 = if off == 0 {
                    0x80
                } else {
                    let (mut t, mut l) = (0u32, 0u32);
                    if off & 0xff00 != 0 {
                        for x in 0..w {
                            t += self.rd8(b.wrapping_sub(256) + x) as u32;
                        }
                        t = (t + (w >> 1)) >> LG[(w >> 2) as usize];
                    }
                    if off & 0xff != 0 {
                        for y in 0..h {
                            l += self.rd8((b + y * 256).wrapping_sub(1)) as u32;
                        }
                        l = (l + (h >> 1)) >> LG[(h >> 2) as usize];
                    }
                    if off & 0xff == 0 {
                        t
                    } else if off & 0xff00 == 0 {
                        l
                    } else {
                        (t + l + 1) >> 1
                    }
                };
                for y in 0..h {
                    for x in 0..w {
                        self.wr8(b + y * 256 + x, dc as u8);
                    }
                }
            }
            3 => {
                // "plane" 0x03000ba4: corner = (T[w-1]+L[h-1]+1)>>1, then bisection fill
                let c = (self.rd8((b + w).wrapping_sub(1 + 256)) as i32
                    + self.rd8((b + (h - 1) * 256).wrapping_sub(1)) as i32
                    + 1)
                    >> 1;
                self.wr8(b + (h - 1) * 256 + w - 1, c as u8);
                let ops = FillIndex::get().ops(Family::PlaneLuma, w as usize, h as usize);
                self.run_fill(bs, ops, b, 0, c);
            }
            _ => self.stats.err += 1,
        }
    }

    fn ipred_chroma(&mut self, bs: &mut Bits, mode: u32, w: u32, h: u32) {
        let b = self.outc + self.uvoff;
        let off = self.uvoff;
        let (cw, ch) = (w >> 1, h >> 1); // chroma pairs / rows
        match mode {
            0 => {
                // DC 0x03000c08
                const LG: [u32; 5] = [0, 1, 2, 0, 3];
                let (du, dv): (u32, u32);
                if off == 0 {
                    du = 0x80;
                    dv = 0x80;
                } else {
                    let (mut tu, mut tv, mut lu, mut lv) = (0u32, 0u32, 0u32, 0u32);
                    if off & 0xff00 != 0 {
                        for x in 0..cw {
                            tu += self.rd8(b.wrapping_sub(256) + 2 * x) as u32;
                            tv += self.rd8(b.wrapping_sub(255) + 2 * x) as u32;
                        }
                        tu = (tu + (w >> 2)) >> LG[(w >> 2) as usize];
                        tv = (tv + (w >> 2)) >> LG[(w >> 2) as usize];
                    }
                    if off & 0xff != 0 {
                        for y in 0..ch {
                            lu += self.rd8((b + y * 256).wrapping_sub(2)) as u32;
                            lv += self.rd8((b + y * 256).wrapping_sub(1)) as u32;
                        }
                        lu = (lu + (h >> 2)) >> LG[(h >> 2) as usize];
                        lv = (lv + (h >> 2)) >> LG[(h >> 2) as usize];
                    }
                    if off & 0xff == 0 {
                        du = tu;
                        dv = tv;
                    } else if off & 0xff00 == 0 {
                        du = lu;
                        dv = lv;
                    } else {
                        du = (tu + lu + 1) >> 1;
                        dv = (tv + lv + 1) >> 1;
                    }
                }
                for y in 0..ch {
                    for x in 0..cw {
                        self.wr8(b + y * 256 + 2 * x, du as u8);
                        self.wr8(b + y * 256 + 2 * x + 1, dv as u8);
                    }
                }
            }
            1 => {
                // H 0x03000d90
                for y in 0..ch {
                    let u = self.rd8((b + y * 256).wrapping_sub(2));
                    let v = self.rd8((b + y * 256).wrapping_sub(1));
                    for x in 0..cw {
                        self.wr8(b + y * 256 + 2 * x, u);
                        self.wr8(b + y * 256 + 2 * x + 1, v);
                    }
                }
            }
            2 => {
                // V 0x03000dd4
                for y in 0..ch {
                    for x in 0..w {
                        let v = self.rd8(b.wrapping_sub(256) + x);
                        self.wr8(b + y * 256 + x, v);
                    }
                }
            }
            3 => {
                // plane 0x03000e44
                let tr = (b + w).wrapping_sub(256);
                let bl = b + ch * 256;
                let cu = (self.rd8(tr.wrapping_sub(2)) as i32
                    + self.rd8(bl.wrapping_sub(258)) as i32
                    + 1)
                    >> 1;
                let cv = (self.rd8(tr.wrapping_sub(1)) as i32
                    + self.rd8(bl.wrapping_sub(257)) as i32
                    + 1)
                    >> 1;
                let br = (b + w + ch * 256).wrapping_sub(258);
                self.wr8(br, cu as u8);
                self.wr8(br.wrapping_add(1), cv as u8);
                let ops = FillIndex::get().ops(Family::PlaneChroma, w as usize, h as usize);
                self.run_fill(bs, ops, 0, b, cu);
                self.run_fill(bs, ops, 0, b + 1, cv);
            }
            _ => self.stats.err += 1,
        }
    }

    /// Mode 6 (0x03000884): ue luma mode, ue chroma mode, then predict.
    fn intra_16(&mut self, bs: &mut Bits, w: u32, h: u32) {
        let lm = bs.ue();
        let cm = bs.ue();
        self.ipred_luma(bs, lm, w, h);
        self.ipred_chroma(bs, cm, w, h);
    }

    /// Intra 4x4 predictors (table 0x030008d8); `p` = block address, `off` = frame offset.
    fn i4x4(&mut self, mode: u32, p: u32, off: u32) {
        let at = |x: i32, y: i32| p.wrapping_add((y * 256 + x) as u32);
        let px = |s: &Self, x: i32, y: i32| s.rd8(at(x, y)) as i32;
        let m = px(self, -1, -1);
        let mut t = [0i32; 8];
        let mut l = [0i32; 4];
        for (i, v) in t.iter_mut().enumerate() {
            *v = px(self, i as i32, -1);
        }
        for (i, v) in l.iter_mut().enumerate() {
            *v = px(self, -1, i as i32);
        }
        let mut out = [[0i32; 4]; 4]; // [y][x]
        match mode {
            0 => {
                for row in out.iter_mut() {
                    row.copy_from_slice(&t[..4]);
                }
            }
            1 => {
                for (y, row) in out.iter_mut().enumerate() {
                    *row = [l[y]; 4];
                }
            }
            2 => {
                let dc = if off == 0 {
                    0x80
                } else {
                    let (mut s, mut n) = (0, 1);
                    if off & 0xff != 0 {
                        s += l[0] + l[1] + l[2] + l[3] + 2;
                        n += 1;
                    }
                    if off & 0xff00 != 0 {
                        s += t[0] + t[1] + t[2] + t[3] + 2;
                        n += 1;
                    }
                    s >> n
                };
                out = [[dc; 4]; 4];
            }
            3 => {
                // diagonal down-left (uses T[4..7] unconditionally)
                for y in 0..4 {
                    for x in 0..4 {
                        let k = x + y;
                        out[y][x] = if k == 6 {
                            (t[6] + 3 * t[7] + 2) >> 2
                        } else {
                            (t[k] + 2 * t[k + 1] + t[k + 2] + 2) >> 2
                        };
                    }
                }
            }
            4 => {
                // diagonal down-right
                for y in 0..4usize {
                    for x in 0..4usize {
                        out[y][x] = if x > y {
                            let k = x - y;
                            let a = if k >= 2 { t[k - 2] } else { m };
                            (a + 2 * t[k - 1] + t[k] + 2) >> 2
                        } else if x < y {
                            let k = y - x;
                            let a = if k >= 2 { l[k - 2] } else { m };
                            (a + 2 * l[k - 1] + l[k] + 2) >> 2
                        } else {
                            (l[0] + 2 * m + t[0] + 2) >> 2
                        };
                    }
                }
            }
            5 => {
                // vertical-right
                let a = (l[2] + l[0] + 2 * l[1] + 2) >> 2;
                let b = (l[1] + m + 2 * l[0] + 2) >> 2;
                let c = (l[0] + t[0] + 2 * m + 2) >> 2;
                let e = (m + t[0] + 1) >> 1;
                let f = (m + t[1] + 2 * t[0] + 2) >> 2;
                let g = (t[0] + t[1] + 1) >> 1;
                let hh = (t[0] + t[2] + 2 * t[1] + 2) >> 2;
                let i = (t[2] + t[1] + 1) >> 1;
                let j = (t[1] + t[3] + 2 * t[2] + 2) >> 2;
                let k = (t[2] + t[3] + 1) >> 1;
                out = [[e, g, i, k], [c, f, hh, j], [b, e, g, i], [a, c, f, hh]];
            }
            6 => {
                // horizontal-down
                let a = (l[3] + l[2] + 1) >> 1;
                let b = (l[3] + l[1] + 2 * l[2] + 2) >> 2;
                let c = (l[2] + l[1] + 1) >> 1;
                let e = (l[2] + l[0] + 2 * l[1] + 2) >> 2;
                let f = (l[1] + l[0] + 1) >> 1;
                let g = (l[1] + m + 2 * l[0] + 2) >> 2;
                let hh = (l[0] + m + 1) >> 1;
                let i = (l[0] + t[0] + 2 * m + 2) >> 2;
                let j = (m + t[1] + 2 * t[0] + 2) >> 2;
                let k = (t[0] + t[2] + 2 * t[1] + 2) >> 2;
                out = [[hh, i, j, k], [f, g, hh, i], [c, e, f, g], [a, b, c, e]];
            }
            7 => {
                // vertical-left
                let q33 = (t[6] + t[4] + 2 * t[5] + 2) >> 2;
                let q32 = (t[5] + t[4] + 1) >> 1;
                let t3 = t[3];
                let q13_32 = (t[5] + t3 + 2 * t[4] + 2) >> 2;
                let q03_22 = (t3 + t[4] + 1) >> 1;
                let q12_31 = (t[4] + t[2] + 2 * t3 + 2) >> 2;
                let q02_21 = (t[2] + t3 + 1) >> 1;
                let q11_30 = (t3 + t[1] + 2 * t[2] + 2) >> 2;
                let q01_20 = (t[2] + t[1] + 1) >> 1;
                let q10 = (t[2] + t[0] + 2 * t[1] + 2) >> 2;
                let q00 = (t[1] + t[0] + 1) >> 1;
                // S(x, y): out[y][x]
                out = [
                    [q00, q01_20, q02_21, q03_22],
                    [q10, q11_30, q12_31, q13_32],
                    [q01_20, q02_21, q03_22, q32],
                    [q11_30, q12_31, q13_32, q33],
                ];
            }
            8 => {
                // horizontal-up
                let a = (l[0] + l[1] + 1) >> 1;
                let b = (l[0] + l[2] + 2 * l[1] + 2) >> 2;
                let c = (l[1] + l[2] + 1) >> 1;
                let d = (l[1] + l[3] + 2 * l[2] + 2) >> 2;
                let e = (l[2] + l[3] + 1) >> 1;
                let f = (l[3] + l[2] + 2 * l[3] + 2) >> 2;
                let g = l[3];
                out = [[a, b, c, d], [c, d, e, f], [e, f, g, g], [g, g, g, g]];
            }
            _ => {
                self.stats.err += 1;
                return;
            }
        }
        for (y, row) in out.iter().enumerate() {
            for (x, &v) in row.iter().enumerate() {
                self.wr8(at(x as i32, y as i32), v as u8);
            }
        }
    }

    /// Mode 7 (0x030008fc): per 4x4 in raster order, pred = min(top, left) of the mode context
    /// (borders fixed at 9; 9 -> 2). Bit 1: mode = pred; bit 0: 3 bits rem, mode = rem +
    /// (rem >= pred). Register-exact bits: cnt may reach -12 before a refill, like the ARM.
    fn intra_4x4(&mut self, bs: &mut Bits, w: u32, h: u32) {
        let mut ctx = [9u8; 25];
        for by in 0..h / 4 {
            for bx in 0..w / 4 {
                let ci = ((by + 1) * 5 + bx + 1) as usize;
                let mut pred = ctx[ci - 5].min(ctx[ci - 1]) as u32;
                if pred == 9 {
                    pred = 2;
                }
                let flag = bs.buf >> 31;
                bs.buf <<= 1;

                let mode = if flag != 0 {
                    bs.cnt -= 1;
                    pred
                } else {
                    let rem = bs.buf >> 29;
                    bs.buf <<= 3;
                    bs.cnt -= 4;
                    if rem >= pred {
                        rem + 1
                    } else {
                        rem
                    }
                };
                ctx[ci] = mode as u8;
                let off = self.yoff + by * 4 * 256 + bx * 4;
                self.i4x4(mode, self.outy + off, off);
                if bs.cnt < -12 {
                    bs.refill();
                }
            }
        }
        if bs.cnt < 0 {
            bs.refill();
        }
        let cm = bs.ue();
        self.ipred_chroma(bs, cm, w, h);
    }

    fn nz_clear(&mut self, w: u32, h: u32) {
        for y in 0..h / 4 {
            for x in 0..w / 4 {
                self.wr8(self.nzy + y * NZY_STRIDE + x, 0);
            }
        }
        for y in 0..h / 8 {
            for x in 0..w / 8 {
                self.wr8(self.nzc + y * NZC_STRIDE + x, 0);
            }
        }
    }

    // ------------------------------------------------------------ partition tree
    /// Dispatchers 0x03001dac (16x16), 0x03002184 (16x8), 0x030030f4 (8x16), 0x03003584
    /// (8x8): 24 modes; smaller sizes: 12 (no residual variants). mode = ue(v).
    ///  m%12: 0 copy ref0 pred-MV | 1 split top/bottom | 2 split left/right | 3 ref0 MV delta
    ///        4 bilinear intra | 5 abs MV + DC offsets | 6 intra "16x16" | 7 intra 4x4
    ///        8 copy ref1 | 9 ref1 MV delta | 10 copy ref2 | 11 ref2 MV delta
    ///  m >= 12: the same, then a residual.
    /// The MV predictor (pmx, pmy) is the MB's median predictor for all sub-blocks.
    fn block(&mut self, bs: &mut Bits, w: u32, h: u32, pmx: i32, pmy: i32) {
        let mut m = bs.ue();
        if let Some(t) = self.trace.as_mut() {
            t.push([w, h, self.yoff, m, bs.bitpos()]);
        }
        let big = w >= 8 && h >= 8;
        if m >= if big { 24 } else { 12 } {
            self.stats.err += 1;
            return;
        }
        let res = m >= 12;
        if res {
            m -= 12;
        }
        self.stats.modes[match w {
            16 => 0,
            8 => 1,
            4 => 2,
            _ => 3,
        }][m as usize] += 1;
        if (m == 4 || ((m == 6 || m == 7) && w >= 4 && h >= 4)) && self.yoff < 256 {
            self.stats.edge_intra += 1;
        }
        match m {
            0 => self.mc_copy(0, w, h, pmx, pmy),
            8 => self.mc_copy(1, w, h, pmx, pmy),
            10 => self.mc_copy(2, w, h, pmx, pmy),
            3 | 9 | 11 => {
                let dx = bs.se();
                let dy = bs.se();
                let r = match m {
                    3 => 0,
                    9 => 1,
                    _ => 2,
                };
                self.mc_copy(r, w, h, pmx.wrapping_add(dx), pmy.wrapping_add(dy));
            }
            1 => {
                if h == 2 {
                    self.stats.err += 1;
                    return;
                }
                self.block(bs, w, h / 2, pmx, pmy);
                self.yoff += (h / 2) * 256;
                self.uvoff += (h / 4) * 256;
                if h == 16 {
                    self.nzy += 2 * NZY_STRIDE;
                    self.nzc += NZC_STRIDE;
                }
                self.block(bs, w, h / 2, pmx, pmy);
                self.yoff -= (h / 2) * 256;
                self.uvoff -= (h / 4) * 256;
                if h == 16 {
                    self.nzy -= 2 * NZY_STRIDE;
                    self.nzc -= NZC_STRIDE;
                }
            }
            2 => {
                if w == 2 {
                    self.stats.err += 1;
                    return;
                }
                self.block(bs, w / 2, h, pmx, pmy);
                self.yoff += w / 2;
                self.uvoff += w / 2;
                if w == 16 {
                    self.nzy += 2;
                    self.nzc += 1;
                }
                self.block(bs, w / 2, h, pmx, pmy);
                self.yoff -= w / 2;
                self.uvoff -= w / 2;
                if w == 16 {
                    self.nzy -= 2;
                    self.nzc -= 1;
                }
            }
            4 => self.intra_bilinear(bs, w, h),
            5 => self.mc_dcoff(bs, w, h),
            6 => {
                if w >= 4 && h >= 4 {
                    self.intra_16(bs, w, h); // no-op (no bits) for 2-wide/2-high
                }
            }
            7 => {
                if w >= 4 && h >= 4 {
                    self.intra_4x4(bs, w, h);
                }
            }
            _ => unreachable!(),
        }
        let (y, uv) = (self.outy + self.yoff, self.outc + self.uvoff);
        match self.tables.variant {
            Variant::Vxgb => {
                if res {
                    let t = self.tables.vxgb.as_ref().unwrap();
                    if vxgb_residual(
                        bs,
                        t,
                        &self.gscale,
                        &mut self.ew[..],
                        y,
                        uv,
                        self.nzy,
                        self.nzc,
                        w,
                        h,
                    ) < 0
                    {
                        self.stats.err += 1;
                    }
                } else if big && !(m == 1 && h == 16) && !(m == 2 && w == 16) {
                    self.nz_clear(w, h); // every leaf mode, and splits into blocks smaller than 8x8
                }
            }
            Variant::VxPP => {
                if res {
                    let t = self.tables.vxpp.as_ref().unwrap();
                    if vx_residual(bs, t, &self.qscale, &mut self.ew[..], y, uv, w, h) < 0 {
                        self.stats.err += 1;
                    }
                }
            }
        }
    }

    /// 0x03000730: VX++ dequant scales LevelScale[QP%6][i] << QP/6.
    fn set_qp(&mut self, qp: u32) {
        let ls = self.tables.vxpp.as_ref().unwrap().level_scale[(qp % 6) as usize];
        for i in 0..3 {
            self.qscale[i] = (ls[i] << (qp / 6)) as i32;
        }
    }

    /// 0x03006d30 + 0x03000520: decode one frame into slot (dec_index & 3).
    pub fn decode_frame(&mut self, stream: &[u8]) {
        let mut bs = Bits::new(stream, self.bits);
        let n = self.dec_index;
        for k in 0..3u32 {
            let s = n.wrapping_sub(1 + k) & 3;
            self.refy[k as usize] = self.lay.slot0 + s * SLOTSZ;
            self.refc[k as usize] = self.lay.slot0 + s * SLOTSZ + CHROMA;
        }
        self.outy = self.lay.slot0 + (n & 3) * SLOTSZ;
        self.outc = self.outy + CHROMA;
        self.frame_bit = bs.bitpos();
        if self.tables.variant == Variant::VxPP {
            let q = bs.ue(); // 0 = keep QP; else new QP
            self.hdr_q = q;
            if q != 0 {
                if q >= 48 {
                    self.stats.err += 1;
                } else {
                    self.set_qp(q);
                }
            }
        }
        self.yoff = 0;
        self.uvoff = 0;
        let (mbw, mbh) = (self.width / 16, self.height / 16);
        let mut mb_bits = self.mb_bits.take();
        if let Some(v) = mb_bits.as_mut() {
            v.clear();
        }
        for my in 0..mbh {
            for mx in 0..mbw {
                self.mvp = self.lay.mvstart + my * MVSTRIDE + mx * 2;
                self.nzy = NZY_START + my * 4 * NZY_STRIDE + mx * 4;
                self.nzc = NZC_START + my * 2 * NZC_STRIDE + mx * 2;
                self.wr16(self.mvp, 0);
                let (ax, ay) = mv_unpack(self.rd16s(self.mvp - 2)); // left
                let (bx, by) = mv_unpack(self.rd16s(self.mvp - MVSTRIDE)); // top
                let (cx, cy) = mv_unpack(self.rd16s(self.mvp - MVSTRIDE + 2)); // top-right
                if let Some(v) = mb_bits.as_mut() {
                    v.push(bs.bitpos());
                }
                self.block(&mut bs, 16, 16, median3(ax, bx, cx), median3(ay, by, cy));
                self.yoff += 16;
                self.uvoff += 16;
            }
            self.yoff = self.yoff.wrapping_add(0x1000 - self.width);
            self.uvoff = self.uvoff.wrapping_add(0x800 - self.width);
        }
        self.mb_bits = mb_bits;
        self.dec_index = self.dec_index.wrapping_add(1);
        self.bits = bs.state();
    }
}
