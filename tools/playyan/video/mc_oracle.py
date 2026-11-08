#!/usr/bin/env python3
"""Oracle for the firmware's motion compensation wrapper py_fw0 0x38a8 (ctx, mvx, mvy, 1-rc):
run it in the VC01 emulator (tools/playyan/vc01, vc01v vector model) for random MB positions and motion
vectors of any range, and compare with the window model the Rust/C decoders implement (fw_mc16).
usage: python3 tools/playyan/video/mc_oracle.py [N] [seed]   (run from anywhere; ~0.3 s per case)"""
import sys, random
import os
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
ISA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'vc01')  # the VC01 model (tools/playyan/vc01)
sys.path.pop(0)
import numpy  # noqa: F401  (before isa/ is on sys.path: isa/dis.py shadows the stdlib)
sys.path.insert(0, ISA); sys.path.insert(0, ISA + '/vec')
import types
for name in ('mp3_ext', 'mp4_ext', 'aac_ext', 'pix_ext'):
    st = types.ModuleType(name); st.H = {}; st.CFG = {}
    st.install = lambda *a, **k: None; st.uninstall = lambda *a, **k: None
    sys.modules[name] = st
import emu
from vc01v import VPU


def fw_mc16(mem, W, H, X, Y, mvx, mvy, rc):
    """Model of 0x38a8: 16x16 luma + 8x8 Cb, Cr prediction for MB origin (X, Y) and a half-pel
    MV, from the frame buffer `mem` (Y plane W*H, then Cb, Cr; flat addressing)."""
    uo, vo = W * H, W * H + W * H // 4
    px, py = 2 * X + mvx, 2 * Y + mvy

    def rd(a):
        return mem[a] if 0 <= a < len(mem) else 0

    def interp(get, ox, oy, fx, fy, n):
        out = []
        for j in range(n):
            row = []
            for i in range(n):
                a = get(ox + i, oy + j)
                if fx and fy:
                    v = (a + get(ox + i + 1, oy + j) + get(ox + i, oy + j + 1) + get(ox + i + 1, oy + j + 1) + 2 - rc) >> 2
                elif fx:
                    v = (a + get(ox + i + 1, oy + j) + 1 - rc) >> 1
                elif fy:
                    v = (a + get(ox + i, oy + j + 1) + 1 - rc) >> 1
                else:
                    v = a
                row.append(v)
            out.append(row)
        return out

    cxf = lambda m: (m >> 1) | (m & 1)
    if 0 <= px <= 2 * W - 32 and 0 <= py <= 2 * H - 32:
        Yp = interp(lambda x, y: rd(y * W + x), X + (mvx >> 1), Y + (mvy >> 1), mvx & 1, mvy & 1, 16)
        cx, cy = cxf(mvx), cxf(mvy)
        cw = W // 2
        res = [Yp]
        for base in (uo, vo):
            res.append(interp(lambda x, y: rd(base + y * cw + x), X // 2 + (cx >> 1), Y // 2 + (cy >> 1), cx & 1, cy & 1, 8))
        return res
    # edge path: 32x32 luma window, 16x16 chroma windows, motion vector & 31 inside them
    def axis(p, X, mv, size, n):
        """source coordinate for window index k (n = window size: 32 luma / 16 chroma)"""
        h = n // 2
        if p < 0:
            return lambda k: min(max(k - h, 0), size - 1)
        if p > 2 * (size if n == 32 else 2 * size) - 32:
            return lambda k: min(max(size - h + k, 0), size - 1)
        o = (X + (mv >> 1)) if n == 32 else ((X + (mv >> 1)) >> 1)
        o &= ~(h - 1)
        return lambda k: o + k
    ax = axis(px, X, mvx, W, 32); ay = axis(py, Y, mvy, H, 32)
    m5x, m5y = mvx & 31, mvy & 31
    Yp = interp(lambda i, j: rd(ay(j) * W + ax(i)), m5x >> 1, m5y >> 1, m5x & 1, m5y & 1, 16)
    cw, ch = W // 2, H // 2
    cax = axis(px, X, mvx, cw, 16); cay = axis(py, Y, mvy, ch, 16)
    cx, cy = cxf(m5x), cxf(m5y)
    res = [Yp]
    for base in (uo, vo):
        res.append(interp(lambda i, j: rd(base + cay(j) * cw + cax(i)), cx >> 1, cy >> 1, cx & 1, cy & 1, 8))
    return res


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    R = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    m = emu.Machine('py_fw0'); m.cpu.vpu = VPU(m.cpu)
    m.call(0x26450)
    vp = m.cpu.vpu
    ok = tot = 0
    for W, H in ((240, 176), (176, 144), (192, 144)):
        mem = bytes(R.randrange(256) for _ in range(W * H * 3 // 2))
        FB = 0x00400000
        m.m.load(FB, mem)
        ctx = m.alloc(bytes(0x200)); tl = m.alloc(bytes(32 * 40)); tu = m.alloc(bytes(16 * 24)); tv = m.alloc(bytes(16 * 24))
        for off, val in ((0x184, W), (0x188, H), (0x194, W * H), (0x198, W * H + W * H // 4), (0x1e8, FB), (0x1ec, tl), (0x1f0, tu), (0x1f4, tv)):
            m.m.write(ctx + off, 4, val)
        for t in range(n // 3):
            mbx = R.choice([0, 1, W // 16 - 1, W // 16 - 2, R.randrange(W // 16)])
            mby = R.choice([0, 1, H // 16 - 1, H // 16 - 2, R.randrange(H // 16)])
            lim = R.choice([32, 64, 128, 256])
            mvx = R.randint(-lim, lim - 1); mvy = R.randint(-lim, lim - 1); rc = R.randrange(2)
            m.m.write(ctx + 0x1c8, 4, 16 * mbx); m.m.write(ctx + 0x1cc, 4, 16 * mby)
            m.call(0x38a8, ctx, mvx & 0xffffffff, mvy & 0xffffffff, 1 - rc)
            want = fw_mc16(mem, W, H, 16 * mbx, 16 * mby, mvx, mvy, rc)
            for k, (y0, x0, s) in enumerate(((32, 0, 16), (32, 32, 8), (32, 40, 8))):
                o = [[vp.get16(y0 + j, x0 + i) for i in range(s)] for j in range(s)]
                tot += 1; ok += o == want[k]
                if o != want[k] and '-v' in sys.argv:
                    print('  W', W, 'mb', mbx, mby, 'mv', mvx, mvy, 'plane', k, o[0][:8], want[k][0][:8])
    print('mc_oracle 0x38a8: %d/%d planes match the window model' % (ok, tot))


if __name__ == '__main__':
    main()
