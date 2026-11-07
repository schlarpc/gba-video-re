#!/usr/bin/env python3
"""Cross-check of the mGBA decode-cycle profile with an independent cost model.

Runs the cartridge's video decoder (0x03005fe8) in the unicorn oracle and prices every
executed instruction and data access from GBATEK's ARM7TDMI timing (uc_cost_model.py, a copy
of the VX suite's model; WAITCNT 0x4014, EWRAM 2 waitstates). The decode path depends only on
the bitstream and the source alignment, not on pixel values, so frames can be costed in any
order. Compares with perf/out/pVeE.npz dec_cyc.

usage: uc_cost_fvmv.py VOL EP [--n 120]"""
import argparse, os, struct, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'emu'))
sys.path.insert(0, HERE)
from uc_fvmv import Oracle, ROMS  # noqa: E402
from uc_cost_model import CostModel  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('vol', type=int)
ap.add_argument('ep', type=int)
ap.add_argument('--n', type=int, default=120)
a = ap.parse_args()
z = np.load(os.path.join(HERE, '..', '..', '..', 'tmp', 'fvmv', 'perf', f'p{a.vol}e{a.ep}.npz'))
dec = z['dec_cyc']
rom = open(ROMS % a.vol, 'rb').read()
o = -4
for _ in range(a.ep):
    o = rom.find(b'FVMV', o + 4)
nf = struct.unpack_from('<I', rom, o + 4)[0]
ptrs = []
p = o + 0x20
for i in range(nf):
    ptrs.append(0x08000000 + p + 0x14)
    p += 16 + struct.unpack_from('<I', rom, p)[0]
rng = np.random.default_rng(1)
heavy = list(np.argsort(dec[1:])[-20:] + 1)
sample = sorted(set(list(range(1, 41)) + list(rng.choice(np.arange(1, nf - 1), 60, replace=False)) + heavy))[:a.n]
orc = Oracle(a.vol, os.path.join(HERE, '..', '..', '..', 'tmp', 'fvmv', 'cap3', f'p{a.vol}e{a.ep}', 'pre_decode0.state'))
names = ['video_decode', 'other']
cm = CostModel(orc, names, [(0x03001ee0, 0x03005be8, 'video_decode'), (0x03005fe8, 0x03006068, 'video_decode')])
rows = []
for f in sample:
    cm.reset()
    out = 0x02000008 + (f & 1) * 0x12c00
    orc.video(ptrs[f], 0x02000008 + ((f & 1) ^ 1) * 0x12c00, out)
    cm._finish()
    uc = cm.cyc[0] + cm.cyc[1]
    rows.append((f, int(dec[f]), uc, cm.ins[0] + cm.ins[1]))
r = np.array(rows, dtype=float)
d = (r[:, 1] - r[:, 2]) / r[:, 2] * 100
print(f'frames {len(rows)}; mGBA - model: mean {d.mean():+.2f}% min {d.min():+.2f}% max {d.max():+.2f}%; '
      f'mean decode {r[:,1].mean():.0f} (mGBA) vs {r[:,2].mean():.0f} (model) cycles')
with open(os.path.join(HERE, '..', '..', '..', 'tmp', 'fvmv', 'perf', f'crossval_p{a.vol}e{a.ep}.csv'), 'w') as fo:
    fo.write('frame,mgba_cycles,model_cycles,model_instructions\n')
    for row in rows:
        fo.write(','.join(map(str, row)) + '\n')
