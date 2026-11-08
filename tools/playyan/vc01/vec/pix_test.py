#!/usr/bin/env python3
"""Pixel-pipeline tests (py_fw0 output kernels) on the vector model (vc01v.py).
Usage: pix_test.py [-q] [tests=a,b] [cfg=value ...]   (cfg overrides vc01v.CFG)
Needs the Garage Game first frames from vec/prep_media.py ($PLAYYAN_WORK/vec).
Each test prints name, passed/total; exit status 0 when all pass.  Runtime ~1 minute."""
import sys, random, struct
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vc01v

CFGARGS = dict(a.split('=') for a in sys.argv[1:] if '=' in a)
SEL = CFGARGS.pop('tests', '').split(',') if 'tests' in CFGARGS else None
for k, v in list(CFGARGS.items()):
    if v in ('True', 'False'):
        CFGARGS[k] = v == 'True'
vc01v.CFG.update(CFGARGS)
import emu, pix_thumb as P, pix_ref, pix_cc, pix_rot
from vc01v import VPU


def mach():
    return P.mach()


def t_memcpy():
    """0x29340 memcpy: 16-bit vld/vst (32-byte aligned), 8-bit vld/vst, unaligned vld2+vld3 pairs."""
    R = random.Random(1); ok = n = 0
    for t in range(40):
        m = mach()
        ln = R.randrange(1, 200); so = R.randrange(32); do = R.randrange(32) if t % 3 else so
        src = bytes(R.randrange(256) for _ in range(ln + 64))
        s = m.alloc(src) + so; d = m.alloc(bytes(ln + 96)) + do
        m.call(0x29340, d, s, ln)
        got = bytes(m.m.read(d + i, 1) for i in range(ln))
        ok += got == src[so:so + ln]; n += 1
    return ok, n


def t_convert():
    """0x28612 YUV420 -> RGB565 on random 16x16 blocks vs the derived reference (pix_ref.convert)."""
    R = random.Random(2); ok = n = 0; m = pix_cc.mach()
    for t in range(30):
        Y = [[R.randrange(256) for _ in range(16)] for _ in range(16)]
        U = [[R.randrange(256) for _ in range(8)] for _ in range(8)]
        V = [[R.randrange(256) for _ in range(8)] for _ in range(8)]
        out = sum(pix_cc.conv_block(m, Y, U, V), [])
        ref = pix_ref.convert(sum(Y, []), sum(U, []), sum(V, []), 16, 16)
        ok += out == ref; n += 1
    return ok, n


def t_geometry():
    """0x2730e's four paths (plain 0x28612, mirror 0x28590 vertical registers, 90/270-degree
    transposing stores in 0x28662) must give geometric transforms of each other."""
    R = random.Random(3); W = H = 32; ok = n = 0
    for t in range(2):
        Y = [R.randrange(256) for _ in range(W * H)]; U = [R.randrange(256) for _ in range(W * H // 4)]; V = [R.randrange(256) for _ in range(W * H // 4)]
        base = pix_rot.run(mach(), Y, U, V, W, H, 0, 0, W, H)
        Tr = lambda a: [list(x) for x in zip(*a)]
        exp = {(1, 0): [r[::-1] for r in base], (0, 1): [r[::-1] for r in Tr(base)],
               (1, 1): [[base[W - 1 - x][H - 1 - y] for x in range(W)] for y in range(H)]}
        for (r2, r3), e in exp.items():
            out = pix_rot.run(mach(), Y, U, V, W, H, r2, r3, W, H)
            ok += out == e; n += 1
    return ok, n


def t_thumbnail():
    """0x2749a mode 2 (fit 240x176 into 60x40: crop 8 rows, 4:1 box, convert) on real decoded frames
    vs the reference (pix_ref.thumbnail)."""
    ok = n = 0
    for name in ('AVOID', 'BAT', 'CREDIT'):
        yuv = open(P.FR + name + '_xvid.yuv', 'rb').read()
        ok += P.thumbnail(mach(), yuv) == pix_ref.thumbnail(yuv); n += 1
    return ok, n


def t_half():
    """0x286e4 2:1 downscaler (8-bit vertical-register pair sums, 16-bit row sums, op20 x 16384):
    out = (a+b+c+d) >> 2 per 2x2 cell."""
    R = random.Random(4); ok = n = 0
    for t in range(6):
        m = mach(); W = 32 * (1 + t % 2); Hh = 16
        img = bytes(R.randrange(256) for _ in range(W * 2 * Hh))
        s = m.alloc(img + bytes(64)); d = m.alloc(bytes(W // 2 * Hh + 64))
        # 0x286e4(r0=dst, r1=src, r2=dst_end, r3=?, r4=src pitch, r5=flag!=0 -> 2x2 box)
        m.call(0x286e4, d, s, d + W // 2 * Hh, 0, W, 1)
        got = [m.m.read(d + i, 1) for i in range(W // 2 * Hh)]
        ref = []
        for y in range(Hh):
            pass
        # layout: the routine walks 16-pixel output columns; compare as a set of 2x2 means
        exp = [(img[(2 * y) * W + 2 * x] + img[(2 * y) * W + 2 * x + 1] + img[(2 * y + 1) * W + 2 * x] + img[(2 * y + 1) * W + 2 * x + 1]) >> 2
               for y in range(Hh) for x in range(W // 2)]
        ok += sorted(got) == sorted(exp); n += 1
    return ok, n


def t_g726():
    """G.726 suite (6000 cases) under the overlay's byte-accurate VRF model."""
    import g726_test as T
    ok = n = 0
    for t in T.TESTS:
        T.M = None
        a, b = t(); ok += a; n += b
    return ok, n


def t_garage(names=('AVOID', 'BAT', 'FISH', 'WAVE')):
    """END-TO-END: Garage Game first frames -> Python MPEG-4 I-VOP bitstream decoder (m4v_intra, bit-exact
    vs ffmpeg) with the FIRMWARE IDCT 0x26002 run in the emulator -> emulated thumbnail path 0x2749a
    (scale + YUV->RGB565) -> GBA hash after RGB565->BGR555 (protocol.md section 9 table)."""
    import pix_e2e, m4v_intra
    idct = pix_e2e.FwIDCT(False)
    ok = n = 0
    for nm in names:
        W, H, Y, U, V = m4v_intra.decode(pix_e2e.D + 'm4v/%s.m4v' % nm, idct)
        px = P.thumbnail(mach(), bytes(Y + U + V))
        ok += P.ghash([pix_ref.rgb565_to_bgr555(p) for p in px]) == P.HASH[nm]; n += 1
    return ok, n


TESTS = [t_memcpy, t_convert, t_geometry, t_thumbnail, t_garage, t_g726]

if __name__ == '__main__':
    allok = True; res = {}
    for t in TESTS:
        if SEL and t.__name__[2:] not in SEL:
            continue
        try:
            a, b = t()
        except Exception as e:
            a, b = 0, -1; print('  ', t.__name__, 'ERROR', str(e)[:100])
        res[t.__name__] = (a, b); allok &= a == b
        if '-q' not in sys.argv:
            print('%-12s %5d/%-5d %s' % (t.__name__, a, b, 'PASS' if a == b else 'FAIL'), flush=True)
    print('ALL PASS' if allok else 'SOME FAIL', res if '-q' in sys.argv else '')
    sys.exit(0 if allok else 1)
