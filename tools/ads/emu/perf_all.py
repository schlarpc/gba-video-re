#!/usr/bin/env python3
"""Run perf.py for every capture of every cart (same navigation as the capture), JOBS in parallel."""
import os, sys, json, subprocess
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/cap'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/perf'
JOBS = int(os.environ.get('JOBS', '3'))
def jobs(cids):
    for cid in cids:
        man = json.load(open(os.path.join(HERE, '..', '..', '..', 'verify', 'ads', f'{cid}.capture.json')))
        for sub in sorted({v['capture'] for v in man.values()}):
            meta = json.load(open(os.path.join(CAP, cid, sub, 'meta.json')))
            if meta['mode'] == 'boot': yield cid, sub, ['boot', f'{OUT}/{cid}/{sub}.csv.gz']
            else: yield cid, sub, ['movie', str(meta['episode']), f'{OUT}/{cid}/{sub}.csv.gz', '--nav', meta['nav']]
def run(j):
    cid, sub, args = j
    out = [a for a in args if a.endswith('.csv.gz')][0]
    if os.path.exists(out): return f'skip {cid} {sub}'
    os.makedirs(os.path.dirname(out), exist_ok=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, 'perf.py'), cid] + args, capture_output=True, text=True)
    return (r.stdout.strip().splitlines() or ['?'])[-1] + (f' ERR {r.stderr[-300:]}' if r.returncode else '')
if __name__ == '__main__':
    js = list(jobs(sys.argv[1:]))
    print(len(js), 'jobs', flush=True)
    with ThreadPoolExecutor(JOBS) as ex:
        for r in ex.map(run, js): print(r, flush=True)
