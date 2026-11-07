#!/usr/bin/env python3
"""PC -> activity bucket map for the Shrek player (used by the mGBA instrumentation and the unicorn
cost model). Static: hand ranges for known routines + capstone flood fill of the partition-tree
mode handlers (MC vs intra vs dispatch). Writes buckets.json."""
import json
import os
import struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from capstone.arm import ARM_OP_IMM, ARM_REG_PC, ARM_REG_LR

HERE = os.path.dirname(os.path.abspath(__file__))
PL = os.path.join(HERE, "..", "player")
IW = open(os.path.join(PL, "iwram.bin"), "rb").read()
B = 0x03000000

NAMES = ["other", "main_spin", "main_loop", "vdec_frame", "vdec_bits", "vdec_refill", "vdec_mapwait",
         "vdec_dispatch", "vdec_mc", "vdec_intra", "vdec_resid_vlc", "vdec_idct", "blit",
         "aud_unpack", "aud_synth", "aud_packet", "av_tick", "timer1_tick", "irq_dispatch", "isr_thunk",
         "irq_bios", "bios_swi", "ui_keys", "ui_osd", "matrix_map", "ewram_other", "seek_prefill",
         "player_init", "aud_reset"]
NAMES += [f"unused{i}" for i in range(len(NAMES), 60)] + ["irq_entry", "rom_code", "bios_hi", "other_region"]
BID = {n: i for i, n in enumerate(NAMES)}

# ---- hand ranges [lo, hi) -> bucket
RANGES = [
    (0x00000000, 0x00004000, "bios_swi"),
    (0x00000180, 0x000001A0, "irq_bios"),
    (0x02000000, 0x02040000, "ewram_other"),
    (0x02000100, 0x02000158, "isr_thunk"),
    (0x020002c4, 0x02000358, "seek_prefill"),
    (0x020003a8, 0x02000400, "matrix_map"),
    (0x02000400, 0x0200067c, "seek_prefill"),
    (0x020006c0, 0x02000874, "seek_prefill"),
    (0x02000874, 0x020008d8, "ui_keys"),
    (0x020008d8, 0x020009b4, "ui_osd"),
    (0x02000a74, 0x020010a4, "ui_osd"),
    (0x03000000, 0x03008000, "other"),
    (0x03000040, 0x03000168, "irq_dispatch"),
    (0x03000370, 0x03000520, "blit"),
    (0x03000520, 0x0300076c, "vdec_frame"),
    (0x0300076c, 0x030007a8, "vdec_bits"),
    (0x030007a8, 0x030007fc, "vdec_refill"),
    (0x030007fc, 0x0300081c, "vdec_mapwait"),
    (0x0300081c, 0x03000864, "vdec_bits"),
    (0x03005650, 0x03005794, "vdec_resid_vlc"),   # CBP + per-4x4 calls
    (0x03005794, 0x03005828, "vdec_resid_vlc"),   # coefficient VLC
    (0x03005828, 0x030059b4, "vdec_idct"),        # dequant + IDCT rows (+ DC-only path)
    (0x030059b4, 0x03005a94, "vdec_resid_vlc"),   # escapes
    (0x03005a94, 0x03006260, "vdec_idct"),        # IDCT columns + add/clamp/store (6 alignments)
    (0x03006260, 0x030069c0, "aud_synth"),
    (0x03006288, 0x0300645c, "aud_unpack"),       # prediction/overlap (incl. coef setup)
    (0x030069c0, 0x03006a14, "aud_reset"),
    (0x03006a14, 0x03006ba0, "aud_packet"),       # packet + 4x upsample/noise-shape output
    (0x03006ba0, 0x03006c60, "aud_unpack"),
    (0x03006c60, 0x03006d30, "aud_packet"),
    (0x03006d30, 0x03006df4, "vdec_frame"),
    (0x03006df4, 0x03007974, "player_init"),
    (0x030072f0, 0x03007318, "main_spin"),
    (0x03007318, 0x030073e4, "main_loop"),
    (0x03007974, 0x03007ab0, "timer1_tick"),
    (0x03007ab0, 0x03007cf4, "av_tick"),
]

md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
md.detail = True


def insn(a):
    try:
        return next(md.disasm(IW[a - B:a - B + 4], a))
    except StopIteration:
        return None


def flow(i):
    """-> (fallthrough, branch_target or None, is_call, stops)"""
    m, op = i.mnemonic, i.op_str
    cond = i.cc not in (0, 15) if hasattr(i, "cc") else False
    regs_w = [r for r in (i.regs_access()[1] if i.id else [])]
    if m.startswith("bl") and not m.startswith("bls") and not m.startswith("blt") and not m.startswith("ble") \
            and m.startswith("bl") and i.operands and i.operands[0].type == ARM_OP_IMM:
        return True, i.operands[0].imm, True, False
    if m.startswith("b") and not m.startswith("bx") and not m.startswith("bic") and i.operands and \
            i.operands[0].type == ARM_OP_IMM and (m in ("b",) or len(m) == 3):
        return cond, i.operands[0].imm, False, not cond
    if m.startswith("bx") or ARM_REG_PC in regs_w:
        return cond, None, False, not cond
    return True, None, False, False


