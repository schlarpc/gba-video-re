#!/usr/bin/env python3
"""Second method for the perf numbers: an independent cycle-cost model of the player's IWRAM
routines, run in the unicorn oracle (emu/uc_oracle.py). Every executed ARM instruction is priced
from GBATEK's ARM7TDMI timing table and every data access by region / width / sequentiality
(WAITCNT 0x4014: ROM WS0 N4/S2 for 16-bit; EWRAM 3/6; VRAM/PAL 1/2; IWRAM 1). No mGBA timing code
is involved. The cost model class is copied from the Shrek perf tooling (tmp/shrek/perf/uc_cost.py,
same author) unchanged.

  uc_cost.py CART STREAM --segments 0,1,2 --out uc.csv

For each sampled segment: the codebook inflate + unpredict + convert, then for every frame the
index request (read straight from the ROM, as the cartridge does) and the blit. Compare with
per_frame.csv of hyperf.py (the window of frame k holds blit k and the index request of frame k+1).
"""
import argparse, csv, json, os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "emu")); sys.path.insert(0, os.path.join(HERE, ".."))
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_CPSR  # noqa: E402
from unicorn import arm_const  # noqa: E402
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM  # noqa: E402
from capstone import arm as carm  # noqa: E402

# code-fetch cost (S, N) for a 32-bit ARM opcode, by region
FETCH = {0: (1, 1), 2: (6, 6), 3: (1, 1), 8: (4, 6), 9: (4, 6)}


def data_cost(addr, size, seq):
    r = addr >> 24
    if r == 2:
        return 3 if size <= 2 else 6
    if r in (5, 6):
        return 1 if size <= 2 else 2
    if r in (8, 9):
        if size <= 2:
            return 2 if seq else 4
        return 4 if seq else 6
    return 1


REG_MAP = {}
for _n in dir(carm):
    if _n.startswith("ARM_REG_"):
        u = "UC_ARM_REG_" + _n[8:]
        if hasattr(arm_const, u):
            REG_MAP[getattr(carm, _n)] = getattr(arm_const, u)


def cond_ok(cc, cpsr):
    n, z, c, v = (cpsr >> 31) & 1, (cpsr >> 30) & 1, (cpsr >> 29) & 1, (cpsr >> 28) & 1
    C = carm
    return {C.ARM_CC_EQ: z, C.ARM_CC_NE: not z, C.ARM_CC_HS: c, C.ARM_CC_LO: not c, C.ARM_CC_MI: n,
            C.ARM_CC_PL: not n, C.ARM_CC_VS: v, C.ARM_CC_VC: not v, C.ARM_CC_HI: c and not z,
            C.ARM_CC_LS: (not c) or z, C.ARM_CC_GE: n == v, C.ARM_CC_LT: n != v,
            C.ARM_CC_GT: (not z) and n == v, C.ARM_CC_LE: z or n != v}.get(cc, True)


class Info:
    __slots__ = ("cls", "cc", "pcw", "regshift", "rs", "acc", "region", "bucket", "nregs")


