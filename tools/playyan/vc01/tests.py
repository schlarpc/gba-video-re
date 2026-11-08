#!/usr/bin/env python3
"""Self-checking tests: run firmware routines in the VC01 emulator and compare with Python references.
Usage: tests.py [-v] [name...]"""
import sys, random, struct, math, traceback
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu import *

R = random.Random(1234)
M64 = (1 << 64) - 1
def s32(x): return sx(x, 32)
def s64(x): return x - (1 << 64) if x >> 63 else x
def split(x): return x & M32, (x >> 32) & M32
def j(lo, hi): return lo | hi << 32
def d2w(f): return split(struct.unpack('<Q', struct.pack('<d', f))[0])
def w2d(lo, hi): return struct.unpack('<d', struct.pack('<Q', j(lo, hi)))[0]

TESTS = []
def test(fn): TESTS.append(fn); return fn

def rnd64():
    k = R.choice([8, 16, 31, 32, 33, 48, 63, 64])
    return R.getrandbits(k)

LDR = None
def ldr():
    global LDR
    if LDR is None: LDR = Machine('py_fwldr')
    return LDR

@test
def muldi3():
    m = ldr(); n = 0
    for _ in range(300):
        a, b = rnd64(), rnd64()
        n += j(*m.call(0xc0456, *split(a), *split(b))) == (a * b) & M64
    return n, 300

@test
def udivdi3():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a, b = rnd64(), rnd64() or 1
        n += j(*m.call(0xc04c2, *split(a), *split(b))) == a // b
    return n, N

@test
def umoddi3():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a, b = rnd64(), rnd64() or 1
        n += j(*m.call(0xc059a, *split(a), *split(b))) == a % b
    return n, N

@test
def divdi3():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a, b = s64(rnd64() if R.random() < .5 else (-rnd64()) & M64), s64(rnd64() or 1)
        if R.random() < .5: b = -b
        q = abs(a) // abs(b); q = q if (a < 0) == (b < 0) else -q
        n += j(*m.call(0xc0476, *split(a & M64), *split(b & M64))) == q & M64
    return n, N

@test
def moddi3():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a, b = s64(rnd64() if R.random() < .5 else (-rnd64()) & M64), s64(rnd64() or 1)
        if R.random() < .5: b = -b
        r = abs(a) % abs(b); r = -r if a < 0 else r
        n += j(*m.call(0xc05be, *split(a & M64), *split(b & M64))) == r & M64
    return n, N

@test
def modsi3():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a, b = s32(R.getrandbits(32)), s32(R.getrandbits(R.choice([4, 16, 32]))) or 3
        r = abs(a) % abs(b); r = -r if a < 0 else r
        n += m.call(0xc0360, a, b)[0] == r & M32
    return n, N

@test
def umodsi3():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a, b = R.getrandbits(32), R.getrandbits(R.choice([4, 16, 32])) or 3
        n += m.call(0xc0608, a, b)[0] == a % b
    return n, N

def rndd():
    c = R.random()
    if c < .1: return R.choice([0.0, -0.0, 1.0, -1.0, 0.5, 2.0, 1e300, -1e-300, float('inf'), 3.0])
    return R.choice([1, -1]) * math.ldexp(R.random() + .5, R.randint(-60, 60))

@test
def dcmp():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a = rndd(); b = R.choice([a, -a, rndd(), rndd()])
        exp = (a > b) - (a < b)
        n += s32(m.call(0xc0614, *d2w(a), *d2w(b))[0]) == exp
    return n, N

@test
def fixdfsi():
    m = ldr(); n = 0; N = 300
    for _ in range(N):
        a = R.choice([1, -1]) * math.ldexp(R.random() + .5, R.randint(-3, 30))
        n += s32(m.call(0xc0940, *d2w(a))[0]) == int(a)
    return n, N

def main():
    v = '-v' in sys.argv
    names = [x for x in sys.argv[1:] if not x.startswith('-')]
    tot_ok = 0
    for t in TESTS:
        if names and t.__name__ not in names: continue
        try:
            ok, n = t()
            st = 'PASS' if ok == n else 'FAIL'
            print('%-14s %s %d/%d' % (t.__name__, st, ok, n))
        except Exception as e:
            print('%-14s ERROR %s' % (t.__name__, e))
            if v: traceback.print_exc()

