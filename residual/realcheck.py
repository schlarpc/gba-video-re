#!/usr/bin/env python3
"""Cross-check residual.c bit consumption on the real movie stream: walk frames with
container/vxvwalk.py and, at every residual, run vx_residual() from the same bit position
and require it to end at the same bit and return 0."""
import ctypes, os, struct, sys, subprocess
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, '../container'))
import vxvwalk
so = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'libvxres_real.so')
subprocess.check_call(['gcc', '-O2', '-shared', '-fPIC', '-o', so, os.path.join(H, '../decoder/residual.c')])
lib = ctypes.CDLL(so)
lib.vx_residual.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
class B(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('size', ctypes.c_size_t), ('pos', ctypes.c_size_t),
                ('buf', ctypes.c_uint32), ('cnt', ctypes.c_int)]
class Q(ctypes.Structure):
    _fields_ = [('scale', ctypes.c_int32 * 3), ('qp', ctypes.c_int)]
rom = open(os.path.join(H, '../shrek.gba'), 'rb').read()
F = 0x20200
w, h = struct.unpack_from('<II', rom, F + 8)
nfr = int(sys.argv[1]) if len(sys.argv) > 1 else 40
video = rom[F + 0x38:F + 0x38 + 0x1800000]
vbuf = ctypes.create_string_buffer(video, len(video))
lut = rom[0x9ae4:0x9ae4 + 0x2100]
Y = ctypes.create_string_buffer(256 * 32); UV = ctypes.create_string_buffer(256 * 32)
q = Q((ctypes.c_int32 * 3)(10, 13, 16), 0)
stats = [0, 0]
orig = vxvwalk.PyWalker.residual
def residual(self, bw, bh):
    p0 = self.p
    orig(self, bw, bh)
    bs = B(); bs.base = ctypes.cast(vbuf, ctypes.c_void_p); bs.size = len(video)
    hw = (p0 >> 4) * 2; skip = p0 & 15
    g = lambda o: video[o] | video[o + 1] << 8
    bs.buf = (((g(hw) << 16) | g(hw + 2)) << skip) & 0xffffffff; bs.cnt = 16 - skip; bs.pos = hw + 4
    rc = lib.vx_residual(ctypes.byref(bs), ctypes.byref(q), ctypes.addressof(Y) + 8,
                         ctypes.addressof(UV) + 8, bw, bh)
    end = bs.pos * 8 - (bs.cnt + 16)
    stats[0] += 1
    if rc != 0 or end != self.p:
        stats[1] += 1
        if stats[1] < 5: print('mismatch at bit', p0, bw, bh, 'rc', rc, 'end', end, 'walker', self.p)
vxvwalk.PyWalker.residual = residual
wk = vxvwalk.PyWalker(video, lut)
for f in range(nfr):
    wk.frame(w, h)
print('%d frames, %d residual calls, %d mismatches, end bit %d' % (nfr, stats[0], stats[1], wk.p))
