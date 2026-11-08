#!/usr/bin/env python3
"""Test harness helpers: load a VC01 image, call a function with args, return r0/r1."""
import struct, sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vc01 import *
from common import load, BASE

RET = 0xFFFFFFF0
STACK = 0x00F00000


class Machine:
    def __init__(self, img, base=None, gp=None):
        self.data = load(img) if isinstance(img, str) else img
        self.base = BASE.get(img, 0) if base is None else base
        self.cpu = CPU()
        self.m = self.cpu.m
        self.m.load(self.base, self.data)
        if self.base:   # loader: bytes 0..0x1ff go to address 0 as well
            self.m.load(0, self.data[:0x200])
        if gp is None:
            gp = struct.unpack_from('<I', self.data, 0x202)[0]
        self.gp = gp
        self.mmio_log = []
        self.m.mmio_read = lambda a, n: (self.mmio_log.append(('r', a, n)) or 0)
        self.m.mmio_write = lambda a, n, v: (self.mmio_log.append(('w', a, n, v)) or True)
        self.heap = 0x00E00000

    def alloc(self, data):
        a = self.heap
        self.m.load(a, bytes(data))
        self.heap += (len(data) + 15) & ~15
        return a

    def call(self, fn, *args, maxsteps=5_000_000, trace=False):
        c = self.cpu
        c.r = [0] * 32
        for k, v in enumerate(args):
            c.r[k] = v & M32
        c.r[24] = self.gp; c.r[25] = STACK; c.r[26] = RET; c.r[31] = fn
        if trace:
            c.trace = lambda cpu, i: print('  ', i, '| r0-7', ' '.join('%x' % x for x in cpu.r[:8]))
        else:
            c.trace = None
        n = 0
        while c.r[31] != RET:
            c.step(); n += 1
            if n > maxsteps:
                raise EmuError('step limit at %#x' % c.r[31])
        self.steps = n
        return c.r[0], c.r[1]
