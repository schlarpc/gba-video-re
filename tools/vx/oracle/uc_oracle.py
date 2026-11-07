#!/usr/bin/env python3
"""Standalone Unicorn oracle for the VX++ video decoder (no savestate needed).

Loads the player's EWRAM/IWRAM images straight from the ROM, builds the player state
struct (VRAM 0x06013E80) the way player_run (0x03006df4) / seek (0x02000584) do, and
calls the real per-frame wrapper 0x03006d30. The Matrix mapper is serviced by a write hook.

  uc_oracle.py [--rom ROM] [--stream OFF|IDX] --start-frame N --count M --out DIR
                                                       (N must be 0 or a seek-table frame)
  as a module: o = Oracle(rom, stream); o.reset(bit=None|seekbit); o.decode() -> 0xF000 bytes

Per-MB trace: o.mb_trace = [] collects (mb_index, bitpos) at every 16x16 MB start.
"""
import argparse
import os
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
from unicorn.arm_const import *

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..", "..")
sys.path.insert(0, os.path.join(ROOT, "reference", "vx", "container"))
import vxcart  # noqa: E402
ROM_DEFAULT = os.path.join(ROOT, "shrek.gba")
ST = 0x06013E80
SLOT0 = 0x020032C8
SENTINEL = 0x00003FF0
MVARR = 0x0203F2C8


