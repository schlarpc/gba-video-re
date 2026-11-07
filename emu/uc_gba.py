"""Unicorn-based GBA CPU sandbox seeded from an mGBA savestate.

Only the CPU + memory are emulated: no PPU, no timers, no IRQs. Enough to run
pure-CPU routines (the VX decoder) exactly as the real player does, with
hooks for tracing. Handles:
  * memory map: BIOS (zeros), EWRAM, IWRAM, IO, palette, VRAM, OAM, ROM
  * GBA Video "Matrix" cart mapper (writes to 0x08800180..0x0880018C copy
    512-byte pages of the 64 MB ROM file into the 0x08000000 window)
  * immediate DMA0-3 (when control written with enable + start-timing 0)
  * a few BIOS SWIs (Div, DivArm, Sqrt, CpuSet, CpuFastSet)

The mGBA savestate layout (GBASerializedState, see mgba/internal/gba/serialize.h):
  0x020 gprs[16], 0x060 cpsr, 0x328 matrix mappings[16], 0x400 IO, 0x800 palette,
  0xC00 OAM, 0x1000 VRAM(0x18000), 0x19000 IWRAM(0x8000), 0x21000 EWRAM(0x40000)
"""
import os
import struct

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_MEM_WRITE, UC_HOOK_INTR, UC_HOOK_CODE, \
    UC_HOOK_MEM_UNMAPPED, UC_PROT_ALL
from unicorn.arm_const import *

HERE = os.path.dirname(os.path.abspath(__file__))
ROM_DEFAULT = os.path.join(HERE, "..", "shrek.gba")

REGS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
        UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
        UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC]

RETURN_MAGIC = 0x00003FF0  # inside the zeroed BIOS area; used as a sentinel return address


