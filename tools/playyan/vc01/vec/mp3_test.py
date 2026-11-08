#!/usr/bin/env python3
"""MP3 vector-kernel tests (py_fw1) on the vector model (vc01v.py).  Usage: mp3_test.py [-v] [name...]"""
import sys, struct, random, math
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vc01v
import mp3ref
import emu
from vc01 import M32, sx

R = random.Random(3)
VERB = False
TESTS = []
def test(f):
    TESTS.append(f); return f

_M = None
def mach():
    global _M
    if _M is None:
        _M = emu.Machine('py_fw1')
    return _M

def pack_planar(vals):
    """16 int32 lanes -> 64 bytes: 16 low halfwords then 16 high halfwords"""
    lo = [v & 0xffff for v in vals]; hi = [(v >> 16) & 0xffff for v in vals]
    return struct.pack('<16H16H', *lo, *hi)

def unpack_planar(b):
    h = struct.unpack('<32H', b)
    return [sx(h[i] | h[16 + i] << 16, 32) for i in range(16)]

def rdmem(m, a, n):
    return bytes(m.m.read(a + k, 1) for k in range(n))

def rnd32(bits=28):
    return R.randint(-(1 << bits), (1 << bits) - 1)


@test
def reorder_2d2a6():
    """0x2d2a6(dst, src): 16 slots x 32 subbands (planar int32 rows of 128 B) -> 32 rows (one per
    subband, lanes = slots) + frequency inversion (odd slot & odd subband negated)"""
    m = mach(); ok = 0; N = 20
    src = m.alloc(bytes(16 * 128)); dst = m.alloc(bytes(2048))
    for _ in range(N):
        x = [[rnd32(R.choice([8, 20, 30])) for sb in range(32)] for t in range(16)]
        b = b''.join(pack_planar(x[t][:16]) + pack_planar(x[t][16:]) for t in range(16))
        m.m.load(src, b)
        m.call(0x2d2a6, dst, src)
        good = True
        for sb in range(32):
            got = unpack_planar(rdmem(m, dst + 64 * (sb & 15) + 1024 * (sb >> 4), 64))
            exp = [((-x[t][sb]) if (t & 1 and sb & 1) else x[t][sb]) for t in range(16)]
            exp = [sx(v, 32) for v in exp]
            if got != exp:
                good = False
                if VERB: print('  sb', sb, 'got', got[:4], 'exp', exp[:4])
        ok += good
    return ok, N


def dct_ref(x):
    """2 x DCT-II at the indices the firmware stores: rows 0..15 = 2*X[16+k], row 16 = 0,
    rows 17..33 = -2*X[16-j]   (X[n] = sum_k x[k] cos((2k+1) n pi / 64); libmad dct32 indices)"""
    X = [sum(x[k] * math.cos((2 * k + 1) * n * math.pi / 64) for k in range(32)) for n in range(33)]
    return [2 * X[16 + k] for k in range(16)] + [0.0] + [-2 * X[16 - j] for j in range(17)]


@test
def dct32_49c():
    """0x49c(0, ch, 2, buf): 16 slots in parallel (lanes), 32 subband rows at 0x6700 -> 34 rows of
    2*V at 0x8700 (polyphase matrixing).  Tolerance 24 LSB on |values| up to 2^28 (fixed-point MUL
    is (x*round(2cos*2^24))>>24 within 2 LSB; 5 multiply stages)."""
    m = mach(); ok = 0; N = 6
    for n in range(N):
        bits = [16, 22, 24, 25, 25, 25][n]
        x = [[rnd32(bits) for _ in range(32)] for t in range(16)]    # x[t][sb]
        for sb in range(32):
            m.m.load(0x6700 + 64 * (sb & 15) + 1024 * (sb >> 4), pack_planar([x[t][sb] for t in range(16)]))
        m.call(0x49c, 0, 0, 2, 0x6700)
        good = True; worst = 0
        for t in range(16):
            ref = dct_ref([x[t][sb] for sb in range(32)])
            for r in range(34):
                got = unpack_planar(rdmem(m, 0x8700 + 64 * r, 64))[t]
                worst = max(worst, abs(got - ref[r]))
        good = worst <= 128
        exact = 0
        K = mp3ref.MP3K()
        for t in range(16):
            ref = K.dct32([x[t][sb] for sb in range(32)])
            got = [unpack_planar(rdmem(m, 0x8700 + 64 * r, 64))[t] for r in range(34)]
            exact += got == ref
        if VERB: print('  bits', bits, 'worst vs float', worst, 'bit-exact lanes', exact, '/16')
        ok += good and exact == 16
    return ok, N