def stub_targets():
    tabs = [(0x03001d4c, 24), (0x03002124, 24), (0x03003094, 24), (0x03003524, 24), (0x0300247c, 12),
            (0x030037e8, 12), (0x0300419c, 12), (0x03004438, 12), (0x03004720, 12)]
    tabs += [(a - 0x30, 12) for a in (0x030028ec, 0x03003adc, 0x03004950, 0x03004fd0, 0x03005294, 0x03005494, 0x030055fc)]
    out = []
    for a, n in tabs:
        for m in range(n):
            t = struct.unpack_from("<I", IW, a + 4 * m - B)[0]
            if t:
                out.append((m, t))
    return out


def follow_stub(t, limit=12):
    """calls made by a stub (BL targets and tail-branch targets out of the stub)"""
    calls, a = [], t
    for _ in range(limit):
        i = insn(a)
        ft, tgt, call, stop = flow(i)
        if tgt is not None:
            if call:
                calls.append(tgt)
            elif abs(tgt - a) > 0x40 or tgt in (0x03005650,):
                calls.append(tgt)
                if stop:
                    break
            else:
                a = tgt
                continue
        if stop:
            break
        a += 4
    return calls


def build():
    lab = {}
    for lo, hi, n in RANGES:
        for a in range(lo & ~1, hi, 2):
            lab[a] = n
    # workers from mode stubs
    blk = [0x03001dac, 0x03002184, 0x030030f4, 0x03003584, 0x030024ac, 0x03003818, 0x030041cc, 0x03004468,
           0x03004750, 0x030028ec, 0x03003adc, 0x03004950, 0x03004fd0, 0x03005294, 0x03005494, 0x030055fc]
    workers = {}
    for m, t in stub_targets():
        kind = m % 12
        for c in follow_stub(t):
            if c in blk or c == 0x03005650:
                continue
            if kind in (1, 2):
                continue
            workers.setdefault(c, set()).add("vdec_intra" if kind in (4, 6, 7) else "vdec_mc")
    flood = {}
    known = set(workers) | set(blk) | {0x0300076c, 0x0300081c, 0x030007a8, 0x030007fc, 0x03005650, 0x03005794}
    queue = [(w, (sorted(k)[0] if len(k) == 1 else "vdec_mc")) for w, k in workers.items()]
    seen_entry = set()
    while queue:
        e, cat = queue.pop()
        if (e, cat) in seen_entry:
            continue
        seen_entry.add((e, cat))
        todo, vis = [e], set()
        while todo:
            a = todo.pop()
            if a in vis or not (0x03000864 <= a < 0x03005650):
                continue
            vis.add(a)
            flood.setdefault(a, cat)
            i = insn(a)
            if i is None:
                continue
            ft, tgt, call, stop = flow(i)
            if tgt is not None:
                if call:
                    if tgt not in known and 0x03000864 <= tgt < 0x03005650:
                        queue.append((tgt, cat))
                elif tgt not in known:
                    todo.append(tgt)
            if ft and not stop:
                todo.append(a + 4)
    # dispatch: everything in the tree region not reached from a worker
    for a in range(0x03000864, 0x03005650, 4):
        lab[a] = lab[a + 2] = flood.get(a, None) or "?"
    # gap fill: unlabeled -> label of previous labeled instruction, else dispatch
    prev = "vdec_dispatch"
    for a in range(0x03000864, 0x03005650, 4):
        if lab[a] == "?":
            lab[a] = lab[a + 2] = prev if prev != "vdec_dispatch" else "vdec_dispatch"
        prev = lab[a]
    # the stubs and blk dispatchers themselves are dispatch
    for a in blk:
        for k in range(0, 0x14, 2):
            lab[a + k] = "vdec_dispatch"
    for m, t in stub_targets():
        a = t
        for _ in range(12):
            if flood.get(a):
                break
            lab[a] = lab[a + 2] = "vdec_dispatch"
            i = insn(a)
            ft, tgt, call, stop = flow(i)
            if stop or call or tgt:
                if call:  # stub continues after a bl (e.g. split / mode+residual)
                    a += 4
                    continue
                break
            a += 4
    return lab, workers


def main():
    lab, workers = build()
    print("workers:", {hex(k): sorted(v) for k, v in sorted(workers.items())})
    # compress to ranges
    rng = []
    for a in sorted(lab):
        n = lab[a]
        if rng and rng[-1][2] == n and rng[-1][1] == a:
            rng[-1][1] = a + 2
        else:
            rng.append([a, a + 2, n])
    json.dump({"names": NAMES, "ranges": rng}, open(os.path.join(HERE, "buckets.json"), "w"))
    from collections import Counter
    c = Counter()
    for lo, hi, n in rng:
        if 0x03000864 <= lo < 0x03005650:
            c[n] += hi - lo
    print({k: hex(v) for k, v in c.items()}, len(rng), "ranges")


if __name__ == "__main__":
    main()
