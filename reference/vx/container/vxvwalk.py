"""Parse-only walker for the VX++ video bitstream: consumes exactly the bits the
player's decoder consumes, without reconstructing pixels, to find per-frame bit
boundaries. Bit consumption does not depend on pixel data or motion vectors.

Grammar (player IWRAM 0x03000520 / 0x03001dac and the per-size dispatchers; mode
semantics cross-checked against quatric/mobipeg libavcodec/vx.c, which decodes this ROM):

frame      := ue(q) [q != 0 -> QP = q]  mb[h/16][w/16]
mb(w,h)    := ue(m) -> action[GBA_MODE_MAP[m]]
residual   := for each 8x8 in block: ue(idx) -> cbp = CBP64[idx];
              for each set bit of 6 (4 luma 4x4, U, V): coeff tokens until last=1
coeff      := 12-bit LUT lookup (ROM 0x9ae4, 4096 LE u16) with escapes when top 7 bits == 0000011

Pure Python; the whole movie walks in a few minutes.
"""
import os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
LUT_ROM = 0x9ae4          # = EWRAM 0x020010e4, ctx+0x20 in the decoder
LUT_SIZE = 0x2000 + 0x80 + 0x80

GBA_MODE_MAP = (1, 2, 0, 4, 7, 3, 11, 15, 9, 5, 14, 6,
                12, 13, 8, 16, 23, 10, 22, 19, 20, 17, 21, 18)
CBP64 = (0x00, 0x0F, 0x1F, 0x08, 0x02, 0x01, 0x04, 0x3F, 0x0A, 0x05, 0x0E, 0x0B, 0x03, 0x0C, 0x10, 0x0D,
         0x07, 0x2F, 0x06, 0x09, 0x20, 0x1B, 0x1E, 0x17, 0x1A, 0x1D, 0x15, 0x11, 0x13, 0x12, 0x18, 0x14,
         0x1C, 0x37, 0x3B, 0x3E, 0x19, 0x2B, 0x21, 0x27, 0x16, 0x2A, 0x2E, 0x25, 0x22, 0x3D, 0x2D, 0x28,
         0x24, 0x35, 0x23, 0x3A, 0x33, 0x2C, 0x29, 0x30, 0x26, 0x31, 0x3C, 0x32, 0x39, 0x36, 0x34, 0x38)
# action codes after mode map: what is read, (split, residual)
#  'S' = no bits, 'D' = se,se, 'C' = 5*se (abs MV + 3 DC), 'P' = 3*se (plane/bilinear),
#  'N' = ue luma + ue chroma, '4' = intra4x4 + ue chroma, 'V'/'H' = split
ACTION = {0: ('V', 0), 1: ('S', 0), 2: ('H', 0), 3: ('C', 0), 4: ('D', 0), 5: ('D', 0), 6: ('D', 0),
          7: ('P', 0), 8: ('V', 1), 9: ('S', 0), 10: ('C', 1), 11: ('N', 0), 12: ('S', 1), 13: ('H', 1),
          14: ('S', 0), 15: ('4', 0), 16: ('D', 1), 17: ('D', 1), 18: ('D', 1), 19: ('4', 1), 20: ('S', 1),
          21: ('S', 1), 22: ('N', 1), 23: ('P', 1)}


def swap16(buf):
    """Cartridge LE halfwords -> plain MSB-first byte order (+8 pad bytes)."""
    b = bytearray(buf)
    if len(b) & 1:
        b.append(0)
    b[0::2], b[1::2] = b[1::2], b[0::2]
    return bytes(b) + b'\0' * 8      # padding for 40-bit peeks


