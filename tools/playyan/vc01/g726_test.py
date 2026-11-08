#!/usr/bin/env python3
"""Execution tests: py_fw0 G.726 routines in the VC01 emulator vs the Python reference (g726_ref).
Usage: g726_test.py [-v] [name...]"""
import sys, struct, random, math
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g726_vemu as V
import g726_ref as G
from vc01 import sx, M32
import emu

R = random.Random(726)
s16 = G.s16

PREDICT, UPDATE, QUANTIZE, ENC32, DEC32, QTAB721 = 0xb5c, 0xc80, 0xebe, 0x720, 0x7d6, 0x710
STATE_SIZE = 92


def pack(st):
    b = bytearray(STATE_SIZE)
    struct.pack_into('<6h', b, 0, *st.b)
    struct.pack_into('<6h', b, 32, *st.dq)
    struct.pack_into('<i5h', b, 64, st.yl, st.yu, st.dms, st.dml, st.ap, st.a[0])
    struct.pack_into('<5h', b, 78, st.a[1], st.pk[0], st.pk[1], st.sr[0], st.sr[1])
    b[88] = st.td
    return bytes(b)


def unpack(b):
    st = G.State()
    st.b = list(struct.unpack_from('<6h', b, 0))
    st.dq = list(struct.unpack_from('<6h', b, 32))
    st.yl, st.yu, st.dms, st.dml, st.ap, a0 = struct.unpack_from('<i5h', b, 64)
    a1, p0, p1, s0, s1 = struct.unpack_from('<5h', b, 78)
    st.a = [a0, a1]; st.pk = [p0, p1]; st.sr = [s0, s1]; st.td = b[88]
    return st


M = None
def mach():
    global M
    if M is None:
        M = V.machine('py_fw0')
        M.sbuf = M.alloc(bytes(128))
    return M


def put_state(m, st):
    m.m.load(m.sbuf, pack(st))
    return m.sbuf


def get_state(m):
    return unpack(bytes(m.m.read(m.sbuf + k, 1) for k in range(STATE_SIZE)))


def call_stack(m, fn, regs, stack):
    for k, v in enumerate(stack):
        m.m.write(emu.STACK + 4 * k, 4, v & M32)
    return m.call(fn, *regs)


# --- realistic states from the reference codec on synthetic audio
def signal(n, seed):
    rr = random.Random(seed)
    f = [rr.uniform(0.01, 0.6) for _ in range(3)]; amp = [rr.uniform(500, 12000) for _ in range(3)]
    out = []
    for k in range(n):
        v = sum(a * math.sin(k * w) for a, w in zip(amp, f)) + rr.gauss(0, rr.choice([10, 300, 3000]))
        if rr.random() < 0.01:
            v = rr.choice([32767, -32768])
        out.append(max(-32768, min(32767, int(v))))
    return out


STATES = None
def states():
    global STATES
    if STATES is None:
        STATES = []
        for seed in range(6):
            st = G.State()
            for x in signal(250, seed):
                G.g721_encoder(x, st)
                STATES.append(st.copy())
    return STATES


def rnd_state():
    st = R.choice(states()).copy()
    if R.random() < 0.5:          # fuzz the predictor inputs
        st.b = [R.randint(-32768, 32767) for _ in range(6)]
        st.a = [R.randint(-15360, 15360), R.randint(-12288, 12288)]
        st.dq = [R.randint(-32768, 32767) for _ in range(6)]
        st.sr = [R.randint(-32768, 32767) for _ in range(2)]
    return st


TESTS = []
def test(fn): TESTS.append(fn); return fn
VERB = False


@test
def predictor():
    """0xb5c: r0 = predictor_zero (sezi), r1 = predictor_pole"""
    m = mach(); ok = [0, 0]; N = 400
    for k in range(N):
        st = rnd_state()
        r0, r1 = m.call(PREDICT, put_state(m, st))
        z, p = G.predictor_zero(st), G.predictor_pole(st)
        ok[0] += s16(r0) == s16(z); ok[1] += s16(r1) == s16(p)
        if VERB and (s16(r0) != s16(z) or s16(r1) != s16(p)) and k < 400:
            print('  pred mismatch', st.__dict__, s16(r0), z, s16(r1), p)
    return ok[0] + ok[1], 2 * N


@test
def quantize():
    """0xebe quantize(d, y, qtab_721, 7)"""
    m = mach(); n = 0; N = 600
    for _ in range(N):
        d = R.choice([R.randint(-20, 20), R.randint(-4000, 4000), R.randint(-16384, 16383)])
        y = R.randint(544, 5120)
        n += m.call(QUANTIZE, d & M32, y, QTAB721, 7)[0] == G.quantize(d, y, G.QTAB_721, 7)
    return n, N


