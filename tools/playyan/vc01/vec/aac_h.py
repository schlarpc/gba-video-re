"""Shared helpers for AAC kernel tests (mi_fw0)."""
import sys, os, struct, random
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import emu, vc01v
M = {}
def mach(img=os.environ.get('AAC_IMG', 'mi_fw0')):
    if img not in M:
        m = emu.Machine(img); m.cpu.vpu = vc01v.VPU(m.cpu); M[img] = m
    return M[img]
def put32(m, a, xs): m.m.load(a, struct.pack('<%di' % len(xs), *xs))
def get32(m, a, n): return list(struct.unpack('<%di' % n, bytes(m.m.read(a + k, 1) for k in range(4 * n))))
def put16(m, a, xs): m.m.load(a, struct.pack('<%dh' % len(xs), *xs))
def get16(m, a, n): return list(struct.unpack('<%dh' % n, bytes(m.m.read(a + k, 1) for k in range(2 * n))))
def buf(m, nbytes, key):
    if not hasattr(m, 'bufs'): m.bufs = {}
    if key not in m.bufs: m.bufs[key] = m.alloc(bytes(nbytes + 64))
    return m.bufs[key]
from vc01v import sx
def put_block(m, a, Z):
    """16 complex int32 -> planar block [re lo][re hi][im lo][im hi] (firmware FFT layout)."""
    re = [int(z.real) for z in Z]; im = [int(z.imag) for z in Z]
    put16(m, a, [sx(x & 0xffff, 16) for x in re] + [sx((x >> 16) & 0xffff, 16) for x in re] +
          [sx(x & 0xffff, 16) for x in im] + [sx((x >> 16) & 0xffff, 16) for x in im])
def get_block(m, a):
    H = [h & 0xffff for h in get16(m, a, 64)]
    return [complex(sx(H[t] | H[16 + t] << 16, 32), sx(H[32 + t] | H[48 + t] << 16, 32)) for t in range(16)]
