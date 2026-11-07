"""Unicorn oracle for the FVMV player: runs the cart's own IWRAM decoder routines
(video 0x03005fe8, audio 0x03001b88) on a CPU+memory sandbox seeded from an mGBA savestate."""
import os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from uc_gba import GBA, RETURN_MAGIC
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import *

ROMS = os.path.join(os.environ.get('GBAV_ROMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'roms'), 'Game Boy Advance Video - Pokemon - Volume %d (USA).gba')
VDEC = 0x03005fe8
ADEC = 0x03001b88
PARAM = 0x03007e40
SP = 0x03007e00
BLOCK_FUNCS = {0x030024d0: (8, 8), 0x03002f80: (8, 4), 0x03003610: (4, 8), 0x03003d84: (8, 2),
               0x03004a78: (4, 4), 0x030041f8: (2, 8), 0x03004704: (8, 1), 0x03004f14: (4, 2),
               0x03005230: (2, 4), 0x03004880: (1, 8), 0x0300555c: (4, 1), 0x03005688: (1, 4),
               0x030057d0: (2, 2), 0x03005a0c: (2, 1), 0x03005b00: (1, 2)}


class Oracle:
    def __init__(self, vol, state):
        self.g = GBA(state, rom_path=ROMS % vol)
        self.uc = self.g.uc
        self.trace = None

    def _hook(self, uc, addr, size, _):
        if addr in BLOCK_FUNCS:
            r = [uc.reg_read(x) for x in (UC_ARM_REG_R2, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R5, UC_ARM_REG_R6)]
            self.trace.append((BLOCK_FUNCS[addr], r[0], r[1], r[2], r[3], r[4]))

    def video(self, frame_addr, ref, out, trace=False):
        uc = self.uc
        uc.mem_write(PARAM, struct.pack('<5I', ref, out, frame_addr, 240, 160))
        h = None
        if trace:
            self.trace = []
            h = uc.hook_add(UC_HOOK_CODE, self._hook, begin=0x03001ee0, end=0x03005be8)
        self.g.call(VDEC, (PARAM,), sp=SP)
        if h is not None:
            uc.hook_del(h)
        return bytes(uc.mem_read(out, 240 * 160 * 2))

    def audio(self, src, dst):
        self.g.call(ADEC, (src, dst), sp=SP)
        return bytes(self.uc.mem_read(dst, 0x400))

    def ewram(self):
        return bytes(self.uc.mem_read(0x02000000, 0x40000))

    def set_ewram(self, b):
        self.uc.mem_write(0x02000000, b)
