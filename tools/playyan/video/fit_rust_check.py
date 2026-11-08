#!/usr/bin/env python3
"""Rust Fitter (examples/fitdump) vs the firmware fit 0x2749a in the emulator, random I420 pictures
of the given sizes.  usage: fit_rust_check.py FW_FILE WxH...  [--thumb]"""
import os, random, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fit_oracle  # noqa
import emu, pix_thumb as P
from vc01v import VPU
FD = os.path.join(HERE, '..', '..', '..', 'target', 'release', 'examples', 'fitdump')
fw = sys.argv[1]; thumb = '--thumb' in sys.argv
dw, dh = (60, 40) if thumb else (240, 160)
R = random.Random(9); ok = n = 0
for s in [a for a in sys.argv[2:] if 'x' in a]:
    W, H = map(int, s.split('x'))
    yuv = bytes(R.randrange(256) for _ in range(W * H)) + bytes(R.randrange(40, 216) for _ in range(W * H // 2))
    m = emu.Machine('py_fw0'); m.cpu.vpu = VPU(m.cpu); m.call(0x26450)
    # the decoder's picture layout: MB-aligned pitch and rows, Cb then Cr at pitch/2
    sp, hp = (W + 15) & ~15, (H + 15) & ~15
    buf = bytearray(sp * hp * 3 // 2 + 256)
    for y in range(H):
        buf[y * sp:y * sp + W] = yuv[y * W:(y + 1) * W]
    for k, base in ((0, sp * hp), (1, sp * hp * 5 // 4)):
        for y in range(H // 2):
            o = W * H + k * (W // 2) * (H // 2) + y * (W // 2)
            buf[base + y * (sp // 2):base + y * (sp // 2) + W // 2] = yuv[o:o + W // 2]
    src = P.desc(m, 3, W, H, sp, sp * hp * 3 // 2, m.alloc(bytes(buf)))
    dpitch = 2 * ((dw + 15) & ~15)
    dp = m.alloc(bytes(dpitch * dh + 256))
    dst = P.desc(m, 1, dw, dh, dpitch, dpitch * dh, dp)
    m.call(0x2749a, dst, src, 2, maxsteps=50_000_000)
    e = [m.m.read(dp + y * dpitch + 2 * x, 2) for y in range(dh) for x in range(dw)]
    with tempfile.TemporaryDirectory() as td:
        open(td + '/i', 'wb').write(bytes(buf[:sp * hp * 3 // 2]))
        subprocess.run([FD, fw, str(W), str(H), td + '/i', td + '/o', str(dw), str(dh)], check=True)
        r = struct.unpack('<%dH' % (dw * dh), open(td + '/o', 'rb').read())
    d = sum(a != b for a, b in zip(e, r)); n += 1; ok += d == 0
    print('%s: %s' % (s, 'exact' if d == 0 else '%d pixels differ' % d), [(i // dw, i % dw, hex(e[i]), hex(r[i])) for i in range(dw * dh) if e[i] != r[i]][:6], flush=True)
print('%d/%d sizes bit-exact' % (ok, n))
