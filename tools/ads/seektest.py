#!/usr/bin/env python3
"""Seek test: decode from every chunk start (keyframe) of a stream with --start-frame and compare
the first frames with the manifest (cartridge) md5s. usage: seektest.py CART STREAM [N]"""
import sys, os, gzip, csv, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carts
from explain_stale import chunks_of
cid, nm = sys.argv[1], sys.argv[2]; n = int(sys.argv[3]) if len(sys.argv) > 3 else 3
want = [r['md5'] for r in csv.DictReader(gzip.open(os.path.join(HERE, '..', '..', 'verify', 'ads', f"{cid}_{nm.replace(' ', '_')}.frame_md5.csv.gz"), 'rt'))]
d, chs = chunks_of(cid, nm)
f0 = 0; ok = bad = 0
for c in chs:
    out = subprocess.run([os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec'), carts.rom_path(cid), '--stream', nm, '--start-frame', str(f0), '--count', str(n), '--md5']
                         + carts.video_args(cid, nm), capture_output=True, text=True).stdout.split()
    got = out[1::2]
    if got == want[f0:f0 + len(got)]: ok += 1
    else: bad += 1; print('seek mismatch at frame', f0)
    f0 += c['nfr']
print(cid, nm, 'keyframes ok', ok, 'bad', bad)