class GBA:
    def __init__(self, state, rom_path=ROM_DEFAULT):
        if isinstance(state, str):
            with open(state, "rb") as f:
                state = f.read()
        self.state = state
        with open(rom_path, "rb") as f:
            self.romfile = f.read()
        try:
            from unicorn import UC_CPU_ARM_926
            self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM, cpu=UC_CPU_ARM_926)
        except Exception:
            self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc = self.uc
        uc.mem_map(0x00000000, 0x4000)
        uc.mem_map(0x02000000, 0x40000)
        uc.mem_map(0x03000000, 0x8000)
        uc.mem_map(0x04000000, 0x1000)
        uc.mem_map(0x05000000, 0x1000)
        uc.mem_map(0x06000000, 0x18000)
        uc.mem_map(0x07000000, 0x1000)
        uc.mem_map(0x08000000, 0x2000000)
        s = state
        uc.mem_write(0x04000000, s[0x400:0x800])
        uc.mem_write(0x05000000, s[0x800:0xC00])
        uc.mem_write(0x07000000, s[0xC00:0x1000])
        uc.mem_write(0x06000000, s[0x1000:0x19000])
        uc.mem_write(0x03000000, s[0x19000:0x21000])
        uc.mem_write(0x02000000, s[0x21000:0x61000])
        uc.mem_write(0x08000000, self.romfile[:0x2000000])
        # Matrix mapper window: 16 x 512-byte pages at 0x08000000..0x08001FFF
        self.matrix = dict(zip(("cmd", "paddr", "vaddr", "size"), struct.unpack_from("<4I", s, 0x2D0)))
        for i, paddr in enumerate(struct.unpack_from("<16I", s, 0x328)):
            uc.mem_write(0x08000000 + (i << 9), self.romfile[paddr:paddr + 0x200])
        g = struct.unpack_from("<16I", s, 0x20)
        cpsr = struct.unpack_from("<I", s, 0x60)[0]
        self.saved_cpsr = cpsr
        # run everything in SYS mode, IRQ/FIQ masked
        uc.reg_write(UC_ARM_REG_CPSR, (cpsr & ~0x3F) | 0x1F | 0xC0)
        for r, v in zip(REGS[:15], g[:15]):
            uc.reg_write(r, v)
        self.pc0 = g[15]
        self.matrix_log = []
        self.swi_log = []
        uc.hook_add(UC_HOOK_MEM_WRITE, self._hook_matrix, begin=0x08800100, end=0x088001FF)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._hook_io, begin=0x04000000, end=0x040003FF)
        uc.hook_add(UC_HOOK_INTR, self._hook_intr)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._hook_unmapped)
        self.u32 = lambda a: struct.unpack("<I", uc.mem_read(a, 4))[0]

    # ---------------- hooks
    def _hook_matrix(self, uc, access, addr, size, value, _):
        off = addr & 0x3C
        m = self.matrix
        if size == 2:  # 16-bit write: merge low half
            key = {0: "cmd", 4: "paddr", 8: "vaddr", 0xC: "size"}.get(off & 0xC)
            value = (value & 0xFFFF) | ({"cmd": m["cmd"], "paddr": m["paddr"], "vaddr": m["vaddr"],
                                         "size": m["size"] >> 9}[key] & 0xFFFF0000)
        if off == 0x0:
            m["cmd"] = value
            if value in (0x01, 0x11):
                vaddr, paddr, size = m["vaddr"], m["paddr"], m["size"]
                data = self.romfile[paddr:paddr + size]
                uc.mem_write(0x08000000 + vaddr, data)
                self.matrix_log.append((paddr, vaddr, size))
        elif off == 0x4:
            m["paddr"] = value & 0x03FFFFFF
        elif off == 0x8:
            m["vaddr"] = value & 0x007FFFFF
        elif off == 0xC and value:
            m["size"] = value << 9
        # the write itself must not modify ROM contents
        return True

    def _hook_io(self, uc, access, addr, size, value, _):
        off = addr - 0x04000000
        # DMA control high halfword at 0xBA + 12*n (or a 32-bit write at 0xB8 + 12*n)
        for n in range(4):
            base = 0xB0 + 12 * n
            if off == base + 10 or (off == base + 8 and size == 4):
                ctl = (value >> 16) if (off == base + 8) else value
                cnt = (value & 0xFFFF) if (off == base + 8) else struct.unpack(
                    "<H", uc.mem_read(0x04000000 + base + 8, 2))[0]
                if ctl & 0x8000 and ((ctl >> 12) & 3) == 0:
                    self._dma(n, base, ctl & 0xFFFF, cnt)

    def _dma(self, n, base, ctl, cnt):
        uc = self.uc
        src, dst = struct.unpack("<II", uc.mem_read(0x04000000 + base, 8))
        word = (ctl >> 10) & 1
        unit = 4 if word else 2
        if cnt == 0:
            cnt = 0x10000 if n == 3 else 0x4000
        dmode, smode = (ctl >> 5) & 3, (ctl >> 7) & 3
        step = {0: unit, 1: -unit, 2: 0, 3: unit}
        src &= ~(unit - 1)
        dst &= ~(unit - 1)
        for _ in range(cnt):
            uc.mem_write(dst, bytes(uc.mem_read(src, unit)))
            src += step[smode]
            dst += step[dmode]
        # clear enable (no repeat for immediate DMA)
        uc.mem_write(0x04000000 + base + 10, struct.pack("<H", ctl & 0x7FFF))

    def _hook_intr(self, uc, intno, _):
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        pc = uc.reg_read(UC_ARM_REG_PC)
        if cpsr & 0x20:
            num = uc.mem_read(pc - 2, 1)[0]
        else:
            num = uc.mem_read(pc - 4 + 2, 1)[0]
        self.swi_log.append((pc, num))
        r = [uc.reg_read(x) for x in REGS[:4]]
        s32 = lambda v: v - (1 << 32) if v & 0x80000000 else v
        if num in (6, 7):
            a, b = (s32(r[0]), s32(r[1])) if num == 6 else (s32(r[1]), s32(r[0]))
            q = int(a / b) if b else 0
            m = a - q * b
            uc.reg_write(UC_ARM_REG_R0, q & 0xFFFFFFFF)
            uc.reg_write(UC_ARM_REG_R1, m & 0xFFFFFFFF)
            uc.reg_write(UC_ARM_REG_R3, abs(q) & 0xFFFFFFFF)
        elif num == 8:
            import math
            uc.reg_write(UC_ARM_REG_R0, math.isqrt(r[0]))
        elif num in (0x0B, 0x0C):
            src, dst, c = r
            n = c & 0x1FFFFF
            fill = c & (1 << 24)
            unit = 4 if (num == 0x0C or c & (1 << 26)) else 2
            if num == 0x0C:
                n = (n + 7) & ~7
            v = bytes(uc.mem_read(src, unit))
            for i in range(n):
                if not fill:
                    v = bytes(uc.mem_read(src + i * unit, unit))
                uc.mem_write(dst + i * unit, v)
        else:
            raise RuntimeError(f"unhandled SWI 0x{num:02x} at {pc:08x}")

    def _hook_unmapped(self, uc, access, addr, size, value, _):
        print(f"unmapped access type={access} addr={addr:08x} size={size} pc={uc.reg_read(UC_ARM_REG_PC):08x}")
        return False

    # ---------------- API
    def read(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def call(self, func, args=(), sp=None, thumb=False, max_insns=0):
        """Call func(args...) and run until it returns to RETURN_MAGIC."""
        uc = self.uc
        for r, v in zip(REGS[:4], args):
            uc.reg_write(r, v)
        if sp is not None:
            uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, RETURN_MAGIC)
        start = func | (1 if thumb else 0)
        uc.emu_start(start, RETURN_MAGIC, count=max_insns)
        return uc.reg_read(UC_ARM_REG_R0)

    def run_until(self, start_pc, stop_pc, thumb=False, max_insns=0):
        self.uc.emu_start(start_pc | (1 if thumb else 0), stop_pc, count=max_insns)
