"""Speaker-stream manifest from DSP captures (no headphones):  python3 make_spk_manifest.py OUT.csv SECS RATE:FILE[:m] ...
line: file,rate,mono,blocks,md5 over the blocks (per block: 288 ch1 words then 288 ch2 words, u16 LE)"""
import sys, os, hashlib, struct, spk_cap2
rows = []
for spec in sys.argv[3:]:
    p = spec.split(':'); rate, f, mono = int(p[0]), p[1], len(p) > 2
    r = spk_cap2.capture(f, float(sys.argv[2]))
    h = hashlib.md5()
    for size, c1, c2 in r['blocks']: h.update(struct.pack('<288H288H', *c1, *c2))
    rows.append('%s,%d,%d,%d,%s' % (os.path.basename(f), rate, mono, len(r['blocks']), h.hexdigest()))
    print(rows[-1], flush=True)
open(sys.argv[1], 'w').write('# file,rate,mono,blocks,md5: the NMP DSP 8-bit speaker blocks (tools/playyan/nmp/spk_cap2.py, McBSP1 DR low)\n' + '\n'.join(rows) + '\n')
