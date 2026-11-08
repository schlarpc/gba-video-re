#!/usr/bin/env python3
"""End-to-end thumbnail check: Python MPEG-4 I-VOP decode (m4v_intra) with a chosen IDCT ->
firmware thumbnail path (pix_ref, proven equal to the emulated firmware) -> GBA hash."""
import sys, os, functools
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import m4v_intra, pix_ref
from pix_thumb import HASH, ghash
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import workdir
D = workdir.work('vec') + '/'   # m4v/<NAME>.m4v from vec/prep_media.py

class FwIDCT:
    """Run the firmware IDCT kernel 0x26002 (py_fw0) in the emulator, 4 blocks per call."""
    def __init__(self, transpose=False, **cfg):
        import emu
        from vc01v import VPU
        self.M = emu.Machine('py_fw0'); self.M.cpu.vpu = VPU(self.M.cpu); self.vp = self.M.cpu.vpu
        self.M.call(0x26450); self.T = transpose; self.cache = {}
    def __call__(self, blk):
        key = tuple(map(tuple, blk))
        r = self.cache.get(key)
        if r is None:
            b = [list(x) for x in zip(*blk)] if self.T else blk
            vp = self.vp
            for y in range(16):
                for x in range(16): vp.wcell(y, x, True, 0)
            for y in range(8):
                for x in range(8): vp.wcell(y, x, True, b[y][x])
            self.M.call(0x26002, 0, 1, 1, 1, 1)
            o = [[vp.rcell(y, x, True) for x in range(8)] for y in range(8)]
            r = [list(x) for x in zip(*o)] if self.T else o
            self.cache[key] = r
        return r

def run(names, idct, swapuv=False):
    ok = 0
    for n in names:
        W, H, Y, U, V = m4v_intra.decode(D + 'm4v/%s.m4v' % n, idct)
        px = [pix_ref.rgb565_to_bgr555(p) for p in pix_ref.thumbnail(bytes(Y + U + V), swapuv=swapuv)]
        h = ghash(px); ok += h == HASH[n]
        print('%-9s %08x %08x %s' % (n, h, HASH[n], 'MATCH' if h == HASH[n] else ''), flush=True)
    return ok

if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if '=' not in a]
    cfg = dict(a.split('=') for a in sys.argv[1:] if '=' in a)
    which = cfg.pop('idct', 'fw'); T = cfg.pop('T', '0') == '1'; sw = cfg.pop('swapuv', '0') == '1'
    for k in ('OP16', 'OP21', 'HSUM'):
        if k in cfg: setattr(pix_ref, k, cfg.pop(k))
    idct = FwIDCT(T, **cfg)
    names = args or sorted(HASH)
    print('matched', run(names, idct, sw), '/', len(names))
