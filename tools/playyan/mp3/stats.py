"""Side-info statistics of MPEG-1 L3 files: block types, stereo modes (coverage of a test set)."""
import sys, collections
from fwmp3 import frames

def sideinfo(d, off):
    h = int.from_bytes(d[off:off + 4], 'big'); p = off + 4 + (0 if (h >> 16) & 1 else 2)
    mode = (h >> 6) & 3; mx = (h >> 4) & 3; nch = 1 if mode == 3 else 2
    bits = int.from_bytes(d[p:p + 32], 'big'); n = 256; pos = [0]
    def g(k):
        v = (bits >> (n - pos[0] - k)) & ((1 << k) - 1); pos[0] += k; return v
    g(9); g(5 if nch == 1 else 3); [g(4) for _ in range(nch)]
    out = []
    for gr in range(2):
        for ch in range(nch):
            g(12); g(9); g(8); g(4); ws = g(1)
            if ws: bt = g(2); mixed = g(1); g(10); g(9)
            else: bt = 0; mixed = 0; g(15); g(7)
            g(3)
            out.append((bt, mixed))
    return mode, mx, out

if __name__ == '__main__':
    for f in sys.argv[1:]:
        d = open(f, 'rb').read(); c = collections.Counter()
        for off, *_ in frames(d):
            mode, mx, gc = sideinfo(d, off)
            c['mode%d/%d' % (mode, mx)] += 1
            for bt, m in gc: c['bt%d%s' % (bt, 'm' if m else '')] += 1
        print(f.split('/')[-1], dict(sorted(c.items())))
