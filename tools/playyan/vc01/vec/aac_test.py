#!/usr/bin/env python3
"""AAC (Micro video image mi_fw0) vector-kernel tests: firmware routines run in the emulator with the
vector model (vc01v.py), compared with integer reference models (bit-exact) or, for the whole IMDCT,
with a float IMDCT (documented fixed-point tolerance).  Usage: aac_test.py [-v] [name...]"""
import sys, os, random, struct
import numpy as np                       # before the isa/ dir is on sys.path (isa/dis.py shadows stdlib dis)
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from aac_h import *
from vc01v import sx

R = random.Random(2005)
W32 = lambda x: ((x + 2**31) % 2**32) - 2**31
from common import workdir
FW = workdir.firmware(os.environ.get('AAC_IMG', 'mi_fw0'))
IMG = open(FW, 'rb').read()


def r32():
    return R.randint(-2**31, 2**31 - 1)


RND = 0x8000 if os.environ.get('AAC_OP21', 'round') == 'round' else 0   # op21 partial rounding


def H16(p):
    """op21 high half of a signed x unsigned product (round-to-nearest unless AAC_OP21=floor)"""
    return (p + RND) >> 16


def M(x, w):
    """firmware 32x32 -> high-32 product (0xa5ec): drops xl*wl; xh*wh exact (op14+op18), the two
    cross partials via op21."""
    xh, xl = x >> 16, x & 0xffff
    wh, wl = w >> 16, w & 0xffff
    return W32(xh * wh + H16(xh * wl) + H16(wh * xl))


# ---------------------------------------------------------------- simple int32 array kernels
def t_add32():          # 0x6944: out[i] = a[i] + b[i], 128 int32, carry via vadd setf + op9.ifc
    m = mach(); a = buf(m, 512, 'a'); b = buf(m, 512, 'b'); o = buf(m, 512, 'o'); ok = 0
    for _ in range(50):
        A = [r32() for _ in range(128)]; B = [r32() for _ in range(128)]
        put32(m, a, A); put32(m, b, B); m.call(0x6944, a, b, o)
        ok += get32(m, o, 128) == [W32(x + y) for x, y in zip(A, B)]
    return ok, 50


def t_window():         # 0x6840: out[i] = 2*floor(a[i]*w[i]/2^16) (MUL by Q15 window), 0x68b4: out += same
    m = mach(); a = buf(m, 512, 'a'); w = buf(m, 256, 'w'); o = buf(m, 512, 'o'); ok = 0
    for t in range(60):
        A = [r32() for _ in range(128)]; Wn = [R.randint(-32768, 32767) for _ in range(128)]
        O = [r32() for _ in range(128)]
        put32(m, a, A); put16(m, w, Wn); put32(m, o, O)
        fn = 0x6840 if t % 2 == 0 else 0x68b4
        m.call(fn, a, w, o)
        p = [W32(2 * ((x >> 16) * y + H16(y * (x & 0xffff)))) for x, y in zip(A, Wn)]
        if fn == 0x68b4:
            p = [W32(x + y) for x, y in zip(p, O)]
        ok += get32(m, o, 128) == p
    return ok, 60


def t_copyzero():       # 0x6a0c copy 1024 bytes (callers step 512: overlapping) ; 0x6a1e / 0x6a34 / 0x6a4a zero 1024 / 512 / 256 bytes
    m = mach(); a = buf(m, 2048, 'a2'); o = buf(m, 2048, 'o2'); ok = 0
    A = [r32() for _ in range(512)]
    put32(m, a, A); put32(m, o, [7] * 512); m.call(0x6a0c, a, o)
    O = get32(m, o, 512); ok += O[:256] == A[:256] and O[256:] == [7] * 256   # 32 rows x 32 bytes
    for fn, n in ((0x6a1e, 256), (0x6a34, 128), (0x6a4a, 64)):
        put32(m, o, [7] * 512); m.call(fn, o)
        O = get32(m, o, 512); ok += O[:n] == [0] * n and O[n:] == [7] * (512 - n)
    return ok, 4


def t_interleave():     # 0x9c0a: int32[] -> planar blocks; 0x9c30: planar -> int32[] (inverse)
    m = mach(); a = buf(m, 4096, 'in'); o = buf(m, 8192 + 1024, 'out'); ok = 0
    for _ in range(4):
        X = [r32() for _ in range(1024)]
        put32(m, a, X); m.call(0x9c0a, o, a, 512)
        H = [h & 0xffff for h in get16(m, o, 2048)]
        good = True
        for j in range(32):
            e = [X[32*j+2*t] & 0xffff for t in range(16)] + [(X[32*j+2*t] >> 16) & 0xffff for t in range(16)] + \
                [X[32*j+2*t+1] & 0xffff for t in range(16)] + [(X[32*j+2*t+1] >> 16) & 0xffff for t in range(16)]
            good &= H[64*j:64*j+64] == e
        ok += good
        m.call(0x9c30, o, o, 1024)
        ok += get32(m, o, 1024) == X
    return ok, 8


