#!/usr/bin/env python3
"""VXGB vs VX++ on the same movie: decode cost, residual-entropy cost per coded video bit."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
bs = json.load(open(os.path.join(HERE, "..", "..", "..", "tmp", "vx", "bitstats.json")))
pairs = [("shrek", "Shrek"), ("shrek2", "Shrek 2"), ("sharktale", "Shark Tale")]
print("| movie | build | video kbit/s | decode mean / seek-pt mean / max (k cyc) | residual entropy (VLC/CAVLC + CBP + grid) cycles per video bit | dequant+IDCT share of decode | entropy share of decode |")
print("|---|---|---|---|---|---|---|")
for c, name in pairs:
    for rev in ("rev6", "rev5"):
        r = f"{c}-{rev}_20200"
        p = os.path.join(HERE, "runs", r, "summary.json")
        if not os.path.exists(p):
            continue
        S = json.load(open(p))
        b = bs[f"{c}-{rev}:0x20200"]
        tot = S["wall_cycles"]
        ent = S["buckets"].get("vdec_resid_vlc", 0) + S["buckets"].get("vdec_grid", 0)
        vb = b["video_bits"]
        bd = S["decode_breakdown"]
        print(f"| {name} | {'VXGB' if rev == 'rev5' else 'VX++'} | {b['video_kbps_mean']:.1f} | {S['decode_cyc']['mean']/1e3:.0f} / {S['decode_cyc_keyframes']['mean']/1e3:.0f} / {S['decode_cyc']['max']/1e3:.0f} "
              f"| {ent*tot/vb:.2f} | {100*bd['vdec_idct']:.1f} % | {100*(bd['vdec_resid_vlc']+bd.get('vdec_grid',0)):.1f} % |")
