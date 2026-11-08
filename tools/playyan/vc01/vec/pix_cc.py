#!/usr/bin/env python3
"""Drive the py_fw0 YUV420 -> 16-bit RGB converter 0x28612 directly."""
import sys, struct, random
import os
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import emu
from vc01 import M32

def conv_block(m, Y, U, V, W=16, H=16):
    """Y: H rows of W bytes, U/V: H/2 rows of W/2.  returns H x W 16-bit words."""
    yb = m.alloc(bytes(sum(Y, [])) + bytes(64)); ub = m.alloc(bytes(sum(U, [])) + bytes(64)); vb = m.alloc(bytes(sum(V, [])) + bytes(64))
    dst = m.alloc(bytes(2 * W * H + 64))
    m.m.write(emu.STACK, 4, 2 * W); m.m.write(emu.STACK + 4, 4, W)
    m.call(0x28612, yb, ub, vb, dst, W, W // 2)
    return [[m.m.read(dst + 2 * (y * W + x), 2) for x in range(W)] for y in range(H)]

def mach():
    m = emu.Machine('py_fw0')
    from vc01v import VPU
    m.cpu.vpu = VPU(m.cpu)
    return m

if __name__ == '__main__':
    cfg = dict(a.split('=') for a in sys.argv[1:])
    m = mach()
    for (y, u, v) in [(16,128,128),(235,128,128),(128,128,128),(81,90,240),(145,54,34),(41,240,110)]:
        out = conv_block(m, [[y]*16]*16, [[u]*8]*8, [[v]*8]*8)
        vals = set(sum(out, []))
        print((y,u,v), ['%04x' % x for x in sorted(vals)][:4])
    R = random.Random(1)
    Y = [[16*r + c for c in range(16)] for r in range(16)]
    out = conv_block(m, Y, [[128]*8]*8, [[128]*8]*8)
    for row in out[:3]: print(' '.join('%04x' % x for x in row))
