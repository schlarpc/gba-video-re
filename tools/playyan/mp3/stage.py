"""Compare decoder state (is[], xr[]) after frame K of a fuzz dump: python3 stage.py DUMP K"""
import sys, struct, ctypes as C
from fwmp3 import FwMp3
from cref import CMp3
buf = open(sys.argv[1], 'rb').read(); K = int(sys.argv[2])
BR = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]; SR = [44100, 48000, 32000, 1]
offs = []; p = 0
while p + 4 <= len(buf) and len(offs) < 8:
    h = int.from_bytes(buf[p:p + 4], 'big'); n = 144000 * BR[(h >> 12) & 15] // SR[(h >> 10) & 3] + ((h >> 9) & 1)
    offs.append((p, n)); p += n
fw = FwMp3(); fw.load(buf); c = CMp3()
c.L.pymp3_debug.restype = C.c_void_p; c.L.pymp3_debug.argtypes = [C.c_void_p, C.c_int]
D = fw.dec
for k, (off, n) in enumerate(offs[:K + 1]):
    fw.frame(off); c.frame(buf[off:off + n + 8])
cis = (C.c_int16 * (4 * 592)).from_address(c.L.pymp3_debug(c.d, 0))
cxr = (C.c_int32 * (4 * 592)).from_address(c.L.pymp3_debug(c.d, 1))
ptr = fw.rd32(D + 0x2a58, 4)
for gr in range(2):
    for ch in range(2):
        fis = fw.rd16(D + 0x1858 + gr * 2304 + ch * 1152, 576)
        mis = [i for i in range(576) if fis[i] != cis[(gr * 2 + ch) * 592 + i]]
        fxr = fw.rd32(ptr[gr * 2 + ch] & 0xffffffff, 576)
        mxr = [i for i in range(576) if fxr[i] != cxr[(gr * 2 + ch) * 592 + i]]
        print('gr', gr, 'ch', ch, 'is diff', len(mis), mis[:6], [(fis[i], cis[(gr*2+ch)*592+i]) for i in mis[:4]],
              'xr diff', len(mxr), mxr[:6], [(fxr[i], cxr[(gr*2+ch)*592+i]) for i in mxr[:3]])
cov = (C.c_int32 * (2 * 32 * 18)).from_address(c.L.pymp3_debug(c.d, 2))
OVB = fw.rd32(D + 0x3340 + 32)[0] & 0xffffffff
def planar(addr):
    lo = fw.rd16(addr, 16); hi = fw.rd16(addr + 32, 16)
    return [(h << 16) | (l & 0xffff) for l, h in zip(lo, hi)]
for ch in range(2):
    bad = []
    for k in range(18):
        row = planar(OVB + ch * 2304 + 128 * k) + planar(OVB + ch * 2304 + 128 * k + 64)
        for sb in range(32):
            v = row[sb]; v = v - (1 << 32) if v >= 1 << 31 else v
            if v != cov[(ch * 32 + sb) * 18 + k]: bad.append((sb, k, v, cov[(ch * 32 + sb) * 18 + k]))
    print('ov ch', ch, 'diffs', len(bad), bad[:6])