def upd_args(st):
    """random but self-consistent update() arguments for the 32 kbit/s codec"""
    i = R.randrange(16)
    y = G.step_size(st)
    dq = G.reconstruct(i & 8, G.DQLNTAB[i], y)
    if R.random() < 0.05:
        dq = R.choice([0, -0x8000])
    se = R.randint(-8000, 8000); sez = R.randint(-8000, 8000)
    sr = (se - (dq & 0x3FFF)) if dq < 0 else se + dq
    if R.random() < 0.05:
        sr = R.choice([0, -32768, 32767])
    dqsr = sr + sez - se
    if R.random() < 0.1:
        dqsr = 0
    return (4, y, G.WITAB[i] << 5, G.FITAB[i], dq, sr, dqsr)


@test
def update():
    """0xc80 update(4, y, wi, fi, dq, sr, dqsr, state): whole state compared"""
    m = mach(); n = 0; N = 400
    for k in range(N):
        st = R.choice(states()).copy()
        if R.random() < 0.2:
            st.td = 1
        args = upd_args(st)
        put_state(m, st)
        call_stack(m, UPDATE, args[:6], [args[6], m.sbuf])
        ref = st.copy(); G.update(*args, ref)
        got = get_state(m)
        n += got == ref
        if VERB and got != ref and k < 400:
            print('  update mismatch args', args)
            for f in ref.__dict__:
                if getattr(ref, f) != getattr(got, f):
                    print('    %s ref %s got %s' % (f, getattr(ref, f), getattr(got, f)))
    return n, N


@test
def encoder32():
    """0x720 g721_encoder(sl, state): codes + full state after each sample"""
    m = mach(); n = 0; N = 0
    for seed in range(3):
        st = G.State(); put_state(m, st)
        for x in signal(300, 100 + seed):
            c = m.call(ENC32, x & M32, m.sbuf)[0]
            rc = G.g721_encoder(x, st)
            N += 1; n += (c == rc and get_state(m) == st)
            if c != rc or get_state(m) != st:
                if VERB:
                    print('  enc diverged', seed, x, c, rc)
                put_state(m, st)        # resync
    return n, N


@test
def decoder32():
    """0x7d6 g721_decoder(i, out_coding=linear(3), state)"""
    m = mach(); n = 0; N = 0
    for seed in range(3):
        st = G.State(); put_state(m, st)
        codes = [R.randrange(16) for _ in range(150)]
        est = G.State(); codes += [G.g721_encoder(x, est) for x in signal(150, 200 + seed)]
        for c in codes:
            out = m.call(DEC32, c, 3, m.sbuf)[0]
            ro = G.g721_decoder(c, st)
            N += 1; good = (sx(out, 32) == ro and get_state(m) == st)
            n += good
            if not good:
                if VERB:
                    print('  dec diverged', seed, c, sx(out, 32), ro)
                put_state(m, st)
    return n, N


@test
def roundtrip32():
    """firmware encoder -> firmware decoder, compared with reference encoder -> decoder"""
    m = mach(); ok = 0; N = 0
    es, ds = m.alloc(bytes(128)), m.alloc(bytes(128))
    m.m.load(es, pack(G.State())); m.m.load(ds, pack(G.State()))
    rs, rd = G.State(), G.State()
    for x in signal(400, 999):
        c = m.call(ENC32, x & M32, es)[0]
        y = sx(m.call(DEC32, c, 3, ds)[0], 32)
        rc = G.g721_encoder(x, rs); ry = G.g721_decoder(rc, rd)
        N += 1; ok += (c == rc and y == ry)
    return ok, N


CODECS = {24: (0x4f8, 0x5ae), 40: (0x9a0, 0xa56)}


def codec_test(rate):
    """G.723-24/40 encoder(sl, state) and decoder(i, out_coding=3, state) from the same firmware"""
    enc, dec = CODECS[rate]
    m = mach(); n = 0; N = 0
    for seed in range(2):
        st = G.State(); put_state(m, st)
        for x in signal(250, 300 + seed):
            c = m.call(enc, x & M32, m.sbuf)[0]
            rc = G.encoder(rate, x, st)
            N += 1; good = c == rc and get_state(m) == st; n += good
            if not good:
                if VERB: print('  enc%d diverged' % rate, x, c, rc)
                put_state(m, st)
        st = G.State(); put_state(m, st)
        for c in [R.randrange(1 << G.RATES[rate]['bits']) for _ in range(250)]:
            out = sx(m.call(dec, c, 3, m.sbuf)[0], 32)
            ro = G.decoder(rate, c, st)
            N += 1; good = out == ro and get_state(m) == st; n += good
            if not good:
                if VERB: print('  dec%d diverged' % rate, c, out, ro)
                put_state(m, st)
    return n, N


@test
def codec24():
    return codec_test(24)


@test
def codec40():
    return codec_test(40)


if __name__ == '__main__':
    args = sys.argv[1:]
    if '-v' in args:
        VERB = True; args.remove('-v')
    tot = [0, 0]
    for t in TESTS:
        if args and t.__name__ not in args:
            continue
        try:
            p, n = t()
            print('%-12s %5d/%-5d %s' % (t.__name__, p, n, 'PASS' if p == n else 'FAIL'))
        except Exception as e:
            import traceback; traceback.print_exc()
            p, n = 0, 1
            print('%-12s ERROR %s' % (t.__name__, e))
        tot[0] += p; tot[1] += n
    print('total %d/%d' % tuple(tot))
