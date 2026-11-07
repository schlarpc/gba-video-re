#!/usr/bin/env python3
"""Which stream did a capture play? usage: identify.py CART CAPDIR"""
import sys, os, subprocess, csv
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carts
ADSDEC = os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec')
def streams(cid):
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    return [l.split('mmstr@')[0].split(None, 1)[1].strip() for l in out.splitlines() if l and l[0].isdigit()]
def identify(cid, cap, n=400):
    caps = [r['fb_md5'] for r in csv.DictReader(open(os.path.join(cap, 'frames.csv')))]
    best = []
    for s in streams(cid):
        out = subprocess.run([ADSDEC, carts.rom_path(cid), '--stream', s, '--md5', '--count', str(n)] + carts.video_args(cid, s), capture_output=True, text=True).stdout
        mine = [l.split()[1] for l in out.splitlines()]
        sm = set(mine)
        best.append((sum(m in sm for m in caps[:n]), s, caps[:3] == mine[:3]))
    best.sort(reverse=True)
    return best
if __name__ == '__main__':
    print(identify(sys.argv[1], sys.argv[2]))