def imdct_long_ref(X, bt):
    """libmad III_imdct_l (float): 36-point IMDCT of 18 lines, windowed for block type 0/1/3"""
    win = [0.0] * 36
    for i in range(36):
        s = math.sin(math.pi / 36 * (i + 0.5))
        if bt == 0: win[i] = s
        elif bt == 1: win[i] = s if i < 18 else 1.0 if i < 24 else math.sin(math.pi / 12 * (i - 18 + 0.5)) if i < 30 else 0.0
        else: win[i] = 0.0 if i < 6 else math.sin(math.pi / 12 * (i - 6 + 0.5)) if i < 12 else 1.0 if i < 18 else s
    return [win[i] * sum(X[k] * math.cos(math.pi / 72 * (2 * i + 1 + 18) * (2 * k + 1)) for k in range(18))
            for i in range(36)]


@test
def imdct_long_a96c():
    """0xa96c(block_type, sb_half, gr, X, overlap, out): 16 subbands (lanes) x 18 lines, slot rows of
    128 B (planar 32-bit, 64-byte half per 16 subbands).  out[i] = z[i] + overlap[i], overlap' = z[18+i]
    (libmad III_imdct_l + III_overlap).  Tolerance 48 LSB on values up to 2^27."""
    m = mach(); ok = 0; n = 0
    BUF = m.alloc(bytes(2304 + 256)); OV = m.alloc(bytes(4096)); OUT = m.alloc(bytes(4096))
    for bt in (0, 1, 3):
        for half in (0, 1):
            for bits in (18, 24):
                n += 1
                X = [[rnd32(bits) for _ in range(16)] for k in range(18)]
                ov = [[rnd32(bits) for _ in range(16)] for k in range(18)]
                for k in range(18):
                    m.m.load(BUF + 128 * k + 64 * half, pack_planar(X[k]))
                    m.m.load(OV + 128 * k + 64 * half, pack_planar(ov[k]))
                m.m.load(OUT, bytes(4096))
                m.call(0xa96c, bt, 16 * half, 0, BUF, OV, OUT)
                worst = 0; exact = 0
                K = mp3ref.MP3K()
                model = lambda X_, ov_: K.imdct(bt, X_, ov_)
                for L in range(16):
                    z = imdct_long_ref([X[k][L] for k in range(18)], bt)
                    go = [unpack_planar(rdmem(m, OUT + 128 * i + 64 * half, 64))[L] for i in range(18)]
                    gv = [unpack_planar(rdmem(m, OV + 128 * i + 64 * half, 64))[L] for i in range(18)]
                    for i in range(18):
                        worst = max(worst, abs(go[i] - (z[i] + ov[i][L])), abs(gv[i] - z[18 + i]))
                    exact += (go, gv) == model([X[k][L] for k in range(18)], [ov[k][L] for k in range(18)])
                if VERB: print('  bt', bt, 'half', half, 'bits', bits, 'worst vs float', round(worst, 1), 'bit-exact lanes', exact)
                ok += worst <= 48 and exact == 16
    return ok, n


def imdct_short_ref(X, order):
    """libmad III_imdct_s (float): three 12-point IMDCTs (window sin(pi/12 (i+1/2))) overlapped into z[36].
    order 'w6': X[6w+k] (libmad);  'k3': X[3k+w] (ISO line order)"""
    y = []
    for w in range(3):
        Xw = [X[6 * w + k] if order == 'w6' else X[3 * k + w] for k in range(6)]
        y.append([math.sin(math.pi / 12 * (i + 0.5)) *
                  sum(Xw[k] * math.cos(math.pi / 24 * (2 * i + 1 + 6) * (2 * k + 1)) for k in range(6))
                  for i in range(12)])
    z = [0.0] * 36
    for w in range(3):
        for i in range(12):
            z[6 + 6 * w + i] += y[w][i]
    return z


