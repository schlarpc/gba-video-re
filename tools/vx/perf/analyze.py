#!/usr/bin/env python3
"""Summary statistics from per_frame.csv / per_tick.csv / totals.json (mgba_perf.py) and
crossval vs the unicorn cost model (uc/*.csv). Writes summary.json and prints tables."""
import csv, glob, json, os
import numpy as np
from pf import load
HERE = os.path.dirname(os.path.abspath(__file__))
W = 3 * 12483 * 64          # one movie frame = 3 Timer3 ticks
TICK = 12483 * 64
F = 1 << 24
d = load(os.path.join(HERE, "per_frame.csv"))
t = json.load(open(os.path.join(HERE, "totals.json")))
kf = np.array([int(x) for x in open(os.path.join(HERE, "keyframes.txt"))])
S = {}

def st(x):
    x = x[~np.isnan(x)]
    return dict(mean=float(x.mean()), p50=float(np.median(x)), p99=float(np.quantile(x, .99)),
                p999=float(np.quantile(x, .999)), max=float(x.max()), min=float(x.min()))

fr = d["frame"].astype(int)
iskf = np.isin(fr, kf)
S["decode_cyc"] = st(d["d_vdec"]); S["decode_cyc_keyframes"] = st(d["d_vdec"][iskf]); S["decode_cyc_nonkey"] = st(d["d_vdec"][~iskf])
S["decode_wall"] = st(d["d_wall"])
for c in ["w_busy", "w_vdec", "w_blit", "w_audio", "w_irq", "w_spin", "w_dma_blocked", "w_dma_cyc", "w_mainloop", "w_ui", "w_other"]:
    S[c] = st(d[c])
ok = ~np.isnan(d["w_wall"])
S["windows"] = int(ok.sum())
S["saturated_windows"] = int((d["w_spin"][ok] == 0).sum())
S["slack"] = st(d["slack"])
wt = d["w_ticks"][ok]
S["window_ticks"] = {int(v): int((wt == v).sum()) for v in np.unique(wt)}
S["lead_after"] = {int(v): int((d["lead_after"] == v).sum()) for v in np.unique(d["lead_after"])}
# late frames: frame f+1 shown one tick late when the window of f is 4 ticks
late = fr[ok][wt == 4] + 1
lr = []
idx = {f: i for i, f in enumerate(fr)}
for f in late:
    i = idx[f]
    prev = [int(d["d_vdec"][idx[g]]) for g in range(max(0, f - 6), f + 1)]
    lr.append(dict(frame=int(f), decode=int(d["d_vdec"][i]), last7=prev, sum7=int(sum(prev)),
                   keyframe_near=int(kf[np.searchsorted(kf, f, side="right") - 1]), slack=float(d["slack"][i])))
S["late_frames"] = lr
# sub-function breakdown of decode
vd = ["vdec_frame", "vdec_bits", "vdec_refill", "vdec_mapwait", "vdec_dispatch", "vdec_mc", "vdec_intra", "vdec_resid_vlc", "vdec_idct"]
tot = d["d_vdec"].sum()
S["decode_breakdown"] = {k: float(d["d_" + k].sum() / tot) for k in vd}
S["decode_stall_frac"] = float(d["d_vdec_memwait"].sum() / tot)
S["decode_cpi"] = float(tot / d["d_vdec_ins"].sum())
o = np.argsort(-d["d_vdec"])[:10]
S["worst"] = [dict(frame=int(fr[i]), keyframe=bool(iskf[i]), cyc=int(d["d_vdec"][i]), wall=int(d["d_wall"][i]),
                   **{k: int(d["d_" + k][i]) for k in vd}, memwait=int(d["d_vdec_memwait"][i]), slack=float(d["slack"][i]),
                   lead_after=int(d["lead_after"][i])) for i in o]
# blit
S["blit_cyc"] = st(d["blit_cyc"]); S["blit_wall"] = st(d["blit_wall"])
for k in ["blit_vram_active", "blit_vram_hblank", "blit_vram_vblank"]:
    S[k] = float(np.nanmean(d[k]))
