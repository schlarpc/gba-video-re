#!/usr/bin/env python3
"""Compare the C reference decoder (reference/playyan/video/pyvdec --rgb565) with raw firmware
frame dumps (LLE_FRAMES format; index.txt elapsed_ms = pts + 100).
usage: ccmp.py FILE.asf DUMPDIR"""
import os, subprocess, sys, tempfile, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
PYVDEC = os.path.join(HERE, '..', '..', '..', 'reference', 'playyan', 'video', 'pyvdec')
asf, dump = sys.argv[1], sys.argv[2]
idx = {}
for l in open(os.path.join(dump, 'index.txt')):
    n, _, _, ms = l.split()
    idx[int(ms) - 100] = int(n)
with tempfile.TemporaryDirectory() as td:
    lines = subprocess.run([PYVDEC, asf, '--rgb565', '--out', td], capture_output=True, text=True).stdout.split('\n')
    ok = n = 0
    for l in lines:
        if not l:
            continue
        i, pts, ty, md5 = l.split()
        k = idx.get(int(pts))
        if k is None:
            continue
        a = open(os.path.join(td, 'f%05d.bin' % int(i)), 'rb').read()
        b = open(os.path.join(dump, 'f%05d.bin' % k), 'rb').read()
        n += 1; ok += a == b
    print('%s: C reference %d/%d dumped frames bit-exact' % (os.path.basename(asf), ok, n))