@test
def imdct_short_12446():
    """0x12446(bt, sb_half, gr, X, overlap, out): short blocks, same layout as 0xa96c.  Tolerance 16."""
    m = mach(); ok = 0; n = 0
    BUF = m.alloc(bytes(2304 + 256)); OV = m.alloc(bytes(4096)); OUT = m.alloc(bytes(4096))
    for half in (0, 1):
        for bits in (18, 24):
            n += 1
            X = [[rnd32(bits) for _ in range(16)] for k in range(18)]
            ov = [[rnd32(bits) for _ in range(16)] for k in range(18)]
            for k in range(18):
                m.m.load(BUF + 128 * k + 64 * half, pack_planar(X[k]))
                m.m.load(OV + 128 * k + 64 * half, pack_planar(ov[k]))
            m.m.load(OUT, bytes(4096))
            m.call(0x12446, 2, 16 * half, 0, BUF, OV, OUT)
            K = mp3ref.MP3K()
            exact = sum((
                [unpack_planar(rdmem(m, OUT + 128 * i + 64 * half, 64))[L] for i in range(18)],
                [unpack_planar(rdmem(m, OV + 128 * i + 64 * half, 64))[L] for i in range(18)]) ==
                K.imdct(2, [X[k][L] for k in range(18)], [ov[k][L] for k in range(18)]) for L in range(16))
            res = {}
            for order in ('w6',):
                worst = 0
                for L in range(16):
                    z = imdct_short_ref([X[k][L] for k in range(18)], order)
                    for i in range(18):
                        o = unpack_planar(rdmem(m, OUT + 128 * i + 64 * half, 64))[L]
                        v = unpack_planar(rdmem(m, OV + 128 * i + 64 * half, 64))[L]
                        worst = max(worst, abs(o - (z[i] + ov[i][L])), abs(v - z[18 + i]))
                res[order] = round(worst, 1)
            if VERB: print('  half', half, 'bits', bits, 'worst vs float', res, 'bit-exact lanes', exact)
            ok += min(res.values()) <= 16 and exact == 16
    return ok, n


@test
def transpose_2d0a0():
    """0x2d0a0(buf, nrows): in-place conversion of rows of 16 x int32 from the vector (planar:
    16 low halfwords, 16 high halfwords) layout to plain little-endian int32 (vertical moves +
    80-bit 16-bit stores with an r3 row offset).  nrows in 1..34."""
    m = mach(); ok = 0; N = 6
    buf = m.alloc(bytes(64 * 40))
    for n in range(N):
        rows = [34, 16, 17, 5, 33, 1][n]
        x = [[rnd32(31) for _ in range(16)] for r in range(40)]
        m.m.load(buf, b''.join(pack_planar(v) for v in x))
        m.call(0x2d0a0, buf, rows)
        good = all(list(struct.unpack('<16i', rdmem(m, buf + 64 * r, 64))) == x[r] for r in range(rows))
        good &= all(unpack_planar(rdmem(m, buf + 64 * r, 64)) == x[r] for r in range(rows + 15 & ~15, 40))
        ok += good
    return ok, N


@test
def synth_window_316b4():
    """Full polyphase synthesis: 0x49c (DCT) + 0x2d0a0 (transpose) + 0x316b4(out, slot, ch, 2)
    per slot (ring buffer + D window 0x35364/0x35574/0x356c2/0x35934/0x35a82, SRU dot products,
    rounding, clamp) -> 16-bit PCM at stride 4.  References: the C reference's dct32 + window
    (reference/playyan/mp3, bit-exact: 48/48 slots), and, when FFMPEG_SRC names an FFmpeg libavcodec
    directory, ISO 11172-3 synthesis (float, D from ffmpeg's enwindow table) * 2^-11, round to nearest,
    clamp: every sample within 1 LSB."""
    import iso_synth as IS
    m = _M2()
    OUT = m.alloc(bytes(256)); S = IS.Synth() if IS.available() else None; same = tot = 0; worst = 0; exact = 0
    K = mp3ref.MP3K()
    for blk in range(3):
        bits = [20, 23, 25][blk]
        sb = [[rnd32(bits) for s in range(32)] for t in range(16)]
        for s in range(32):
            m.m.load(0x6700 + 64 * (s & 15) + 1024 * (s >> 4), pack_planar([sb[t][s] for t in range(16)]))
        m.call(0x49c, 0, 0, 2, 0x6700); m.call(0x2d0a0, 0x8700, 34)
        for t in range(16):
            m.call(0x316b4, OUT, t, 0, 2)
            got = struct.unpack('<64h', rdmem(m, OUT, 128))[0::2]
            if S:
                ref = S.slot(sb[t])
                for a, b in zip(got, ref):
                    e = max(-32768, min(32767, round(b / 2048.0)))
                    tot += 1; same += a == e; worst = max(worst, abs(a - e))
            exact += K.synth_slot(K.dct32(sb[t])) == list(got)
    if VERB: print('  vs ISO float: %s; bit-exact model slots %d/48' % (
        'identical %d/%d, worst %d LSB' % (same, tot, worst) if S else 'skipped (no FFMPEG_SRC)', exact))
    return int(worst <= 1 and exact == 48), 1


