#!/usr/bin/env python3
"""Plots from per_frame.csv (needs matplotlib: run via
nix shell --impure --expr '(import (builtins.getFlake "nixpkgs") {}).python3.withPackages (p: [p.matplotlib p.numpy])' -c python3 plots.py)"""
import csv, json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
HERE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(HERE, "per_frame.csv"))))
def col(k): return np.array([float(r[k]) if r.get(k) not in (None, "") else np.nan for r in rows])
fr = col("frame"); dec = col("d_vdec"); W = 3 * 12483 * 64
kf = np.array([int(x) for x in open(os.path.join(HERE, "keyframes.txt"))])
S = json.load(open(os.path.join(HERE, "summary.json")))
late = np.array([l["frame"] for l in S["late_frames"]])
BLUE, ORANGE, AQUA, YELLOW, MAG, RED, GRAY, INK, INK2 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#d03b3b", "#c3c2b7", "#0b0b0b", "#52514e"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtext.color" if False else "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": "#fcfcfb",
                     "axes.facecolor": "#fcfcfb", "axes.grid": True, "grid.color": "#e6e5e0", "grid.linewidth": 0.6})
tmin = fr / 7 / 60
fixed = np.nanmean(col("w_blit") + col("w_audio") + col("w_irq") + col("w_dma_blocked") + col("w_mainloop") + col("w_ui") + col("w_other"))

# 1. decode cycles per frame over the movie
fig, ax = plt.subplots(figsize=(13, 5))
ax.plot(tmin, dec / 1e6, color=BLUE, lw=0.4, alpha=0.55, label="decode cycles, each frame")
k = 70
roll = np.convolve(dec, np.ones(k) / k, mode="same")
ax.plot(tmin, roll / 1e6, color=INK, lw=1.4, label="10 s moving average")
ax.axhline(W / 1e6, color=RED, lw=1.5, ls="-", label=f"movie-frame period {W/1e6:.3f} M cycles (7 fps)")
ax.axhline((W - fixed) / 1e6, color=ORANGE, lw=1.5, ls="--", label=f"left for decode after blit+audio+IRQ: {(W-fixed)/1e6:.2f} M")
if len(late):
    li = np.searchsorted(fr, late)
    ax.plot(tmin[li], dec[li] / 1e6, "v", color=RED, ms=7, mec="#fcfcfb", mew=1, ls="none", label=f"frame shown 1 tick late ({len(late)})")
ax.set_xlabel("movie time (min)"); ax.set_ylabel("CPU cycles (millions)")
ax.set_title("Video decode cost per frame, whole movie (37,837 frames)", loc="left", color=INK)
ax.set_xlim(0, tmin.max()); ax.set_ylim(0, 3.3)
ax.legend(loc="upper right", frameon=False, ncol=2)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "decode_timeline.png"), dpi=130); plt.close(fig)

# 2. CPU share per display window, 10 s average, stacked
parts = [("video decode", col("w_vdec"), BLUE), ("YUV->RGB blit", col("w_blit"), ORANGE), ("audio decode", col("w_audio"), AQUA),
         ("IRQ + DMA + UI", col("w_irq") + col("w_dma_blocked") + col("w_mainloop") + col("w_ui") + col("w_other"), MAG),
         ("idle (main-loop spin)", col("w_spin"), GRAY)]
ok = ~np.isnan(col("w_wall")); ww = col("w_wall")[ok]; t2 = tmin[ok]
ys = [np.convolve(p[1][ok] / ww * 100, np.ones(k) / k, mode="same") for p in parts]
fig, ax = plt.subplots(figsize=(13, 4.5))
ax.stackplot(t2, ys, colors=[p[2] for p in parts], labels=[p[0] for p in parts], edgecolor="#fcfcfb", linewidth=0.3)
ax.set_ylim(0, 100); ax.set_xlim(0, t2.max()); ax.set_ylabel("% of CPU cycles"); ax.set_xlabel("movie time (min)")
ax.set_title("Where the CPU time goes (10 s moving average of each movie-frame window)", loc="left", color=INK)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=5, frameon=False)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "cpu_share.png"), dpi=130); plt.close(fig)

# 3. distribution of decode cycles
fig, ax = plt.subplots(figsize=(8, 4.5))
bins = np.linspace(0, 3.0, 121)
isk = np.isin(fr, kf)
ax.hist(dec[~isk] / 1e6, bins=bins, color=BLUE, label="inter frames", edgecolor="#fcfcfb", linewidth=0.3)
ax.hist(dec[isk] / 1e6, bins=bins, color=ORANGE, label="keyframes (seek points, 195)", edgecolor="#fcfcfb", linewidth=0.3)
ax.axvline((W - fixed) / 1e6, color=ORANGE, ls="--", lw=1.2)
ax.axvline(W / 1e6, color=RED, lw=1.2)
ax.text((W - fixed) / 1e6, ax.get_ylim()[1] * 0.92, " average budget\n after blit+audio", color=INK2, fontsize=9)
ax.text(W / 1e6, ax.get_ylim()[1] * 0.92, " frame period", color=INK2, fontsize=9)
ax.set_yscale("log"); ax.set_xlabel("decode cycles per frame (millions)"); ax.set_ylabel("frames (log)")
ax.set_title("Distribution of per-frame decode cost", loc="left", color=INK)
ax.legend(frameon=False, loc="center right"); fig.tight_layout(); fig.savefig(os.path.join(HERE, "decode_hist.png"), dpi=130); plt.close(fig)

# 4. zoom: worst stretch, decode cost vs buffer lead
lo, hi = 11000, 11400
m = (fr >= lo) & (fr < hi)
fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
a1.bar(fr[m], dec[m] / 1e6, width=0.9, color=BLUE, label="decode cycles")
a1.axhline((W - fixed) / 1e6, color=ORANGE, ls="--", lw=1.2, label="average budget after blit+audio")
a1.axhline(W / 1e6, color=RED, lw=1.2, label="frame period")
lm = (late >= lo) & (late < hi)
a1.plot(late[lm], np.full(lm.sum(), 3.0), "v", color=RED, ms=7, ls="none", label="displayed 1 tick late")
a1.set_ylabel("M cycles"); a1.legend(frameon=False, ncol=4, loc="upper right"); a1.set_ylim(0, 3.3)
a1.set_title(f"Worst stretch (frames {lo}-{hi}): sustained heavy frames drain the 4-frame decode-ahead queue", loc="left", color=INK)
a2.step(fr[m], col("lead_after")[m], where="post", color=INK, lw=1.5)
a2.set_ylabel("frames decoded\nahead of display"); a2.set_ylim(0, 4.5); a2.set_yticks([0, 1, 2, 3, 4]); a2.set_xlabel("VX frame")
fig.tight_layout(); fig.savefig(os.path.join(HERE, "worst_stretch.png"), dpi=130); plt.close(fig)
print("ok")
