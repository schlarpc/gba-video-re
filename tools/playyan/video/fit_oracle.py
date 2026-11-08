#!/usr/bin/env python3
"""Oracle for the frame fit + conversion 0x2749a (mode 2) at any source size: run it in the
Python VC01 model on an I420 picture and print/compare the 240x160 RGB565 result.
usage: fit_oracle.py YUV W H INDEX [DUMP.bin]"""
import sys, time
import os
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
ISA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'vc01')  # the VC01 model (tools/playyan/vc01)
sys.path.pop(0)
import numpy  # noqa: F401
sys.path.insert(0, ISA); sys.path.insert(0, ISA + '/vec')
import types
for name in ('mp3_ext', 'mp4_ext', 'aac_ext', 'pix_ext'):
    st = types.ModuleType(name); st.H = {}; st.CFG = {}
    st.install = lambda *a, **k: None; st.uninstall = lambda *a, **k: None
    sys.modules[name] = st
import emu
from vc01v import VPU
import pix_thumb as P


def run(yuv, W, H, dw=240, dh=160):
    m = emu.Machine('py_fw0'); m.cpu.vpu = VPU(m.cpu)
    m.call(0x26450)
    return P.thumbnail(m, yuv, W, H, dw=dw, dh=dh, mode=2)


if __name__ == '__main__':
    path, W, H, k = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    fs = W * H * 3 // 2
    yuv = open(path, 'rb').read()[k * fs:(k + 1) * fs]
    t = time.time()
    px = run(yuv, W, H)
    print('%.1f s' % (time.time() - t))
    if len(sys.argv) > 5:
        import struct
        fw = struct.unpack('<38400H', open(sys.argv[5], 'rb').read())
        d = sum(a != b for a, b in zip(px, fw))
        print('differs in', d, 'of 38400 pixels')
        print('positions', [(i // 240, i % 240) for i in range(38400) if px[i] != fw[i]][:20])