class Oracle:
    def __init__(self, rom=ROM_DEFAULT, stream=None):
        self.rom = open(rom, "rb").read()
        self.vx = vxcart.pick_stream(self.rom, stream)
        L = vxcart.player_layout(self.rom)
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for a, s in ((0, 0x4000), (0x02000000, 0x40000), (0x03000000, 0x8000), (0x04000000, 0x1000),
                     (0x06000000, 0x18000), (0x08000000, 0x2000), (0x08800000, 0x1000)):
            uc.mem_map(a, s)
        (eo, en), (io, isz) = L["ewram"], L["iwram"]
        uc.mem_write(0x02000000, self.rom[eo:eo + en])
        uc.mem_write(0x03000000, self.rom[io:io + isz])
        uc.mem_write(SENTINEL, struct.pack("<I", 0xE1A00000))
        uc.hook_add(UC_HOOK_MEM_WRITE, self._mapper, begin=0x08800180, end=0x088001FF)
        self.hdr = struct.unpack_from("<14I", self.rom, self.vx)
        self.mb_trace = None
        self.pages = [0] * 4
        self.frame = 0
        self._mbhook = uc.hook_add(UC_HOOK_CODE, self._mb, begin=0x0300061c, end=0x0300061c)

    def _mb(self, uc, addr, size, _):
        if self.mb_trace is None:
            return
        r1 = uc.reg_read(UC_ARM_REG_R1)
        r2 = uc.reg_read(UC_ARM_REG_R2)
        r2 = r2 - (1 << 32) if r2 & 0x80000000 else r2
        self.mb_trace.append(self.bitpos(r1, r2))

    def w32(self, a, v):
        self.uc.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))

    def r32(self, a):
        return struct.unpack("<I", self.uc.mem_read(a, 4))[0]

    def _map(self, src, win, nblk):
        off = src & 0x03FFFFFF
        self.uc.mem_write(0x08000000 + (win & 0x1FFF), self.rom[off:off + nblk * 512])
        for k in range(nblk):
            p = ((win & 0x1FFF) - 0x1000 >> 9) + k
            if 0 <= p < 4:
                self.pages[p] = off + k * 512

    def _mapper(self, uc, access, addr, size, value, _):
        if addr == 0x08800180 and value in (1, 0x11):
            src = self.r32(0x08800184)
            win = struct.unpack("<H", uc.mem_read(0x08800188, 2))[0]
            n = struct.unpack("<H", uc.mem_read(0x0880018c, 2))[0]
            self._map(src, win, n)

    def reset(self, seekbit=None):
        """Fresh player state; seekbit None = power-on start, else seek-table video_bit."""
        uc = self.uc
        for i in range(4):
            y = SLOT0 + i * 0xF000
            self.w32(ST + 4 * i, y)
            self.w32(ST + 0x10 + 4 * i, y + 0xA000)
            uc.mem_write(y, b"\0" * 0xA000 + b"\x80" * 0x5000)
        uc.mem_write(MVARR, b"\0" * 0x318)
        self.w32(ST + 0x24, 2)
        self.w32(ST + 0x28, 3)
        self.w32(ST + 0x30, 0)
        self.w32(ST + 0xf4, 0x0203F314)
        self.w32(ST + 0x10c, 0x020010e4)
        self.w32(ST + 0x104, self.hdr[2])
        self.w32(ST + 0x108, self.hdr[3])
        base = 0x08000000 + self.vx
        self.w32(ST + 0x20, base)
        if seekbit is None:
            self._map(base, 0x1000, 4)
            self.w32(ST + 0xbc, base + 0x800)
            self.w32(ST + 0xc0, 0x0800103a)
            self.w32(ST + 0xc4, 0)
            hw = struct.unpack_from("<H", self.rom, self.vx + 0x38)[0]
            self.w32(ST + 0xc8, hw << 16)
        else:
            b = seekbit + 0x1c0
            sec = b >> 12
            rem = b - sec * 0x1000
            i = (rem >> 4) * 2
            sk = rem & 15
            self._map(base + sec * 0x200, 0x1000, 4)
            h0, h1 = struct.unpack("<HH", uc.mem_read(0x08001000 + i, 4))
            self.w32(ST + 0xbc, base + sec * 0x200 + 0x800)
            self.w32(ST + 0xc0, 0x08001004 + i)
            self.w32(ST + 0xc8, ((h0 << 16) | h1) << sk)
            self.w32(ST + 0xc4, 16 - sk)

    def bitpos(self, r1=None, cnt=None):
        """Bit position relative to VX header+0x38 for reader state (r1, r2)."""
        if r1 is None:
            r1 = self.r32(ST + 0xc0)
            cnt = struct.unpack("<i", struct.pack("<I", self.r32(ST + 0xc4)))[0]
        h = (r1 - 0x08001000) >> 9          # 512-byte page of the window
        fo = self.pages[h] + (r1 & 0x1FF)
        return (fo - (self.vx + 0x38)) * 8 - (cnt + 16)

    def decode(self):
        uc = self.uc
        uc.reg_write(UC_ARM_REG_CPSR, 0x1F | 0xC0)
        uc.reg_write(UC_ARM_REG_R0, ST)
        uc.reg_write(UC_ARM_REG_SP, 0x03007E00)
        uc.reg_write(UC_ARM_REG_LR, SENTINEL)
        n = self.r32(ST + 0x28)
        uc.emu_start(0x03006d30, SENTINEL, count=200_000_000)
        y = self.r32(ST + 4 * (n & 3))
        self.frame += 1
        return bytes(uc.mem_read(y, 0xF000))

    def seek_table(self):
        h = self.hdr
        off = self.vx + h[10]
        out = []
        for i in range(h[11]):
            out.append(struct.unpack_from("<4I", self.rom, off + 16 * i))
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=ROM_DEFAULT)
    ap.add_argument("--stream", type=lambda x: int(x, 0))
    ap.add_argument("--start-frame", type=int, default=0)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    o = Oracle(a.rom, a.stream)
    if a.start_frame == 0:
        o.reset(None)
    else:
        ent = [e for e in o.seek_table() if e[0] == a.start_frame]
        if not ent:
            sys.exit("start frame is not a seek point")
        o.reset(ent[0][1])
    os.makedirs(a.out, exist_ok=True)
    for k in range(a.count):
        f = a.start_frame + k
        open(os.path.join(a.out, f"{f:05d}.yuv"), "wb").write(o.decode())
        print(f"frame {f}", file=sys.stderr)


if __name__ == "__main__":
    main()
