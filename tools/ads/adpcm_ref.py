"""Python reference of the Majesco ADPCM audio decoder ("Disney-style" variant, from the SRMTHFG build).
Slow; used to develop the C version in reference/majesco/."""
import struct

VLC = [0x01, 0x01, 0x01, 0x01, 0x12, 0x12, 0x23, 0x33]   # low nibble = length, >>3 = shift
PAL = [0xd8, 0x72, 0xe4, 0x4e, 0x78, 0xd2, 0xe1]
PFX = [(0, 2), (0, 2), (0, 2), (0, 2), (1, 3), (1, 3), (2, 3), (2, 3), (3, 4), (4, 4), (5, 4), (6, 4)]
ESC = [0x0c, 0x0d, 0x08, 0x06]
BLOCK = 0x404a

class Bits:
    def __init__(self, buf, off):
        self.buf, self.base, self.pos = buf, off, 0   # pos in bits
    def peek(self, n):
        v = 0
        for i in range(n):
            p = self.pos + i
            w = struct.unpack_from('<I', self.buf, self.base + (p >> 5) * 4)[0]
            v = (v << 1) | ((w >> (31 - (p & 31))) & 1)
        return v
    def get(self, n):
        v = self.peek(n); self.pos += n; return v

def sx(v, n): return v - (1 << n) if v >> (n - 1) else v

class Decoder:
    def __init__(self, buf, off):
        self.b = Bits(buf, off)
        self.step = 0; self.prev = 0; self.h0 = self.h1 = 0; self.f0 = self.f1 = self.f2 = 0
        self.s1 = self.s2 = 0; self.ctx = 0; self.tab = [0] * 64; self.left = 0
    def block_header(self):
        b = self.b
        if b.peek(1):
            b.get(1)
        else:
            b.get((32 - (b.pos & 31)) & 31)
            b.get(1)
            self.step = sx(b.get(24), 24)
            self.f2 = 0; self.prev = 1; self.f0 = self.f1 = 0; self.h0 = self.h1 = 1; self.s1 = self.s2 = 0
        for i in range(64):
            t = b.peek(4)
            if t < 12:
                k, n = PFX[t]; b.get(n); self.tab[i] = PAL[k]
            else:
                b.get(2)
                a = b.get(2); c = b.get(2); sel = b.get(1)
                m = (1 << a) | (1 << c)
                lo, hi = ESC[m & 3], ESC[m >> 2]
                if sel: f2v, f3v = hi >> 2, lo & 3
                else: f2v, f3v = lo & 3, hi >> 2
                self.tab[i] = (c << 2) | a | (f2v << 4) | (f3v << 6)
        self.left = BLOCK; self.ctx = 0
    def sample(self):
        b = self.b
        e = VLC[b.peek(3)]; b.get(e & 15)
        sym = (self.tab[self.ctx] >> (e >> 3)) & 3
        self.ctx = ((self.ctx << 2) & 0x3f) | sym
        st = self.step
        r4 = (st >> 8) + 0x74 + (0xf9 if sym & 1 else 0)
        mag = (((r4 & 0x7f) | 0x80) << 7) >> (14 - ((r4 >> 7) & 15))
        if sym & 2: mag = -mag
        lr = st >> 6
        if sym & 1:
            fp = 0x1400 if lr > 0x12e0 else lr + ((0x36e0 - lr) >> 5)
        else:
            fp = 0x220 if lr < 0x248 else lr + ((-(lr + 0x2c0)) >> 5)
        self.step = st + fp + ((-st) >> 6)
        pf = self.prev * self.f2
        acc = ((self.h1 * (self.f1 >> 2)) >> 11) + ((self.h0 * (self.f0 >> 2)) >> 11) + (pf >> 11)
        h = mag + (acc >> 1)
        self.h1 = self.h0
        self.h0 = h if h else 1
        sb = mag + (pf >> 12)
        o = int(h / 64) if h >= 0 else -((-h) // 64)
        o = max(-128, min(127, o))
        f0 = self.f0 - (self.f0 >> 8); f1 = self.f1 - (self.f1 >> 7); r5 = f0; r4 = f0
        sg = 1 if sb < 0 else 0
        if sb != 0:
            if sg == self.s1: r5 = r4 + 0xc0; r4 = -r4
            else: r5 = r4 - 0xc0
            if 0 <= r4 + 0x1fff <= 0x3fff: f1 += r4 >> 5
            elif r4 < 0: f1 -= 0x100
            else: f1 += 0xff
            f1 += -0x80 if sg != self.s2 else 0x80
        lim = 0x3c00 - f1
        if r5 > lim: r5 = lim
        self.f0, self.f1 = r5, f1
        self.f2 = self.f2 - (self.f2 >> 8) + (0x20 if (mag ^ self.prev) >= 0 else -0x20)
        self.s2 = self.s1; self.s1 = sg; self.prev = mag
        self.left -= 1
        return o
    def decode(self, n):
        out = bytearray()
        while n:
            if self.left == 0: self.block_header()
            k = min(n, self.left)
            for _ in range(k): out.append(self.sample() & 0xff)
            n -= k
        return out

def payload_off(buf, roff):
    n = struct.unpack_from('<H', buf, roff + 14)[0]
    return roff + ((16 + 2 * n + 3) & ~3), n
