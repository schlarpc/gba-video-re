#!/usr/bin/env python3
"""Markdown tables for perf-vx.md from runs/*/summary.json (vx_analyze.py)."""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = ["shrek-rev6_20200", "shrek-rev5_20200", "shrek2-rev6_20200", "shrek2-rev5_20200", "sharktale-rev6_20200",
         "sharktale-rev5_20200", "shrek-sharktale-rev5_20200", "shrek-sharktale-rev5_1eef800",
         "shrek-sharktale-rev5_3dd3600", "shrek-sharktale-rev5_3e76a00"]
F = 1 << 24
WIN = 3 * 12483 * 64


def load():
    S = {}
    for p in glob.glob(os.path.join(HERE, "runs", "*", "summary.json")):
        s = json.load(open(p))
        S[s["run"]] = s
    return [S[k] for k in ORDER if k in S]


def main():
    L = load()
    print("| stream | build | frames | busy mean | busy p99 / max (per frame window) | video | blit | audio | IRQ+DMA+UI | idle | saturated windows |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in L:
        sh = s["shares"]
        print(f"| {s['run']} | {s['profile']} | {s['frames']:,} | {100*sh['busy']:.1f} % | {100*s['busy_window']['p99']:.1f} / {100*s['busy_window']['max']:.1f} % "
              f"| {100*sh['video']:.1f} % | {100*sh['blit']:.1f} % | {100*sh['audio']:.1f} % | {100*(sh['irq']+sh['dma']+sh['mainloop_ui_other']):.2f} % "
              f"| {100*sh['spin']:.1f} % | {s['saturated_windows']} |")
    print()
    print("| stream | decode mean / p50 / p99 / max (k cycles) | max as % of a frame period | seek-point mean | max wall (decode + IRQs) | CPI | data stalls | late frames (late ticks, max) | skipped blits | min audio-refill margin |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for s in L:
        d = s["decode_cyc"]
        aw = s["audio_window"]
        mm = aw["margin"]["min"] / F * 1e3 if aw["margin"] else float("nan")
        lost = f"; **{aw['lost_requests']} lost request**" if aw["lost_requests"] else ""
        print(f"| {s['run']} | {d['mean']/1e3:.0f} / {d['p50']/1e3:.0f} / {d['p99']/1e3:.0f} / {d['max']/1e3:.0f} | {100*d['max']/WIN:.0f} % "
              f"| {s['decode_cyc_keyframes']['mean']/1e3:.0f} k | {s['decode_wall']['max']/1e3:.0f} k | {s['decode_cpi']:.2f} | {100*s['decode_stall']:.1f} % "
              f"| {s['late_frames']} ({s['late_ticks_total']}, {s['late_max_ticks']}) | {s['skipped_blits']} | {mm:.1f} ms{lost} |")
    print()
    keys = ["vdec_mc", "vdec_intra", "vdec_idct", "vdec_resid_vlc", "vdec_bits", "vdec_dispatch", "vdec_grid", "vdec_frame", "vdec_refill"]
    print("| stream | " + " | ".join(k[5:] for k in keys) + " |")
    print("|---|" + "---|" * len(keys))
    for s in L:
        b = s["decode_breakdown"]
        print(f"| {s['run']} | " + " | ".join(f"{100*b.get(k, 0):.1f}" for k in keys) + " |")


if __name__ == "__main__":
    main()
