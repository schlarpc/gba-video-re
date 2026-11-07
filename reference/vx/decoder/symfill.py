#!/usr/bin/env python3
"""Symbolic executor for the player's straight-line "bilinear" intra fill routines
(mode 4 at every block size). Produces the exact ordered list of byte stores:
    (plane, offset, expr)   expr trees over ('M',plane,off) memory reads, ('SE',k) se(v) reads,
    ('+',a,b) ('>>',a,n) ('<<',a,n) ('c',k)
Used to (1) document the fill and (2) check the generic algorithm in vxdec.c (gen_ops()).
"""
import struct
import sys
import os
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
from capstone.arm import ARM_OP_REG, ARM_OP_IMM, ARM_OP_MEM, ARM_SFT_LSL, ARM_SFT_ASR, ARM_SFT_LSR

HERE = os.path.dirname(os.path.abspath(__file__))
ROM = open(os.path.join(HERE, "..", "..", "..", "shrek.gba"), "rb").read()
IW = ROM[0xbcc8:0xbcc8 + 0x7cf4]
md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
md.detail = True
SE_FN = 0x0300081c


def insn_at(a):
    return next(md.disasm(IW[a - 0x03000000:a - 0x03000000 + 4], a))


class Sym:
    def __init__(self):
        self.regs = {}
        self.stores = []
        self.mem = {}
        self.nse = 0
        self.plane = None

    def reg(self, name):
        return self.regs.get(name, ('R', name))

    def run(self, addr, depth=0):
        a = addr
        while True:
            i = insn_at(a)
            m, ops = i.mnemonic, i.operands
            nm = lambda k: i.reg_name(ops[k].reg)
            if m in ("stmdb", "push") or (m == "ldm" and "pc" not in i.op_str) and False:
                pass
            if m in ("stmdb", "push"):
                a += 4; continue
            if (m in ("ldm", "pop") and "pc" in i.op_str) or (m == "mov" and i.op_str == "pc, lr"):
                return
            if m in ("ldm", "pop"):
                a += 4; continue
            if m == "bl":
                t = ops[0].imm
                if t == SE_FN:
                    self.regs['r6'] = ('SE', self.nse); self.nse += 1
                else:
                    self.run(t, depth + 1)
                a += 4; continue
            if m == "ldr" and ops[1].type == ARM_OP_MEM and i.reg_name(ops[1].mem.base) == "r0":
                d = ops[1].mem.disp
                self.regs[nm(0)] = ('CTX', d); a += 4; continue
            if m == "ldrb":
                base = i.reg_name(ops[1].mem.base)
                assert base == "r4", i
                off = self.r4 + ops[1].mem.disp
                key = (self.plane, off)
                self.regs[nm(0)] = self.mem.get(key, ('M', self.plane, off))
                a += 4; continue
            if m == "strb":
                base = i.reg_name(ops[1].mem.base)
                assert base == "r4", i
                off = self.r4 + ops[1].mem.disp
                v = self.reg(nm(0))
                self.stores.append((self.plane, off, v))
                self.mem[(self.plane, off)] = ('S', len(self.stores) - 1)
                a += 4; continue
            if m in ("add", "sub") and nm(0) == "r4":
                if ops[2].type == ARM_OP_IMM:
                    self.r4 += ops[2].imm if m == "add" else -ops[2].imm
                elif m == "add":   # add r4, r4, sb  (block offset) after ldr r4,[r0,-8]
                    c1, c2 = self.reg(nm(1)), self.reg(i.reg_name(ops[2].reg))
                    assert c1[0] == 'CTX' and c2[0] == 'CTX'
                    self.plane = 'Y' if c1[1] == -8 else 'C'
                    self.r4 = 0
                a += 4; continue
            if m == "ldr" and nm(0) == "r4":
                a += 4; self.regs['r4'] = ('CTX', ops[1].mem.disp); continue
            if m == "add":
                x = self.reg(nm(1))
                if ops[2].type == ARM_OP_IMM:
                    y = ('c', ops[2].imm)
                else:
                    y = self.reg(i.reg_name(ops[2].reg))
                    if ops[2].shift.type == ARM_SFT_LSL:
                        y = ('<<', y, ops[2].shift.value)
                self.regs[nm(0)] = ('+', x, y); a += 4; continue
            if m in ("asr", "lsr"):
                n = ops[2].imm if len(ops) > 2 else ops[1].shift.value
                self.regs[nm(0)] = ('>>', self.reg(nm(1)), n); a += 4; continue
            if m == "mov" and ops[1].type == ARM_OP_REG:
                self.regs[nm(0)] = self.reg(i.reg_name(ops[1].reg)); a += 4; continue
            raise RuntimeError(f"unhandled {i.address:x} {m} {i.op_str}")


def trace(entry):
    s = Sym()
    s.r4 = 0
    s.run(entry)
    return s.stores


def fmt(e, S=256):
    t = e[0]
    if t == 'M':
        return f"{e[1]}[{e[2]:+d}]"
    if t == 'S':
        return f"s{e[1]}"
    if t == 'SE':
        return f"se{e[1]}"
    if t == 'c':
        return str(e[1])
    if t == '+':
        return f"({fmt(e[1])}+{fmt(e[2])})"
    if t in ('>>', '<<'):
        return f"({fmt(e[1])}{t}{e[2]})"
    return str(e)


if __name__ == "__main__":
    for a in sys.argv[1:]:
        for k, (p, off, v) in enumerate(trace(int(a, 16))):
            print(k, p, off, fmt(v))
