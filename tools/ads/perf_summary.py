#!/usr/bin/env python3
"""Summarise the perf runs (work/perf/<cart>/<capture>.csv.gz) per stream and per build.
Prints Markdown tables used in docs/perf-ads.md."""
import os, sys, json, gzip, glob
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carts, images
PERF = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tmp', 'ads') + '/perf'
REFRESH = 280896
NAMES = {1: 'LZMA', 2: 'codebook', 3: 'blit', 4: 'ADPCM', 5: 'IWRAM other', 6: 'Thumb player', 14: 'BIOS'}

def stream_rows():
    out = []
    for cid in carts.CARTS:
        capj = json.load(open(os.path.join(HERE, '..', '..', 'verify', 'ads', f'{cid}.capture.json')))
        for nm, cap in capj.items():
            f = os.path.join(PERF, cid, f"{cap['capture']}.csv.gz")
            if not os.path.exists(f): continue
            R = np.loadtxt(gzip.open(f, 'rt'), delimiter=',', skiprows=1, ndmin=2)
            R = R[cap['first_blit']:cap['first_blit'] + max(0, cap['frames'] - 1)]
            if not len(R): continue
            out.append((cid, nm, R))
    return out

def main():
    rows = stream_rows()
    per_build = {}
    print('| cart | stream | windows | busy mean | p99 | max | LZMA | codebook | blit | ADPCM | Thumb player | other | late windows |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for cid, nm, R in rows:
        w = R[:, 0] * REFRESH; busy = R[:, 1:17].sum(1) / w; tot = w.sum()
        sh = {j: R[:, 1 + j].sum() / tot for j in range(16)}
        other = sh[5] + sh[14] + sh[15] + sh[0]
        late = int((R[:, 0] != 5).sum())
        print(f"| {cid} | {nm} | {len(R)} | {busy.mean():.3f} | {np.percentile(busy, 99):.3f} | {busy.max():.3f} | "
              f"{sh[1]:.3f} | {sh[2]:.4f} | {sh[3]:.3f} | {sh[4]:.3f} | {sh[6]:.4f} | {other:.4f} | {late} |")
        if nm.startswith('movie') or nm in ('res1', 'res2', 'res3', 'res4') and cid in ('jimmy',) or (cid == 'noah-proto' and nm in ('res1', 'res2')):
            per_build.setdefault(images.BUILD_OF[cid], []).append(R)
    print()
    print('| build | episodes | frames | busy mean | p99 | max | LZMA | codebook | blit | ADPCM | Thumb | LZMA cycles/frame mean | p99 | late windows |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for b, Rs in sorted(per_build.items()):
        R = np.concatenate(Rs); w = R[:, 0] * REFRESH; busy = R[:, 1:17].sum(1) / w; tot = w.sum()
        sh = {j: R[:, 1 + j].sum() / tot for j in range(16)}
        lz = R[:, 2]
        print(f"| `{b}` | {len(Rs)} | {len(R)} | {busy.mean():.3f} | {np.percentile(busy, 99):.3f} | {busy.max():.3f} | {sh[1]:.3f} | "
              f"{sh[2]:.4f} | {sh[3]:.3f} | {sh[4]:.3f} | {sh[6]:.4f} | {lz.mean():.0f} | {np.percentile(lz, 99):.0f} | {int((R[:, 0] != 5).sum())} |")
    allR = np.concatenate([R for _, nm, R in rows])
    w = allR[:, 0] * REFRESH; busy = allR[:, 1:17].sum(1) / w
    print(f"\nall streams: {len(rows)} streams, {len(allR)} windows, busy mean {busy.mean():.3f}, p99 {np.percentile(busy, 99):.3f}, max {busy.max():.3f}, "
          f"late windows {int((allR[:, 0] != 5).sum())} (refresh deltas: {sorted(set(allR[:, 0].astype(int)))})")
    # blit cost per mode check
    print('blit cycles per window: ', {cid: int(np.median(R[:, 4])) for cid, nm, R in rows if nm.startswith('movie')} )

if __name__ == '__main__':
    main()
