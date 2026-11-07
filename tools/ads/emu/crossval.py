#!/usr/bin/env python3
"""Cross-check of the instrumented-mGBA cycle counts with an independent GBATEK cost model.

1. mGBA (bucket counters, perf.py's table): SRMTHFG episode 1 from the menu; for every call of the
   player's LZMA decode (0x03000554) on the index-plane object and every VQ blit (0x03001748),
   record cycles and instructions charged to the LZMA/blit buckets between entry and return,
   plus the call's arguments and the decoder's ring position.
2. unicorn (uc_lzma.Oracle + uc_cost.CostModel): replay the same LZMA call sequence on the same
   blobs from the segment start, and run the cart's blit for the same mode, pricing every
   instruction from GBATEK.
Writes work/perf/crossval.csv and prints per-routine differences.
usage: crossval.py NFRAMES
"""
import os, sys, struct, csv
import numpy as np, ctypes as C
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import ademu, carts, adsref, adsfs
import perf, uc_lzma, uc_cost
from builds import discover

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/perf/crossval.csv'
LZ_OBJ = 0x0300672C

def mgba_side(nframes):
    cid = 'srmt'
    e = ademu.Emu(carts.emu_rom_path(cid))
    e.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/menus/srmt_menu.state', 'rb').read())
    e.run(1)
    cyc, ins = perf.setup(e, cid)
    calls = []; cur = {}
    blits = []
    def lz_in(g):
        if (g[0] & 0xffffffff) != LZ_OBJ: return
        cur['lz'] = dict(c0=int(cyc[1]), i0=int(ins[1]), n=g[1] & 0xffffffff, nin=g[2] & 0xffffffff, pos0=e.u32(LZ_OBJ + 0x0C),
                         inp0=e.u32(LZ_OBJ + 0x28), over0=e.u32(LZ_OBJ + 0x1C))
    def lz_out(g):
        if 'lz' not in cur: return
        d = cur.pop('lz')
        d.update(cyc=int(cyc[1]) - d['c0'], ins=int(ins[1]) - d['i0'], pos1=e.u32(LZ_OBJ + 0x0C), inp1=e.u32(LZ_OBJ + 0x28))
        calls.append(d)
    def bl_in(g): cur['bl'] = (int(cyc[3]), int(ins[3]))
    def bl_out(g):
        if 'bl' in cur:
            c0, i0 = cur.pop('bl'); blits.append((int(cyc[3]) - c0, int(ins[3]) - i0))
    e.press('A', 3, 600); e.press('A', 3, 1)
    # 0x020022b8 = return address of the index-plane decode call (bl 0x0200886a in 0x02002130)
    e.set_hooks([(0x03000554, lz_in), (0x020022b8, lz_out), (0x03001748, bl_in), (0x0200732e, bl_out)])
    while len(blits) < nframes: e.run(60)
    return calls, blits

def uc_side(calls, nblits):
    rom = carts.load('srmt')
    m = adsfs.parse_mmstr(rom, adsfs.parse_sfcd(rom)['entries'][0]['rom'])
    r = [x for x in m['resources'] if x['type'] == 1][0]
    v = adsref.parse_video(rom, r['off'], r['size'])
    o = uc_lzma.Oracle()
    cm = uc_cost.CostModel(o, ['lzma', 'other'], [(0x03000404, 0x03000610, 'lzma'), (0x03001c58, 0x03002a90, 'lzma')])
    # group the recorded calls by segment: a call whose input pointer is a segment's B blob start + 8
    starts = {0x08000000 + ch['B'][0] + 13: i for i, ch in enumerate(v['chunks'])}   # init has read 5 bytes
    res = []
    seg = None
    for d in calls:
        if d['inp0'] in starts:
            seg = starts[d['inp0']]; ch = v['chunks'][seg]
            blob = rom[ch['B'][0]:ch['B'][0] + ch['B'][1]]
            us = struct.unpack_from('<I', blob)[0]
            # set up exactly like the player: init, then the calls of this segment one by one
            o.decode(0x08000000 + ch['B'][0], us, [], len(blob))
            base_cart = d['pos0']
        if seg is None: continue
        cm.reset()
        # the player's decode can also stop early on a deadline flag set by its IRQ handler, so the
        # replay asks for exactly the bytes the cartridge produced in this call: the distance from
        # the replay's ring position to the cartridge's position after the call (both stop on a
        # symbol boundary, so equal positions mean the same symbols were decoded)
        mypos = o.g.u32(uc_lzma.OBJ + 0x0C)
        s32 = lambda x: x - (1 << 32) if x & 0x80000000 else x
        over = s32(o.g.u32(uc_lzma.OBJ + 0x1C))
        want = ((d['pos1'] - base_cart) - mypos) & 0x1FFFF
        o.g.call(uc_lzma.DECODE, (uc_lzma.OBJ, want + (over if over < 0 else 0) if want + over > 0 else want, d['nin']),
                 sp=uc_lzma.SP, max_insns=200_000_000)
        d['sync'] = o.g.u32(uc_lzma.OBJ + 0x0C) == (d['pos1'] - base_cart) & 0x1FFFF
        cm._finish()
        res.append(dict(kind='lzma', segment=seg, n=d['n'], mgba_ins=d['ins'], uc_ins=cm.ins[0],
                        mgba_cyc=d['cyc'], uc_cyc=cm.cyc[0], in_sync=d['sync']))
    return res

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    calls, blits = mgba_side(n)
    res = uc_side(calls, len(blits))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, list(res[0])); w.writeheader(); w.writerows(res)
    R = np.array([[r['mgba_ins'], r['uc_ins'], r['mgba_cyc'], r['uc_cyc']] for r in res], float)
    same = int((R[:, 0] == R[:, 1]).sum())
    dc = (R[:, 2] - R[:, 3]) / R[:, 3] * 100
    print(f'LZMA calls compared: {len(R)}; identical instruction counts: {same}; '
          f'cycles mGBA-uc: mean {dc.mean():+.2f}% min {dc.min():+.2f}% max {dc.max():+.2f}%')
    b = np.array(blits[1:], float)
    print(f'blit (mode 3) per frame in mGBA: cycles mean {b[:, 0].mean():.0f} sd {b[:, 0].std():.0f}, instructions {b[:, 1].mean():.0f}')

if __name__ == '__main__':
    main()
