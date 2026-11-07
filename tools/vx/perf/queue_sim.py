#!/usr/bin/env python3
"""Replay the player's decode-ahead queue + Timer3 display logic (timer3_av_tick) with the measured
per-frame decode cost scaled by a factor, to find how much decode-cost headroom remains before
frames are shown late / blits are skipped. Per tick: Timer3 handler first (blit if a frame is shown,
then audio), the rest of the tick is available to the main loop (decode). Costs from per_frame.csv
and per_tick.csv.  queue_sim.py [scale ...]"""
import sys, numpy as np
from pf import load
TICK = 12483 * 64
d = load("per_frame.csv"); t = load("per_tick.csv")
dec = d["d_vdec"]
BLIT = np.nanmean(d["blit_cyc"])
# non-decode, non-blit cost per tick (audio + IRQ + DMA + misc), cycled from the measured series
other = (t["audio"] + t["irq"] + t["dma_blocked"] + t["mainloop"] + t["ui"] + t["other"])
N = len(dec)

def sim(scale, verbose=False):
    need = dec * scale
    decoded = 4          # prefill: 4 frames decoded before timers start
    shown = 0
    ac, b0 = 0, 0
    late = skipped = 0
    k = 0
    cur_left = need[4] if N > 4 else 0
    min_lead = 4
    tick = 0
    while shown < N - 4:
        # Timer3 handler at the start of the tick
        busy = other[tick % len(other)]
        if ac < 1 and shown < decoded and b0 == 0:
            if ac > -0x21 or (ac & 1) == 0:
                busy += BLIT
            else:
                skipped += 1
            shown += 1; ac += 2; b0 = 1
        else:
            if b0 == 0 and ac < 1 and shown >= decoded:
                late += 1
            ac -= 1; b0 = 0
        avail = TICK - busy
        # main loop decodes while the queue has room
        while avail > 0 and decoded < N:
            if decoded - shown >= 4:
                break
            if cur_left <= avail:
                avail -= cur_left; decoded += 1
                cur_left = need[decoded] if decoded < N else 0
            else:
                cur_left -= avail; avail = 0
        min_lead = min(min_lead, decoded - shown)
        tick += 1
    return late, skipped, tick

for s in [float(x) for x in sys.argv[1:]] or [0.9, 1.0, 1.02, 1.05, 1.1, 1.2, 1.3, 1.5]:
    late, sk, ticks = sim(s)
    print(f"decode x{s:.2f}: late display ticks {late}, skipped blits {sk}, run length {ticks} ticks (nominal {3*(N-4)})")
