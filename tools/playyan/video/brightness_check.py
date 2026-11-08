#!/usr/bin/env python3
"""Check the frames of a fw_av.py capture against the C reference at every brightness level:
each dumped frame must equal our frame (matched by pts) at the brightness of the latest
VIDEO_MODE_SET (0x300) command sent before it.  usage: brightness_check.py FILE.asf CAPTUREDIR [pts_offset]"""
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
PYVDEC = os.path.join(HERE, '..', '..', '..', 'reference', 'playyan', 'video', 'pyvdec')
asf, cap = sys.argv[1], sys.argv[2]
off = int(sys.argv[3]) if len(sys.argv) > 3 else 100
recs = [json.loads(l) for l in open(os.path.join(cap, 'records.jsonl'))]
levels = sorted({int(r['words'][0], 16) for r in recs if r.get('cmd') == '300'} | {256})
mine = {}
with tempfile.TemporaryDirectory() as td:
    for b in levels:
        d = os.path.join(td, str(b)); os.makedirs(d)
        out = subprocess.run([PYVDEC, asf, '--rgb565', '--brightness', str(b), '--out', d], capture_output=True, text=True).stdout
        for l in out.split('\n'):
            if l:
                i, pts, ty, md5 = l.split()
                mine[(b, int(pts))] = open(os.path.join(d, 'f%05d.bin' % int(i)), 'rb').read()
cur = 256; ok = n = 0; wrong_level = 0; bad = []
for r in recs:
    if r.get('cmd') == '300':
        cur = int(r['words'][0], 16)
    if 'frame' in r:
        pts = int(r['rec'][6], 16) - off
        fw = open(os.path.join(cap, 'f%05d.bin' % r['frame']), 'rb').read()
        n += 1
        if mine.get((cur, pts)) == fw:
            ok += 1
        else:
            m = [b for b in levels if mine.get((b, pts)) == fw]
            if m:
                wrong_level += 1
            bad.append((r['frame'], pts, cur, m))
print('%s: %d/%d frames bit-exact at the commanded brightness (levels %s); %d match another level'
      % (os.path.basename(asf), ok, n, [hex(x) for x in levels], wrong_level))
for b in bad[:8]:
    print('  frame %d pts %d commanded %#x matches %s' % (b[0], b[1], b[2], [hex(x) for x in b[3]]))
