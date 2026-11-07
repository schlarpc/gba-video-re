#!/usr/bin/env python3
"""Whole-stream CPU profile of the FVMV player in the instrumented mGBA core.

The core (mGBA + tools/mgba/mgba-prof.patch) adds, after every executed instruction, the cycles
mGBA charged for it (fetch + execute + data waitstates) to a per-PC histogram.  This driver
plays one episode from power-on and, after every emulated video refresh (280,896 cycles),
folds the histogram delta into activity buckets by PC.  Cycles not attributed to any
instruction are DMA cycle steal (the CPU never halts during playback).  PC breakpoints mark
each frame's decode start, so the per-refresh rows can be grouped into movie-frame windows.

Output: perf/out/pVeE.npz with
  run_cyc[n_refresh, n_bucket]   cycles per refresh and bucket (last bucket = unattributed)
  dec_run[n_frames]              refresh index in which frame i's decode started
  dec_cyc[n_frames]              video decode cycles of frame i (exact: decode-only PCs)
  names                          bucket names
usage: prof_fvmv.py VOL EP [--frames N]
"""
import argparse, os, sys, time
import ctypes as C
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'emu'))
import retro  # noqa: E402

PROF_CORE = os.environ.get('GBAV_MGBA_PROF_CORE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'mgba', 'prof', 'mgba_libretro.so')  # tools/mgba/mgba-prof.patch
ROMS = os.path.join(os.environ.get('GBAV_ROMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'roms'), 'Game Boy Advance Video - Pokemon - Volume %d (USA).gba')
REFRESH = 280896

NAMES = ['video_decode', 'audio_synth', 'audio_output', 'audio_ring', 'blit', 'idle_wait',
         'irq', 'ui_main', 'bios', 'dma_steal']


def buckets(vol):
    iw = np.full(0x2000, NAMES.index('ui_main'), np.int32)       # IWRAM, 4-byte granules
    rom = np.full(0x600, NAMES.index('ui_main'), np.int32)       # ROM code, 16-byte granules

    def setiw(lo, hi, nm):
        iw[(lo - 0x03000000) >> 2:(hi - 0x03000000) >> 2] = NAMES.index(nm)

    setiw(0x03001ee0, 0x03005be8, 'video_decode')   # block tree, pattern blocks, bit refill
    setiw(0x03005fe8, 0x03006068, 'video_decode')   # frame driver
    setiw(0x0300146c, 0x03001d18, 'audio_synth')    # unpack, RPE expansion, lattice
    setiw(0x03001d18, 0x03001e48, 'audio_output')   # 4x interpolation + quantiser
    setiw(0x03006cb0, 0x03006d78, 'audio_ring')     # ring bookkeeping
    setiw(0x030006dc, 0x03000818, 'blit')           # memcpy / brightness-LUT row copy
    setiw(0x03000610, 0x030006dc, 'irq')            # IRQ dispatcher
    setiw(0x030068ac, 0x03006910, 'irq')            # Timer1 + VBlank handlers
    setiw(0x03006bd4, 0x03006cb0, 'irq')            # Timer1: DMA ring restart
    setiw(0x03006960, 0x03006964, 'irq')
    setiw(0x03006424, 0x0300647c, 'idle_wait')      # "frame due?" test
    setiw(0x03006910, 0x03006960, 'idle_wait')      # scanline clock read
    sh = 0x20 if vol >= 3 else 0
    # the wait loop in the episode loop ("do { r = frame_due_test(ctx) } while r"): build A
    # 0x080028a4..0x080028b4, build B 0x080028a8..0x080028b8
    lo, hi = (0x080028a8, 0x080028b8) if vol >= 3 else (0x080028a4, 0x080028b4)
    rom[(lo - 0x08000000) >> 4:(hi + 4 - 0x08000000 + 15) >> 4] = NAMES.index('idle_wait')
    rom[(0x080032fc + sh - 0x08000000) >> 4:(0x08003474 + sh - 0x08000000) >> 4] = NAMES.index('audio_ring')
    rom[(0x08003474 + sh - 0x08000000) >> 4:(0x08003560 + sh - 0x08000000) >> 4] = NAMES.index('video_decode')
    rom[(0x080035a4 + sh - 0x08000000) >> 4:(0x080036f4 + sh - 0x08000000) >> 4] = NAMES.index('blit')
    return iw, rom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('vol', type=int)
    ap.add_argument('ep', type=int)
    ap.add_argument('--frames', type=int, default=0)
    a = ap.parse_args()
    out = os.path.join(HERE, '..', '..', '..', 'tmp', 'fvmv', 'perf')
    os.makedirs(out, exist_ok=True)
    rom = open(ROMS % a.vol, 'rb').read()
    o = rom.find(b'FVMV') if a.ep == 1 else rom.find(b'FVMV', rom.find(b'FVMV') + 4)
    nframes = int.from_bytes(rom[o + 4:o + 8], 'little')
    if a.frames:
        nframes = min(nframes, a.frames)
    r = retro.Retro(ROMS % a.vol, core=PROF_CORE)
    L = r.lib
    h_iw = np.ctypeslib.as_array((C.c_uint64 * 0x2000).in_dll(L, 'retro_prof_iwram'))
    h_rom = np.ctypeslib.as_array((C.c_uint64 * 0x200000).in_dll(L, 'retro_prof_rom'))
    h_bios = np.ctypeslib.as_array((C.c_uint64 * 0x1000).in_dll(L, 'retro_prof_bios'))
    h_ew = np.ctypeslib.as_array((C.c_uint64 * 0x10000).in_dll(L, 'retro_prof_ewram'))
    h_oth = np.ctypeslib.as_array((C.c_uint64 * 16).in_dll(L, 'retro_prof_other'))
    biw, brom = buckets(a.vol)
    nb = len(NAMES)
    bp = (C.c_uint32 * 8).in_dll(L, 'retro_bp_addr')
    bp[0] = 0x03005fe8
    C.c_int.in_dll(L, 'retro_bp_n').value = 1
    logn = C.c_uint32.in_dll(L, 'retro_bp_logn')
    r.run(1000)
    if a.ep == 2:
        r.buttons = {'DOWN'}; r.run(6); r.buttons = set(); r.run(10)
    r.buttons = {'A'}
    logn.value = 0

    def snap():
        v = np.zeros(nb, np.int64)
        v += np.bincount(biw, weights=h_iw, minlength=nb).astype(np.int64)
        v += np.bincount(brom, weights=h_rom[:0x600], minlength=nb).astype(np.int64)
        v[NAMES.index('bios')] += int(h_bios.sum())
        v[NAMES.index('ui_main')] += int(h_rom[0x600:].sum()) + int(h_ew.sum()) + int(h_oth.sum())
        return v

    rows, dec_run = [], []
    prev = snap()
    t0 = time.time()
    k = 0
    started = False
    while True:
        r.run(1)
        if r.buttons and r.frame_no >= 1006 + (16 if a.ep == 2 else 0):
            r.buttons = set()
        n = logn.value
        logn.value = 0
        cur = snap()
        d = cur - prev
        prev = cur
        if n:
            started = True
        if not started:
            continue
        att = int(d.sum())
        d[NAMES.index('dma_steal')] = REFRESH - att
        rows.append(d)
        for _ in range(n):
            dec_run.append(k)
        k += 1
        if len(dec_run) > nframes:
            break
        if len(dec_run) == nframes and k - dec_run[-1] > 10:
            break
        if k % 20000 == 0:
            print(k, len(dec_run), f'{time.time()-t0:.0f}s', flush=True)
    run_cyc = np.array(rows)
    dec_run = np.array(dec_run[:nframes])
    # exact per-frame decode cycles: decode bucket summed from this decode's refresh to the next one's
    vd = run_cyc[:, NAMES.index('video_decode')]
    cs = np.concatenate([[0], np.cumsum(vd)])
    ends = np.concatenate([dec_run[1:], [len(rows)]])
    dec_cyc = cs[ends] - cs[dec_run]
    np.savez_compressed(os.path.join(out, f'p{a.vol}e{a.ep}.npz'), run_cyc=run_cyc, dec_run=dec_run,
                        dec_cyc=dec_cyc, names=np.array(NAMES))
    print('done', len(dec_run), 'frames,', len(rows), 'refreshes,', f'{time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