class PyWalker:
    def __init__(self, video, lut_blob):
        self.d = swap16(video)
        self.lut = struct.unpack_from('<4096H', lut_blob, 0)
        self.vtab = lut_blob[0x2000:0x2080]
        self.rtab = lut_blob[0x2080:0x2100]
        self.p = 0

    def peek32(self):
        p = self.p
        q = p >> 3
        v = int.from_bytes(self.d[q:q + 5], 'big')
        return (v >> (8 - (p & 7))) & 0xffffffff

    def bits(self, n):
        v = (self.peek32() >> (32 - n)) if n else 0
        self.p += n
        return v

    def ue(self):
        a = self.peek32()
        if a == 0:
            raise ValueError('ue overflow at %d' % self.p)
        z = 32 - a.bit_length()
        self.p += z + 1
        return (1 << z) - 1 + self.bits(z)

    def se(self):
        k = self.ue()
        return (k + 1) >> 1 if k & 1 else -(k >> 1)

    def coeff_block(self):
        pos = 0
        for _ in range(24):
            acc = self.peek32()
            if (acc >> 25) != 3:
                cell = self.lut[acc >> 20]
            elif not (acc & (1 << 24)):
                self.p += 8
                acc = self.peek32(); cell = self.lut[acc >> 20]
            elif not (acc & (1 << 23)):
                self.p += 9
                acc = self.peek32(); cell = self.lut[acc >> 20]
                last = cell >> 15
                value = (cell >> 4) & 0x1f
                run = ((cell >> 9) & 0x3f) + self.rtab[last * 0x40 + value]
                n = cell & 15
                if not n:
                    raise ValueError('bad VLC at %d' % self.p)
                self.p += n
                pos += run + 1
                if pos > 16:
                    raise ValueError('run overflow at %d' % self.p)
                if last:
                    return
                continue
            else:
                self.p += 9
                last = self.bits(1); run = self.bits(6); self.bits(12)
                pos += run + 1
                if pos > 16:
                    raise ValueError('run overflow at %d' % self.p)
                if last:
                    return
                continue
            n = cell & 15
            if not n:
                raise ValueError('bad VLC at %d' % self.p)
            self.p += n
            pos += ((cell >> 9) & 0x3f) + 1
            if pos > 16:
                raise ValueError('run overflow at %d' % self.p)
            if cell >> 15:
                return
        raise ValueError('no last coeff at %d' % self.p)

    def residual(self, w, h):
        for _ in range(0, h, 8):
            for _ in range(0, w, 8):
                idx = self.ue()
                if idx >= 64:
                    raise ValueError('cbp idx %d at %d' % (idx, self.p))
                m = CBP64[idx]
                for b in range(6):
                    if m & (1 << b):
                        self.coeff_block()

    def mb(self, w, h):
        m = self.ue()
        if m >= 24:
            raise ValueError('mode %d at %d (%dx%d)' % (m, self.p, w, h))
        a, res = ACTION[GBA_MODE_MAP[m]]
        if a == 'V':
            if w == 2:
                raise ValueError('vsplit 2 at %d' % self.p)
            self.mb(w // 2, h); self.mb(w // 2, h)
        elif a == 'H':
            if h == 2:
                raise ValueError('hsplit 2 at %d' % self.p)
            self.mb(w, h // 2); self.mb(w, h // 2)
        elif a == 'D':
            self.se(); self.se()
        elif a == 'C':
            for _ in range(5):
                self.se()
        elif a == 'P':
            for _ in range(3):
                self.se()
        elif a == 'N':
            if self.ue() > 3 or self.ue() > 3:
                raise ValueError('intra mode at %d' % self.p)
        elif a == '4':
            for _ in range((w // 4) * (h // 4)):
                if not self.bits(1):
                    self.bits(3)
            if self.ue() > 3:
                raise ValueError('chroma mode at %d' % self.p)
        if res:
            self.residual(w, h)

    def frame(self, width, height):
        """Returns qp-or-0 read at frame start."""
        q = self.ue()
        for _ in range(height // 16):
            for _ in range(width // 16):
                self.mb(16, 16)
        return q


def walk_segment(video_swapped, lut_blob, start_bit, nframes, width, height):
    """Returns (frame_start_bits[nframes+1], qps[nframes]); last entry = end bit."""
    w = PyWalker(b'', lut_blob)
    w.d = video_swapped
    w.p = start_bit
    starts, qps = [start_bit], []
    for _ in range(nframes):
        qps.append(w.frame(width, height))
        starts.append(w.p)
    return starts, qps


# ---------------------------------------------------------------------------------------------
# VXGB (GBA Video Rev 5 player: Shark Tale, Shrek 2). Same macroblock grammar; no frame header;
# residuals are CAVLC-coded with an nC context kept in two persistent byte grids (docs/vxgb.md).
# The code tables are read from the cart's player IWRAM image (ROM 0xb548).
VXGB_IW_ROM = 0xB548
NZ_SY, NZ_SC = 0x41, 0x21
NZY0, NZC0 = 0x42, 0xA69 + 0x22      # MB (0,0) entries in one buffer: luma grid then chroma grid


class GBTables:
    def __init__(self, rom):
        iw = rom[VXGB_IW_ROM:VXGB_IW_ROM + 0x7CD0]
        b = lambda a: iw[a - 0x03000000]
        w32 = lambda a: struct.unpack_from('<I', iw, a - 0x03000000)[0]
        self.cbp = [b(0x03005E90 + i) for i in range(32)]
        self.node = [b(0x03006120 + i) for i in range(8)]
        self.lens = [0x03006120 - 0x03000000 + b(0x03006128 + i) for i in range(8)]
        t = w32(0x03006910) - 0x03000000
        self.tree = [x - 256 if x > 127 else x for x in iw[t:t + 49 * 16]]
        self.tz = [(b(0x03006200 + k), w32(0x03006210 + 4 * k) - 0x03000000) for k in range(15)]
        self.rb = [(b(0x03006250 + k), w32(0x03006258 + 4 * k) - 0x03000000) for k in range(6)]
        self.thr = struct.unpack_from('<6I', iw, 0x6080)
        self.iw = iw


class PyWalkerGB(PyWalker):
    """Parse-only VXGB walker. self.nz is the pair of nC grids; keep the same walker across
    frames (the player never resets the grids, except at power-on)."""

    def __init__(self, video, tabs):
        self.d = swap16(video)
        self.t = tabs
        self.p = 0
        self.nz = bytearray(0xA69 + 0x2B5)

    def tab(self, width, base):
        v = self.t.iw[base + 16 + (self.peek32() >> (32 - width))]
        self.p += self.t.iw[base + v]
        return v

    def coef4x4(self, nC):
        t = self.t
        if nC >= 8:
            v = self.bits(6)
            t1, tc = v & 3, (v >> 2) + 1
            if t1 > tc:
                tc = 0
        else:
            a = self.peek32()
            node = t.node[nC]
            for lvl in range(4):
                sym = t.tree[node * 16 + ((a >> (28 - 4 * lvl)) & 15)]
                if sym >= 0:
                    break
                node = -sym
                if node >= 49:
                    break
            if sym < 0:
                raise ValueError('bad coeff_token at %d' % self.p)
            self.p += t.iw[t.lens[nC] + sym]
            t1, tc = sym & 3, sym >> 2
        if tc == 0:
            return 0
        n = tc
        zl = self.tab(*t.tz[tc - 1]) if tc < 16 else 0
        if tc + zl > 16:
            raise ValueError('total_zeros overflow at %d' % self.p)
        sl = 0
        while True:
            if t1 > 0:
                t1 -= 1
                self.p += 1
            else:
                a = self.peek32()
                if a == 0:
                    raise ValueError('level prefix overflow at %d' % self.p)
                z = 32 - a.bit_length()
                self.p += z + 1
                if z == 15:
                    lc = self.bits(11) + (15 << sl)
                elif z > 15:
                    raise ValueError('level prefix %d at %d' % (z, self.p))
                else:
                    lc = (self.bits(sl) if sl else 0) + (z << sl)
                self.p += 1                       # sign
                if lc > t.thr[sl]:
                    sl += 1
            n -= 1
            if n == 0:
                return tc
            if zl == 0:
                continue
            if zl > 6:
                a = self.peek32()
                z = 32 - a.bit_length()
                run = z + 4 if z >= 3 else 7 - (a >> 29)
                self.p += z + 1 if z >= 3 else 3
            else:
                run = self.tab(*t.rb[zl - 1])
            if run > zl:
                raise ValueError('run_before overflow at %d' % self.p)
            zl -= run

    def residual(self, w, h, ny, nc):
        nz = self.nz
        for by in range(0, h, 8):
            for bx in range(0, w, 8):
                idx = self.ue()
                if idx >= 32:
                    raise ValueError('cbp idx %d at %d' % (idx, self.p))
                cbp = self.t.cbp[idx]
                y0 = ny + (by >> 2) * NZ_SY + (bx >> 2)
                c0 = nc + (by >> 3) * NZ_SC + (bx >> 3)
                for k in range(4):
                    q = y0 + (k >> 1) * NZ_SY + (k & 1)
                    nz[q] = self.coef4x4((nz[q - 1] + nz[q - NZ_SY] + 1) >> 1) if cbp & (1 << k) else 0
                if cbp & 0x10:
                    nC = (nz[c0 - 1] + nz[c0 - NZ_SC] + 1) >> 1
                    tu = self.coef4x4(nC)
                    nz[c0] = (tu + self.coef4x4(nC) + 1) >> 1
                else:
                    nz[c0] = 0

    def mb(self, w, h, ny=0, nc=0):
        m = self.ue()
        if m >= (24 if w >= 8 and h >= 8 else 12):
            raise ValueError('mode %d at %d (%dx%d)' % (m, self.p, w, h))
        a, res = ACTION[GBA_MODE_MAP[m]]
        if a == 'V':
            if w == 2:
                raise ValueError('vsplit 2 at %d' % self.p)
            self.mb(w // 2, h, ny, nc)
            self.mb(w // 2, h, ny + 2, nc + 1)
        elif a == 'H':
            if h == 2:
                raise ValueError('hsplit 2 at %d' % self.p)
            self.mb(w, h // 2, ny, nc)
            self.mb(w, h // 2, ny + 2 * NZ_SY, nc + NZ_SC)
        elif a == 'D':
            self.se(); self.se()
        elif a == 'C':
            for _ in range(5):
                self.se()
        elif a == 'P':
            for _ in range(3):
                self.se()
        elif a == 'N' and w >= 4 and h >= 4:
            if self.ue() > 3 or self.ue() > 3:
                raise ValueError('intra mode at %d' % self.p)
        elif a == '4' and w >= 4 and h >= 4:
            for _ in range((w // 4) * (h // 4)):
                if not self.bits(1):
                    self.bits(3)
            if self.ue() > 3:
                raise ValueError('chroma mode at %d' % self.p)
        if w >= 8 and h >= 8:
            if res:
                self.residual(w, h, ny, nc)
            elif not (a == 'H' and h == 16) and not (a == 'V' and w == 16):
                for y in range(h // 4):
                    self.nz[ny + y * NZ_SY:ny + y * NZ_SY + w // 4] = bytes(w // 4)
                for y in range(h // 8):
                    self.nz[nc + y * NZ_SC:nc + y * NZ_SC + w // 8] = bytes(w // 8)

    def frame(self, width, height):
        """No frame header in VXGB; returns 0 (the QP is the VX header's +0x14)."""
        for my in range(height // 16):
            for mx in range(width // 16):
                self.mb(16, 16, NZY0 + my * 4 * NZ_SY + mx * 4, NZC0 + my * 2 * NZ_SC + mx * 2)
        return 0


def walk_segment_gb(video_swapped, tabs, start_bit, nframes, width, height, walker=None):
    """VXGB version of walk_segment. Pass walker to carry the nC grids over (continuous play);
    by default a fresh walker = zeroed grids, which is what the player has after power-on."""
    w = walker or PyWalkerGB(b'', tabs)
    w.d = video_swapped
    w.p = start_bit
    starts = [start_bit]
    for _ in range(nframes):
        w.frame(width, height)
        starts.append(w.p)
    return starts, [0] * nframes
