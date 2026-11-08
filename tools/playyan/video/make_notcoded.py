#!/usr/bin/env python3
"""Make an ASF whose every 7th P-VOP is a vop_coded=0 VOP (to see what the firmware sends for a
not-coded VOP). Edits the VOP headers in place (the rest of each such object is left as is and
ignored after vop_coded = 0).  usage: make_notcoded.py IN.asf OUT.asf TIME_INC_BITS"""
import sys
inp, outp, tib = sys.argv[1], sys.argv[2], int(sys.argv[3])
d = bytearray(open(inp, 'rb').read())
i = 0; n = 0; done = 0
while True:
    i = d.find(b'\x00\x00\x01\xb6', i + 1)
    if i < 0:
        break
    b = ''.join(format(x, '08b') for x in d[i + 4:i + 12])
    if b[:2] != '01':
        continue
    n += 1
    if n % 7:
        continue
    q = 2
    while b[q] == '1':
        q += 1
    q += 1 + 1 + tib + 1
    hdr = b[:q] + '0'
    hdr += '0' + '1' * ((-len(hdr) - 1) % 8)
    nb = int(hdr, 2).to_bytes(len(hdr) // 8, 'big')
    d[i + 4:i + 4 + len(nb)] = nb
    done += 1
open(outp, 'wb').write(d)
print('wrote', outp, 'with', done, 'not-coded VOPs')
