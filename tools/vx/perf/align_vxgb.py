#!/usr/bin/env python3
"""Map the Rev 6 (VX++) PC->bucket labels onto the Rev 5 (VXGB) player by aligning the two
IWRAM images instruction by instruction (normalised mnemonics; branch targets and literal
offsets ignored). Writes buckets_vxgb.json. Unaligned Rev 5 code is labelled by hand ranges."""
import difflib, json, os, sys
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
from roms import rom_path  # noqa: E402
R6 = open(rom_path("shrek-rev6"), "rb").read()
R5 = open(rom_path("sharktale-rev5"), "rb").read()
IW6, IW5 = R6[0xbcc8:0xbcc8 + 0x7cf4], R5[0xb548:0xb548 + 0x7cd0]
md = Cs(CS_ARCH_ARM, CS_MODE_ARM)

def toks(img):
    out = []
    for o in range(0, len(img) - 3, 4):
        ins = list(md.disasm(img[o:o + 4], 0x03000000 + o))
        if not ins:
            out.append("?" + img[o:o + 4].hex()); continue
        i = ins[0]
        op = i.op_str
        if i.mnemonic.startswith("b") and "#" in op and not i.mnemonic.startswith("bic"):
            op = "T"
        elif "[pc" in op:
            op = op.split(",")[0] + ",LIT"
        out.append(i.mnemonic + " " + op)
    return out

def main():
    b6 = json.load(open(os.path.join(HERE, "buckets.json")))
    names = list(b6["names"]); names[29] = "vdec_grid"
    lab6 = {}
    for lo, hi, n in b6["ranges"]:
        for a in range(lo, hi, 2):
            lab6[a] = n
    t6, t5 = toks(IW6), toks(IW5)
    sm = difflib.SequenceMatcher(None, t6, t5, autojunk=False)
    m5 = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            m5[0x03000000 + 4 * (blk.b + k)] = 0x03000000 + 4 * (blk.a + k)
    print(f"aligned {len(m5)}/{len(t5)} Rev 5 IWRAM instructions", file=sys.stderr)
    lab = {}
    # EWRAM (Rev 5 C code moved here) and BIOS: hand ranges
    hand = [
        (0x00000000, 0x00004000, "bios_swi"), (0x00000180, 0x000001A0, "irq_bios"),
        (0x02000000, 0x02040000, "ewram_other"),
        (0x02000100, 0x02000158, "isr_thunk"),
        (0x02000268, 0x020002c0, "matrix_map"),
        (0x020002c0, 0x02000a00, "seek_prefill"),
        (0x02000a00, 0x02002000, "player_init"),
        (0x020010b0, 0x020010d8, "main_spin"),
        (0x020010d8, 0x02001bcc, "main_loop"),
        (0x02001194, 0x020011d8, "matrix_map"),   # audio-window refill (inline)
        (0x02001bcc, 0x02001d08, "timer1_tick"),
        (0x02001d08, 0x02001f4c, "av_tick"),
        (0x0200234c, 0x02002990, "ui_osd"),
    ]
    for lo, hi, n in hand:
        for a in range(lo, hi, 2):
            lab[a] = n
    for a in range(0x03000000, 0x03000000 + len(IW5), 4):
        a6 = m5.get(a)
        n = lab6.get(a6, None) if a6 is not None else None
        lab[a] = lab[a + 2] = n or "?"
    # unaligned IWRAM: inherit from the previous aligned instruction; the CAVLC residual
    # region gets its own hand labels below
    prev = "other"
    for a in range(0x03000000, 0x03000000 + len(IW5), 4):
        if lab[a] == "?":
            lab[a] = lab[a + 2] = prev
        prev = lab[a]
    for lo, hi, n in RESID:
        for a in range(lo, hi, 2):
            lab[a] = n
    unal = [a for a in range(0x03000000, 0x03000000 + len(IW5), 4) if a not in m5]
    runs = []
    for a in unal:
        if runs and runs[-1][1] == a:
            runs[-1][1] = a + 4
        else:
            runs.append([a, a + 4])
    for lo, hi in runs:
        if hi - lo >= 16:
            print(f"  unaligned {lo:#x}..{hi:#x} ({(hi - lo) // 4} insns) -> {lab[lo]}", file=sys.stderr)
    rng = []
    for a in sorted(lab):
        n = lab[a]
        if rng and rng[-1][2] == n and rng[-1][1] == a:
            rng[-1][1] = a + 2
        else:
            rng.append([a, a + 2, n])
    json.dump({"names": names, "ranges": rng, "map5to6": {hex(k): hex(v) for k, v in m5.items()}},
              open(os.path.join(HERE, "buckets_vxgb.json"), "w"))

RESID = [
    (0x03000370, 0x03000510, "blit"),
    (0x03000510, 0x03000680, "vdec_frame"),      # decode_frame (MB loop, MV prediction)
    (0x03000680, 0x030006bc, "vdec_bits"),       # ue(v)
    (0x030006bc, 0x03000710, "vdec_refill"),
    (0x03000710, 0x03000730, "vdec_mapwait"),
    (0x03000730, 0x03000778, "vdec_bits"),       # se(v)
    (0x03000778, 0x030007a0, "vdec_dispatch"),
    # grid zeroing after modes without residual (docs/vxgb.md 4)
    (0x03001b20, 0x03001b80, "vdec_grid"), (0x03002040, 0x03002080, "vdec_grid"),
    (0x03003190, 0x030031d0, "vdec_grid"), (0x03003720, 0x03003750, "vdec_grid"),
    (0x03005e84, 0x030062e0, "vdec_resid_vlc"),  # residual(): CBP32 ue(v), per-4x4 nC, calls
    (0x030062e0, 0x03006524, "vdec_resid_vlc"),  # coef4x4: coeff_token, total_zeros, levels, runs, zero fill
    (0x03006524, 0x03006920, "vdec_idct"),       # dequant + IDCT + add/clamp/store
]
if __name__ == "__main__":
    main()
