#!/usr/bin/env python3
"""Per-stream summary of a vx_perf.py run directory (per_frame.csv.gz, per_tick.csv.gz,
totals.json, lev.csv.gz) -> RUN/summary.json, and a one-line table row on stdout.

  vx_analyze.py runs/shrek2-rev5_20200 [...]

CPU figures: "busy" = wall - main-loop idle spin - halted, per display window (one blit start to
the next, nominally 3 Timer3 ticks = 2,396,736 cycles). Late / skipped frames come from the
blit schedule: frame k is due at Timer3 tick T0 + 3k (T0 from the on-time frames); it is late if
its blit starts at a later tick, skipped if the player never blits it.
Audio window: replays the 2 KiB audio window (two 1 KiB halves at 0x08001800/0x08001C00) from
the light events: every packet's read range, every refill request (ctx+0xB4) and every refill
(main loop), and reports the refill margin = time from a half's refill to the start of the
Timer3 handler that first reads it again (negative = stale read), lost requests (a request
overwritten before the main loop serviced it) and stale reads.
"""
import csv
import gzip
import json
import os
import struct
import sys

import numpy as np

TICK = 12483 * 64
WIN = 3 * TICK
F = 1 << 24
H0, H1, WEND = 0x08001800, 0x08001C00, 0x08002000


def load_csv(p):
    with gzip.open(p, "rt") as f:
        rows = list(csv.DictReader(f))
    cols = {}
    for k in rows[0]:
        cols[k] = np.array([float(r[k]) if r.get(k) not in (None, "") else np.nan for r in rows])
    return cols


