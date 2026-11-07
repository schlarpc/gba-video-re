#!/usr/bin/env python3
"""Standalone Unicorn oracle for the VXGB video decoder (GBA Video Rev 5 carts: Shark Tale,
Shrek 2). No savestate needed.

Loads the Rev 5 player's EWRAM/IWRAM images from the ROM, builds the player state struct
(VRAM 0x06013E80) the way player_run (EWRAM 0x02000a00) and seek (0x020004c8) do, and runs
the real decode_frame 0x03000510 once per frame, setting the reference/output pointers like
the main loop that player_run inlines. The Matrix mapper is serviced by a write hook.

  uc_oracle_vxgb.py --rom CART.gba --start-frame N --count M --out DIR   (N = 0 or a seek frame)
  as a module: o = OracleGB(rom); o.reset(seekbit=None); o.decode() -> 0xF000 bytes

o.mb_trace = [] collects the bit position at every 16x16 MB start.
"""
import argparse
import os
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP, \
    UC_ARM_REG_LR, UC_ARM_REG_CPSR

ST = 0x06013E80
VXFILE = 0x20200
EW_ROM, EW_LEN = 0x8A00, 0x2B48       # player EWRAM image (ROM offset, size)
IW_ROM, IW_LEN = 0xB548, 0x7CD0       # player IWRAM image
SLOT0 = 0x02002B48                    # frame slot i at SLOT0 + i*0xF000
NZY, NZY_LEN, NZY_START = 0x0203EB48, 0xA69, 0x0203EB8A   # luma nnz grid, stride 0x41
NZC, NZC_LEN, NZC_START = 0x0203F5B1, 0x2B5, 0x0203F5D3   # chroma nnz grid, stride 0x21
MVARR, MV_LEN, MV_START = 0x0203F866, 0x318, 0x0203F8B2   # MV array, stride 0x24
DEQ_TAB = 0x02002A14                  # u8 [6][8] dequant scale table (EWRAM)
DECODE_FRAME = 0x03000510
MB_CALL = 0x030005F8                  # "bl 16x16 dispatch" in the MB loop
SENTINEL = 0x00003FF0


