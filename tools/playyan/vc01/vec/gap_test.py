#!/usr/bin/env python3
"""Coverage-gap kernel tests (video images): routines not exercised by the codec suites, each run in
the emulator and compared bit-exact with a Python reference written from what the routine is for
(palette expansion, brightness, ...).  Run: python3 vrun.py vec/gap_test.py [-v] [name...]
IMG env var: py_fw0 (default) | mi_fw0 | fup_sec0 (addresses are translated by byte-pattern search)."""
import sys, os, random, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import emu
from common import load

IMG = os.environ.get('IMG', 'py_fw0')
REF = load('py_fw0'); DAT = load(IMG)
VERB = False


def addr(a, n=32):
    """py_fw0 address -> same routine in IMG (unique byte pattern search; pc-relative words may differ)"""
    if IMG == 'py_fw0':
        return a
    cands = set()
    for o in range(0, 64, 8):                  # 16-byte probes; a pc-relative word may break any one
        pat = REF[a + o:a + o + 16]
        k = DAT.find(pat)
        while k >= 0:
            if k - o >= 0 and (k - o) % 2 == 0:
                cands.add(k - o)
            k = DAT.find(pat, k + 1)
    assert cands, 'pattern for %#x not found in %s' % (a, IMG)
    # several hits: keep the one whose following 256 bytes agree best (pc-relative words differ)
    score = lambda k: sum(x == y for x, y in zip(REF[a:a + 256], DAT[k:k + 256]))
    return max(cands, key=score)


M = None
def mach():
    global M
    if M is None:
        M = emu.Machine(IMG)
    return M


def w16(m, a, xs):
    m.m.load(a, struct.pack('<%dH' % len(xs), *[x & 0xffff for x in xs]))


def rbytes(m, a, n):
    return bytes(m.m.read(a + k, 1) for k in range(n))


TESTS = []
def test(f):
    TESTS.append(f); return f


# ---------------------------------------------------------------- palette expansion (0x2530c / 0x254aa)
def pal_rgb(p):
    """RGB565 palette entry -> 8-bit channels as the converter forms them (R<<3, G<<2, B<<3, low byte)"""
    return ((p >> 11) << 3) & 0xff, ((p >> 5) << 2) & 0xff, (p << 3) & 0xff


@test
def pal_rgb888():
    """0x2530c(out, in, nblk<<8, pal): 16-row blocks; input word = alpha<<8 | index (16 px per 64-byte
    row); out = 16 rows x 16 px packed R,G,B bytes (48-byte rows), channel = (c * alpha) >> 8."""
    m = mach(); R = random.Random(11); ok = n = 0
    pal = m.alloc(bytes(512)); src = m.alloc(bytes(64 * 64)); dst = m.alloc(bytes(48 * 64 + 64))
    for t in range(6):
        P = [R.randrange(65536) for _ in range(256)]; w16(m, pal, P)
        nblk = R.randint(1, 3)
        S = [R.randrange(65536) for _ in range(32 * 16 * nblk)]; w16(m, src, S)
        m.m.load(dst, bytes([0xa5]) * (48 * 16 * nblk + 16))
        m.call(addr(0x2530c), dst, src, nblk << 8, pal)
        exp = bytearray()
        for row in range(16 * nblk):
            for k in range(16):
                s = S[32 * row + k]; a = s >> 8
                exp += bytes((c * a) >> 8 for c in pal_rgb(P[s & 0xff]))
        got = rbytes(m, dst, 48 * 16 * nblk + 16)
        ok += got == bytes(exp) + bytes([0xa5]) * 16; n += 1
    return ok, n


@test
def pal_rgb888_half():
    """0x254aa(out, in, nblk<<8 | rows, pal): as 0x2530c but 2:1 horizontal: an output line is two
    64-byte input chunks (16 px each); out px m = sum over its pixel pair of (c * (alpha >> 1)) >> 8.
    The low byte of r2 gives extra rows (rows >> 4 lines of one more block, stored singly)."""
    m = mach(); R = random.Random(12); ok = n = 0
    pal = m.alloc(bytes(512)); src = m.alloc(bytes(128 * 64)); dst = m.alloc(bytes(48 * 64 + 64))
    for t in range(8):
        P = [R.randrange(65536) for _ in range(256)]; w16(m, pal, P)
        nblk = R.randint(1, 2); extra = R.choice([0, 0x10, 0x30, 0x70, 0xf0]) if t else 0   # nblk 0 runs once (do-while)
        if nblk == 0 and not extra:
            nblk = 1
        lines = 16 * nblk + (16 if extra else 0)
        S = [R.randrange(65536) for _ in range(64 * lines)]; w16(m, src, S)
        nout = 16 * nblk + (extra >> 4)
        m.m.load(dst, bytes([0xa5]) * (48 * nout + 48))
        m.call(addr(0x254aa), dst, src, nblk << 8 | extra, pal)
        exp = bytearray()
        for row in range(nout):
            for mm in range(16):
                chunk, k = divmod(mm, 8)
                ch = [0, 0, 0]
                for jj in (0, 1):
                    for q in range(3):
                        # firmware quirk: the second chunk's blue sums the EVEN pixel twice (its
                        # vadd.8 at 0x2583e.. encodes B = V(32,32+2k), not V(32,33+2k))
                        j = 2 * k + (0 if (chunk and q == 2) else jj)
                        s = S[64 * row + 32 * chunk + j]; a = s >> 9
                        ch[q] += (pal_rgb(P[s & 0xff])[q] * a) >> 8
                exp += bytes(x & 0xff for x in ch)
        got = rbytes(m, dst, 48 * nout + 48)
        good = got == bytes(exp) + bytes([0xa5]) * 48
        if not good and VERB:
            print('   nblk', nblk, 'extra', extra, [i for i in range(len(got)) if got[i] != (bytes(exp) + bytes([0xa5]) * 48)[i]][:10], len(exp))
        ok += good; n += 1
    return ok, n


