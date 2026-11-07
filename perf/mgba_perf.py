#!/usr/bin/env python3
"""Cycle-level resource accounting of the Shrek GBA Video player, measured in an instrumented
mGBA libretro core (perf/mgba-src, built to perf/mgba-src/mgba_libretro.so).

The core attributes every emulated cycle:
  * per instruction: cycles charged by mGBA (fetch + execute + data waitstates), instruction count,
    data-access cycles and their waitstate part, sequential-fetch waitstates -> key = ctx*64 + bucket
    (bucket = activity from the PC LUT built by classify.py; ctx = 0 main, 1 IRQ dispatch, 2 Timer1, 3 Timer3)
  * per data access: region (addr>>24), bytes, waitstate cycles, split by ctx
  * DMA units/bytes/cycles per channel, cycles the CPU was blocked by DMA, halted cycles
  * IRQ exception entry cycles, Matrix mapper commands, CPU VRAM writes by raster state
and takes a full counter snapshot at event PCs (decode start/end, Timer3 tick, blit start/end).

Outputs (in --out): per_frame.csv (one row per VX frame: its decode + the display window that
shows it), per_tick.csv (one row per Timer3 tick), totals.json (whole playback), and per-PC LUT.

  mgba_perf.py --out .                       # whole movie from power-on (about 10 min)
  mgba_perf.py --state ../emu/states/vx_19997.state --frames 300 --out sample   # a segment
"""
import argparse
import csv
import ctypes as C
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.join(HERE, "..", "emu")
sys.path.insert(0, EMU)
import retro  # noqa: E402

CORE = os.path.join(HERE, "mgba-src", "mgba_libretro.so")
ROM = os.path.join(HERE, "..", "shrek.gba")
CTX = 0x06013E80
NB, NCTX = 64, 4
NK = NB * NCTX

# ---- C struct layout (include/mgba/internal/gba/perf.h)
FIELDS = [("now", 1), ("elapsed", 1), ("cyc", NK), ("ins", NK), ("memcyc", NK), ("memwait", NK),
          ("fetchwait", NK), ("halted", 1), ("blocked", 1), ("irq_entry_cyc", 1), ("irqs", 1),
          ("dma_units", 4), ("dma_bytes", 4), ("dma_cyc", 4), ("matrix_cmds", 1), ("matrix_bytes", 1),
          ("rd_n", 64), ("wr_n", 64), ("rd_b", 64), ("wr_b", 64), ("rd_w", 64), ("wr_w", 64),
          ("fetch_b", 16), ("matrix_cmds_ctx", 4), ("dma_rd_b", 16), ("dma_wr_b", 16), ("vram_wr_b", 3)]
OFF = {}
_o = 0
for _n, _k in FIELDS:
    OFF[_n] = slice(_o, _o + _k)
    _o += _k
NWORDS = _o
SNAP_BYTES = 16 + 8 * NWORDS

# event PCs
E_DEC_S, E_DEC_E, E_DEC_E2, E_T3, E_BLIT_S, E_BLIT_E = 0x03006D30, 0x03007320, 0x02000310, 0x03007AB0, 0x03000370, 0x03007CCC
EVENTS = [E_DEC_S, E_DEC_E, E_DEC_E2, E_T3, E_BLIT_S, E_BLIT_E]
F_POP, F_EVENT = 0x0800, 0x1000
CTXSET = {0x02000100: 2, 0x0200012C: 3, 0x030072F0: 0, 0x03007318: 0}
POP_PC = [0x00000198]

REGIONS = {2: "ewram", 3: "iwram", 4: "io", 5: "pal", 6: "vram", 7: "oam", 8: "rom"}


def load_buckets():
    b = json.load(open(os.path.join(HERE, "buckets.json")))
    return b["names"], b["ranges"]