def st(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if not len(x):
        return None
    return dict(mean=float(x.mean()), p50=float(np.median(x)), p99=float(np.quantile(x, .99)),
                p999=float(np.quantile(x, .999)), max=float(x.max()), min=float(x.min()))


def seek_frames(rom, off):
    h = struct.unpack_from("<4s13I", rom, off)
    return sorted({struct.unpack_from("<4I", rom, off + h[10] + 16 * i)[0] for i in range(h[11])})


def audio_window(levp):
    """Replay the audio window. Returns dict of stats and the event list."""
    pending = {}            # half -> post time (requested, not yet refilled)
    last_refill = {}        # half -> time of last refill (not yet re-read)
    t3 = None
    prev_rd = None
    prev_req = 0
    margins = []            # (refill time, deadline T3 time, margin, half, packet index)
    stale = []
    lost = []
    req_wait = []           # post -> refill latency
    npk = 0
    posted_at = {}
    first_stale = None
    serviced = None
    race = []               # a request posted between the map command and the clear of ctx+0xB4
    with gzip.open(levp, "rt") as f:
        rd = csv.DictReader(f)
        for r in rd:
            ev = r["ev"]
            t = int(r["t"])
            if ev == "t3":
                t3 = t
            elif ev == "apkt":
                cur = int(r["aud_rd"])
                req = int(r["refill_req"])
                if prev_rd is not None:
                    size = (cur - prev_rd) % 0x800
                    # halves touched by bytes [prev_rd, prev_rd + size)
                    touched = set()
                    for b in (0, max(size - 1, 0)):
                        a = H0 + ((prev_rd - H0 + b) % 0x800)
                        touched.add(H0 if a < H1 else H1)
                    for h in touched:
                        if h in pending:
                            stale.append((t, h, npk, pending[h]))
                            if first_stale is None:
                                first_stale = dict(t=t, half=hex(h), packet=npk, dec=int(r["dec"]), disp=int(r["disp"]),
                                                   played=int(r["played"]), adec=int(r["adec"]))
                        if h in last_refill:
                            tr = last_refill.pop(h)
                            margins.append((tr, t3, t3 - tr, h, npk))
                if req == 0:
                    serviced = None
                if req != prev_req and req != 0 and req != serviced:
                    if serviced is not None:
                        race.append(dict(t=t, req=hex(req), packet=npk))
                    if prev_req != 0 and prev_req in pending:
                        lost.append(dict(t=t, overwritten=hex(prev_req), by=hex(req), packet=npk, dec=int(r["dec"]),
                                         disp=int(r["disp"])))
                    pending[req] = t
                    posted_at[req] = t
                prev_req = req
                prev_rd = cur
                npk += 1
            elif ev == "refill":
                h = int(r["refill_req"])
                if h in pending:
                    req_wait.append(t - pending.pop(h))
                last_refill[h] = t
                # the main loop clears ctx+0xB4 a few instructions after the map command; a Timer3
                # IRQ in between still sees the old value (not a new request)
                serviced = h
                prev_req = h
    m = np.array([x[2] for x in margins], float) if margins else np.array([np.nan])
    order = np.argsort(m)
    worst = [dict(refill_t=int(margins[i][0]), deadline_t=int(margins[i][1]), margin_cyc=int(margins[i][2]),
                  margin_ms=margins[i][2] / F * 1000, half=hex(margins[i][3]), packet=margins[i][4]) for i in order[:8]]
    return dict(packets=npk, refills=len(margins), lost_requests=len(lost), lost=lost[:10], stale_reads=len(stale), posts_in_clear_window=len(race), race=race[:10],
                first_stale=first_stale, margin=st(m), worst_margins=worst,
                request_to_refill=st(req_wait))


def analyze(run, romdir):
    tot = json.load(open(os.path.join(run, "totals.json")))
    d = load_csv(os.path.join(run, "per_frame.csv.gz"))
    S = {"run": os.path.basename(run), "profile": tot["profile"], "rom_sha1": tot["rom_sha1"],
         "stream": hex(tot["stream"]), "decodes": tot["decodes"], "blits": tot["blits"], "t3": tot["t3"],
         "end_reason": tot["end_reason"], "lost": tot["lost"], "lev_lost": tot["lev_lost"]}
    names = tot["names"]
    f = {k: np.array(v) for k, v in tot["fields"].items()}
    wall = tot["wall"]
    cyc = f["cyc"].reshape(4, 64).sum(0)
    ins = f["ins"].reshape(4, 64).sum(0)
    mw = f["memwait"].reshape(4, 64).sum(0)
    bk = {names[b]: float(cyc[b] / wall) for b in np.argsort(-cyc) if cyc[b]}
    S["wall_cycles"], S["wall_s"] = wall, wall / F
    S["buckets"] = bk

    def grp(pfx=None, names_=()):
        return sum(v for k, v in bk.items() if (pfx and k.startswith(pfx)) or k in names_)
    blocked = f["blocked"][0] / wall
    shares = dict(video=grp("vdec_"), blit=grp(names_=("blit",)), audio=grp("aud_"),
                  irq=grp(names_=("irq_entry", "irq_dispatch", "isr_thunk", "irq_bios", "timer1_tick", "av_tick")),
                  dma=float(blocked), spin=grp(names_=("main_spin",)),
                  mainloop_ui_other=grp(names_=("main_loop", "matrix_map", "player_init", "ui_keys", "ui_osd", "other",
                                                "ewram_other", "seek_prefill", "bios_swi", "rom_code", "bios_hi",
                                                "other_region")))
    shares["busy"] = 1 - shares["spin"] - f["halted"][0] / wall
    S["shares"] = shares
    vd = [k for k in names if k.startswith("vdec_")]
    tv = sum(cyc[names.index(k)] for k in vd)
    S["decode_breakdown"] = {k: float(cyc[names.index(k)] / tv) for k in vd if cyc[names.index(k)]}
    S["decode_cpi"] = float(tv / sum(ins[names.index(k)] for k in vd))
    S["decode_stall"] = float(sum(mw[names.index(k)] for k in vd) / tv)
    S["matrix_cmds"] = int(f["matrix_cmds"][0])
    rom = open(os.path.join(romdir, run_cart(run) + ".gba"), "rb").read()
    kf = seek_frames(rom, tot["stream"])
    fr = d["frame"].astype(int)
    iskf = np.isin(fr, kf)
    S["frames"] = int(len(fr))
    S["decode_cyc"] = st(d["d_vdec"])
    S["decode_cyc_keyframes"] = st(d["d_vdec"][iskf])
    S["decode_wall"] = st(d["d_wall"])
    o = np.argsort(-d["d_vdec"])[:8]
    S["worst_decodes"] = [dict(frame=int(fr[i]), keyframe=bool(iskf[i]), cyc=int(d["d_vdec"][i]),
                               wall=int(d["d_wall"][i]), resid_vlc=int(d["d_vdec_resid_vlc"][i]),
                               idct=int(d["d_vdec_idct"][i]), intra=int(d["d_vdec_intra"][i]), mc=int(d["d_vdec_mc"][i]),
                               lead_after=int(d["lead_after"][i])) for i in o]
    ok = ~np.isnan(d["w_wall"])
    busyfrac = d["w_busy"][ok] / d["w_wall"][ok]
    S["windows"] = int(ok.sum())
    S["busy_window"] = st(busyfrac)
    S["saturated_windows"] = int((d["w_spin"][ok] == 0).sum())
    for c in ("w_vdec", "w_blit", "w_audio"):
        S[c + "_frac"] = st(d[c][ok] / d["w_wall"][ok])
    S["lead_after"] = {int(v): int((d["lead_after"] == v).sum()) for v in np.unique(d["lead_after"][~np.isnan(d["lead_after"])])}
    # late / skipped from the blit schedule
    bvx = tot["blit_vx"]
    shown = set(bvx)
    skipped = sorted(set(range(min(bvx), max(bvx) + 1)) - shown) if bvx else []
    bt = d["blit_t"]
    have = ~np.isnan(bt)
    tick = np.round((bt[have] - np.nanmin(bt)) / TICK).astype(int)
    rel = tick - 3 * fr[have]
    base = int(np.median(rel))
    late = fr[have][rel > base]
    early = fr[have][rel < base]
    S["late_frames"] = int(len(late))
    S["late_ticks_total"] = int((rel[rel > base] - base).sum())
    S["late_max_ticks"] = int((rel - base).max())
    S["late_list"] = late.tolist()[:200]
    S["early_frames"] = int(len(early))
    S["skipped_blits"] = len(skipped)
    S["skipped_list"] = skipped[:200]
    S["late_or_dropped"] = int(len(set(late.tolist()) | set(skipped)))
    S["min_slack"] = float(np.nanmin(d["slack"]))
    S["audio_window"] = audio_window(os.path.join(run, "lev.csv.gz"))
    json.dump(S, open(os.path.join(run, "summary.json"), "w"), indent=1)
    return S


def run_cart(run):
    b = os.path.basename(run.rstrip("/"))
    return b.rsplit("_", 1)[0]


def main():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
    from roms import rom_dir
    romdir = rom_dir()
    for run in sys.argv[1:]:
        S = analyze(run, romdir)
        sh = S["shares"]
        aw = S["audio_window"]
        print(f"{S['run']:32s} {S['profile']} frames {S['frames']} busy {sh['busy']:.3f} p99win {S['busy_window']['p99']:.3f} "
              f"vdec {sh['video']:.3f} blit {sh['blit']:.3f} aud {sh['audio']:.3f} dec mean {S['decode_cyc']['mean']/1e3:.0f}k "
              f"max {S['decode_cyc']['max']/1e3:.0f}k late {S['late_frames']} skip {S['skipped_blits']} "
              f"audio margin min {aw['margin']['min']/F*1000 if aw['margin'] else float('nan'):.1f} ms lost {aw['lost_requests']} stale {aw['stale_reads']}")


if __name__ == "__main__":
    main()
