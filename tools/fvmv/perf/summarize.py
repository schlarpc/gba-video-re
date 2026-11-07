#!/usr/bin/env python3
"""Summarise perf/out/*.npz into a Markdown table (stdout) and perf/out/summary.json."""
import json, os, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
FP = 1704 * 1232
res = {}
print('| stream | frames | busy mean | busy p99 | busy max | video | audio synth | audio out | blit | DMA | other | decode/frame mean | p99 | max (% of period) | audio/frame mean | min idle/frame |')
print('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
for v in (1, 2, 3, 4):
    for e in (1, 2):
        z = np.load(os.path.join(HERE, '..', '..', '..', 'tmp', 'fvmv', 'perf', f'p{v}e{e}.npz'))
        rc = z['run_cyc'].astype(np.int64); n = list(z['names']); dr = z['dec_run']; dc = z['dec_cyc']
        ix = {k: i for i, k in enumerate(n)}
        cs = np.vstack([np.zeros(len(n), np.int64), np.cumsum(rc, 0)]); w = cs[dr[1:]] - cs[dr[:-1]]
        tot = rc[dr[0]:dr[-1]].sum(0); T = tot.sum()
        sh = {k: tot[i] / T for k, i in ix.items()}
        busy = 1 - w[:, ix['idle_wait']] / w.sum(1)
        aud = w[:, ix['audio_synth']] + w[:, ix['audio_output']] + w[:, ix['audio_ring']]
        other = sh['irq'] + sh['ui_main'] + sh['bios']
        r = dict(frames=int(len(dr)), busy_mean=1 - sh['idle_wait'], busy_p99=float(np.percentile(busy, 99)),
                 busy_max=float(busy.max()), shares={k: float(x) for k, x in sh.items()},
                 dec_mean=float(dc.mean()), dec_p50=float(np.median(dc)), dec_p99=float(np.percentile(dc, 99)),
                 dec_max=int(dc.max()), dec_argmax=int(dc.argmax()), aud_mean=float(aud.mean()), aud_max=int(aud.max()),
                 blit_mean=float(w[:, ix['blit']].mean()), min_idle=int(w[:, ix['idle_wait']].min()),
                 total_cycles=int(T))
        res[f'p{v}e{e}'] = r
        print(f"| Vol {v} ep {e} | {r['frames']} | {100*r['busy_mean']:.1f} % | {100*r['busy_p99']:.1f} % | {100*r['busy_max']:.1f} % | "
              f"{100*sh['video_decode']:.1f} % | {100*sh['audio_synth']:.1f} % | {100*(sh['audio_output']+sh['audio_ring']):.1f} % | {100*sh['blit']:.1f} % | "
              f"{100*sh['dma_steal']:.2f} % | {100*other:.2f} % | {r['dec_mean']/1e3:.0f} k | {r['dec_p99']/1e3:.0f} k | {r['dec_max']/1e3:.0f} k ({100*r['dec_max']/FP:.0f} %) | "
              f"{r['aud_mean']/1e3:.0f} k | {r['min_idle']/1e3:.0f} k |")
json.dump(res, open(os.path.join(HERE, '..', '..', '..', 'tmp', 'fvmv', 'perf', 'summary.json'), 'w'), indent=1)
