"""C pygba (GBA 8-bit speaker stream) vs the firmware routine 0x3185e on the C VC01 core."""
import ctypes as C, random, struct, sys
from fwmp3 import FwMp3
from cref import CMp3

def fw_stream(rate, vol, chunks):
    d = FwMp3(); gp = d.gp
    def wr(off, v): struct.pack_into('<I', d.mv, gp + off, v & 0xffffffff)
    wr(1520, 0); struct.pack_into('<I', d.mv, gp + 37748, 1); wr(1424, rate); wr(1660, vol)
    for o in (1412, 1564, 1416, 1420): wr(o, 0)
    sent = []
    def snd(m):
        r = m.r; sent.append(bytes(m.mv[r[2]:r[2] + r[3]])); r[31] = r[26]
    d.set_hooks({0x33670: snd})
    W = d.malloc(4 * 4096)
    for ws in chunks:
        d.mv[W:W + 4 * len(ws)] = struct.pack('<%dI' % len(ws), *ws)
        d.call(0x3185e, W, len(ws))
    A = []; B = []
    for blk in sent:
        A += [x - 256 if x > 127 else x for x in blk[:576]]; B += [x - 256 if x > 127 else x for x in blk[576:1152]]
    return A, B

def c_stream(rate, vol, chunks):
    c = CMp3(); L = c.L
    g = (C.c_uint8 * 4096)()
    L.pygba_init(g, rate, vol)
    A = []; B = []
    EMIT = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(C.c_int8), C.POINTER(C.c_int8))
    def emit(u, a, b):
        A.extend(a[i] for i in range(576)); B.extend(b[i] for i in range(576))
    cb = EMIT(emit)
    L.pygba_push.argtypes = [C.c_void_p, C.c_void_p, C.c_int, EMIT, C.c_void_p]
    for ws in chunks:
        lr = []
        for w in ws:
            l, r = w & 0xffff, w >> 16
            lr += [l - 65536 if l > 32767 else l, r - 65536 if r > 32767 else r]
        buf = (C.c_int16 * len(lr))(*lr)
        L.pygba_push(g, buf, len(ws), cb, None)
    return A, B

if __name__ == '__main__':
    R = random.Random(3); ok = 0; n = 0
    for rate, vol in ((44100, 32), (48000, 20), (32000, 45), (44100, 56), (48000, 0), (32000, 63), (44100, 7)):
        chunks = [[R.getrandbits(32) for _ in range(1152)] for _ in range(4)]
        a = fw_stream(rate, vol, chunks); b = c_stream(rate, vol, chunks)
        n += 1; ok += a == b
        print(rate, vol, 'blocks', len(a[0]) // 576, 'exact' if a == b else 'DIFF')
    print('gba %d/%d' % (ok, n))
