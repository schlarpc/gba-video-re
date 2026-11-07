#!/usr/bin/env python3
"""Explain frames where the cartridge's framebuffer differs from the decoder: try rendering the
frame's index plane with the codebook of each of the previous 16 segments. A match means the
player showed the frame with a stale codebook (its codebook pipeline had not caught up).
usage: explain_stale.py CART STREAM            (uses manifests/ for the cartridge's md5s)"""
import os, sys, gzip, csv, hashlib, struct, subprocess
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carts, adsref
ADSDEC = os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec')

def chunks_of(cid, stream):
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    line = [l for l in out.splitlines() if l and l[0].isdigit() and l.split('mmstr@')[0].split(None, 1)[1].strip() == stream][0]
    vo = int(line.split('video@')[1].split()[0], 16)
    d = carts.load(cid)
    if carts.CARTS and cid in ('jimmy', 'noah-proto'):
        w0, w1, nseg = struct.unpack_from('<IIH', d, vo); p = vo + 12
        nchap = w0 & 0xff
        for _ in range(nchap):
            z = d.index(b'\0', p + 2); p = (z + 2) & ~1
        p = (p + 3) & ~3
        chunks = []
        for _ in range(nseg):
            a, b = struct.unpack_from('<II', d, p); sa, sb = (b & 0x1fff) * 4, (b >> 13) * 4
            chunks.append(dict(nfr=a >> 16, mode=a & 7, ncb=(a >> 6) & 0x3ff, A=(p + 8, sa), B=(p + 8 + sa, sb))); p += 8 + sa + sb
        return d, chunks
    v = adsref.parse_video(d, vo, 1 << 30)
    return d, v['chunks']

def render(ix, rgb, mode):
    bw, bh = adsref.DIMS[mode]; gw, gh = 240 // bw, 160 // bh
    R = np.array(rgb, np.uint16).reshape(256, 16)
    I = np.frombuffer(ix, np.uint8).reshape(gh, gw)
    fb = np.zeros((160, 240), np.uint16)
    for y in range(bh):
        for x in range(bw):
            fb[y:gh * bh:bh, x:gw * bw:bw] = R[I, y * bw + x]
    return hashlib.md5(fb.tobytes()).hexdigest()

def main():
    cid, stream = sys.argv[1], sys.argv[2]
    want = [r['md5'] for r in csv.DictReader(gzip.open(os.path.join(HERE, '..', '..', 'verify', 'ads', f"{cid}_{stream.replace(' ', '_')}.frame_md5.csv.gz"), 'rt'))]
    got = [l.split()[1] for l in subprocess.run([ADSDEC, carts.rom_path(cid), '--stream', stream, '--md5'] + carts.video_args(cid, stream),
                                                capture_output=True, text=True).stdout.splitlines()]
    bad = [i for i, (a, b) in enumerate(zip(want, got)) if a != b]
    d, chunks = chunks_of(cid, stream)
    gain = 307 if carts.video_args(cid, stream) else 256
    cache = {}
    def rgb_of(ci):
        if ci not in cache:
            c = chunks[ci]
            cb = adsref.unpredict(adsref.unlzma(d[c['A'][0]:c['A'][0] + c['A'][1]]), c['mode'], c['ncb'])
            cache[ci] = adsref.convert(cb, c['mode'], c['ncb'])
        return cache[ci]
    f0 = 0; first = []
    for c in chunks: first.append(f0); f0 += c['nfr']
    res = {}
    for f in bad:
        ci = max(i for i in range(len(chunks)) if first[i] <= f)
        c = chunks[ci]
        ix = adsref.unlzma(d[c['B'][0]:c['B'][0] + c['B'][1]])
        n = (240 // adsref.DIMS[c['mode']][0]) * (160 // adsref.DIMS[c['mode']][1])
        plane = ix[(f - first[ci]) * n:(f - first[ci] + 1) * n]
        hit = [ci - k for k in range(1, 17) if ci - k >= 0 and render(plane, rgb_of(ci - k), c['mode']) == want[f]]
        res[f] = (ci, hit)
        print(f'frame {f} segment {ci}: cartridge shows it with the codebook of segment(s) {hit}', flush=True)
    ok = sum(1 for v in res.values() if v[1])
    print(f'{cid} {stream}: {len(bad)} mismatching frames, {ok} explained by a stale codebook')

if __name__ == '__main__':
    main()
