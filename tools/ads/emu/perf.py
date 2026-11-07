#!/usr/bin/env python3
"""CPU-load measurement for ADS playback with the instrumented mGBA core (work/emu/mgba-cb).

The core adds the cycles mGBA charges for every executed instruction (fetch + execute + data
waitstates + VRAM stall model) to one of 16 buckets chosen by a PC lookup table. This script fills
the table from the build's located codec routines (work/builddiff.json), plays a stream from the
menu (as capture.py does) and snapshots the buckets at every VQ blit entry, giving one row per
displayed video frame:

  frame window = cycles between two consecutive blit entries (normally 5 refreshes = 1,404,480)
  busy = all attributed instruction cycles in the window / window cycles

Buckets: 1 LZMA (init/decode/core), 2 codebook (unpredict + YCbCr->RGB), 3 VQ blit, 4 ADPCM sample
loop, 5 other IWRAM ARM (IRQ dispatcher, misc), 6 Thumb player code in EWRAM/ROM (UI, A/V
scheduling, ADPCM block headers and bit-reader refills, chunk handling), 14 BIOS, 15 other.

usage: perf.py CART boot|movie K OUT.csv.gz [--frames N]
"""
import os, sys, json, gzip, argparse, ctypes as C
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import ademu, carts, images
from builds import discover

BD = json.load(open(os.path.join(HERE, '..', '..', '..', 'tmp', 'ads', 'builddiff.json')))
NAMES = {1: 'lzma', 2: 'codebook', 3: 'blit', 4: 'adpcm', 5: 'iwram_other', 6: 'thumb_player', 14: 'bios', 15: 'other'}
FUNC_BUCKET = {'lzma_init': 1, 'lzma_decode': 1, 'lzma_core': 1, 'cb_convert': 2, 'unpredict_luma': 2,
               'unpredict_chroma': 2, 'vq_blit': 3, 'adpcm_samples': 4}
REFRESH = 280896

def setup(e, cid):
    L = e.L
    iw = np.ctypeslib.as_array((C.c_uint8 * 0x4000).in_dll(L, 'retro_bk_iw'))
    ew = np.ctypeslib.as_array((C.c_uint8 * 0x20000).in_dll(L, 'retro_bk_ew'))
    rom = np.ctypeslib.as_array((C.c_uint8 * 0x200000).in_dll(L, 'retro_bk_rom'))
    iw[:] = 5; ew[:] = 6; rom[:] = 6
    b = BD[images.BUILD_OF[cid]]['funcs']
    B = discover(cid)
    for k, bk in FUNC_BUCKET.items():
        f = b.get(k)
        if not f: continue
        a = int(f['addr'], 16)
        if a >> 24 != 3: continue
        n = f['n'] * 4
        if k == 'adpcm_samples': a, n = B['audio'], B['audio_end'] + 8 - B['audio']
        if k == 'vq_blit': a = B['blit']
        iw[(a & 0x7fff) >> 1:((a + n) & 0x7fff) >> 1] = bk
    C.c_int.in_dll(L, 'retro_bk_on').value = 1
    cyc = np.ctypeslib.as_array((C.c_uint64 * 16).in_dll(L, 'retro_bk_cyc'))
    ins = np.ctypeslib.as_array((C.c_uint64 * 16).in_dll(L, 'retro_bk_ins'))
    return cyc, ins

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cart'); ap.add_argument('mode'); ap.add_argument('args', nargs='+')
    ap.add_argument('--frames', type=int, default=0)
    ap.add_argument('--nav', default='')
    a = ap.parse_args()
    cid = a.cart
    B = discover(cid)
    e = ademu.Emu(carts.emu_rom_path(cid))
    rows = []
    state = dict(last=None, last_emu=None)
    def snap(g):
        c = cyc.copy(); i = ins.copy()
        # sub-frame position: the core's cycle counter is not exported, so windows are whole
        # refreshes between blits (the blit starts at the same point of the frame schedule)
        fr = e.r.frame_no
        if state['last'] is not None:
            d = c - state['last'][0]; di = i - state['last'][1]
            rows.append([fr - state['last_emu']] + d.tolist() + di.tolist())
        state['last'] = (c, i); state['last_emu'] = fr
    if a.mode == 'boot':
        out = a.args[0]
        e.run(1)
        cyc, ins = setup(e, cid)
        e.set_hooks([(B['blit'], snap)])
        n0 = -1
        while len(rows) != n0 or e.r.frame_no < 1500:
            n0 = len(rows); e.run(600)
    else:
        k, out = int(a.args[0]), a.args[1]
        e.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + f'/menus/{cid}_menu.state', 'rb').read())
        e.run(1)
        cyc, ins = setup(e, cid)
        nav = a.nav or ' '.join(['D w90'] * (k - 1) + ['A'])
        for t in nav.split():
            if t[0] == 'w': e.run(int(t[1:]))
            else: e.press(dict(D='DOWN', U='UP', L='LEFT', R='RIGHT', A='A', B='B', S='START')[t], 3, 20)
        e.set_hooks([(B['blit'], snap)])
        waited = 0
        while not rows and waited < 3000:
            e.run(60); waited += 60
            if not rows and waited % 600 == 0: e.press('A', 3, 1)
        idle = 0
        while idle < 900 and (not a.frames or len(rows) < a.frames):
            n0 = len(rows); e.run(60)
            idle = idle + 60 if len(rows) == n0 else 0
    with gzip.open(out, 'wt') as f:
        f.write('refreshes,' + ','.join(f'cyc{j}' for j in range(16)) + ',' + ','.join(f'ins{j}' for j in range(16)) + '\n')
        for r in rows: f.write(','.join(map(str, r)) + '\n')
    R = np.array(rows, dtype=np.float64)
    if len(R):
        win = R[:, 0] * REFRESH
        busy = R[:, 1:17].sum(1) / win
        print(cid, a.mode, a.args[:1], 'windows', len(R), 'busy mean %.3f p99 %.3f max %.3f' % (busy.mean(), np.percentile(busy, 99), busy.max()),
              'late', int((R[:, 0] != 5).sum()),
              {NAMES.get(j, j): round(float(R[:, 1 + j].sum() / win.sum()), 4) for j in range(16) if R[:, 1 + j].sum()})

if __name__ == '__main__':
    main()
