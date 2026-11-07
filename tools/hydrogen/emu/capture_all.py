#!/usr/bin/env python3
"""Run capture.py over every stream of every Hydrogen cart (parallel), skipping finished ones."""
import sys, os, subprocess, concurrent.futures as cf
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import hycart
from carts import CARTS, rom_path
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '../../../tmp/hydrogen/cap')
JOBS = int(os.environ.get('JOBS', '4'))
os.makedirs(OUT, exist_ok=True)
jobs = []
for code in CARTS:
    rom = open(rom_path(code), 'rb').read()
    for i, c in enumerate(hycart.find_containers(rom)):
        s = hycart.Stream(rom, c['video'][0], c['video'][1])
        jobs.append((s.frames, code, i))
jobs.sort(key=lambda j: (j[2] != 1, j[0]))   # episode 1 of each cart first
def run(j):
    n, code, i = j
    pre = f'{OUT}/{code}_{i}'
    if os.path.exists(pre + '.done'): return pre + ' skip'
    p = subprocess.run([sys.executable, os.path.join(HERE, 'capture.py'), rom_path(code), str(i), pre],
                       capture_output=True, text=True)
    open(pre + '.log', 'w').write(p.stdout + p.stderr)
    if p.returncode == 0: open(pre + '.done', 'w').write(p.stdout)
    return pre + ' ' + p.stdout.strip()[-200:]
with cf.ThreadPoolExecutor(JOBS) as ex:
    for r in ex.map(run, jobs): print(r, flush=True)
