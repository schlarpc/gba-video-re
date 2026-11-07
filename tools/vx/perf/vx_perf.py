#!/usr/bin/env python3
"""[vx suite copy: generalised to every VX++ / VXGB cart and stream; see PROFILES, --rom/--stream,
light events (lev.csv), skipped-blit-aware display numbering.]

Cycle-level resource accounting of the Shrek GBA Video player, measured in an instrumented
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
import gzip
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
import capture  # noqa: E402  (menu tables)

CORE = os.path.join(HERE, "mgba-src", "mgba_libretro.so")
CTX = 0x06013E80
import hashlib  # noqa: E402

# Per player build: event PCs, context-set PCs, bucket map, frame slot 0, light-event PCs.
# Light events record 16 player-ctx words (LEV_OFFS) with a cycle time stamp.
PROFILES = {
    # VX++ player (Rev 6 carts and the Shrek + Shark Tale combo), player sha1 7324ebf3...
    "vxpp": dict(dec_s=0x03006D30, dec_e=0x03007320, dec_e2=0x02000310, t3=0x03007AB0, blit_s=0x03000370,
                 blit_e=0x03007CCC, ctxset={0x02000100: 2, 0x0200012C: 3, 0x030072F0: 0, 0x03007318: 0},
                 buckets="buckets.json", slot0=0x020032C8,
                 lev={0x03007C0C: "apkt", 0x03007348: "refill", 0x03007AB0: "t3", 0x03000370: "blit",
                      0x03006D30: "dec_s", 0x03007320: "dec_e"}),
    # VXGB player (Rev 5 Shrek, Shark Tale, Shrek 2), player sha1 33a2f3a7...
    "vxgb": dict(dec_s=0x03000510, dec_e=0x02001164, dec_e2=0x02000F84, t3=0x02001D08, blit_s=0x03000370,
                 blit_e=0x02001F24, ctxset={0x02000100: 2, 0x0200012C: 3, 0x020010B0: 0, 0x020010D8: 0},
                 buckets="buckets_vxgb.json", slot0=0x02002B48,
                 lev={0x02001E64: "apkt", 0x020011C8: "refill", 0x02001D08: "t3", 0x03000370: "blit",
                      0x03000510: "dec_s", 0x02001164: "dec_e"}),
}
PLAYER_SHA1 = {"7324ebf35a10601b8c09bcff008b76144e2734cb": "vxpp", "33a2f3a71000ac5dcfbe6791252eeb0916dc3a33": "vxgb"}
LEV_OFFS = [0x24, 0x28, 0x2C, 0x30, 0x90, 0x98, 0x9C, 0xA0, 0xA4, 0xAC, 0xB0, 0xB4, 0xBC, 0xC0, 0x20, 0x00]
LEV_NAMES = ["n24", "dec", "disp", "vxcnt", "aud_next", "aud_rd", "ring", "played", "adec", "ac", "b0", "refill_req",
             "vid_next", "vid_rd", "stream", "slot0"]
F_LEV = 0x2000
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

F_POP, F_EVENT = 0x0800, 0x1000
POP_PC = [0x00000198]


def set_profile(name):
    global E_DEC_S, E_DEC_E, E_DEC_E2, E_T3, E_BLIT_S, E_BLIT_E, EVENTS, CTXSET, PROF
    PROF = PROFILES[name]
    E_DEC_S, E_DEC_E, E_DEC_E2, E_T3, E_BLIT_S, E_BLIT_E = (PROF[k] for k in ("dec_s", "dec_e", "dec_e2", "t3", "blit_s", "blit_e"))
    EVENTS = [E_DEC_S, E_DEC_E, E_DEC_E2, E_T3, E_BLIT_S, E_BLIT_E]
    CTXSET = PROF["ctxset"]

REGIONS = {2: "ewram", 3: "iwram", 4: "io", 5: "pal", 6: "vram", 7: "oam", 8: "rom"}


def load_buckets():
    b = json.load(open(os.path.join(HERE, PROF["buckets"])))
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
        for pc in PROF["lev"]:
            lut[pc] = lut.get(pc, 0) | F_LEV
        L.retro_perf_lev_off.argtypes = [C.c_int, C.c_uint32]
        for i, o in enumerate(LEV_OFFS):
            L.retro_perf_lev_off(i, (CTX + o) & 0x1FFFF)
        self.lev_size = L.retro_perf_lev_size()
        assert self.lev_size == 16 + 64
        self.lbuf = C.create_string_buffer(self.lev_size * 4096)
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

    def lev_drain(self):
        out = []
        while True:
            n = self.r.lib.retro_perf_lev_drain(self.lbuf, 4096)
            raw = self.lbuf.raw
            for k in range(n):
                o = k * self.lev_size
                pc, ctx, now = struct.unpack_from("<IIQ", raw, o)
                out.append((pc, ctx, now) + struct.unpack_from("<16I", raw, o + 16))
            if n < 4096:
                return out

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
    ap.add_argument("--rom", required=True)
    ap.add_argument("--stream", type=lambda x: int(x, 0), default=0x20200)
    ap.add_argument("--menu", help="menu presses 'frame:BUTTON,...' (default: capture.py's per-cart table)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=10 ** 9, help="stop after this many decodes")
    ap.add_argument("--no-lev", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    romb = open(args.rom, "rb").read()
    prof = PLAYER_SHA1[hashlib.sha1(romb[0x8a00:0x18a00]).hexdigest()]
    set_profile(prof)
    print(f"player profile {prof}", file=sys.stderr)

    r = retro.Retro(args.rom, core=CORE)
    r.run(1)
    p = Perf(r)
    cols = Cols(p.names)
    presses = capture.parse_menu(args.menu or capture.menu_for(args.rom, args.stream))
    capture.boot_to_play(r, presses)
    r.lib.retro_perf_reset()

    dec_rows = {}
    tick_rows = []
    cur_dec = None
    n_dec = 0
    last = {}
    t3_count = 0
    blit_count = 0
    blit_vx = []         # vx shown by each blit, in order
    started = False
    play_start = None
    t0 = time.time()
    emu0 = r.frame_no
    stop_after = None
    levf = gzip.open(os.path.join(args.out, "lev.csv.gz"), "wt")
    levf.write("ev,ctx,t," + ",".join(LEV_NAMES) + "\n")
    levname = PROF["lev"]
    blit_disp = {}
    end_reason = ""

    while True:
        r.run(1)
        capture.press_keys(r)
        for e in p.lev_drain():
            pc, cx, now = e[0], e[1], e[2]
            levf.write(f"{levname.get(pc, hex(pc))},{cx},{now}," + ",".join(str(x) for x in e[3:]) + "\n")
            if pc == E_BLIT_S:
                blit_disp[now] = e[3 + 2]          # ctx+0x2c at blit start = decoder index shown
        for pc, ctx, vc, v in p.drain():
            if pc == E_DEC_S:
                cur_dec = (n_dec, v, vc)
                if not started:
                    started = True
                    w = struct.unpack("<64I", r.read(CTX, 256))
                    if (w[8] & 0x03FFFFFF) != args.stream:
                        sys.exit(f"menu started stream 0x{w[8] & 0x03FFFFFF:x}, not 0x{args.stream:x}")
            elif pc in (E_DEC_E, E_DEC_E2) and cur_dec is not None:
                vx, v0, _ = cur_dec
                d = v - v0
                row = {"frame": vx, "dec_start": int(v0[OFF["now"]][0]), "dec_end": int(v[OFF["now"]][0])}
                row.update(cols.row(d, "d_"))
                shown = blit_vx[-1] + 1 if blit_vx else 0
                row["lead_after"] = (vx + 1) - shown
                row["prefill"] = int(pc == E_DEC_E2)
                dec_rows[vx] = row
                n_dec += 1
                cur_dec = None
                if n_dec >= args.frames and stop_after is None:
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
            elif pc == E_BLIT_S:
                now = int(v[OFF["now"]][0])
                vxs = blit_disp.pop(now, None)
                if vxs is None:
                    vxs = (blit_vx[-1] + 1) if blit_vx else 0
                    print(f"WARNING: no lev for blit at {now}", file=sys.stderr)
                else:
                    vxs -= 3
                last["blit_s"] = (v, vc, vxs)
                if "win" in last:
                    d = v - last["win"][0]
                    vp = last["win"][1]
                    if vp in dec_rows:
                        dec_rows[vp].update(cols.row(d, "w_"))
                        dec_rows[vp]["w_ticks"] = round(int(d[OFF["now"]][0]) / 798912)
                last["win"] = (v, vxs)
                blit_vx.append(vxs)
            elif pc == E_BLIT_E and "blit_s" in last:
                v0, vc0, vx = last["blit_s"]
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
            if w[0] != PROF["slot0"]:
                end_reason = "ctx invalid: movie ended"
                break
        if stop_after is not None and r.frame_no >= stop_after:
            end_reason = "frame limit"
            break
        if not started and r.frame_no > presses[-1][0] + 1200:
            sys.exit("movie never started")
        if (r.frame_no - emu0) % 20000 == 0:
            print(f"emu {r.frame_no} vx {n_dec} blits {blit_count} t3 {t3_count} {time.time() - t0:.0f}s lost={r.lib.retro_perf_lost()}/{r.lib.retro_perf_lev_lost()}",
                  file=sys.stderr)
    levf.close()
    final = p.counters()
    lost = r.lib.retro_perf_lost()
    dt = time.time() - t0
    print(f"done ({end_reason}): {n_dec} decodes, {blit_count} blits, {t3_count} T3 ticks, {r.frame_no - emu0} emu frames, "
          f"{dt:.0f}s, lost snaps {lost}, lost lev {r.lib.retro_perf_lev_lost()}", file=sys.stderr)
    keys = []
    for row in dec_rows.values():
        for k in row:
            if k not in keys:
                keys.append(k)
    with gzip.open(os.path.join(args.out, "per_frame.csv.gz"), "wt", newline="") as f:
        cw = csv.DictWriter(f, keys)
        cw.writeheader()
        for vx in sorted(dec_rows):
            cw.writerow(dec_rows[vx])
    if tick_rows:
        with gzip.open(os.path.join(args.out, "per_tick.csv.gz"), "wt", newline="") as f:
            cw = csv.DictWriter(f, list(tick_rows[0]))
            cw.writeheader()
            cw.writerows(tick_rows)
    tot = {"profile": prof, "rom_sha1": hashlib.sha1(romb).hexdigest(), "stream": args.stream,
           "names": p.names, "lost": lost, "lev_lost": r.lib.retro_perf_lev_lost(), "decodes": n_dec,
           "blits": blit_count, "t3": t3_count, "blit_vx": blit_vx, "end_reason": end_reason}
    if play_start is not None:
        d = final - play_start
        d[OFF["now"]] = 0
        tot["wall"] = int(final[OFF["elapsed"]][0] - play_start[OFF["elapsed"]][0])
        tot["fields"] = {n: d[OFF[n]].tolist() for n, _ in FIELDS}
    json.dump(tot, open(os.path.join(args.out, "totals.json"), "w"))


if __name__ == "__main__":
    main()
