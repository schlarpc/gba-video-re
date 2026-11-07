#!/usr/bin/env python3
"""Run every ground-truth capture (boot + each episode of each cart), JOBS in parallel, skipping
captures that already have meta.json.  usage: capture_all.py [CART...]"""
import os, sys, subprocess, itertools
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import carts
CAP = os.environ.get('ADS_CAP', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/cap')
ADSDEC = os.path.join(HERE, '..', '..', '..', 'reference', 'ads', 'adsdec')
JOBS = int(os.environ.get('JOBS', '3'))

def episodes(cid):
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    names = [l.split()[1] for l in out.splitlines() if l and l[0].isdigit()]
    if any(n.startswith('movie_') for n in names):
        return len([n for n in names if n.startswith('movie_')])
    # Hydrogen packaging: episodes are the streams with chapters
    return sum(1 for l in out.splitlines() if l and l[0].isdigit() and int(l.split('frames')[0].split()[-1]) > 2000)

def jobs(cids):
    for cid in cids:
        yield (cid, 'boot', [f'{CAP}/{cid}/boot'])
        for k in range(1, episodes(cid) + 1):
            yield (cid, 'movie', [str(k), f'{CAP}/{cid}/movie{k}'])

def run(j):
    cid, mode, args = j
    out = args[-1]
    if os.path.exists(os.path.join(out, 'meta.json')): return f'skip {cid} {mode} {args}'
    os.makedirs(out, exist_ok=True)
    with open(out + '.log', 'w') as lf:
        r = subprocess.run([sys.executable, os.path.join(HERE, 'capture.py'), cid, mode] + args, stdout=lf, stderr=subprocess.STDOUT)
    return f'{cid} {mode} {args} rc={r.returncode}'

if __name__ == '__main__':
    cids = sys.argv[1:] or list(carts.CARTS)
    js = list(jobs(cids))
    print(len(js), 'jobs', flush=True)
    with ThreadPoolExecutor(JOBS) as ex:
        for r in ex.map(run, js): print(r, flush=True)