# ---------------------------------------------------------------- complex multiply kernels
def vrf_set_row(c, y, x0, vals):
    for L, v in enumerate(vals):
        c.vpu.vb[y][x0 + L] = v & 0xffff


def vrf_row(c, y, x0):
    return [c.vpu.vb[y][x0 + L] for L in range(16)]


def set32(c, y, vals):  # 32-bit row pair: lo row y, hi row y+1 (x 0..15)
    vrf_set_row(c, y, 0, [v & 0xffff for v in vals]); vrf_set_row(c, y + 1, 0, [(v >> 16) & 0xffff for v in vals])


def get32r(c, y):
    lo = vrf_row(c, y, 0); hi = vrf_row(c, y + 1, 0)
    return [sx(l | h << 16, 32) for l, h in zip(lo, hi)]


def call_vrf(m, fn, setup):
    """call a VRF-to-VRF leaf routine with prepared VRF state"""
    import vc01v
    if m.cpu.vpu is None:
        m.cpu.vpu = vc01v.VPU(m.cpu)
    setup(m.cpu)
    m.call(fn)
    return m.cpu


def t_cmul16():         # 0xa73e: (re,im) 32-bit x twiddle (c,s) u16 Q16 >= 0: re'=fl(c*re)-fl(s*im), im'=fl(s*re)+fl(c*im)
    m = mach(); ok = 0
    for _ in range(40):
        re = [R.randint(-2**30, 2**30) for _ in range(16)]; im = [R.randint(-2**30, 2**30) for _ in range(16)]
        cc = [R.randint(0, 32767) for _ in range(16)]; ss = [R.randint(0, 32767) for _ in range(16)]
        def setup(c):
            set32(c, 0, re); set32(c, 2, im); vrf_set_row(c, 4, 0, cc); vrf_set_row(c, 5, 0, ss)
        c = call_vrf(m, 0xa73e, setup)
        P = lambda c_, v_: (v_ >> 16) * c_ + H16(c_ * (v_ & 0xffff))
        er = [W32(P(a, x) - P(b, y)) for a, b, x, y in zip(cc, ss, re, im)]
        ei = [W32(P(b, x) + P(a, y)) for a, b, x, y in zip(cc, ss, re, im)]
        ok += get32r(c, 0) == er and get32r(c, 2) == ei
    return ok, 40


def t_cmul32():         # 0xa580 (x<<2), 0xa5b8 (x<<1), 0xa5ec (x<<0) then complex x * (cos + i sin), 32-bit twiddles
    m = mach(); ok = 0; n = 0
    for fn, sh in ((0xa580, 2), (0xa5b8, 1), (0xa5ec, 0)):
        for _ in range(30):
            lim = 2**(31 - sh) - 1
            re = [R.randint(-lim, lim) for _ in range(16)]; im = [R.randint(-lim, lim) for _ in range(16)]
            cw = [r32() for _ in range(16)]; sw = [r32() for _ in range(16)]
            def setup(c):
                set32(c, 0, re); set32(c, 2, im); set32(c, 4, cw); set32(c, 6, sw)
            c = call_vrf(m, fn, setup)
            X = [W32(x << sh) for x in re]; Y = [W32(y << sh) for y in im]
            er = [W32(M(x, a) - M(y, b)) for x, y, a, b in zip(X, Y, cw, sw)]
            ei = [W32(M(x, b) + M(y, a)) for x, y, a, b in zip(X, Y, cw, sw)]
            ok += get32r(c, 0) == er and get32r(c, 2) == ei; n += 1
    return ok, n


# ---------------------------------------------------------------- 16-point FFT (0xab46)
def tw32(addr):
    h = struct.unpack_from('<64h', IMG, addr)
    lo = [x & 0xffff for x in h[0:16]]; hi = h[16:32]; slo = [x & 0xffff for x in h[32:48]]; shi = h[48:64]
    return [W32(a << 16 | b) for a, b in zip(hi, lo)], [W32(a << 16 | b) for a, b in zip(shi, slo)]