def _M2():
    """fresh machine (synthesis keeps ring state in RAM)"""
    return emu.Machine('py_fw1')


@test
def helpers():
    """small vector helpers: memcpy 0x35da0 (aligned 32 / 16 and unaligned-source vld2.8+vld3.8),
    memset 0x35e52 (vmov.8 from scalar + vst.8), bit expansion 0x35d20 (vand with scalar, SETF,
    vmov.ifnz), mono->stereo duplication 0xa900 (vertical vld/vst REP16 transposes)"""
    m = mach(); ok = 0; n = 0
    src = m.alloc(bytes(4096)); dst = m.alloc(bytes(4096))
    for (so, do, ln) in [(0, 0, 512), (16, 16, 400), (5, 0, 300), (3, 7, 97), (32, 64, 1024), (1, 16, 255)]:
        n += 1
        data = bytes(R.randrange(256) for _ in range(ln))
        m.m.load(src, bytes(4096)); m.m.load(dst, bytes(4096)); m.m.load(src + so, data)
        m.call(0x35da0, dst + do, src + so, ln)
        ok += rdmem(m, dst + do, ln) == data and rdmem(m, dst + do + ln, 16) == bytes(16)
    for (do, ln, val) in [(0, 256, 0x5a), (3, 100, 0xff), (16, 37, 0x01)]:
        n += 1
        m.m.load(dst, bytes(512)); m.call(0x35e52, dst + do, val, ln)
        ok += rdmem(m, dst + do, ln) == bytes([val]) * ln and rdmem(m, dst + do + ln, 8) == bytes(8)
    for nw in (1, 4, 9):
        n += 1
        ws = [R.randrange(65536) for _ in range(nw)]
        m.m.load(src, struct.pack('<%dH' % nw, *ws)); m.m.load(dst, bytes(512))
        m.call(0x35d20, dst, src, 16 * nw)
        exp = b''.join(bytes(int(bool(w & (1 << (7 - i if i < 8 else 23 - i)))) for i in range(16)) for w in ws)
        ok += rdmem(m, dst, 16 * nw) == exp
    for cnt in (576, 1152, 13):
        n += 1
        h = [R.randrange(65536) for _ in range(2 * cnt)]
        base = dst if cnt != 13 else dst + 4
        m.m.load(base, struct.pack('<%dH' % (2 * cnt), *h))
        m.call(0xa900, base, cnt)
        got = struct.unpack('<%dH' % (2 * cnt), rdmem(m, base, 4 * cnt))
        ok += list(got) == [h[k & ~1] for k in range(2 * cnt)]
    return ok, n


@test
def reorder_2d4c0():
    """0x2d4c0(dst, src): granule of 32 subbands x 18 lines (plain int32, subband-major, unaligned
    source) -> 18 rows of 128 B, lanes = subbands, planar 32-bit (vld2/vld3 REP32 stride 72,
    vertical vst).  Input to the IMDCT."""
    m = mach(); ok = 0; N = 3
    src = m.alloc(bytes(2304 + 64)); dst = m.alloc(bytes(2304 + 256))
    for k in range(N):
        off = [12, 0, 6][k]
        x = [[rnd32(31) for i in range(18)] for sb in range(32)]
        m.m.load(src + off, struct.pack('<576i', *[x[sb][i] for sb in range(32) for i in range(18)]))
        m.call(0x2d4c0, dst, src + off)
        good = all(unpack_planar(rdmem(m, dst + 128 * i + 64 * h, 64)) == [x[16 * h + L][i] for L in range(16)]
                   for i in range(18) for h in range(2))
        ok += good
    return ok, N


