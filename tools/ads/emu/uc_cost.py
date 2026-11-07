#!/usr/bin/env python3
"""Independent cycle-cost model (GBATEK ARM7TDMI timings) for code run in the unicorn oracles.
Copied from the Shrek VX project's perf/uc_cost.py (same author group, MIT); only the driver differs.

Per-instruction cost (S/N = sequential/non-sequential code fetch in the code region, I = internal,
D = data access cost):
  condition false 1S | ALU 1S (+1I reg-shift) (+1S+1N if Rd=PC) | MUL 1S+mI (MLA +1I, long +1I)
  LDR 1S+1D+1I (+1S+1N if PC) | STR 1N+1D | LDM 1S+nD+1I (+1S+1N if PC) | STM 1N+nD
  B/BL/BX 2S+1N | SWP 1S+2D+1I | MRS/MSR 1S
Data access cost per access (1 + waitstates), WAITCNT 0x4014 as the ADS players set it:
IWRAM/IO/OAM 1; PAL/VRAM 1 (16-bit) / 2 (32-bit); EWRAM 3 / 6; ROM WS0 N 4 S 2 (16-bit), N 6 S 4 (32-bit).
"""
import argparse
import csv
import json
import os
import sys

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
        ins = next(self.md.disasm(bytes(self.uc.mem_read(a, 4)), a), None)
        if ins is None:        # not an ARM opcode (data executed as Thumb by unicorn): price as 1S
            x = Info(); x.cls = 'dp'; x.cc = 0; x.region = a >> 24; x.bucket = self.bid.get('other', 0)
            x.pcw = False; x.regshift = False; x.rs = None; x.acc = False; x.nregs = 0
            self.cache[a] = x
            return x
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
        if size == 2:      # Thumb code (player glue in EWRAM): not modelled, counted in 'other'
            self.ins[self.bid.get('other', 0)] += 1
            return
        x = self.info(addr)
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        ok = cond_ok(x.cc, cpsr)
        rsval = uc.reg_read(x.rs) if (x.rs is not None and ok) else 0
        self.pend = (x, ok, rsval)
        self.acc = []


