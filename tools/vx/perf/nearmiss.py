#!/usr/bin/env python3
"""For the closest audio-window refills of a run: the decode that ended at the refill, the
CPU-bound span (from the Timer3 tick that released the main loop to the refill) and the uniform
CPU slow-down that would turn the near miss into a stale read (margin / span)."""
import csv, gzip, json, sys
F = 1 << 24
run = sys.argv[1]
S = json.load(open(run + "/summary.json"))
targets = {w["refill_t"]: w for w in S["audio_window"]["worst_margins"][:int(sys.argv[2]) if len(sys.argv) > 2 else 3]}
last_t3 = last_ds = None; last_dec = None
t3_before_ds = None
out = []
with gzip.open(run + "/lev.csv.gz", "rt") as f:
    for r in csv.DictReader(f):
        t = int(r["t"])
        if r["ev"] == "t3":
            last_t3 = t
        elif r["ev"] == "dec_s":
            last_ds, last_dec, t3_before_ds = t, int(r["dec"]), last_t3
        elif r["ev"] == "refill" and t in targets:
            w = targets[t]
            span = t - t3_before_ds
            out.append(dict(frame=last_dec - 3, refill_ms=t / F * 1e3, margin_ms=w["margin_ms"], decode_wall_ms=(t - last_ds) / F * 1e3,
                            span_ms=span / F * 1e3, slowdown_to_fail=w["margin_cyc"] / span))
for o in out:
    print(json.dumps(o))
