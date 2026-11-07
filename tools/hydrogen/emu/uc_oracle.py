#!/usr/bin/env python3
"""Unicorn oracle: run the cartridge's own Hydrogen decoder routines (ARM, IWRAM) on chosen inputs.

Seeded from a playback savestate made by mkstate.py (STATE.state + STATE.json with the
StreamBase address). Functions are located in the cartridge's IWRAM image by the first bytes
of the All Grown Up (build 04cbcc07fe42) routines, so the same oracle runs on every build.

  inflate(blob, chunks)        Inflate::Initialize + repeated Inflate::UncompressBlock(n)
  codebook(raw, mode, ncb, gain) StreamBase::UnPredictLuminance/Chrominance + the YCbCr->RGB555
                               converter; returns the 256x16 VRAM codebook halfwords
  blit(idx, mode, vram_cb)     the 4-way VQ blit; returns the 240x160 framebuffer
"""
import os, sys, json, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
from uc_gba import GBA, RETURN_MAGIC
from unicorn.arm_const import UC_ARM_REG_SP

# first 16 bytes of each routine in All Grown Up's IWRAM image (literal-free prologues)
SIGS = {
    'inf_init': 'f8402de90040a0e1000091e50060a0e3',   # Inflate::Initialize(obj, src, dst, circular)
    'inf_block': 'f04f2de90040a0e1382094e5480090e5',  # Inflate::UncompressBlock(obj, n)
    'unpred_l': '38402de90c0090e50150a0e1001090e5', 'unpred_c': '70402de90c0090e5003090e5',
    'convert': 'f04f2de908d04de22c609de50080d3e5',   # converter(ctx, src, dst, dims, ncb)
    'blit': 'f05f2de90c4090e5004094e5844ea0e1',
}
AGU_ADDR = {'inf_init': 0x03000094, 'inf_block': 0x030005c8, 'unpred_l': 0x03001ba8, 'unpred_c': 0x03001cac,
            'convert': 0x03001430, 'blit': 0x03001ff4}
SCRATCH = 0x02020000        # EWRAM scratch: input blob
OUTBUF = 0x02000000         # EWRAM: inflate output (up to 0x20000)
SEGHDR = 0x0203ff00         # fake segment header word for the VQ routines


def iwram_image(rom):
    t = struct.unpack_from('<I', rom, 0x18c)[0] + 0x18c
    for i in range(0, 0x30, 12):
        s, d, n = struct.unpack_from('<3I', rom, t + i)
        if d == 0x03000000: return s - 0x08000000, n


