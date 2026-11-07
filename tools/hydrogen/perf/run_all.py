#!/usr/bin/env python3
"""Whole-stream perf (hyperf.py) of every stream of every Hydrogen cart; skips finished ones."""
import sys, os, subprocess, concurrent.futures as cf
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
import hycart
from carts import CARTS, rom_path
OUT = os.path.join(HERE, '..', '..', '..', 'tmp', 'hydrogen', 'perf')
jobs = []
for code in CARTS:
    rom = open(rom_path(code), 'rb').read()
    for i, c in enumerate(hycart.find_containers(rom)):
        jobs.append((-hycart.Stream(rom, c['video'][0], c['video'][1]).frames, code, i))
jobs.sort()
def run(j):
    _, code, i = j
    d = f'{OUT}/{code}_{i}'
    if os.path.exists(d + '/totals.json'): return d + ' skip'
    p = subprocess.run([sys.executable, os.path.join(HERE, 'hyperf.py'), code, str(i), d], capture_output=True, text=True)
    open(d + '.log', 'w').write(p.stdout + p.stderr)
    return d + ' ' + p.stdout.strip()[-120:]
with cf.ThreadPoolExecutor(int(os.environ.get('JOBS', '4'))) as ex:
    for r in ex.map(run, jobs): print(r, flush=True)