S["blit_vcount_s"] = st(d["blit_vcount_s"])
# totals
f = {k: np.array(v) for k, v in t["fields"].items()}
names = t["names"]; wall = t["wall"]
cyc = f["cyc"].reshape(4, 64).sum(0); ins = f["ins"].reshape(4, 64).sum(0); mw = f["memwait"].reshape(4, 64).sum(0)
S["wall_cycles"] = wall; S["wall_s"] = wall / F
S["buckets"] = {names[b]: dict(frac=float(cyc[b] / wall), cyc=int(cyc[b]), ins=int(ins[b]), memwait=int(mw[b]))
                for b in np.argsort(-cyc) if cyc[b]}
S["halted"] = int(f["halted"][0]); S["dma_blocked"] = int(f["blocked"][0])
S["dma"] = dict(units=f["dma_units"].tolist(), bytes=f["dma_bytes"].tolist(), cyc=f["dma_cyc"].tolist())
S["irqs"] = int(f["irqs"][0]); S["irq_entry_cyc"] = int(f["irq_entry_cyc"][0])
S["matrix"] = dict(cmds=int(f["matrix_cmds"][0]), bytes=int(f["matrix_bytes"][0]), by_ctx=f["matrix_cmds_ctx"].tolist())
reg = {0: "bios", 2: "ewram", 3: "iwram", 4: "io", 5: "pal", 6: "vram", 7: "oam", 8: "rom"}
R = {}
for r, n in reg.items():
    g = lambda k: f[k].reshape(4, 16)[:, r].sum()
    R[n] = dict(rd_b=int(g("rd_b")), wr_b=int(g("wr_b")), rd_n=int(g("rd_n")), wr_n=int(g("wr_n")), wait=int(g("rd_w") + g("wr_w")),
                rd_Bps=float(g("rd_b") / S["wall_s"]), wr_Bps=float(g("wr_b") / S["wall_s"]), fetch_b=int(f["fetch_b"][r]),
                dma_rd=int(f["dma_rd_b"][r]), dma_wr=int(f["dma_wr_b"][r]))
S["regions"] = R
S["vram_wr_by_raster"] = f["vram_wr_b"].tolist()
# crossval
rows = []
for p in sorted(glob.glob(os.path.join(HERE, "uc", "*.csv"))):
    rows += list(csv.DictReader(open(p)))
cv = []
for u in rows:
    i = idx[int(u["frame"])]
    cv.append((int(u["frame"]), d["d_vdec"][i], float(u["uc_vdec_cyc"]), d["d_vdec_ins"][i], float(u["uc_vdec_ins"]),
               d["d_vdec_memwait"][i], float(u["uc_vdec_wait"])))
cv = np.array(cv)
rel = (cv[:, 1] - cv[:, 2]) / cv[:, 1]
S["crossval"] = dict(frames=len(cv), keyframes=int(np.isin(cv[:, 0], kf).sum()), ins_exact=int((cv[:, 3] == cv[:, 4]).sum()),
                     rel_mean=float(rel.mean()), rel_min=float(rel.min()), rel_max=float(rel.max()),
                     memwait_rel_mean=float(((cv[:, 5] - cv[:, 6]) / cv[:, 5]).mean()), max_frame_cyc=float(cv[:, 1].max()))
with open(os.path.join(HERE, "crossval.csv"), "w", newline="") as fo:
    w = csv.writer(fo); w.writerow(["frame", "mgba_vdec_cyc", "uc_vdec_cyc", "mgba_ins", "uc_ins", "mgba_memwait", "uc_memwait", "rel_diff"])
    for row, rr in zip(cv, rel):
        w.writerow([int(x) for x in row] + [f"{rr:.5f}"])
json.dump(S, open(os.path.join(HERE, "summary.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in S.items() if k not in ("buckets", "late_frames")}, indent=1)[:9000])
print("late:", S["late_frames"][:50])
