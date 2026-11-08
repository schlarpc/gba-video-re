#!/usr/bin/env python3
"""Boot an image from 0x200 in the emulator with logging MMIO stubs; report how far it gets.
Usage: boot.py IMG [maxsteps]"""
import sys, collections
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu import *
n = sys.argv[1] if len(sys.argv) > 1 else 'py_fwldr'
maxs = int(float(sys.argv[2])) if len(sys.argv) > 2 else 2_000_000
m = Machine(n)
c = m.cpu; c.r = [0] * 32; c.r[31] = (m.base + 0x200) if m.base else 0x200
mm = collections.Counter(); writes = []
def rd(a, nb):
    mm[('r', a)] += 1
    return 0
def wr(a, nb, v):
    mm[('w', a)] += 1
    if len(writes) < 60: writes.append((hex(c.r[31]), hex(a), hex(v)))
    return True
m.m.mmio_read = rd; m.m.mmio_write = wr
mbox = []
ow = m.m.write
def w2(a, nb, v):
    if 0xff000 <= a < 0xff040: mbox.append((hex(c.r[31]), hex(a), hex(v & M32)))
    ow(a, nb, v)
m.m.write = w2
steps = 0; why = 'step limit'
pcs = collections.Counter()
try:
    while steps < maxs:
        pc = c.r[31]
        i = c.fetch(pc)
        if i.op in ('sleep', 'bkpt'):
            why = 'sleep at %#x' % pc; break
        if i.op == 'rti':
            print('rti at %#x -> pc %#x sr %#x' % (pc, m.m.read(c.r[25] + 4, 4), m.m.read(c.r[25], 4)))
        if i.op == 'unk' and i.words[0] in (0x4330, 0x5430):   # treat unknown misc as nop
            c.r[31] = pc + 2; steps += 1; continue
        c.step(); steps += 1; pcs[pc >> 8] += 1
except Exception as e:
    why = 'exception: %s' % e
print('stopped after %d steps: %s' % (steps, why))
print('mailbox writes (0xff000..):', mbox[:20])
print('first MMIO writes:', writes[:25])
print('MMIO hot spots:', [(k[0], hex(k[1]), v) for k, v in mm.most_common(12)])
print('hot code pages:', [(hex(k << 8), v) for k, v in pcs.most_common(8)])
