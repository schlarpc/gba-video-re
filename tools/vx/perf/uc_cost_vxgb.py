#!/usr/bin/env python3
"""Second-method cross-check for the VXGB (Rev 5) player: the GBATEK cost model of uc_cost.py
run on the Rev 5 player's own decode_frame (0x03000510) in the standalone unicorn oracle
(tools/vx/oracle/uc_oracle_vxgb.py), compared frame by frame with the instrumented-mGBA run.

The oracle starts cold at a seek point; VXGB seek points decode identically from a cold start
(docs/vxgb.md 9), and the decoder's control flow depends only on the bitstream, so the
instruction count of frame f must equal mGBA's.

  uc_cost_vxgb.py --rom ROM --run runs/CART_20200 --out crossval_CART.csv [--n 40]
"""
import argparse
import csv
import gzip
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oracle"))
sys.path.insert(0, HERE)
from uc_oracle_vxgb import OracleGB  # noqa: E402
from uc_cost import CostModel  # noqa: E402


class _G:
    def __init__(self, uc):
        self.uc = uc


class _O:
    def __init__(self, uc):
        self.g = _G(uc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=40, help="approximate number of frames to cost")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    b = json.load(open(os.path.join(HERE, "buckets_vxgb.json")))
    names = b["names"]
    vd = [n for n in names if n.startswith("vdec_")]
    mg = {int(r["frame"]): r for r in csv.DictReader(gzip.open(os.path.join(a.run, "per_frame.csv.gz"), "rt"))}
    o = OracleGB(a.rom)
    st = [e for e in o.seek_table() if e[0] < o.hdr[1]]
    sp = [e[0] for e in st if e[0] + 12 in mg]
    # plan: (seek frame, count): heaviest frames reachable within 12 frames of a seek point,
    # plus random seek points (first 4 frames each)
    heavy = sorted(mg, key=lambda f: -int(mg[f]["d_vdec"]))
    plan = {}
    for f in heavy:
        s = max(x for x in sp if x <= f)
        if f - s < 12:
            plan[s] = max(plan.get(s, 0), f - s + 1)
        if sum(plan.values()) >= a.n // 2:
            break
    rnd = random.Random(a.seed)
    for s in rnd.sample(sp, len(sp)):
        if sum(plan.values()) >= a.n:
            break
        plan.setdefault(s, 4)
    cm = CostModel(_O(o.uc), names, b["ranges"])
    rows = []
    for s in sorted(plan):
        ent = [e for e in st if e[0] == s][0]
        o.reset(None if s == 0 else ent[1])
        for k in range(plan[s]):
            f = s + k
            cm.reset()
            o.decode()
            cm._finish()
            uc_c = sum(cm.cyc[cm.bid[x]] for x in vd)
            uc_i = sum(cm.ins[cm.bid[x]] for x in vd)
            uc_w = sum(cm.wait[cm.bid[x]] for x in vd)
            m = mg[f]
            rows.append(dict(frame=f, mgba_vdec_cyc=int(m["d_vdec"]), uc_vdec_cyc=uc_c, mgba_ins=int(m["d_vdec_ins"]),
                             uc_ins=uc_i, mgba_memwait=int(m["d_vdec_memwait"]), uc_memwait=uc_w,
                             rel_diff=f"{(int(m['d_vdec']) - uc_c) / int(m['d_vdec']):.5f}"))
            print(rows[-1], file=sys.stderr)
    with open(a.out, "w", newline="") as fo:
        w = csv.DictWriter(fo, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rel = [float(r["rel_diff"]) for r in rows]
    print(f"{len(rows)} frames: instructions equal {sum(r['mgba_ins'] == r['uc_ins'] for r in rows)}/{len(rows)}; "
          f"mGBA - model: mean {100 * sum(rel) / len(rel):+.2f}% min {100 * min(rel):+.2f}% max {100 * max(rel):+.2f}%")


if __name__ == "__main__":
    main()
