#!/usr/bin/env python3
"""LZMA statistics per stream (max match distance, blob sizes) using the C decoder."""
import sys, os, struct, ctypes as C, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'emu'))
import carts, subprocess
from uc_lzma import LzmaDec, lib
ADSDEC = os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec')
def chunks(cid, name):
    import adsref, adsfs
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    line = [l for l in out.splitlines() if l and l[0].isdigit() and l.split('mmstr@')[0].split(None, 1)[1].strip() == name][0]
    from explain_stale import chunks_of
    return chunks_of(cid, name)
res = {}
for cid in sys.argv[1:]:
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    for l in out.splitlines():
        if not (l and l[0].isdigit()): continue
        nm = l.split('mmstr@')[0].split(None, 1)[1].strip()
        d, chs = chunks(cid, nm)
        md = 0; mdA = 0
        for c in chs:
            for key in ('A', 'B'):
                o, n = c[key]; blob = d[o:o + n]
                us, par = struct.unpack_from('<II', blob)
                probs = (C.c_uint16 * lib.lzma_probs_size(0, 0))(); ring = (C.c_uint8 * 0x20000)()
                buf = C.create_string_buffer(blob, len(blob)); x = LzmaDec()
                lib.lzma_init(C.byref(x), 0, 0, 2, probs, ring, 0x1FFFF, C.addressof(buf) + 8, C.addressof(buf) + len(blob))
                lib.lzma_decode(C.byref(x), us)
                if key == 'B': md = max(md, x.max_dist)
                else: mdA = max(mdA, x.max_dist)
        res[f'{cid}/{nm}'] = dict(max_dist_index=md, max_dist_codebook=mdA)
        print(cid, nm, md, mdA, flush=True)
json.dump(res, open(os.path.join(HERE, '..', '..', 'tmp', 'ads', 'lzstats.json'), 'w'), indent=1)