class OracleGB:
    def __init__(self, rom):
        self.rom = open(rom, "rb").read() if isinstance(rom, str) else rom
        if self.rom[VXFILE:VXFILE + 4] != b"VXGB":
            raise ValueError("not a VXGB cartridge")
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for a, s in ((0, 0x4000), (0x02000000, 0x40000), (0x03000000, 0x8000), (0x04000000, 0x1000),
                     (0x06000000, 0x18000), (0x08000000, 0x2000), (0x08800000, 0x1000)):
            uc.mem_map(a, s)
        uc.mem_write(0x02000000, self.rom[EW_ROM:EW_ROM + EW_LEN])
        uc.mem_write(0x03000000, self.rom[IW_ROM:IW_ROM + IW_LEN])
        uc.mem_write(SENTINEL, struct.pack("<I", 0xE1A00000))
        uc.hook_add(UC_HOOK_MEM_WRITE, self._mapper, begin=0x08800180, end=0x088001FF)
        self.hdr = struct.unpack_from("<14I", self.rom, VXFILE)
        self.mb_trace = None
        self.pages = [0] * 4
        self.frame = 0
        uc.hook_add(UC_HOOK_CODE, self._mb, begin=MB_CALL, end=MB_CALL)

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
            n = struct.unpack("<H", uc.mem_read(0x0880018C, 2))[0]
            self._map(src, win, n)

    def set_qp(self, qp):
        """player_run: ctx+0x100 + 4i = DEQ[qp%6][i] << qp/6, i = 0..7."""
        tab = self.rom[EW_ROM + DEQ_TAB - 0x02000000:][:48]
        for i in range(8):
            self.w32(ST + 0x100 + 4 * i, tab[(qp % 6) * 8 + i] << (qp // 6))

    def reset(self, seekbit=None, clear=True):
        """Fresh player state (player_run). seekbit None = start of the stream, else a
        seek-table video_bit. clear=False keeps frame slots / nnz grids / MV array (a seek
        during playback does not reset them)."""
        uc = self.uc
        for i in range(4):
            y = SLOT0 + i * 0xF000
            self.w32(ST + 4 * i, y)
            self.w32(ST + 0x10 + 4 * i, y + 0xA000)
            if clear:
                uc.mem_write(y, b"\0" * 0xA000 + b"\x80" * 0x5000)
        if clear:
            uc.mem_write(NZY, b"\0" * NZY_LEN)
            uc.mem_write(NZC, b"\0" * NZC_LEN)
            uc.mem_write(MVARR, b"\0" * MV_LEN)
            self.w32(ST + 0x24, 2)
            self.w32(ST + 0x28, 3)
            self.w32(ST + 0x30, 0)
        self.w32(ST + 0xF4, NZY_START)
        self.w32(ST + 0xF8, NZC_START)
        self.w32(ST + 0xFC, MV_START)
        self.set_qp(self.hdr[5])
        self.w32(ST + 0x120, self.hdr[2])
        self.w32(ST + 0x124, self.hdr[3])
        base = 0x08000000 + VXFILE
        self.w32(ST + 0x20, base)
        if seekbit is None:
            self._map(base, 0x1000, 4)
            self.w32(ST + 0xBC, base + 0x800)
            self.w32(ST + 0xC0, 0x0800103A)
            self.w32(ST + 0xC4, 0)
            hw = struct.unpack_from("<H", self.rom, VXFILE + 0x38)[0]
            self.w32(ST + 0xC8, hw << 16)
        else:
            b = seekbit + 0x1C0
            sec = b >> 12
            rem = b - sec * 0x1000
            i = (rem >> 4) * 2
            sk = rem & 15
            self._map(base + sec * 0x200, 0x1000, 4)
            h0, h1 = struct.unpack("<HH", uc.mem_read(0x08001000 + i, 4))
            self.w32(ST + 0xBC, base + sec * 0x200 + 0x800)
            self.w32(ST + 0xC0, 0x08001004 + i)
            self.w32(ST + 0xC8, ((h0 << 16) | h1) << sk)
            self.w32(ST + 0xC4, 16 - sk)

    def bitpos(self, r1=None, cnt=None):
        """Bit position relative to VX header+0x38 for reader state (r1, r2)."""
        if r1 is None:
            r1 = self.r32(ST + 0xC0)
            cnt = struct.unpack("<i", struct.pack("<I", self.r32(ST + 0xC4)))[0]
        h = (r1 - 0x08001000) >> 9
        fo = self.pages[h] + (r1 & 0x1FF)
        return (fo - (VXFILE + 0x38)) * 8 - (cnt + 16)

    def setup_frame(self):
        """The main loop's per-frame pointer setup (player_run, inlined decode wrapper)."""
        r = self.r32(ST + 0x24)
        n = self.r32(ST + 0x28)
        Y = [self.r32(ST + 4 * i) for i in range(4)]
        C = [self.r32(ST + 0x10 + 4 * i) for i in range(4)]
        for k, (y, c) in enumerate(((Y[r & 3], C[r & 3]), (Y[(r - 1) & 3], C[(r - 1) & 3]),
                                    (Y[(r - 2) & 3], C[(r - 2) & 3]), (Y[n & 3], C[n & 3]))):
            self.w32(ST + 0xCC + 8 * k, y)
            self.w32(ST + 0xD0 + 8 * k, c)
        self.w32(ST + 0xEC, 0)
        self.w32(ST + 0xF0, 0)
        return Y[n & 3]

    def decode(self):
        uc = self.uc
        y = self.setup_frame()
        uc.reg_write(UC_ARM_REG_CPSR, 0x1F | 0xC0)
        uc.reg_write(UC_ARM_REG_R0, ST + 0xBC)
        uc.reg_write(UC_ARM_REG_SP, 0x03007E00)
        uc.reg_write(UC_ARM_REG_LR, SENTINEL)
        uc.emu_start(DECODE_FRAME, SENTINEL, count=200_000_000)
        for o in (0x24, 0x28, 0x30):
            self.w32(ST + o, self.r32(ST + o) + 1)
        self.frame += 1
        return bytes(uc.mem_read(y, 0xF000))

    def seek_table(self):
        h = self.hdr
        off = VXFILE + h[10]
        return [struct.unpack_from("<4I", self.rom, off + 16 * i) for i in range(h[11])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True)
    ap.add_argument("--start-frame", type=int, default=0)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    o = OracleGB(a.rom)
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