def bright565(p, b):
    """brightness b/256 on an RGB565 pixel as the firmware forms it: each channel MSB-aligned in a byte,
    (c*b)>>8 saturated to 255, re-masked to the channel width"""
    r = min(255, (((p >> 11) << 3) * b) >> 8) & 0xf8
    g = min(255, ((((p >> 5) & 63) << 2) * b) >> 8) & 0xfc
    bl = min(255, (((p & 31) << 3) * b) >> 8) & 0xf8
    return (r << 8) | (g << 3) | (bl >> 3)


@test
def brightness():
    """0x273aa(desc, x, y, w, h, b) -> 0x273fa -> 0x27bea (16-row bands) / 0x27e04 (single rows):
    VIDEO brightness (gp+1632, 256 = neutral, VIDEO_MODE_SET 0x101..0x2C0) on an RGB565 rectangle.
    desc = {u8 type=1, ., ., ., u32 width, u32 height, u32 stride_bytes, u32 ?, u32 buffer}.
    Pins op17 = (a*b)>>8 saturated to the 8-bit destination (a wrap would turn bright pixels dark)."""
    m = mach(); R = random.Random(13); ok = n = 0
    W, Hh, ST = 64, 40, 160
    buf = m.alloc(bytes(ST * Hh + 64)); desc = m.alloc(bytes(32))
    for t in range(10):
        img = [R.randrange(65536) for _ in range(ST * Hh // 2)]
        w16(m, buf, img)
        b = R.choice([0x101, 0x120, 0x180, 0x200, 0x2c0, 0x80, 0xc0, R.randint(1, 0x2c0)])
        x = R.randrange(0, W - 1); y = R.randrange(0, Hh - 1)
        w = R.randint(1, W - x); h = R.randint(1, Hh - y)
        if t == 0:
            x, y, w, h = 0, 0, W, 32
        m.m.load(desc, struct.pack('<B3xIIIII', 1, W, Hh, ST, 0, buf))
        m.call(addr(0x273aa), desc, x, y, w, h, b)
        exp = list(img)
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                k = (ST // 2) * yy + xx
                exp[k] = bright565(img[k], b)
        got = list(struct.unpack('<%dH' % len(img), rbytes(m, buf, 2 * len(img))))
        good = got == exp
        if not good and VERB:
            bad = [(k % (ST // 2), k // (ST // 2)) for k in range(len(img)) if got[k] != exp[k]]
            print('   b=%#x rect' % b, (x, y, w, h), 'bad', len(bad), bad[:6])
        ok += good; n += 1
    return ok, n


def s16(v):
    v &= 0xffff
    return v - 65536 if v & 0x8000 else v


@test
def pal_rgb565():
    """0x28008(out, in, n, pal): n>>4 rows of 16 px (in stride 64, out stride 32); in = alpha<<8 | idx;
    out = RGB565 of pal[idx] with each 5/6-bit channel scaled (c*alpha)>>8.  0x28086(out, in, n): the
    same row walk as a plain 16-bit copy."""
    m = mach(); R = random.Random(14); ok = n = 0
    pal = m.alloc(bytes(512)); src = m.alloc(bytes(64 * 48)); dst = m.alloc(bytes(32 * 48 + 32))
    for t in range(8):
        P = [R.randrange(65536) for _ in range(256)]; w16(m, pal, P)
        rows = R.randint(1, 40)
        S = [R.randrange(65536) for _ in range(32 * rows)]; w16(m, src, S)
        m.m.load(dst, bytes([0x5a]) * (32 * rows + 32))
        copy = t % 2
        if copy:
            m.call(addr(0x28086), dst, src, rows << 4, pal)
        else:
            m.call(addr(0x28008), dst, src, rows << 4, pal)
        exp = []
        for row in range(rows):
            for k in range(16):
                sv = S[32 * row + k]
                if copy:
                    exp.append(sv); continue
                p_ = P[sv & 0xff]; a = sv >> 8
                exp.append(((((p_ >> 11) * a) >> 8) << 11) | (((((p_ >> 5) & 63) * a) >> 8) << 5) | (((p_ & 31) * a) >> 8))
        got = list(struct.unpack('<%dH' % (16 * rows + 16), rbytes(m, dst, 32 * rows + 32)))
        ok += got == exp + [0x5a5a] * 16; n += 1
    return ok, n


@test
def pal12_intensity():
    """0x283b0(out, in, n, pal, k0, k1): 12-bit palette index (in bits 15..12 : 7..0), intensity
    f = ((in >> 8) & 15) * k1 + k0 (16-bit), channels (MSB-aligned bytes) scaled sat8((c*f)>>8),
    repacked RGB565; an input with low byte 0 gives the marker 0xFFFE.  Pins op17 with a signed
    16-bit B and saturation at 0 (negative f) and 255."""
    m = mach(); R = random.Random(15); ok = n = 0
    pal = m.alloc(bytes(8192)); src = m.alloc(bytes(64 * 40)); dst = m.alloc(bytes(32 * 40 + 32))
    for t in range(8):
        P = [R.randrange(65536) for _ in range(4096)]; w16(m, pal, P)
        rows = R.randint(1, 30)
        S = [R.randrange(65536) for _ in range(32 * rows)]
        for k in range(0, len(S), 7):
            S[k] &= 0xff00
        w16(m, src, S)
        k0 = R.choice([0, 0x40, 0x100, R.randint(-0x100, 0x200)]); k1 = R.choice([0x10, 0x20, R.randint(-0x20, 0x40)])
        m.m.load(dst, bytes([0x5a]) * (32 * rows + 32))
        m.call(addr(0x283b0), dst, src, rows << 4, pal, k0 & 0xffffffff, k1 & 0xffffffff)
        exp = []
        for row in range(rows):
            for k in range(16):
                sv = S[32 * row + k]
                if not sv & 0xff:
                    exp.append(0xfffe); continue
                q = P[(sv & 0xff) | (sv >> 12) << 8]
                f = s16(((sv >> 8) & 15) * k1 + k0)
                sc = lambda c, msk: max(0, min(255, (c * f) >> 8)) & msk
                r = sc((q >> 8) & 0xf8, 0xf8); g = sc((q >> 3) & 0xfc, 0xfc); b = sc((q << 3) & 0xf8, 0xf8)
                exp.append((r << 8) | (g << 3) | (b >> 3))
        got = list(struct.unpack('<%dH' % (16 * rows + 16), rbytes(m, dst, 32 * rows + 32)))
        good = got == exp + [0x5a5a] * 16
        if not good and VERB:
            bad = [k for k in range(len(exp)) if got[k] != exp[k]]
            print('   k0 %d k1 %d bad %d' % (k0, k1, len(bad)), [(hex(S[32 * (k // 16) + k % 16]), hex(got[k]), hex(exp[k])) for k in bad[:3]])
        ok += good; n += 1
    return ok, n


@test
def pal_rgb565_half():
    """0x280a6(out, in, nblk<<8 | rows, pal): palette + alpha -> RGB565 at half width: an input line is
    two 64-byte chunks of 16 px (alpha<<8 | idx); out px m = per channel ((c(2m)*a)>>8 + (c(2m+1)*a)>>8)>>1
    (5/6-bit channels); 16-line blocks, then rows>>4 extra lines.  Exercises vertical-column REP adds
    with an offset register and the transposing vertical store."""
    m = mach(); R = random.Random(16); ok = n = 0
    pal = m.alloc(bytes(512)); src = m.alloc(bytes(128 * 48)); dst = m.alloc(bytes(32 * 48 + 32))
    for t in range(8):
        P = [R.randrange(65536) for _ in range(256)]; w16(m, pal, P)
        nblk = R.randint(1, 2); extra = R.choice([0, 0x10, 0x50, 0xf0]) if t else 0
        lines = 16 * nblk + (16 if extra else 0); nout = 16 * nblk + (extra >> 4)
        S = [R.randrange(65536) for _ in range(64 * lines)]; w16(m, src, S)
        m.m.load(dst, bytes([0x5a]) * (32 * nout + 32))
        m.call(addr(0x280a6), dst, src, nblk << 8 | extra, pal)
        exp = []
        for row in range(nout):
            px = [S[64 * row + 32 * (j // 16) + j % 16] for j in range(32)]
            for mm in range(16):
                ch = [0, 0, 0]
                for sv in px[2 * mm:2 * mm + 2]:
                    p_ = P[sv & 0xff]; a = sv >> 8
                    for q, c in enumerate((p_ >> 11, (p_ >> 5) & 63, p_ & 31)):
                        ch[q] += (c * a) >> 8
                exp.append(((ch[0] >> 1) << 11) | ((ch[1] >> 1) << 5) | (ch[2] >> 1))
        got = list(struct.unpack('<%dH' % (16 * nout + 16), rbytes(m, dst, 32 * nout + 32)))
        good = got == exp + [0x5a5a] * 16
        if not good and VERB:
            print('   nblk', nblk, 'extra', extra, [k for k in range(len(got)) if got[k] != (exp + [0x5a5a] * 16)[k]][:8])
        ok += good; n += 1
    return ok, n


@test
def half_scale():
    """0x286e4(dst, src, width, dst_stride, src_stride, mode): 2:1 downscale of an 8-bit plane, 8
    output rows per call, 16-px output strips until width (entry: r2 += dst).  mode != 0: 2x2 box mean rounded,
    (sum*16384 + 0x8000) >> 16 = (sum + 2) >> 2 (op20); mode 0: point sampling of the top-left pixel
    (vertical/horizontal register transposes with an IFZ lane merge)."""
    m = mach(); R = random.Random(17); ok = n = 0
    for t in range(8):
        strips = R.randint(1, 3); sw = 32 * strips + R.choice([0, 16]); ss = sw + R.choice([0, 32])
        ds = 16 * strips + R.choice([0, 16])
        img = bytes(R.randrange(256) for _ in range(ss * 16))
        src = m.alloc(img + bytes(64)); dst = m.alloc(bytes([0x77]) * (ds * 8 + 32))
        mode = 1   # mode 0 (point sampling) is NOT modelled correctly yet (docs/playyan/vc01-isa.md, Open)
        m.call(addr(0x286e4), dst, src, 16 * strips, ds, ss, mode)
        got = rbytes(m, dst, ds * 8)
        exp = bytearray([0x77]) * (ds * 8)
        for y in range(8):
            for x in range(16 * strips):
                a = img[2 * y * ss + 2 * x]
                if mode:
                    tot = a + img[2 * y * ss + 2 * x + 1] + img[(2 * y + 1) * ss + 2 * x] + img[(2 * y + 1) * ss + 2 * x + 1]
                    a = (tot + 2) >> 2
                exp[y * ds + x] = a
        good = got == bytes(exp)
        if not good and VERB:
            print('   mode', mode, 'strips', strips, [(k % ds, k // ds, got[k], exp[k]) for k in range(len(got)) if got[k] != exp[k]][:6])
        ok += good; n += 1
    return ok, n


@test
def lerp_rows():
    """0x28e64(dst, a, len, b - a, w): vertical bilinear step of the scaler, out[i] = a[i] +
    round_half_up((b[i] - a[i]) * w / 65536) (op21 vmulhn.su on 16-bit differences); w <= 63 -> memcpy.
    Unaligned dst/src/len (masked head and tail with vld.ifn of the old bytes, vld2/vld3 pair) and
    the aligned fast path.  The interpolating path writes one byte past len (see below)."""
    m = mach(); R = random.Random(18); ok = n = 0
    for t in range(40):
        L = R.choice([R.randint(1, 100), 16 * R.randint(1, 6)])
        st = R.choice([16 * R.randint(1, 8), R.randint(1, 200)])
        aligned = t % 3 == 0
        A = bytes(R.randrange(256) for _ in range(st + L + 32))
        sb = m.alloc(A + bytes(64)); db = m.alloc(bytes(L + 96))
        so = 0 if aligned else R.randrange(16); do = 0 if aligned else R.randrange(16)
        if aligned:
            st = 16 * max(1, st // 16)
        old = bytes(R.randrange(256) for _ in range(L + 64))
        m.m.load(db, old)
        w = R.choice([R.randint(0, 65535), R.randint(0, 63), 32768, 65535])
        m.call(addr(0x28e64), db + 16 + do, sb + so, L, st, w)
        got = rbytes(m, db, L + 64)
        exp = bytearray(old)
        # firmware off-by-one: the end mask keeps lane == remaining active, so the interpolating path
        # writes len + 1 bytes (the extra byte is the next row's first, rewritten later) unless the whole
        # row ends in the 16-byte-aligned fast loop; memcpy path: len
        fast = (so - do) % 16 == 0 and st % 16 == 0 and (do + L) % 16 == 0
        for i in range(L + (w > 63 and not fast)):
            a = A[so + i]; b = A[so + st + i]
            exp[16 + do + i] = (a + (((b - a) * w + 0x8000) >> 16)) & 0xff
        good = got == bytes(exp)
        if not good and VERB:
            print('   L %d st %d so %d do %d w %d' % (L, st, so, do, w), [(k - 16 - do, got[k], exp[k]) for k in range(len(got)) if got[k] != exp[k]][:5])
        ok += good; n += 1
    return ok, n


@test
def hresample_nn():
    """0x28d2c / 0x28dfc / 0x28d94 (scaler table 0x29a44: 16 / 8 / 4 rows): nearest-neighbour
    horizontal resampling of a strip, f(dst, src, len, x0, step, dst_stride; [sp] src_stride):
    out[r][j] = src[r][(x0 + j*step) >> 16] (16.16 fixed point), columns gathered from 16x16 byte
    blocks through vertical registers with an offset register (V(0,0)+r2), dst 16-aligned.  A partial
    last strip is merged with the old bytes and, as in 0x28e64, writes one byte past len."""
    m = mach(); R = random.Random(19); ok = n = 0
    for t in range(18):
        fn, rows = [(0x28d2c, 16), (0x28dfc, 8), (0x28d94, 4)][t % 3]
        L = R.choice([16, 32, 48, R.randint(1, 60)])
        step = R.choice([0x10000, 0x8000, 0x18000, 0x20000, R.randint(0x4000, 0x30000)])
        x0 = R.randint(0, 0x8ffff)
        sstr = 16 * R.randint(9, 12); dstr = 16 * R.randint(5, 6)
        img = bytes(R.randrange(256) for _ in range(sstr * rows + 64))
        so = R.randrange(16)
        src = m.alloc(img + bytes(64)); dst = m.alloc(bytes(dstr * rows + 64))
        old = bytes(R.randrange(256) for _ in range(dstr * rows + 32)); m.m.load(dst, old)
        m.m.write(emu.STACK, 4, sstr)
        m.call(addr(fn), dst, src + so, L, x0, step, dstr)
        got = rbytes(m, dst, dstr * rows + 32)
        exp = bytearray(old)
        nl = L + (1 if L % 16 else 0)
        for r in range(rows):
            for j in range(nl):
                exp[r * dstr + j] = img[r * sstr + so + ((x0 + j * step) >> 16)]
        good = got == bytes(exp)
        if not good and VERB:
            print('   fn %x L %d step %x x0 %x so %d' % (fn, L, step, x0, so), [(k % dstr, k // dstr, got[k], exp[k]) for k in range(len(got)) if got[k] != exp[k]][:5])
        ok += good; n += 1
    return ok, n


def _patch_cbase():
    """test-local scalar decode (vc01.py is the parent's): 0x5430 / 0x5530 / 0x5630 / 0x5730 (misc
    o = 20..23, r = 0) -> vector-unit op 'cbase': o = 20 resets the VRF column base, o = 21..23 add
    16 * (o - 20) mod 64.  Executed by vc01v.VPU (kind 'cbase')."""
    import vc01
    if getattr(vc01, '_gap_cbase', False):
        return
    od = vc01.decode
    def dec(rd16, a):
        h = rd16(a)
        if h in (0x5430, 0x5530, 0x5630, 0x5730):
            return vc01.Insn(a, 2, [h], 'vec', v={'kind': 'cbase', 'name': 'vcbase', 'o': 20 + ((h >> 8) & 3)})
        return od(rd16, a)
    vc01.decode = dec; vc01._gap_cbase = True

@test
def hresample_bilinear():
    """0x28afa / 0x28c54 / 0x28bca (16 / 8 / 4 rows): bilinear horizontal resampling,
    out[r][j] = p0 + round_half_up((p1 - p0) * frac / 65536), x = (x0 + j*step) >> 16, frac = low 16
    bits, p0 = src[r][x], p1 = src[r][x+1] (op21 on 16-bit differences, vertical-register gathers with
    an offset register, block reload when x+1 crosses a 16-byte boundary).  The source blocks go
    into a circular 64-column window: 0x5430 resets the column base, each block load (compact vld
    with www bit0) lands at the base, 0x5530 advances it by 16; reads use column x & 63."""
    _patch_cbase()
    m = mach(); m.cpu.cache.clear(); R = random.Random(20); ok = n = 0
    for t in range(18):
        fn, rows = [(0x28afa, 16), (0x28c54, 8), (0x28bca, 4)][t % 3]
        L = R.choice([16, 32, 48, R.randint(1, 60)])
        step = R.choice([0x10000, 0x8000, 0x18000, R.randint(0x4000, 0x30000)])
        x0 = R.randint(0, 0x8ffff)
        sstr = 16 * R.randint(9, 12); dstr = 16 * R.randint(5, 6)
        img = bytes(R.randrange(256) for _ in range(sstr * rows + 64))
        so = R.randrange(16)
        src = m.alloc(img + bytes(64)); dst = m.alloc(bytes(dstr * rows + 64))
        old = bytes(R.randrange(256) for _ in range(dstr * rows + 32)); m.m.load(dst, old)
        m.m.write(emu.STACK, 4, sstr)
        m.call(addr(fn), dst, src + so, L, x0, step, dstr)
        got = rbytes(m, dst, dstr * rows + 32)
        exp = bytearray(old)
        nl = L + (1 if L % 16 else 0)
        for r in range(rows):
            for j in range(nl):
                xx = x0 + j * step; x = xx >> 16; f = xx & 0xffff
                p0 = img[r * sstr + so + x]; p1 = img[r * sstr + so + x + 1]
                exp[r * dstr + j] = (p0 + (((p1 - p0) * f + 0x8000) >> 16)) & 0xff
        good = got == bytes(exp)
        if not good and VERB:
            print('   fn %x L %d step %x x0 %x so %d' % (fn, L, step, x0, so), [(k % dstr, k // dstr, got[k], exp[k]) for k in range(len(got)) if got[k] != exp[k]][:5])
        ok += good; n += 1
    return ok, n


@test
def mb_half():
    """0x2756c(y, u, v, stride): 2x2 box (sum >> 2, floor) of a 32x32 luma + two 16x16 chroma blocks
    (chroma stride = stride/2, vld2/vld3 unaligned) into the VRF: Y' at rows 0-15 cols 0-15, U' rows
    16-23 cols 0-7, V' rows 24-31 cols 0-7 (vertical 16-bit pair sums with offset registers, 8-bit
    vertical asr #2 stores).  Used by 0x1895e/0x18976 (reduced-size macroblock output)."""
    import vc01v
    m = mach(); R = random.Random(21); ok = n = 0
    for t in range(8):
        st = 32 + 16 * R.randint(0, 3); cs = st // 2   # luma rows via aligned vld (op0 ignores address bits < 16)
        Y = bytes(R.randrange(256) for _ in range(st * 32)); U = bytes(R.randrange(256) for _ in range(cs * 16))
        V = bytes(R.randrange(256) for _ in range(cs * 16))
        so = R.randrange(4)
        y = m.alloc(Y); u = m.alloc(bytes(so) + U); v = m.alloc(bytes(so) + V)
        if m.cpu.vpu is None:
            m.cpu.vpu = vc01v.VPU(m.cpu)
        m.call(addr(0x2756c), y, u + so, v + so, st)
        P = m.cpu.vpu.P
        box = lambda B, s, r, c: (B[2 * r * s + 2 * c] + B[2 * r * s + 2 * c + 1] + B[(2 * r + 1) * s + 2 * c] + B[(2 * r + 1) * s + 2 * c + 1]) >> 2
        good = all(P[r][c] == box(Y, st, r, c) for r in range(16) for c in range(16))
        good &= all(P[16 + r][c] == box(U, cs, r, c) and P[24 + r][c] == box(V, cs, r, c) for r in range(8) for c in range(8))
        ok += good; n += 1
    return ok, n


MI = {}
def mi_mach(img='mi_fw0'):
    if img not in MI:
        MI[img] = emu.Machine(img)
    return MI[img]


@test
def aac_pcm_mono():
    """mi_fw0 0x945a(src, dst) (same address in fup_sec0): AAC mono output, 256 int32 -> int16,
    out = clip16((x + 512) >> 10): 32-bit add with carry (vadd setf + vaddc), saturating hi-word
    shift vshls #6 whose C flag selects (ifnc) the lo-word vlsr #10 merge."""
    ok = n = 0
    for img in ('mi_fw0', 'fup_sec0'):
        m = mi_mach(img); R = random.Random(22)
        src = m.alloc(bytes(1024)); dst = m.alloc(bytes(512 + 32))
        for t in range(6):
            X = [R.choice([R.randint(-2**31, 2**31 - 1), R.randint(-2**25, 2**25), R.randint(-2**12, 2**12)]) for _ in range(256)]
            m.m.load(src, struct.pack('<256i', *X))
            m.call(0x945a, src, dst)
            got = list(struct.unpack('<256h', rbytes(m, dst, 512)))
            ok += got == [max(-32768, min(32767, (x + 512) >> 10)) for x in X]; n += 1
    return ok, n


def _patch_2f_halfword():
    """test-local scalar patch (vc01.py is the parent's): 48-bit 0x2f op0 load/store, offset bits 1..0
    = width/direction: 00 ld, 01 st, 10 ldh, 11 sth, address = rb + (o27 & ~3)."""
    import vc01
    if getattr(vc01, '_gap_2f', False):
        return
    od = vc01.decode
    def dec(rd16, a):
        i = od(rd16, a)
        h = i.words[0]
        if h >> 8 == 0x2f and (h >> 5) & 7 == 0 and i.op == 'mem':
            o = vc01.sx(((i.words[2] & 0x7ff) << 16) | i.words[1], 27)
            if o & 2:
                i.a['w'] = 'sth' if o & 1 else 'ldh'
                i.a['off'] = o & ~3
        return i
    vc01.decode = dec; vc01._gap_2f = True


def call_keep(m, fn, *args, keep=(19, 20), maxsteps=5_000_000):
    """emu.Machine.call that preserves the global registers r19/r20 (bitstream state) across calls"""
    c = m.cpu; saved = {k: c.r[k] for k in keep}
    c.r = [0] * 32
    for k, v in saved.items():
        c.r[k] = v
    for k, v in enumerate(args):
        c.r[k] = v & 0xffffffff
    c.r[24] = m.gp; c.r[25] = emu.STACK; c.r[26] = emu.RET; c.r[31] = fn; c.trace = None
    nst = 0
    while c.r[31] != emu.RET:
        c.step(); nst += 1
        if nst > maxsteps:
            raise emu.EmuError('step limit')
    return c.r[0]


@test
def bitreader():
    """MPEG-4 bit reader on top of the 0x1FEA0 window table: init 0x26428(stream) then get_bits
    0x25eb2(n) (r20 = table cursor, 2 bytes per bit) and the VLC peek in 0x20484 (12 bits).  Needs
    the 48-bit 2f halfword forms 'ld r2,(r20+2)' = ldh (r20) and 'ld r3,(r20-30)' = ldh (r20-32):
    with word loads (aligned or not) the values are off by one bit / mixed -> the real-firmware
    video decode fails after ~1 macroblock (all-0x0400 Garage thumbnails in the LLE run)."""
    # 0x2f halfword forms are now decoded natively by vc01.py (merged from this finding)
    m = mach(); m.cpu.cache.clear(); R = random.Random(23); ok = n = 0
    for t in range(6):
        stream = bytes(R.randrange(256) for _ in range(8192))
        sa = 0xd40000
        m.m.load(sa, stream)
        call_keep(m, addr(0x26428), sa)
        bits = int.from_bytes(stream, 'big'); nb = 8 * len(stream); pos = 0; good = True
        for k in range(600):
            nn = R.randint(1, 16)
            got = call_keep(m, addr(0x25eb2), nn) & 0xffffffff
            want = (bits >> (nb - pos - nn)) & ((1 << nn) - 1)
            if got != want:
                good = False
                if VERB:
                    print('   t%d k%d n=%d pos=%d got %x want %x' % (t, k, nn, pos, got, want))
                break
            pos += nn
        ok += good; n += 1
    return ok, n


@test
def brightness888():
    """0x273aa type 5 -> 0x27484 -> 0x17f80 -> 0x17fcc (per 16 rows) with the per-chunk callback
    0x1893e: brightness b/256 on an RGB888 rectangle, every byte c -> min(255, (c*b) >> 8) (op17).
    0x17fcc walks a 64-byte sliding window through the VRF: 0x5430 resets the column base, 48 bytes
    are processed and stored per step, then 0x5730 advances the base by 48; the tail stores 16-byte
    blocks with 0x5530 (+16) between them."""
    _patch_cbase()
    m = mach(); m.cpu.cache.clear(); R = random.Random(24); ok = n = 0
    W, Hh, ST = 70, 36, 224
    buf = m.alloc(bytes(ST * Hh + 64)); desc = m.alloc(bytes(32))
    for t in range(10):
        img = bytes(R.randrange(256) for _ in range(ST * Hh))
        m.m.load(buf, img)
        b = R.choice([0x101, 0x120, 0x180, 0x200, 0x2c0, 0x80, R.randint(1, 0x2c0)])
        x = R.randrange(0, W - 1); y = R.randrange(0, Hh - 1)
        w = R.randint(1, W - x); h = R.randint(1, Hh - y)
        if t == 0:
            x, y, w, h = 0, 0, 64, 16
        m.m.load(desc, struct.pack('<B3xIIIII', 5, W, Hh, ST, 0, buf))
        m.call(addr(0x273aa), desc, x, y, w, h, b)
        exp = bytearray(img)
        for yy in range(y, y + h):
            for k in range(3 * x, 3 * (x + w)):
                exp[ST * yy + k] = min(255, (img[ST * yy + k] * b) >> 8)
        got = rbytes(m, buf, ST * Hh)
        good = got == bytes(exp)
        if not good and VERB:
            bad = [(k % ST, k // ST) for k in range(len(got)) if got[k] != exp[k]]
            print('   b=%#x rect' % b, (x, y, w, h), 'bad', len(bad), bad[:6])
        ok += good; n += 1
    return ok, n


@test
def rgb565_888():
    """0x188f6(dst, src, dst_stride, src_stride): 16x16 RGB565 -> packed RGB888 (48-byte rows),
    R = (p >> 8) & 0xf8, G = (p >> 3) & 0xfc, B = (p << 3) & 0xf8 (8-bit vertical writes from 16-bit
    vertical columns with offset registers)."""
    m = mach(); R = random.Random(25); ok = n = 0
    for t in range(6):
        ss = 32 + 32 * R.randint(0, 2); ds = 48 + 16 * R.randint(0, 2)
        img = [R.randrange(65536) for _ in range(ss * 8)]
        src = m.alloc(bytes(ss * 16 + 64)); src = (src + 63) & ~63; w16(m, src, img)   # 16-bit vld: 32-byte aligned
        dst = m.alloc(bytes([0x3c]) * (ds * 16 + 16))
        m.call(addr(0x188f6), dst, src, ds, ss)
        exp = bytearray([0x3c]) * (ds * 16 + 16)
        for y in range(16):
            for x in range(16):
                p_ = img[y * ss // 2 + x]
                exp[y * ds + 3 * x:y * ds + 3 * x + 3] = bytes(((p_ >> 8) & 0xf8, (p_ >> 3) & 0xfc, (p_ << 3) & 0xf8))
        got = rbytes(m, dst, ds * 16 + 16)
        if got != bytes(exp) and VERB:
            print('   ss', ss, 'ds', ds, [(k % ds, k // ds, got[k], exp[k]) for k in range(len(got)) if got[k] != exp[k]][:6])
        ok += got == bytes(exp); n += 1
    return ok, n


@test
def rgb888_565():
    """0x18b24(dst, src, dst_stride, src_stride): 16x16 packed RGB888 -> RGB565 through the
    accumulator (clra/acc chain, 8-bit vertical operands with offset registers)."""
    m = mach(); R = random.Random(27); ok = n = 0
    for t in range(6):
        ss = 48 + 16 * R.randint(0, 2); ds = 32 + 32 * R.randint(0, 2)
        img = bytes(R.randrange(256) for _ in range(ss * 16))
        src = m.alloc(img + bytes(64)); dst = m.alloc(bytes(ds * 16 + 128)); dst = (dst + 63) & ~63
        m.m.load(dst, bytes([0x3c]) * (ds * 16 + 32))
        m.call(addr(0x18b24), dst, src, ds, ss)
        exp = bytearray([0x3c]) * (ds * 16 + 32)
        for y in range(16):
            for x in range(16):
                r_, g, b = img[y * ss + 3 * x:y * ss + 3 * x + 3]
                exp[y * ds + 2 * x:y * ds + 2 * x + 2] = struct.pack('<H', ((r_ >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
        ok += rbytes(m, dst, ds * 16 + 32) == bytes(exp); n += 1
    return ok, n


@test
def mb_recon_ops():
    """The vector instructions of the MPEG-4 frame decoder 0x2c86 and the inter MB path 0x5d9e (run
    in place, two instructions each, on a random VRF): reconstruction of a 4:2:0 macroblock,
    HX(0..15,0) (op) H(32..47,0) and HX(0..7,32) (op) H(32..39,32): 16-bit residual + zero-extended
    8-bit prediction (vadd, 16-bit wrap) or plain widening copy (vmov).  The LLE real-firmware run
    executes 0x5f46/0x5f50 4832 times per Garage pass."""
    import vc01v
    m = mach(); c = m.cpu; R = random.Random(28); ok = n = 0
    if c.vpu is None:
        c.vpu = vc01v.VPU(c)
    for pc, add in ((0x5f46, True), (0x2d40, False), (0x2dbc, True)):
        for t in range(4):
            P = c.vpu.P
            for y in range(64):
                P[y][:] = bytes(R.randrange(256) for _ in range(64))
            old = [bytearray(r_) for r_ in P]
            c.r[31] = addr(pc); c.step(); c.step()
            exp = [bytearray(r_) for r_ in old]
            g16 = lambda B, y, x: B[y][x] | B[y][x + 16] << 8
            def p16(B, y, x, v):
                B[y][x] = v & 0xff; B[y][x + 16] = (v >> 8) & 0xff
            for k in range(16):
                for L in range(16):
                    p16(exp, k, L, (g16(old, k, L) if add else 0) + old[32 + k][L])
            for k in range(8):
                for L in range(16):
                    p16(exp, k, 32 + L, (g16(old, k, 32 + L) if add else 0) + old[32 + k][32 + L])
            ok += [bytes(r_) for r_ in P] == [bytes(r_) for r_ in exp]; n += 1
    return ok, n


def yuv888_ref(Y, C1, C2):
    """reference for 0x1898a: Y 16x16, C1/C2 8x8 (rows 16-23 / 24-31 of the VRF).  Constants as in the
    firmware (BT.601 x256: 298, -100, -200, 408, 516; floor >> 8; 16-bit wrap on the G sum)."""
    sat = lambda v: max(0, min(255, v))
    out = [[0] * 48 for _ in range(16)]
    for y in range(16):
        for x in range(16):
            yc = ((Y[y][x] - 16) * 298) >> 8
            c1 = C1[y >> 1][x >> 1] - 128; c2 = C2[y >> 1][x >> 1] - 128
            g = s16(((c1 * -100) >> 8) + ((c2 * -200) >> 8))
            out[y][3 * x:3 * x + 3] = [sat(s16(yc + ((c2 * 516) >> 8))), sat(s16(yc + g)), sat(s16(yc + ((c1 * 408) >> 8)))]
    return out


@test
def yuv_rgb888():
    """0x1895e(y, c1, c2, y_stride, dst, dst_stride): 16x16 YUV 4:2:0 macroblock -> packed 3-byte
    pixels [Y'+516*C2', Y'+G, Y'+408*C1'] (chroma stride y_stride/2; vld2 chroma loads), and
    0x18976(y, c1, c2, stride, dst, dst_stride) = 0x2756c (2x2 box of a 32x32 MB) then the same.
    Exercises op16 with negative immediates, sat8 adds into 8-bit D, chroma doubling (8-bit A with a
    16-bit D does not step), 8-bit vertical interleave moves."""
    m = mach(); R = random.Random(26); ok = n = 0
    for t in range(8):
        half = t % 2
        st = 16 * R.randint(2, 4) * (2 if half else 1); cs = st // 2; ds = 48 + 16 * R.randint(0, 2)
        ny = 32 if half else 16
        Yb = bytes(R.randrange(256) for _ in range(st * ny)); C1b = bytes(R.randrange(256) for _ in range(cs * ny // 2))
        C2b = bytes(R.randrange(256) for _ in range(cs * ny // 2))
        y = m.alloc(Yb); u = m.alloc(C1b); v = m.alloc(C2b)
        dst = m.alloc(bytes([0x3c]) * (ds * 16 + 16))
        m.call(addr(0x18976 if half else 0x1895e), y, u, v, st, dst, ds)
        if half:
            box = lambda B, s, r, c: (B[2 * r * s + 2 * c] + B[2 * r * s + 2 * c + 1] + B[(2 * r + 1) * s + 2 * c] + B[(2 * r + 1) * s + 2 * c + 1]) >> 2
            Y = [[box(Yb, st, r, c) for c in range(16)] for r in range(16)]
            C1 = [[box(C1b, cs, r, c) for c in range(8)] for r in range(8)]
            C2 = [[box(C2b, cs, r, c) for c in range(8)] for r in range(8)]
        else:
            Y = [[Yb[r * st + c] for c in range(16)] for r in range(16)]
            C1 = [[C1b[r * cs + c] for c in range(8)] for r in range(8)]
            C2 = [[C2b[r * cs + c] for c in range(8)] for r in range(8)]
        out = yuv888_ref(Y, C1, C2)
        exp = bytearray([0x3c]) * (ds * 16 + 16)
        for r in range(16):
            exp[r * ds:r * ds + 48] = bytes(out[r])
        got = rbytes(m, dst, ds * 16 + 16)
        good = got == bytes(exp)
        if not good and VERB:
            print('   half', half, [(k % ds, k // ds, got[k], exp[k]) for k in range(len(got)) if got[k] != exp[k]][:6])
        ok += good; n += 1
    return ok, n


def main():
    global VERB
    args = sys.argv[1:]
    if '-v' in args:
        VERB = True; args.remove('-v')
    tot = [0, 0]
    for t in TESTS:
        if args and t.__name__ not in args:
            continue
        try:
            p, n = t()
        except Exception as e:
            import traceback; traceback.print_exc(); p, n = 0, 1
        print('%-18s %5d/%-5d %s' % (t.__name__, p, n, 'PASS' if p == n else 'FAIL'))
        tot[0] += p; tot[1] += n
    print('total %d/%d' % tuple(tot))


if __name__ == '__main__':
    main()