class Perf:
    def __init__(self, r):
        self.r = r
        L = r.lib
        L.retro_perf_counters.restype = C.c_void_p
        L.retro_perf_drain.restype = C.c_int
        L.retro_perf_lost.restype = C.c_uint32
        L.retro_perf_lut_set.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32]
        assert L.retro_perf_snap_size() == SNAP_BYTES, (L.retro_perf_snap_size(), SNAP_BYTES)
        self.names, ranges = load_buckets()
        self.bid = {n: i for i, n in enumerate(self.names)}
        lut = {}
        for lo, hi, n in ranges:
            for a in range(lo, hi, 2):
                lut[a] = self.bid[n]
        for pc in EVENTS:
            lut[pc] = lut.get(pc, 0) | F_EVENT
        for pc, c in CTXSET.items():
            lut[pc] = lut.get(pc, 0) | ((c + 1) << 8)
        for pc in POP_PC:
            lut[pc] = lut.get(pc, 0) | F_POP
        # push as runs
        keys = sorted(lut)
        i = 0
        while i < len(keys):
            j = i
            while j + 1 < len(keys) and keys[j + 1] == keys[j] + 2 and lut[keys[j + 1]] == lut[keys[i]]:
                j += 1
            L.retro_perf_lut_set(keys[i], keys[j] + 2, lut[keys[i]])
            i = j + 1
        self.buf = C.create_string_buffer(SNAP_BYTES * 256)
        self.cptr = L.retro_perf_counters()

    def counters(self):
        return np.frombuffer(C.string_at(self.cptr, 8 * NWORDS), np.uint64).astype(np.int64)

    def drain(self):
        n = self.r.lib.retro_perf_drain(self.buf, 256)
        out = []
        raw = self.buf.raw
        for k in range(n):
            o = k * SNAP_BYTES
            pc, ctx, vc, _ = struct.unpack_from("<4I", raw, o)
            v = np.frombuffer(raw, np.uint64, NWORDS, o + 16).astype(np.int64)
            out.append((pc, ctx, vc, v))
        return out


def boot_to_play(r):
    r.run(880)
    r.buttons = {"A"}
    r.run(5)
    r.buttons = set()


# ---- column extraction from a counter delta
def groups(names):
    g = {
        "vdec": [n for n in names if n.startswith("vdec_")],
        "blit": ["blit"],
        "audio": ["aud_unpack", "aud_synth", "aud_packet", "aud_reset"],
        "irq": ["irq_entry", "irq_dispatch", "isr_thunk", "irq_bios", "timer1_tick", "av_tick"],
        "spin": ["main_spin"],
        "mainloop": ["main_loop", "matrix_map", "player_init"],
        "ui": ["ui_keys", "ui_osd"],
        "other": ["other", "ewram_other", "seek_prefill", "bios_swi", "rom_code", "bios_hi", "other_region"],
    }
    return g