class Oracle:
    def __init__(self, rom_path, state_prefix):
        self.rom = open(rom_path, 'rb').read()
        meta = json.load(open(state_prefix + '.json'))
        self.ctx = meta['ctx']
        self.g = GBA(state_prefix + '.state', rom_path)
        self.base_state = None
        o, n = iwram_image(self.rom)
        img = self.rom[o:o + n]
        agu = None
        self.addr = {}
        # locate each routine by AGU's bytes (exact prologue match); fall back to the AGU-relative shift
        ref_rom = os.environ.get('HY_AGU_ROM')
        for k, a in AGU_ADDR.items():
            sig = SIGS.get(k)
            if sig:
                p = img.find(bytes.fromhex(sig))
                assert p >= 0, k
                self.addr[k] = 0x03000000 + p
        # the player's assert routine (ROM, Thumb), reached through the IWRAM veneer at AGU 0x03002a64
        shift = self.addr['blit'] - AGU_ADDR['blit']
        self.assert_pc = struct.unpack_from('<I', img, 0x2a6c + shift)[0] & ~1
        self.asserted = []
        from unicorn import UC_HOOK_CODE
        def _hk(uc, addr, size, _):
            self.asserted.append(addr); uc.emu_stop()
        self.g.uc.hook_add(UC_HOOK_CODE, _hk, begin=self.assert_pc, end=self.assert_pc + 1)
        self.sp = (self.g.uc.reg_read(UC_ARM_REG_SP) - 0x200) & ~7   # below the live stack (StreamBase lives on it)
        self.inf_obj = self.ctx + 0x1034
        self.snapshot = self._snap()

    def _snap(self):
        uc = self.g.uc
        return {a: bytes(uc.mem_read(a, n)) for a, n in [(0x02000000, 0x40000), (0x03000000, 0x8000), (0x06000000, 0x18000)]}

    def reset(self):
        from unicorn.arm_const import UC_ARM_REG_CPSR
        for a, b in self.snapshot.items(): self.g.uc.mem_write(a, b)
        self.g.uc.reg_write(UC_ARM_REG_CPSR, (self.g.saved_cpsr & ~0x3F) | 0x1F | 0xC0)

    def call(self, name, args, limit=200_000_000):
        return self.g.call(self.addr[name], args, sp=self.sp, max_insns=limit)

    # ---- inflate
    def inflate(self, blob, chunks=None, circular=0):
        """blob = u32 size + bitstream. chunks: list of request sizes (default: one request)."""
        uc = self.g.uc
        uc.mem_write(SCRATCH, bytes(blob) + b'\0' * 64)
        size = struct.unpack_from('<I', blob, 0)[0]
        assert size <= 0x20000
        obj = self.inf_obj
        self.call('inf_init', (obj, SCRATCH, OUTBUF, circular))
        if not chunks: chunks = [size]
        self.asserted = []
        self.offs = []
        for n in chunks:
            r = self.call('inf_block', (obj, n))
            self.offs.append(r - OUTBUF)
            if self.asserted: return None, size     # the cartridge rejected the stream (assert)
        out_ptr = struct.unpack('<I', uc.mem_read(obj + 8, 4))[0]
        return bytes(uc.mem_read(OUTBUF, out_ptr - OUTBUF)), size

    def set_brightness(self, gain):
        """rebuild the clamp table with the cart's own Thumb routine (StreamBase ctor helper):
        `push {r3-r7,lr}; ldr r2,[pc]; movs r6,#1; ldr r2,[r2]; lsls r6,r6,#9; adds r4,r1,r2`"""
        import re
        m = re.search(rb'\xf8\xb5.\x4a\x01\x26\x12\x68\x76\x02\x8c\x18', self.rom[:0x40000], re.S)
        assert m, 'clamp builder not found'
        self.g.call(0x08000000 + m.start(), (self.ctx, gain), sp=self.sp, thumb=True, max_insns=10_000_000)

    # ---- codebook
    def codebook(self, raw, mode, ncb=256, entries_out=0x06012c00):
        uc = self.g.uc
        hdr = (1 << 16) | (ncb << 6) | (6 << 3) | mode
        uc.mem_write(SEGHDR, struct.pack('<I', hdr))
        uc.mem_write(self.ctx + 0xc, struct.pack('<I', SEGHDR))
        buf = 0x02030000
        uc.mem_write(buf, bytes(raw) + b'\0' * 16)
        self.call('unpred_l', (self.ctx, buf))
        self.call('unpred_c', (self.ctx, buf))
        dims = 0x0203ff10
        bw, bh = [(4, 4), (4, 4), (4, 3), (4, 3), (3, 3), (3, 3), (4, 2), (4, 2)][mode]
        uc.mem_write(dims, bytes([bw, bh]))
        # converter(ctx, src, dst, dims, ncb) -- 5th arg on the stack
        uc.mem_write(self.sp, struct.pack('<I', ncb))
        self.call('convert', (self.ctx, buf, entries_out, dims))
        return np.frombuffer(bytes(uc.mem_read(entries_out, 0x2000)), np.uint16).reshape(256, 16).copy()

    def blit(self, idx, mode, vcb):
        uc = self.g.uc
        hdr = (1 << 16) | (256 << 6) | (6 << 3) | mode
        uc.mem_write(SEGHDR, struct.pack('<I', hdr))
        uc.mem_write(self.ctx + 0xc, struct.pack('<I', SEGHDR))
        ib = 0x02031000
        uc.mem_write(ib, bytes(idx))
        uc.mem_write(self.ctx + 0x1030, struct.pack('<I', ib))
        uc.mem_write(0x06012c00, np.asarray(vcb, np.uint16).tobytes())
        self.call('blit', (self.ctx,))
        return np.frombuffer(bytes(uc.mem_read(0x06000000, 240 * 160 * 2)), np.uint16).copy()
