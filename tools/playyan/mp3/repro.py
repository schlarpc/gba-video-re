"""Replay a fuzz failure dump (work/fuzz_fail_N.bin): frame by frame C vs firmware."""
import sys
from fwmp3 import FwMp3, frames
from cref import CMp3
import stats
buf = open(sys.argv[1], 'rb').read()
# frames at the original boundaries: rescan from offset 0 trusting headers is unsafe; use the
# fuzz convention (8 frames, lengths from headers of the first, unmutated frames)
offs = []; p = 0
BR = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]; SR = [44100, 48000, 32000, 1]
while p + 4 <= len(buf) and len(offs) < 8:
    h = int.from_bytes(buf[p:p + 4], 'big')
    n = 144000 * BR[(h >> 12) & 15] // SR[(h >> 10) & 3] + ((h >> 9) & 1)
    offs.append((p, n)); p += n
fw = FwMp3(); fw.load(buf); c = CMp3()
for k, (off, n) in enumerate(offs):
    rf, pf, _ = fw.frame(off); rc, pc, _ = c.frame(buf[off:off + n + 8])
    h = buf[off:off + 4].hex()
    try: si = stats.sideinfo(buf, off)
    except Exception: si = None
    d = [i for i in range(2304) if pf[i] != pc[i]]
    print(k, hex(off), h, 'rf', rf, 'rc', rc, 'ndiff', len(d), d[:4], [(pf[i], pc[i]) for i in d[:4]], si)