class CostModel:
    def __init__(self, oracle, names, ranges):
        self.o = oracle
        self.uc = oracle.g.uc
        self.md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        self.md.detail = True
        self.cache = {}
        self.bid = {n: i for i, n in enumerate(names)}
        self.names = names
        self.lut = {}
        for lo, hi, n in ranges:
            for a in range(lo, hi, 2):
                self.lut[a] = self.bid[n]
        self.nb = len(names)
        self.reset()
        self.pend = None
        self.acc = []
        self.uc.hook_add(UC_HOOK_CODE, self._code)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._mem, user_data=0)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._mem, user_data=1)

    def reset(self):
        self.cyc = [0] * self.nb
        self.ins = [0] * self.nb
        self.wait = [0] * self.nb
        self.reg_bytes = {}
        self.reg_wait = {}

    def info(self, a):
        i = self.cache.get(a)
        if i is not None:
            return i
        ins = next(self.md.disasm(bytes(self.uc.mem_read(a, 4)), a))
        m = ins.mnemonic
        base = m.rstrip("s") if m not in ("mrs", "msr", "bics", "ldrsh", "ldrsb") else m
        x = Info()
        x.cc = ins.cc
        x.region = a >> 24
        x.bucket = self.lut.get(a, self.bid.get("other", 0))
        regs_w = ins.regs_access()[1]
        x.pcw = carm.ARM_REG_PC in regs_w
        x.regshift = any(op.shift.type in (carm.ARM_SFT_ASR_REG, carm.ARM_SFT_LSL_REG, carm.ARM_SFT_LSR_REG,
                                           carm.ARM_SFT_ROR_REG, carm.ARM_SFT_RRX_REG) for op in ins.operands)
        x.rs = None
        x.acc = False
        x.nregs = 0
        mn = ins.mnemonic
        if ins.id in (carm.ARM_INS_MUL, carm.ARM_INS_MLA):
            x.cls = "mul"
            x.rs = REG_MAP[ins.operands[2].reg]
            x.acc = ins.id == carm.ARM_INS_MLA
        elif ins.id in (carm.ARM_INS_UMULL, carm.ARM_INS_SMULL, carm.ARM_INS_UMLAL, carm.ARM_INS_SMLAL):
            x.cls = "mull"
            x.rs = REG_MAP[ins.operands[3].reg]
            x.acc = ins.id in (carm.ARM_INS_UMLAL, carm.ARM_INS_SMLAL)
        elif ins.id in (carm.ARM_INS_LDR, carm.ARM_INS_LDRB, carm.ARM_INS_LDRH, carm.ARM_INS_LDRSB, carm.ARM_INS_LDRSH):
            x.cls = "ldr"
        elif ins.id in (carm.ARM_INS_STR, carm.ARM_INS_STRB, carm.ARM_INS_STRH):
            x.cls = "str"
        elif ins.id in (carm.ARM_INS_LDM, carm.ARM_INS_LDMDA, carm.ARM_INS_LDMDB, carm.ARM_INS_LDMIB, carm.ARM_INS_POP):
            x.cls = "ldm"
        elif ins.id in (carm.ARM_INS_STM, carm.ARM_INS_STMDA, carm.ARM_INS_STMDB, carm.ARM_INS_STMIB, carm.ARM_INS_PUSH):
            x.cls = "stm"
        elif ins.id in (carm.ARM_INS_B, carm.ARM_INS_BL, carm.ARM_INS_BX, carm.ARM_INS_BLX):
            x.cls = "b"
        elif ins.id in (carm.ARM_INS_SWP, carm.ARM_INS_SWPB):
            x.cls = "swp"
        elif ins.id in (carm.ARM_INS_SVC,):
            x.cls = "b"
        elif ins.id in (carm.ARM_INS_MRS, carm.ARM_INS_MSR):
            x.cls = "psr"
        else:
            x.cls = "dp"
        self.cache[a] = x
        return x

    def _mem(self, uc, access, addr, size, value, write):
        self.acc.append((addr, size))

    def _finish(self):
        p = self.pend
        if p is None:
            return
        x, ok, rsval = p
        S, N = FETCH.get(x.region, (1, 1))
        acc = self.acc
        if not ok:
            c = S
            d = w = 0
        else:
            d = w = 0
            prev = None
            for (a, sz) in acc:
                seq = prev is not None and a == prev
                k = data_cost(a, sz, seq)
                d += k
                w += k - 1
                r = a >> 24
                self.reg_bytes[r] = self.reg_bytes.get(r, 0) + sz
                self.reg_wait[r] = self.reg_wait.get(r, 0) + k - 1
                prev = a + sz
            cl = x.cls
            if cl == "dp":
                c = S + (1 if x.regshift else 0) + ((S + N) if x.pcw else 0)
            elif cl in ("mul", "mull"):
                v = rsval
                top = v & 0xFFFFFF00
                if top in (0, 0xFFFFFF00):
                    m = 1
                elif (v & 0xFFFF0000) in (0, 0xFFFF0000):
                    m = 2
                elif (v & 0xFF000000) in (0, 0xFF000000):
                    m = 3
                else:
                    m = 4
                c = S + m + (1 if x.acc else 0) + (1 if cl == "mull" else 0)
            elif cl == "ldr":
                c = S + d + 1 + ((S + N) if x.pcw else 0)
            elif cl == "str":
                c = N + d
            elif cl == "ldm":
                c = S + d + 1 + ((S + N) if x.pcw else 0)
            elif cl == "stm":
                c = N + d
            elif cl == "b":
                c = 2 * S + N
            elif cl == "swp":
                c = S + d + 1
            else:
                c = S
        b = x.bucket
        self.cyc[b] += c
        self.ins[b] += 1
        self.wait[b] += w
        self.acc = []
        self.pend = None

    def _code(self, uc, addr, size, _):
        self._finish()
        x = self.info(addr)
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        ok = cond_ok(x.cc, cpsr)
        rsval = uc.reg_read(x.rs) if (x.rs is not None and ok) else 0
        self.pend = (x, ok, rsval)
        self.acc = []



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cart"); ap.add_argument("stream", type=int)
    ap.add_argument("--segments", default="0,1,2")
    ap.add_argument("--state", required=True, help="mkstate.py prefix for this cart (any playback state)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--adpcm", type=int, default=0, help="instead: N ADPCM calls of 1371 samples from the state's decoder")
    ap.add_argument("--adpcm-ctx", type=lambda x: int(x, 0), default=0x03003070 + 4)
    a = ap.parse_args()
    import hycart
    from carts import rom_path
    from uc_oracle import Oracle, OUTBUF
    from perf.hyperf import build_ranges, NAMES
    rp = rom_path(a.cart)
    o = Oracle(rp, a.state)
    rom = o.rom
    ranges, ev, shift = build_ranges(rom)
    cm = CostModel(o, NAMES, ranges)
    if a.adpcm:
        rows = []
        for k in range(a.adpcm):
            left = struct.unpack("<i", o.g.uc.mem_read(a.adpcm_ctx + 0x34, 4))[0]
            n = 1371 if left >= 1371 else 0
            if not n: break                        # block boundary: the Thumb header parser would run here
            cm.reset(); o.g.call(ev["adpcm"], (a.adpcm_ctx, 0x02030000, n), sp=o.sp, max_insns=10_000_000); cm._finish()
            rows.append(dict(call=k, n=n, uc_adpcm=cm.cyc[cm.bid["adpcm"]], uc_adpcm_wait=cm.wait[cm.bid["adpcm"]]))
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, list(rows[0])); w.writeheader(); w.writerows(rows)
        return
    c = hycart.find_containers(rom)[a.stream]
    s = hycart.Stream(rom, c["video"][0], c["video"][1])
    B = {1: 2400, 3: 3180, 5: 4240, 7: 4800}
    rows = []
    for si in map(int, a.segments.split(",")):
        g = s.segments[si]
        uc = o.g.uc
        obj = o.inf_obj
        # codebook: one request, read from ROM
        cm.reset()
        o.call("inf_init", (obj, 0x08000000 + g["cb_off"], 0x030032c4, 0))
        size = struct.unpack_from("<I", rom, g["cb_off"])[0]
        o.call("inf_block", (obj, size)); cm._finish()
        cb_inf = cm.cyc[cm.bid["inflate"]]
        raw = bytes(uc.mem_read(0x030032c4, size))
        cm.reset(); o.codebook(raw, g["mode"], g["ncb"]); cm._finish()
        conv = cm.cyc[cm.bid["vq_convert"]] + cm.cyc[cm.bid["vq_unpredict"]]
        vcb = bytes(uc.mem_read(0x06012c00, 0x2000))
        # index stream: one request per frame, window in EWRAM like the player
        o.call("inf_init", (obj, 0x08000000 + g["idx_off"], OUTBUF, 1))   # circular window, like the player
        for k in range(g["nframes"]):
            cm.reset()
            fp = o.call("inf_block", (obj, B[g["mode"]])); cm._finish()
            infc, infw = cm.cyc[cm.bid["inflate"]], cm.wait[cm.bid["inflate"]]
            idx = bytes(uc.mem_read(fp, B[g["mode"]]))
            import numpy as np
            cm.reset(); o.blit(idx, g["mode"], np.frombuffer(vcb, np.uint16)); cm._finish()
            rows.append(dict(segment=si, frame=g["frame"] + k, mode=g["mode"], uc_inflate=infc, uc_inflate_wait=infw,
                             uc_blit=cm.cyc[cm.bid["vq_blit"]], uc_cb_inflate=cb_inf if k == 0 else 0,
                             uc_convert=conv if k == 0 else 0))
        print(si, g["nframes"], "frames", file=sys.stderr)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0])); w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    main()