class Cols:
    def __init__(self, names):
        self.names = names
        self.bid = {n: i for i, n in enumerate(names)}
        self.g = groups(names)

    def bucket_sum(self, d, field, buckets, ctxs=range(NCTX)):
        a = d[OFF[field]].reshape(NCTX, NB)
        return int(sum(a[c, self.bid[b]] for c in ctxs for b in buckets))

    def row(self, d, prefix=""):
        row = {}
        wall = int(d[OFF["now"]][0])
        row["wall"] = wall
        cyc = d[OFF["cyc"]].reshape(NCTX, NB)
        for gname, bl in self.g.items():
            row[f"{gname}"] = self.bucket_sum(d, "cyc", bl)
        row["halted"] = int(d[OFF["halted"]][0])
        row["dma_blocked"] = int(d[OFF["blocked"]][0])
        row["dma_cyc"] = int(d[OFF["dma_cyc"]].sum())
        row["busy"] = wall - row["spin"] - row["halted"]
        for b in self.g["vdec"] + ["aud_unpack", "aud_synth", "aud_packet", "matrix_map", "av_tick", "timer1_tick",
                                   "irq_entry", "irq_dispatch", "isr_thunk", "irq_bios"]:
            row[b] = int(cyc[:, self.bid[b]].sum())
        row["vdec_ins"] = self.bucket_sum(d, "ins", self.g["vdec"])
        row["vdec_memwait"] = self.bucket_sum(d, "memwait", self.g["vdec"])
        row["vdec_fetchwait"] = self.bucket_sum(d, "fetchwait", self.g["vdec"])
        row["blit_memwait"] = self.bucket_sum(d, "memwait", ["blit"])
        row["memwait_all"] = int(d[OFF["memwait"]].sum())
        row["fetchwait_all"] = int(d[OFF["fetchwait"]].sum())
        rb = d[OFF["rd_b"]].reshape(NCTX, 16).sum(0)
        wb = d[OFF["wr_b"]].reshape(NCTX, 16).sum(0)
        rw = d[OFF["rd_w"]].reshape(NCTX, 16).sum(0)
        ww = d[OFF["wr_w"]].reshape(NCTX, 16).sum(0)
        for k, n in REGIONS.items():
            row[f"rd_{n}"] = int(rb[k])
            row[f"wr_{n}"] = int(wb[k])
            row[f"wait_{n}"] = int(rw[k] + ww[k])
        row["dma1_bytes"] = int(d[OFF["dma_bytes"]][1])
        row["dma3_bytes"] = int(d[OFF["dma_bytes"]][3])
        row["matrix_cmds"] = int(d[OFF["matrix_cmds"]][0])
        row["matrix_bytes"] = int(d[OFF["matrix_bytes"]][0])
        row["irqs"] = int(d[OFF["irqs"]][0])
        vw = d[OFF["vram_wr_b"]]
        row["vram_wr_active"], row["vram_wr_hblank"], row["vram_wr_vblank"] = int(vw[0]), int(vw[1]), int(vw[2])
        if prefix:
            row = {prefix + k: v for k, v in row.items()}
        return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--state", help="start from an mGBA savestate (emu/states/vx_K.state: VX frame K just decoded)")
    ap.add_argument("--frames", type=int, default=10 ** 9, help="stop after this many decodes")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    r = retro.Retro(ROM, core=CORE)
    r.run(1)
    p = Perf(r)
    cols = Cols(p.names)
    first_vx = 0
    if args.state:
        r.load_state(open(args.state, "rb").read())
        import re
        first_vx = int(re.findall(r"(\d+)", os.path.basename(args.state))[0]) + 1
        r.lib.retro_perf_set_ctx(0)
        w0 = struct.unpack("<64I", r.read(CTX, 256))
        disp_base = w0[11] - 3
        first_vx = w0[10] - 3
    else:
        boot_to_play(r)
        disp_base = 0
    r.lib.retro_perf_reset()

    dec_rows = {}        # vx -> dict
    tick_rows = []
    blits = []           # (time, vcount_s, vcount_e, cycles, ticks_since_prev)
    cur_dec = None
    n_dec = first_vx     # VX index of the next decode start
    last = {}
    t3_count = 0
    t3_since_blit = 0
    t3_since_blit_prev = 0
    blit_count = 0
    disp_seen = None
    skips = 0
    started = bool(args.state)
    play_start = None
    total0 = None
    lead_at_dec_e = []
    t0 = time.time()
    emu0 = r.frame_no
    stop_after = None

    def snapvec(v):
        return v

    while True:
        r.run(1)
        for pc, ctx, vc, v in p.drain():
            if pc == E_DEC_S:
                cur_dec = (n_dec, v, vc)
                started = True
            elif pc in (E_DEC_E, E_DEC_E2) and cur_dec is not None:
                vx, v0, _ = cur_dec
                d = v - v0
                row = {"frame": vx, "dec_start": int(v0[OFF["now"]][0]), "dec_end": int(v[OFF["now"]][0])}
                row.update(cols.row(d, "d_"))
                row["lead_after"] = (vx + 1) - (disp_base + blit_count)  # decoded-not-yet-displayed after this decode
                row["prefill"] = int(pc == E_DEC_E2)
                dec_rows[vx] = row
                n_dec += 1
                cur_dec = None
                if n_dec - first_vx >= args.frames and stop_after is None:
                    stop_after = r.frame_no + 60
            elif pc == E_T3:
                if "t3" in last:
                    d = v - last["t3"]
                    tr = {"tick": t3_count, "t": int(last["t3"][OFF["now"]][0])}
                    tr.update(cols.row(d))
                    tick_rows.append(tr)
                else:
                    play_start = v
                last["t3"] = v
                t3_count += 1
                t3_since_blit += 1
            elif pc == E_BLIT_S:
                last["blit_s"] = (v, vc)
                if "win" in last:
                    # display window: from previous blit start to this blit start (= one movie frame period)
                    d = v - last["win"]
                    vx_prev = blit_count - 1 + disp_base
                    if vx_prev in dec_rows:
                        dec_rows[vx_prev].update(cols.row(d, "w_"))
                        dec_rows[vx_prev]["w_ticks"] = round(int(d[OFF["now"]][0]) / 798912)
                last["win"] = v
                t3_since_blit_prev = t3_since_blit
                t3_since_blit = 0
            elif pc == E_BLIT_E and "blit_s" in last:
                v0, vc0 = last["blit_s"]
                vx = blit_count + disp_base
                if vx in dec_rows:
                    dec_rows[vx].update({"blit_t": int(v0[OFF["now"]][0]), "blit_vcount_s": vc0, "blit_vcount_e": vc,
                                         "blit_wall": int((v - v0)[OFF["now"]][0]),
                                         "blit_cyc": int((v - v0)[OFF["cyc"]].reshape(NCTX, NB)[:, cols.bid["blit"]].sum()),
                                         "blit_vram_active": int((v - v0)[OFF["vram_wr_b"]][0]),
                                         "blit_vram_hblank": int((v - v0)[OFF["vram_wr_b"]][1]),
                                         "blit_vram_vblank": int((v - v0)[OFF["vram_wr_b"]][2]),
                                         "slack": int(v0[OFF["now"]][0]) - dec_rows[vx]["dec_end"]})
                blit_count += 1
        if started:
            w = struct.unpack("<64I", r.read(CTX, 256))
            if w[0] != 0x020032C8:
                print("ctx invalid: movie ended", file=sys.stderr)
                break
            disp = w[11]
            if disp - 3 - disp_base != blit_count and not skips:
                skips += 1
                print(f"WARNING: display count {disp} != blits {blit_count} + base: skipped blit near emu {r.frame_no}",
                      file=sys.stderr)
            disp_seen = disp
        if stop_after is not None and r.frame_no >= stop_after:
            break
        if (r.frame_no - emu0) % 20000 == 0:
            print(f"emu {r.frame_no} vx {n_dec} blits {blit_count} t3 {t3_count} {time.time() - t0:.0f}s lost={r.lib.retro_perf_lost()}",
                  file=sys.stderr)
    final = p.counters()
    lost = r.lib.retro_perf_lost()
    dt = time.time() - t0
    disp_final = disp_seen
    print(f"done: {n_dec - first_vx} decodes, {blit_count} blits, {t3_count} T3 ticks, {r.frame_no - emu0} emu frames, "
          f"{dt:.0f}s, lost snaps {lost}, display count {disp_final}", file=sys.stderr)

    # ---- write per_frame.csv
    keys = []
    for row in dec_rows.values():
        for k in row:
            if k not in keys:
                keys.append(k)
    with open(os.path.join(args.out, "per_frame.csv"), "w", newline="") as f:
        cw = csv.DictWriter(f, keys)
        cw.writeheader()
        for vx in sorted(dec_rows):
            cw.writerow(dec_rows[vx])
    if tick_rows:
        with open(os.path.join(args.out, "per_tick.csv"), "w", newline="") as f:
            cw = csv.DictWriter(f, list(tick_rows[0]))
            cw.writeheader()
            cw.writerows(tick_rows)
    # ---- totals over playback: first T3 tick .. end
    tot = {"names": p.names, "lost": lost, "decodes": n_dec - first_vx, "blits": blit_count, "t3": t3_count,
           "display_count": disp_final, "first_vx": first_vx}
    if play_start is not None:
        d = final - play_start
        d[OFF["now"]] = 0
        tot["wall"] = int(final[OFF["elapsed"]][0] - play_start[OFF["elapsed"]][0])
        tot["fields"] = {n: d[OFF[n]].tolist() for n, _ in FIELDS}
    tot["all_fields"] = {n: final[OFF[n]].tolist() for n, _ in FIELDS}
    json.dump(tot, open(os.path.join(args.out, "totals.json"), "w"))


if __name__ == "__main__":
    main()
