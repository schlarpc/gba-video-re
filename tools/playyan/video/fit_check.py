#!/usr/bin/env python3
"""fit_model vs the emulator (fit_oracle.run) and, if given, the firmware's frame dumps.
usage: fit_check.py YUV W H N [DUMPDIR]   (frames 0..N-1; dump fK = frame K)"""
import os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fit_model
path, W, H, N = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
dump = sys.argv[5] if len(sys.argv) > 5 else None
fs = W * H * 3 // 2
data = open(path, 'rb').read()
st = {}
okd = n = 0
for k in range(N):
    yuv = data[k * fs:(k + 1) * fs]
    mine = fit_model.fit_frame(yuv, W, H, state=st)
    if dump and os.path.exists(os.path.join(dump, 'f%05d.bin' % k)):
        fw = struct.unpack('<38400H', open(os.path.join(dump, 'f%05d.bin' % k), 'rb').read())
        d = [i for i in range(38400) if mine[i] != fw[i]]
        n += 1; okd += not d
        print('frame', k, 'vs firmware dump: differs in', len(d), [(i // 240, i % 240) for i in d[:6]])
if '--emu' in sys.argv:
    import fit_oracle
    yuv = data[:fs]
    emu_px = fit_oracle.run(yuv, W, H)
    mine = fit_model.fit_frame(yuv, W, H)
    d = [i for i in range(38400) if mine[i] != emu_px[i]]
    print('frame 0 vs emulator: differs in', len(d), [(i // 240, i % 240) for i in d[:8]])
print('%d/%d frames match the dumps' % (okd, n))