def fft16_model(re, im):
    """the firmware's in-register radix-2 DIT: stages with lane distance 1,2 (-i on lanes 3 mod 4),
    then twiddle (x<<2 times table 0xa9c0 / 0xaa40) before distance 4 and 8."""
    re = list(re); im = list(im)
    def bfly(d):
        nr, ni = re[:], im[:]
        for t in range(16):
            if (t // d) % 2 == 0:
                nr[t] = W32(re[t] + re[t + d]); ni[t] = W32(im[t] + im[t + d])
            else:
                nr[t] = W32(re[t - d] - re[t]); ni[t] = W32(im[t - d] - im[t])
        re[:], im[:] = nr, ni
    bfly(1)
    for t in range(3, 16, 4):           # multiply lanes 3 mod 4 by -i: (re, im) <- (im, -re)
        re[t], im[t] = im[t], W32(-re[t])
    bfly(2)
    for d, tab in ((4, 0xa9c0), (8, 0xaa40)):
        cw, sw = tw32(tab)
        X = [W32(x << 2) for x in re]; Y = [W32(y << 2) for y in im]
        re = [W32(M(x, a) - M(y, b)) for x, y, a, b in zip(X, Y, cw, sw)]
        im = [W32(M(x, b) + M(y, a)) for x, y, a, b in zip(X, Y, cw, sw)]
        bfly(d)
    return re, im


def t_fft16():
    m = mach(); b = buf(m, 512, 'blk'); ok = 0
    for _ in range(40):
        Z = [complex(R.randint(-2**26, 2**26), R.randint(-2**26, 2**26)) for _ in range(16)]
        put_block(m, b, Z); m.call(0xab46, b, b + 128)
        out = get_block(m, b)
        er, ei = fft16_model([int(z.real) for z in Z], [int(z.imag) for z in Z])
        ok += [int(z.real) for z in out] == er and [int(z.imag) for z in out] == ei
    return ok, 40


def t_radix2():         # 0xaace(a, b, tw, span): per 16-complex block pair t = cmul32(b<<2, w); a += t; b = a - t
    m = mach(); A = buf(m, 1024, 'ra'); Bb = buf(m, 1024, 'rb'); TW = buf(m, 1024, 'rtw'); ok = 0
    for _ in range(10):
        nb = R.choice([1, 2, 4])
        za = [[complex(R.randint(-2**28, 2**28), R.randint(-2**28, 2**28)) for _ in range(16)] for _ in range(nb)]
        zb = [[complex(R.randint(-2**28, 2**28), R.randint(-2**28, 2**28)) for _ in range(16)] for _ in range(nb)]
        tw = [([r32() for _ in range(16)], [r32() for _ in range(16)]) for _ in range(nb)]
        for k in range(nb):
            put_block(m, A + 128 * k, za[k]); put_block(m, Bb + 128 * k, zb[k])
            c_, s_ = tw[k]
            put16(m, TW + 128 * k, [sx(v & 0xffff, 16) for v in c_] + [sx((v >> 16) & 0xffff, 16) for v in c_] +
                  [sx(v & 0xffff, 16) for v in s_] + [sx((v >> 16) & 0xffff, 16) for v in s_])
        m.call(0xaace, A, Bb, TW, 128 * nb)
        good = True
        for k in range(nb):
            c_, s_ = tw[k]
            X = [W32(int(z.real) << 2) for z in zb[k]]; Y = [W32(int(z.imag) << 2) for z in zb[k]]
            tr = [W32(M(x, a) - M(y, b)) for x, y, a, b in zip(X, Y, c_, s_)]
            ti = [W32(M(x, b) + M(y, a)) for x, y, a, b in zip(X, Y, c_, s_)]
            ea = [complex(W32(int(z.real) + p), W32(int(z.imag) + q)) for z, p, q in zip(za[k], tr, ti)]
            eb = [complex(W32(int(z.real) - p), W32(int(z.imag) - q)) for z, p, q in zip(za[k], tr, ti)]
            good &= get_block(m, A + 128 * k) == ea and get_block(m, Bb + 128 * k) == eb
        ok += good
    return ok, 10


# ---------------------------------------------------------------- inverse quantisation (0x885c)
def iq_model(q):
    tab = lambda i: struct.unpack_from('<H', IMG, 0x8948 + 2 * i)[0]      # Q12 cube roots, 1280 entries
    a = abs(q)
    # |q| >= 1280: index = op20(|q|, 8192) = round(|q| / 8) (op20 rounds: a vlsr #3 would floor; the
    # rounded index is also the more accurate one: mean rel. error 4.5e-5 vs -3.1e-4 for floor)
    return q * tab(a) if a < 1280 else 2 * q * tab((a + 4) >> 3)         # |q|^(4/3) * 4096, sign of q


def t_iquant():         # 0x885c(out int32[1024], in int16[1024]): out = q*cbrt(|q|) (Q12 table, |q|>=1280 via |q|/8)
    m = mach(); qb = buf(m, 2048, 'q'); o = buf(m, 4096 + 256, 'oq'); ok = 0
    for t in range(4):
        Q = [R.choice([R.randint(-20, 20), R.randint(-1279, 1279), R.randint(-8191, 8191)]) for _ in range(1024)]
        put16(m, qb, Q); m.call(0x885c, o, qb)
        O = get32(m, o, 1024)
        ok += sum(a == iq_model(b) for a, b in zip(O, Q))
    return ok, 4096


# ---------------------------------------------------------------- PCM output (0x975e stereo, 0x945a)
def clip16(v):
    return max(-32768, min(32767, v))


def t_pcm():            # 0x975e(L int32[128], R int32[128], out int16[256] interleaved): clip16((x + 512) >> 10)
    m = mach(); lb = buf(m, 512, 'L'); rb = buf(m, 512, 'R'); o = buf(m, 1024, 'pcm'); ok = 0
    for t in range(20):
        bits = [20, 24, 26, 28, 31][t % 5]
        L = [R.randint(-2**bits, 2**bits - 1) for _ in range(128)]; Rr = [R.randint(-2**bits, 2**bits - 1) for _ in range(128)]
        put32(m, lb, L); put32(m, rb, Rr); m.call(0x975e, lb, rb, o)
        O = get16(m, o, 256)
        E = [clip16((x + 512) >> 10) for p in zip(L, Rr) for x in p]
        ok += O == E
    return ok, 20


# ---------------------------------------------------------------- whole IMDCTs vs float (tolerance)
def imdct_ref(X, N):
    n = np.arange(N); k = np.arange(N // 2); n0 = (N / 2 + 1) / 2
    return np.cos(2 * np.pi / N * np.outer(n + n0, k + 0.5)) @ np.array(X, float)


def t_imdct():          # 0x97d6 (N=2048) gain 1, 0x99d8 (N=256) gain 8; rel. error < 1e-4 and gain within 1e-4
    m = mach(); a = buf(m, 4096, 'in'); o = buf(m, 8192 + 1024, 'out'); ok = 0; n = 0
    for fn, N, gain in ((0x97d6, 2048, 1.0), (0x99d8, 256, 8.0)):
        for t in range(6):
            bits = [12, 16, 19, 21, 23, 25 if N == 2048 else 22][t]
            X = [R.randint(-2**bits, 2**bits) for _ in range(N // 2)]
            put32(m, a, X + [0] * (1024 - N // 2)); m.call(fn, o, a, 10)
            Y = np.array(get32(m, o, N), float); ref = imdct_ref(X, N)
            g = (Y @ ref) / (ref @ ref); e = Y - g * ref; rel = np.sqrt(e @ e / (Y @ Y))
            ok += abs(g / gain - 1) < 1e-4 and rel < 1e-4; n += 1
            if VERB:
                print('   %#x N=%d bits=%d gain %.7f relerr %.3g' % (fn, N, bits, g, rel))
    return ok, n


def t_imdct_neg():      # block-float normalisation must see negative-dominant spectra: max(max hi, ~min hi)
    # (op28 = signed MIN reduction; a symmetric clip [-b-1, b] ignores the negatives -> overflow, 23/40 fail)
    m = mach(); a = buf(m, 4096, 'in'); o = buf(m, 8192 + 1024, 'out'); ok = 0; n = 0
    for t in range(20):
        N = 2048 if t % 2 == 0 else 256; g = 1.0 if N == 2048 else 8.0
        bits = R.choice([24, 27, 28]) if N == 2048 else R.choice([22, 24])
        X = [R.randint(-2**(bits - 12), 0) for _ in range(N // 2)]
        X[R.randrange(N // 2)] = -R.randint(2**(bits - 1), 2**bits)
        put32(m, a, X + [0] * (1024 - N // 2)); m.call(0x97d6 if N == 2048 else 0x99d8, o, a, 10)
        Y = np.array(get32(m, o, N), float); ref = g * imdct_ref(X, N)
        ok += np.linalg.norm(Y - ref) / np.linalg.norm(ref) < 1e-4; n += 1
    return ok, n


VERB = False
TESTS = [t_add32, t_window, t_copyzero, t_interleave, t_cmul16, t_cmul32, t_fft16, t_radix2, t_iquant, t_pcm, t_imdct, t_imdct_neg]


# ---------------------------------------------------------------- bit-exact whole IMDCT and filter bank (vec/aac_model.py)
import aac_model as AMOD


def t_imdct_exact():    # 0x97d6 / 0x99d8 vs the pure-Python fixed-point model, bit-exact over every output
    m = mach(); a = buf(m, 4096, 'in'); o = buf(m, 8192 + 1024, 'out'); ok = n = 0
    worst = 0.0
    for fn, N, gain in ((0x97d6, 2048, 1.0), (0x99d8, 256, 8.0)):
        for t in range(12):
            bits = [0, 4, 8, 12, 16, 19, 21, 23, 25, 27, 29, 31][t]
            lim = 2**bits - 1 if bits < 31 else 2**31 - 1
            X = [R.randint(-lim - 1, lim) for _ in range(N // 2)]
            if t % 3 == 1:
                X = [x if R.random() < 0.05 else 0 for x in X]
            arg = R.choice([10, 10, 8, 12]) if t > 1 else 10
            put32(m, a, X + [0] * (1024 - N // 2)); m.call(fn, o, a, arg)
            Y = get32(m, o, N)
            ok += Y == AMOD.imdct(X, N, arg); n += 1
            if arg == 10 and 12 <= bits <= 21:                   # float sanity check (no saturation)
                ref = gain * imdct_ref(X, N); Yf = np.array(Y, float)
                worst = max(worst, np.linalg.norm(Yf - ref) / np.linalg.norm(ref))
    if VERB:
        print('   worst relative deviation from the float IMDCT: %.3g' % worst)
    return ok + (worst < 1e-4), n + 1


def t_filterbank():     # 0x21bb2 ifilter_bank, all 4 window sequences, random windows/spectra/overlap, bit-exact
    m = mach(); fb = buf(m, 16800, 'fb'); fq = buf(m, 4096, 'fq'); T = buf(m, 8192, 'T'); tr = buf(m, 8192 + 1024, 'tr')
    wb = buf(m, 2 * (2 * 1024 + 2 * 128) * 2 + 64, 'win'); ok = n = 0
    for t in range(16):
        seq = t % 4; shape = R.randrange(2); prev = R.randrange(2)
        W = {}
        addr = wb
        for kind, L in (('L', 1024), ('S', 128), ('Lrev', 1024), ('Srev', 128)):
            for sh in range(2):
                w = [R.randint(0, 32767) for _ in range(L)]
                put16(m, addr, w); W[(kind, sh)] = w
                off = {'L': 16692, 'S': 16700, 'Lrev': 16708, 'Srev': 16716}[kind]
                m.m.write(fb + off + 4 * sh, 4, addr); addr += 2 * L
        bits = R.choice([16, 20, 24])
        F = [R.randint(-2**bits, 2**bits) for _ in range(1024)]
        T0 = [R.randint(-2**26, 2**26) for _ in range(2048)]
        put32(m, fq, F); put32(m, T, T0)
        m.m.write(emu.STACK, 4, tr)
        m.call(0x21f46 if 'fup' in FW else 0x21bb2, fb, seq, shape, prev, fq, T)   # ifilter_bank
        win = dict(Lprev=W[('L', prev)], Lrev=W[('Lrev', shape)], Sprev=W[('S', prev)], S=W[('S', shape)], Srev=W[('Srev', shape)])
        E = AMOD.ifilter_bank(seq, win, F, list(T0))
        good = get32(m, T, 2048) == E
        pc = buf(m, 1024, 'pcmfb')                      # then the stereo PCM output 0x975e on the time samples
        for c in range(8):
            m.call(0x975e, T + 512 * c, T + 4096 + 512 * c, pc)
            good &= get16(m, pc, 256) == [AMOD.pcm16(x) for i in range(128) for x in (E[128 * c + i], E[1024 + 128 * c + i])]
        ok += good; n += 1
    return ok, n


TESTS += [t_imdct_exact, t_filterbank]


def main(names=()):
    tot = [0, 0]
    for t in TESTS:
        if names and t.__name__[2:] not in names:
            continue
        try:
            p, n = t()
        except Exception as e:
            import traceback; traceback.print_exc(); p, n = 0, 1
        tot[0] += p; tot[1] += n
        print('%-14s %5d/%-5d %s' % (t.__name__[2:], p, n, 'PASS' if p == n else 'FAIL'))
    print('total %d/%d' % tuple(tot))
    return tot


if __name__ == '__main__':
    args = sys.argv[1:]
    if '-v' in args:
        VERB = True; args.remove('-v')
    main(args)