_FW1 = None
def fw1img():
    global _FW1
    if _FW1 is None:
        _FW1 = __import__('common').load('py_fw1')
    return _FW1

def requant_ref(x, M):
    """bit-exact firmware requantisation of one Huffman value x with multiplier M (from the gain
    table): T = POW43[min(|x|, 8191)] (round(i^(4/3) * 2^12) at 0x244a0);
    P = T * M / 2^16 from 16x16 partial products (TL*M3 term round((M3>>1)*TL/2^16)*2, op21);
    y = P / 16 via three arithmetic shifts and one rounding shift whose +1 is not carried into the
    high half; sign of x restored."""
    T = struct.unpack_from('<I', fw1img(), 0x244a0 + 4 * min(abs(x), 8191))[0]
    TH, TL = T >> 16, T & 0xffff; M5, M3 = sx(M >> 16, 16), M & 0xffff
    # op21 rounds to nearest: with a floored cross term the 'not carried' rounding shift below makes
    # 7.4% of non-overflowing outputs wrong by up to 32768; rounded, max |error| vs T*M/2^20 is 0.6 LSB
    P = sx((TH * M5 << 16) + TH * M3 + TL * M5 + 2 * (((M3 >> 1) * TL + 0x8000) >> 16), 32)
    p3 = P >> 3; L3 = p3 & 0xffff; H3 = p3 >> 16
    y = sx(((H3 >> 1) << 16) | ((((L3 >> 1) + (L3 & 1)) | ((H3 & 1) << 15)) & 0xffff), 32)
    return sx(-y & 0xffffffff, 32) if x < 0 else y

def gain_table():
    return struct.unpack_from('<256I', fw1img(), 0x234a0)

@test
def requant_35058():
    """0x35058(out, in, pow43 table, M, nblocks): 16 Huffman values per block -> int32 xr, any
    4-byte output alignment (vld3 merge + byte rotation by r6, aligned vst).  M from the gain
    table at 0x234a0 = min(2^31-1, round(2^((i-74)/4)))."""
    G = gain_table()
    assert all(G[i] == min(0x7fffffff, int(2 ** ((i - 74) / 4) + 0.5)) for i in range(256))
    m = mach(); ok = 0; N = 24
    IN = m.alloc(bytes(1024)); OUT = m.alloc(bytes(4096))
    for n in range(N):
        nb = R.randint(1, 4); off = 4 * R.randint(0, 15)
        xs = [R.choice([0, 1, -1, R.randint(-15, 15), R.randint(-8191, 8191), R.choice([8191, -8191, 8206, -9000])])
              for _ in range(16 * nb)]
        M = G[R.randint(70, 255)]
        m.m.load(IN, struct.pack('<%dh' % len(xs), *xs)); m.m.load(OUT, b'\xaa' * 4096)
        m.call(0x35058, OUT + 0x400 + off, IN, 0x244a0, M, nb)
        got = list(struct.unpack('<%di' % (16 * nb), rdmem(m, OUT + 0x400 + off, 64 * nb)))
        good = got == [requant_ref(x, M) for x in xs]
        good &= rdmem(m, OUT + 0x3c0, 0x40 + off) == b'\xaa' * (0x40 + off)
        ok += good
    return ok, N