def dtest(fn, op, N=400):
    m = ldr(); n = 0; bad = []
    for _ in range(N):
        a = rndd(); b = R.choice([rndd(), a, -a, a * (1 + 2**-40)])
        if (math.isinf(a) and b == 0) or (math.isinf(b) and a == 0): continue  # firmware returns 0 for inf*0
        try:
            e = op(a, b)
        except (ZeroDivisionError, OverflowError):
            continue
        r = w2d(*m.call(fn, *d2w(a), *d2w(b)))
        if r == e or (math.isnan(r) and math.isnan(e)) or (r == 0 and e == 0):
            n += 1
        else:
            bad.append((a, b, r, e)); n += 0
    if bad and '-v' in sys.argv: print('   e.g.', bad[:3])
    return n, N - (N - n - len(bad))

for _nm, _fn, _op in [('dmul', 0xc06b6, lambda a, b: a * b), ('dsub', 0xc0a04, lambda a, b: a - b),
                      ('dadd', 0xc0a0c, lambda a, b: a + b), ('ddiv', 0xc0d8c, lambda a, b: a / b)]:
    def _t(fn=_fn, op=_op): return dtest(fn, op)
    _t.__name__ = _nm
    TESTS.append(_t)


def cstr(m, s):
    return m.alloc(s + b'\0')

@test
def memchr():
    m = ldr(); n = 0; N = 100
    for _ in range(N):
        buf = bytes(R.getrandbits(8) for _ in range(R.randint(0, 40)))
        c = R.choice(list(buf) + [R.getrandbits(8)]) if buf else 7
        a = m.alloc(buf + b'\xaa' * 4)
        r = m.call(0xc8d60, a, c | (R.getrandbits(8) << 8), len(buf))[0]
        e = buf.find(bytes([c]))
        n += r == (0 if e < 0 else a + e)
    return n, N

@test
def memcmp():
    m = ldr(); n = 0; N = 100
    for _ in range(N):
        x = bytes(R.getrandbits(8) for _ in range(R.randint(0, 20)))
        y = bytearray(x)
        if y and R.random() < .7: y[R.randrange(len(y))] = R.getrandbits(8)
        r = s32(m.call(0xc8d78, m.alloc(x), m.alloc(bytes(y)), len(x))[0])
        e = (x > bytes(y)) - (x < bytes(y))
        n += (r > 0) - (r < 0) == e
    return n, N

def lfn_sum(name):
    s = 0
    for c in name:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xff
    return s

@test
def lfn_checksum():
    m = ldr(); n = 0; N = 100
    for _ in range(N):
        nm = bytes(R.getrandbits(8) for _ in range(11))
        n += m.call(0xc62c0, m.alloc(nm))[0] == lfn_sum(nm)
    return n, N


from common import workdir
FUP = os.path.join(workdir.roms_dir(), workdir.ROM_NAMES['fup'])   # $PY_ROMS/playan.fup

@test
def fup_blockcheck():
    """loader 0xc6266(buf, n): 0 iff (sum(buf[0:n]) & 0xff) == 1 -- run over every real playan.fup block
    (good) and the same blocks with one byte flipped (bad)."""
    m = ldr(); f = open(FUP, 'rb').read(); n = tot = 0
    nsec = f[0x3e]
    for s in range(nsec):
        off, size, _, _ = struct.unpack_from('<IIII', f, 0x40 + 16 * s)
        k = off; left = size
        while left > 0:
            ln = min(0x200, left); blk = f[k:k + ln + 1]
            a = m.alloc(blk)
            r = m.call(0xc6266, a, ln + 1)[0]
            n += (r == 0) == ((sum(blk) & 0xff) == 1); tot += 1
            if tot % 7 == 0:   # corrupted copy must fail
                bb = bytearray(blk); bb[R.randrange(len(bb))] ^= 1 << R.randrange(8)
                r = m.call(0xc6266, m.alloc(bytes(bb)), ln + 1)[0]
                n += (r == 0) == ((sum(bb) & 0xff) == 1); tot += 1
            m.heap = 0x00E00000
            k += ln + 1; left -= ln
    return n, tot


def sprintf_call(m, fmt, args):
    buf = m.alloc(b'\xcc' * 256)
    f = m.alloc(fmt + b'\0')
    regs = []
    for a in args:
        regs.append(m.alloc(a + b'\0') if isinstance(a, bytes) else a & M32)
    stk = regs[4:]
    for k, v in enumerate(stk):
        m.m.write(STACK + 4 * k, 4, v)
    m.call(0xce7cc, buf, f, *regs[:4])
    out = bytearray()
    while True:
        c = m.m.read(buf + len(out), 1)
        if c == 0 or len(out) > 250: break
        out.append(c)
    return bytes(out)

