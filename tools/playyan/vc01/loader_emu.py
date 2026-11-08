#!/usr/bin/env python3
"""Dynamic check of the py_fwldr firmware-update path (FIRMWARE_LIST / FIRMWARE_DOWNLOAD).

Runs the real loader code in emu.Machine with a fake Nucleus FILE layer that serves
playan.fup ($PY_ROMS):
  0xc7b9a fopen(name, mode)  0xc7ba8 fread(buf, size, n, f)  0xc8104 fseek(f, off, whence)
  0xc7994 fclose(f)
Two encodings that vc01.py does not decode yet are patched in here (vc01.py is untouched):
  * 010o oooo iii0 dddd   lea rd, (pc & ~3) + 4*(o:iii)      [C here: playan.fup / "rb" / header string]
  * 2f, op 4..7 (48-bit)  ldb rd, (rb + off27 + (op-4))      [P: only the load form, even offset]
Usage: python3 loader_emu.py [fupfile]
"""
import struct, sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vc01
from vc01 import Insn, sx
from emu import Machine, RET

from common import workdir
FUP = sys.argv[1] if len(sys.argv) > 1 and __name__ == '__main__' else os.path.join(workdir.roms_dir(), workdir.ROM_NAMES['fup'])

_orig_decode = vc01.decode


def decode(rd16, addr):
    h = rd16(addr)
    hb = h >> 8
    if 0x40 <= hb <= 0x5f and not (h >> 4) & 1:          # x nibble even: pc-relative lea
        o = hb & 0x1f; x = (h >> 4) & 15
        tgt = (addr & ~3) + 4 * ((o << 3) | (x >> 1))
        return Insn(addr, 2, [h], 'movi', rd=h & 15, imm=tgt)
    if hb == 0x2f and (h >> 5) & 7 >= 4:
        op = (h >> 5) & 7; w1 = rd16(addr + 2); w2 = rd16(addr + 4)
        rb = w2 >> 11; o = sx(((w2 & 0x7ff) << 16) | w1, 27)
        if not o & 1:
            return Insn(addr, 6, [h, w1, w2], 'mem', w='ldb', rd=h & 31, base=rb, idx=None,
                        off=o + (op - 4), mode='')
    return _orig_decode(rd16, addr)


vc01.decode = decode


class FupMachine(Machine):
    FOPEN, FREAD, FSEEK, FCLOSE = 0xc7b9a, 0xc7ba8, 0xc8104, 0xc7994

    def __init__(self, fup):
        super().__init__('py_fwldr')
        self.fup = fup
        self.pos = None
        self.log = []

    def cstr(self, a):
        s = b''
        while True:
            c = self.m.r8(a)
            if not c:
                return s.decode('latin1')
            s += bytes([c]); a += 1

    def run(self, fn, *args, stop_at=None, maxsteps=50_000_000):
        c = self.cpu
        c.r = [0] * 32
        for k, v in enumerate(args):
            c.r[k] = v & 0xffffffff
        c.r[24] = self.gp; c.r[25] = 0x00F00000; c.r[26] = RET; c.r[31] = fn
        c.trace = None
        n = 0
        while c.r[31] != RET:
            pc = c.r[31]
            if stop_at is not None and pc == stop_at:
                return 'stopped', c.r[0]
            r = c.r
            if pc in (self.FOPEN, self.FREAD, self.FSEEK, self.FCLOSE):
                if pc == self.FOPEN:
                    name, mode = self.cstr(r[0]), self.cstr(r[1])
                    self.log.append(('fopen', name, mode))
                    self.pos = 0
                    r[0] = 0x1234 if name.lower() == 'playan.fup' else 0
                elif pc == self.FREAD:
                    buf, sz, cnt = r[0], r[1], r[2]
                    want = sz * cnt
                    data = self.fup[self.pos:self.pos + want]
                    self.m.load(buf, data)
                    self.log.append(('fread', hex(self.pos), hex(want), hex(len(data))))
                    self.pos += len(data)
                    r[0] = len(data) // sz if sz else 0
                elif pc == self.FSEEK:
                    self.log.append(('fseek', hex(r[1]), r[2]))
                    self.pos = r[1]; r[0] = 0
                else:
                    self.log.append(('fclose',)); r[0] = 0
                c.r[31] = r[26]
                continue
            c.step(); n += 1
            if n > maxsteps:
                raise vc01.EmuError('step limit at %#x' % c.r[31])
        return 'returned', c.r[0]


def main():
    fup = open(FUP, 'rb').read()
    ext = workdir.FIRMWARE + '/'

    # FIRMWARE_LIST worker 0xc84f8(&mask) -> status, *mask
    m = FupMachine(fup)
    out = 0x00E00000
    m.m.load(out, b'\xee' * 4)
    how, st = m.run(0xc84f8, out)
    print('LIST 0xc84f8: %s status=%d mask=%#x' % (how, st, m.m.read(out, 4)))
    for e in m.log:
        print('   ', e)

    # FIRMWARE_DOWNLOAD worker 0xc6d0c(mask): stop when it jumps to 0x200
    for mask in (0, 1, 2, 3, 4):
        m = FupMachine(fup)
        m.m.load(0, b'\0' * 0x80000)
        how, st = m.run(0xc6d0c, mask, stop_at=0x200)
        line = 'DOWNLOAD 0xc6d0c(mask=%d): %s r0=%#x' % (mask, how, st)
        if how == 'stopped':
            img = bytes(m.m.read(a, 1) for a in range(0, 0x50000))
            for k in (0, 1):
                sec = open(ext + 'fup_sec%d.bin' % k, 'rb').read()
                if img[:len(sec)] == sec:
                    line += '  RAM[0:%#x] == fup_sec%d.bin' % (len(sec), k)
        print(line)
        print('    io:', ' '.join(str(e[0]) + (('(' + ','.join(map(str, e[1:])) + ')') if len(e) > 1 else '')
                                  for e in m.log[:6]), '...' if len(m.log) > 6 else '')

    # corrupted inputs
    def corrupt(off, val):
        b = bytearray(fup); b[off] ^= val; return bytes(b)
    for desc, data in [('header text byte', corrupt(0x10, 1)), ('header check byte', corrupt(0x3f, 1)),
                       ('entry0 check byte', corrupt(0x4f, 1)), ('entry0 platform=2', None),
                       ('block in sec0', corrupt(0x60 + 0x201 * 5 + 7, 1))]:
        if data is None:
            b = bytearray(fup); b[0x48] = 2; b[0x4f] = (b[0x4f] - 1) & 0xff; data = bytes(b)
        m = FupMachine(data)
        m.m.load(0, b'\0' * 0x1000)
        how, st = m.run(0xc6d0c, 1, stop_at=0x200)
        m2 = FupMachine(data); m2.m.load(out, b'\0' * 4)
        _, lst = m2.run(0xc84f8, out)
        print('corrupt %-18s: DOWNLOAD(1) %s r0=%#x ; LIST status=%d mask=%#x' % (
            desc, how, st, lst, m2.m.read(out, 4)))


if __name__ == '__main__':
    main()
