#!/usr/bin/env python3
"""Markdown tables for docs/hydrogen/perf.md from tmp/hydrogen/perf_summary.json (mkstats.py) and the
per-VBlank CSVs."""
import os, sys, json, csv, collections
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
import numpy as np
from carts import CARTS, BUILD, SHORT
WORK = os.path.join(HERE, '..', '..', '..', 'tmp', 'hydrogen')
P = json.load(open(f'{WORK}/perf_summary.json'))
stats = {f"{SHORT[[c for c in CARTS if 'Game Boy Advance Video - ' + CARTS[c] == r['nointro']][0]]}_{r['stream']}": r
         for r in json.load(open(os.path.join(HERE, '..', '..', '..', 'docs', 'hydrogen', 'stats.json')))}
pct = lambda x: f'{100 * x:.1f} %'

print('### Per stream\n')
print('| stream | build | frames | CPU busy mean | p99 | max | inflate | convert | blit | ADPCM | other | heaviest VBlank | ROM read B/s |')
print('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
for name, p in P.items():
    if not p: continue
    code = [c for c in CARTS if SHORT[c] == name.rsplit('_', 1)[0]][0]
    other = p['cpu_busy_mean'] - p['inflate_share'] - p['convert_share'] - p['blit_share'] - p['audio_decode_share']
    print(f"| {name} | `{BUILD[code]}` | {p['frames'] + 1} | {pct(p['cpu_busy_mean'])} | {pct(p['cpu_busy_p99'])} | {pct(p['cpu_busy_max'])} | "
          f"{pct(p['inflate_share'])} | {pct(p['convert_share'])} | {pct(p['blit_share'])} | {pct(p['audio_decode_share'])} | {pct(other)} | "
          f"{pct(p['vblank_busy_max'])} | {p['rom_read_Bps']:.0f} |")

ok = [p for p in P.values() if p]
print('\n### All streams\n')
for k in ('cpu_busy_mean', 'cpu_busy_p99', 'cpu_busy_max', 'inflate_share', 'convert_share', 'blit_share', 'audio_decode_share',
          'vblank_busy_max', 'vblank_busy_p99'):
    v = np.array([p[k] for p in ok])
    print(f'- {k}: min {pct(v.min())}, median {pct(np.median(v))}, max {pct(v.max())}')
print(f"- VBlanks over their period (steady state): {sum(p['vblanks_over'] for p in ok)} of {sum(p['vblanks'] for p in ok)}")
print(f"- streams measured: {len(ok)} of {len(P)}")

# per-VBlank class costs over all streams
cls = collections.defaultdict(list)
for name in P:
    d = f"{WORK}/perf/{[c for c in CARTS if SHORT[c] == name.rsplit('_', 1)[0]][0]}_{name.rsplit('_', 1)[1]}/per_vblank.csv"
    if not os.path.exists(d): continue
    pv = list(csv.DictReader(open(d)))
    nb = max(int(r['blits_before']) for r in pv)
    for r in pv:
        if not 1 <= int(r['blits_before']) < nb: continue
        k = max(['inflate', 'vq_convert', 'vq_blit', 'adpcm'], key=lambda x: int(r[x]))
        if int(r[k]) == 0: k = 'idle'
        cls[k].append(int(r['busy']))
print('\n### VBlank classes (all streams)\n')
print('| VBlank doing | count | busy p50 | p99 | max (of 280,896 cycles) |')
print('|---|---|---|---|---|')
for k, v in sorted(cls.items(), key=lambda kv: -np.median(kv[1])):
    v = np.array(v)
    print(f'| {k} | {len(v)} | {int(np.median(v))} | {int(np.percentile(v, 99))} | {int(v.max())} ({pct(v.max() / 280896)}) |')