@test
def sprintf():
    """loader 0xce7cc = sprintf(buf, fmt, ...) over a printf core 0xc4df2 (exercises switch.b, ldb++,
    division by 10/16, sp-relative va_list) vs Python %-formatting"""
    m = ldr(); n = 0; tot = 0
    cases = [(b'%d', [0]), (b'%d', [-1]), (b'%d|%d', [2147483647, -2147483648]), (b'%x', [0xdeadbeef]),
             (b'%X', [0xabc]), (b'%08x', [0x1234]), (b'%5d|%-5d|', [42, 42]), (b'%s', [b'hello']),
             (b'[%10s][%-10s]', [b'ab', b'cd']), (b'%c%c%c', [65, 66, 67]), (b'%u', [0xffffffff]),
             (b'%%', []), (b'%s %s\n', [b'Stack Overflow in task/HISR: ', b'TASK1']),
             (b'%d %d %d %d %d %d', [1, -2, 3, -4, 5, -6]), (b'%o', [8]), (b'%ld', [123456]),
             (b'%p', [0x1234]), (b'%3c|', [ord('z')]), (b'%.3s', [b'abcdef'])]
    for _ in range(40):
        v = s32(R.getrandbits(32)) if R.random() < .5 else R.getrandbits(R.choice([4, 8, 16]))
        cases.append((R.choice([b'%d', b'%x', b'%u', b'%5d', b'%-8x|', b'%08X']), [v]))
    bad = []
    for fmt, args in cases:
        pyargs = []
        for a in args:
            pyargs.append(a.decode() if isinstance(a, bytes) else a)
        pf = fmt.replace(b'%l', b'%').replace(b'%p', b'%x')
        try:
            vals = [x & M32 if (isinstance(x, int) and (b'u' in pf or b'x' in pf.lower() or b'o' in pf)) else x for x in pyargs]
            exp = (pf.decode() % tuple(vals)).encode()
        except TypeError:
            continue
        got = sprintf_call(m, fmt, args)
        tot += 1
        if got == exp: n += 1
        else: bad.append((fmt, args, got, exp))
        m.heap = 0x00E00000
    if bad and '-v' in sys.argv:
        for b in bad[:8]: print('   ', b)
    return n, tot


@test
def nucleus_list_remove():
    """loader 0xc14ae: Nucleus-style cleanup that unlinks a suspension block from its object's
    circular doubly-linked wait list (cs_previous at +0, cs_next at +4, owner at +12; owner count at
    +60, list head at +64) and sets status -50 at +28.  Exercises ld/st (rs+4u) e/f forms and 28 ld/st."""
    m = ldr(); ok = tot = 0
    for trial in range(30):
        k = R.randint(1, 5)
        owner = m.alloc(bytes(128))
        nodes = [m.alloc(bytes(64)) for _ in range(k)]
        for i, nd in enumerate(nodes):
            m.m.write(nd + 0, 4, nodes[(i - 1) % k]); m.m.write(nd + 4, 4, nodes[(i + 1) % k])
            m.m.write(nd + 12, 4, owner)
        m.m.write(owner + 60, 4, k); m.m.write(owner + 64, 4, nodes[0])
        victim = R.randrange(k)
        m.call(0xc14ae, nodes[victim])
        rest = nodes[:victim] + nodes[victim + 1:]
        good = m.m.read(owner + 60, 4) == k - 1 and m.m.read(nodes[victim] + 28, 4) == (-50) & M32
        if rest:
            head = m.m.read(owner + 64, 4)
            exp_head = nodes[0] if victim != 0 else nodes[1]
            good &= head == exp_head
            # walk forward from head
            seq = []; p = head
            for _ in range(len(rest)):
                seq.append(p); p = m.m.read(p + 4, 4)
            good &= p == head and sorted(seq) == sorted(rest)
            for x in rest:
                good &= m.m.read(m.m.read(x + 4, 4) + 0, 4) == x
        else:
            good &= m.m.read(owner + 64, 4) == 0
        ok += good; tot += 1
        m.heap = 0x00E00000
    return ok, tot


@test
def loader_fup_download():
    """loader 0xc84f8 (FIRMWARE_LIST worker) and 0xc6d0c (FIRMWARE_DOWNLOAD worker) on the real
    playan.fup with a fake FILE layer (loader_emu.FupMachine): LIST must report status 0 mask 3,
    DOWNLOAD(mask 1/2) must leave fup_sec0/fup_sec1 byte-exact in RAM when it jumps to 0x200.
    Exercises pc-relative lea (4x even), 48-bit byte lanes, 2d gp forms, fread loop, checksums."""
    import vc01 as V
    mine = V.decode
    import loader_emu
    V.decode = mine          # use this decoder, not loader_emu's patch
    fup = open(FUP, 'rb').read(); ok = 0
    m = loader_emu.FupMachine(fup); out = 0x00E00000
    st = m.run(0xc84f8, out)[1]
    ok += st == 0 and m.m.read(out, 4) == 3
    ext = workdir.FIRMWARE + '/'
    for mask, k in ((1, 0), (2, 1)):
        m = loader_emu.FupMachine(fup)
        how, _ = m.run(0xc6d0c, mask, stop_at=0x200)
        sec = open(ext + 'fup_sec%d.bin' % k, 'rb').read()
        ok += how == 'stopped' and m.m.read(0, 1) is not None and \
            all(m.m.read(a, 4) == int.from_bytes(sec[a:a + 4], 'little') for a in range(0, len(sec) - 3, 4))
    return ok, 3