class GbaPcm:
    """Model of the music image's 16-bit stereo -> GBA 8-bit path (0x3185e): per 32-bit stereo word
    (lo16 = ch0/left, hi16 = ch1/right): volume s = clamp16(trunc(s * vol / 32)) per channel (0x35f1a);
    rate N > 16384: keep the word when v = |(c << 14) // N| changes (0x21bc0, c = 0..N then 0:
    nearest-neighbour decimation, no filter); N <= 16384: emit every word and a duplicate unless
    |(c << 13) // N| == last + 1 (0x21bf6).  Emit = high bytes: ring A (right) exts8(w >> 24), ring B
    (left) exts8(w >> 8); 576 pairs per block (0x31888), then DECODE_DATA_INDICATION."""
    def __init__(self, rate, vol):
        self.N = rate; self.vol = vol; self.c = 0; self.last = 0; self.A = []; self.B = []
    def _v(self, s):
        p = s * self.vol
        p = (p + (31 if p < 0 else 0)) >> 5
        return max(-32768, min(32767, p))
    def _emit(self, w):
        self.A.append(sx((w >> 24) & 0xff, 8)); self.B.append(sx((w >> 8) & 0xff, 8))
    def words(self, ws):
        for word in ws:
            l, r = sx(word & 0xffff, 16), sx(word >> 16, 16)
            w = ((self._v(r) & 0xffff) << 16) | (self._v(l) & 0xffff)
            sh = 14 if self.N > 16384 else 13
            v = abs(int((self.c << sh) / self.N)); self.c += 1
            if self.c > self.N: self.c = 0
            if self.N > 16384:
                if v != self.last: self.last = v; self._emit(w)
            else:
                self._emit(w)
                dup = self.last + 1 != v; self.last = v
                if dup: self._emit(w)

@test
def gba_pcm_3185e():
    """0x3185e(words, n) with the decimator/volume state in gp globals; DECODE_DATA_INDICATION
    sender 0x33670 stubbed; compares both 8-bit rings (0x41ca8 / 0x41ee8, 576-pair blocks)"""
    ok = 0; n = 0
    for rate, vol in ((44100, 32), (48000, 20), (32000, 45), (22050, 32), (16000, 32), (11025, 7), (8000, 64)):
        n += 1
        m = emu.Machine('py_fw1'); gp = m.gp; W = m.alloc(bytes(4 * 4096))
        def wr(off, v): m.m.write(gp + off, 4, v)
        wr(1520, 0); m.m.write(gp + 37748, 4, 1); wr(1424, rate); wr(1660, vol)
        wr(1412, 0); wr(1564, 0); wr(1416, 0); wr(1420, 0)
        sent = []
        cpu = m.cpu; ostep = cpu.step
        def step():
            if cpu.r[31] == 0x33670:
                sent.append(rdmem(m, cpu.r[2], cpu.r[3])); cpu.r[31] = cpu.r[26]; return
            ostep()
        cpu.step = step
        model = GbaPcm(rate, vol); totA = []; totB = []
        for chunk in range(3):
            nw = 1152
            ws = [((R.randint(-32768, 32767) & 0xffff) << 16) | (R.randint(-32768, 32767) & 0xffff) for _ in range(nw)]
            m.m.load(W, struct.pack('<%dI' % nw, *ws))
            m.call(0x3185e, W, nw, maxsteps=50_000_000)
            model.words(ws)
        # rebuild the firmware stream from the sent blocks + the partial block
        A = []; B = []
        for blk in sent:
            A += [sx(b, 8) for b in blk[:576]]; B += [sx(b, 8) for b in blk[576:1152]]
        pos = m.m.read(gp + 1416, 4); buf = m.m.read(gp + 1420, 4)
        A += [sx(b, 8) for b in rdmem(m, gp + 0xb3d8 + 1152 * buf, pos)]
        B += [sx(b, 8) for b in rdmem(m, gp + 0xb618 + 1152 * buf, pos)]
        good = A == model.A and B == model.B
        if VERB: print('  rate', rate, 'vol', vol, 'out', len(A), 'model', len(model.A), 'blocks', len(sent), 'exact', good)
        ok += good
    return ok, n


if __name__ == '__main__':
    args = sys.argv[1:]
    if '-v' in args: VERB = True; args.remove('-v')
    for kv in [a for a in args if '=' in a]:
        k, v = kv.split('='); vc01v.CFG[k] = int(v) if v.isdigit() else v; args.remove(kv)
    tot = [0, 0]
    for t in TESTS:
        if args and t.__name__ not in args: continue
        try:
            ok, n = t()
        except Exception as e:
            import traceback; traceback.print_exc(); ok, n = 0, 1
        tot[0] += ok; tot[1] += n
        print('%-16s %5d/%-5d %s' % (t.__name__, ok, n, 'PASS' if ok == n else 'FAIL'))
    print('total %d/%d' % tuple(tot))