@test
def halfword_signedness():
    """loader 0xcc94a(a, b, c): walk list at [gp+540] (next at +64), match u32 +40 == a,
    u32 +44 == b and s16 +48 == c (29/odd load), then ++s16 at +36 (29/odd load, 29/even store).
    Using negative c values discriminates ldsh (sign-extending) from ldh."""
    m = ldr(); ok = tot = 0
    for _ in range(40):
        k = R.randint(1, 6)
        nodes = [m.alloc(bytes(80)) for _ in range(k)]
        keys = []
        for i, nd in enumerate(nodes):
            a, b, c = R.getrandbits(32), R.getrandbits(32), R.randint(-32768, 32767)
            keys.append((a, b, c))
            m.m.write(nd + 40, 4, a); m.m.write(nd + 44, 4, b); m.m.write(nd + 48, 2, c & 0xffff)
            cnt = R.randint(-5, 100); m.m.write(nd + 36, 2, cnt & 0xffff)
            m.m.write(nd + 64, 4, nodes[i + 1] if i + 1 < k else 0)
        m.m.write(m.gp + 540, 4, nodes[0])
        j = R.randrange(k + 1)
        if j < k:
            a, b, c = keys[j]; before = sx(m.m.read(nodes[j] + 36, 2), 16)
            r = m.call(0xcc94a, a, b, c & M32)[0]
            good = r == nodes[j] and sx(m.m.read(nodes[j] + 36, 2), 16) == before + 1
        else:
            r = m.call(0xcc94a, 1, 2, 3)[0]; good = r == 0 or (1, 2, 3) in keys
        ok += good; tot += 1
        m.heap = 0x00E00000
    return ok, tot


def mfile_ref(name, ext):
    out = bytearray(); n = 0; i = 0
    if name[0] == 5:
        out.append(0xe5); i = 1; n = 1
    while True:
        c = name[i]
        if c in (0, 32): break
        out.append(c); i += 1; n += 1
        if n == 8: break
    e = 0
    if ext is not None and ext[0] != 32:
        out.append(46)
        while True:
            c = ext[e]
            if c in (0, 32): break
            out.append(c); e += 1
            if e == 3: break
    if e == 0 and out and out[-1] == 46 and out[0] != 46:
        out.pop()
    return bytes(out), n + e

@test
def fat_mfile():
    """loader 0xc9706(dst, name8, ext3): FAT 8.3 directory name -> 'NAME.EXT' (0x05 -> 0xE5 escape,
    stop at NUL/space, drop a dangling '.'); returns name+ext length.  exercises exts16 counters,
    stb ++, negative 2a offset."""
    m = ldr(); ok = tot = 0
    alpha = b'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_~ '
    for _ in range(60):
        nl = R.randint(1, 8); el = R.randint(0, 3)
        name = bytes(R.choice(alpha[:-1]) for _ in range(nl)) + b' ' * (8 - nl)
        if R.random() < .15: name = b'\x05' + name[1:]
        ext = bytes(R.choice(alpha[:-1]) for _ in range(el)) + b' ' * (3 - el)
        dst = m.alloc(b'\xcc' * 16)
        r = m.call(0xc9706, dst, m.alloc(name + b'\0'), m.alloc(ext + b'\0'))[0]
        got = bytes(m.m.read(dst + k, 1) for k in range(14)).split(b'\0')[0]
        exp, en = mfile_ref(name + b'\0', ext + b'\0')
        ok += got == exp and r == en; tot += 1
        m.heap = 0x00E00000
    return ok, tot


@test
def minstd_rand():
    """loader 0xccd46: Park-Miller 'minimal standard' generator, state at gp+588:
    s = s*16807 mod (2^31-1) computed with 16-bit partial products (mul #imm16, bmask, addcmpbls)."""
    m = ldr(); ok = tot = 0
    for _ in range(20):
        s = R.randrange(1, 2**31 - 1)
        m.m.write(m.gp + 588, 4, s)
        for _ in range(10):
            r = m.call(0xccd46)[0]
            s = s * 16807 % (2**31 - 1)
            ok += r == s and m.m.read(m.gp + 588, 4) == s; tot += 1
    return ok, tot

if __name__ == '__main__':
    main()
