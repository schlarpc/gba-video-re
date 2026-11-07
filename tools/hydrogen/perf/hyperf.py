#!/usr/bin/env python3
"""Cycle-level resource accounting of a Hydrogen GBA Video stream in the instrumented mGBA core
(the Shrek perf patch tools/vx/perf/patch/mgba-perf.patch, unchanged; core in tmp/mgba/perf/).

The core charges every emulated cycle to (context, bucket); buckets come from a PC lookup table
set here (IWRAM routines of the player, translated per build; all ROM Thumb code is one bucket
"rom_code"), and it snapshots all counters at event PCs. Events used: VQ blit entry (frame
boundary), Inflate::UncompressBlock entry, ADPCM entry, codebook converter entry.

The stream is started from a savestate made with emu/mkstate.py (blit 0 = injection point), so the
cart plays the stream exactly as in the verification captures.

  hyperf.py CART STREAM OUTDIR [--frames N]

Writes OUTDIR/per_frame.csv (one row per displayed frame: the window from its blit to the next
blit = 5 VBlanks) and OUTDIR/totals.json.
"""
import sys, os, json, csv, struct, argparse, time, ctypes as C
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'emu')); sys.path.insert(0, os.path.join(HERE, '..'))
import numpy as np
import retro
from carts import rom_path, SHORT
import hycart

CORE = os.environ.get('HY_PERF_CORE') or os.path.join(HERE, '..', '..', '..', 'tmp', 'mgba', 'perf', 'mgba_libretro.so')  # tools/vx/perf/patch/mgba-perf.patch
NB, NCTX = 64, 4
NK = NB * NCTX
FIELDS = [("now", 1), ("elapsed", 1), ("cyc", NK), ("ins", NK), ("memcyc", NK), ("memwait", NK),
          ("fetchwait", NK), ("halted", 1), ("blocked", 1), ("irq_entry_cyc", 1), ("irqs", 1),
          ("dma_units", 4), ("dma_bytes", 4), ("dma_cyc", 4), ("matrix_cmds", 1), ("matrix_bytes", 1),
          ("rd_n", 64), ("wr_n", 64), ("rd_b", 64), ("wr_b", 64), ("rd_w", 64), ("wr_w", 64),
          ("fetch_b", 16), ("matrix_cmds_ctx", 4), ("dma_rd_b", 16), ("dma_wr_b", 16), ("vram_wr_b", 3)]
OFF = {}; _o = 0
for _n, _k in FIELDS: OFF[_n] = slice(_o, _o + _k); _o += _k
NWORDS = _o
SNAP_BYTES = 16 + 8 * NWORDS
F_POP, F_EVENT = 0x0800, 0x1000

NAMES = ['other', 'inflate', 'vq_convert', 'vq_unpredict', 'vq_blit', 'adpcm', 'irq_dispatch', 'osd_font',
         'memutil', 'veneer', 'bios'] + [f'unused{i}' for i in range(11, 60)] + ['irq_entry', 'rom_code', 'bios_hi', 'other_region']
assert len(NAMES) == 64
BID = {n: i for i, n in enumerate(NAMES)}
# All Grown Up (build 04cbcc07fe42) IWRAM routine ranges -> bucket
AGU_RANGES = [(0x03000000, 0x03000b1c, 'inflate'), (0x03000b1c, 0x03000c68, 'memutil'), (0x03000c68, 0x03001430, 'osd_font'),
              (0x03001430, 0x03001ba8, 'vq_convert'), (0x03001ba8, 0x03001de0, 'vq_unpredict'), (0x03001de0, 0x03001e7c, 'osd_font'),
              (0x03001e7c, 0x03001ff4, 'irq_dispatch'), (0x03001ff4, 0x030024a0, 'vq_blit'), (0x030024a0, 0x03002a40, 'inflate'),
              (0x03002a40, 0x03002ad0, 'veneer'), (0x03002ad0, 0x03002d44, 'adpcm'), (0x03002d44, 0x03002da0, 'inflate')]
SIG = {  # AGU address -> first 16 bytes (locates each anchor in other builds)
    0x03000094: 'f8402de90040a0e1000091e50060a0e3', 0x030005c8: 'f04f2de90040a0e1382094e5480090e5',
    0x03001430: 'f04f2de908d04de22c609de50080d3e5', 0x03001ff4: 'f05f2de90c4090e5004094e5844ea0e1',
    0x03002ad0: 'f05f2de9345090e5054062e0344080e5'}


def iwram_image(rom):
    t = struct.unpack_from('<I', rom, 0x18c)[0] + 0x18c
    for i in range(0, 0x30, 12):
        s, d, n = struct.unpack_from('<3I', rom, t + i)
        if d == 0x03000000: return s - 0x08000000, n


def build_ranges(rom):
    """translate AGU ranges to this build: addresses below 0x03000b1c are identical in every build;
    the rest moves by one constant per region, measured on an anchor routine in that region
    (converter/blit/inflate core: 0 or -0x68; veneers, ADPCM and the bit reader: 0 or -0x74)."""
    o, n = iwram_image(rom); img = rom[o:o + n]
    a = {k: 0x03000000 + img.find(bytes.fromhex(v)) for k, v in SIG.items()}
    assert a[0x03000094] == 0x03000094 and a[0x030005c8] == 0x030005c8
    shift = a[0x03001ff4] - 0x03001ff4
    assert a[0x03001430] - 0x03001430 == shift
    shift2 = a[0x03002ad0] - 0x03002ad0
    out = []
    for lo, hi, name in AGU_RANGES:
        if hi <= 0x03000b1c: s1 = s2 = 0
        elif lo >= 0x03002ad0: s1 = s2 = shift2
        elif lo == 0x03002a40: s1, s2 = shift, shift2          # veneers: start moves like the code before, end like ADPCM
        elif lo == 0x03000b1c: s1, s2 = 0, shift               # group-B builds drop Inflate's debug symbol dump here
        else: s1 = s2 = shift
        out.append((lo + s1, hi + s2, name))
    ev = {'blit': a[0x03001ff4], 'inflate': a[0x030005c8], 'adpcm': a[0x03002ad0], 'convert': a[0x03001430],
          'irq': 0x03001e7c + shift}
    return out, ev, (shift, shift2)


class Perf:
    def __init__(self, r, ranges, events):
        self.r = r; L = r.lib
        L.retro_perf_counters.restype = C.c_void_p
        L.retro_perf_drain.restype = C.c_int
        L.retro_perf_lost.restype = C.c_uint32
        L.retro_perf_lut_set.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32]
        assert L.retro_perf_snap_size() == SNAP_BYTES
        L.retro_perf_lut_set(0, 0x4000, BID['bios'])
        L.retro_perf_lut_set(0x00000198, 0x0000019a, BID['bios'] | F_POP)   # HLE BIOS IRQ return
        for lo, hi, nm in ranges: L.retro_perf_lut_set(lo, hi, BID[nm])
        for nm, pc in events:
            b = [x for x in ranges if x[0] <= pc < x[1]][0][2]
            L.retro_perf_lut_set(pc, pc + 2, BID[b] | F_EVENT)
        self.buf = C.create_string_buffer(SNAP_BYTES * 256)
        self.cptr = L.retro_perf_counters()

    def counters(self):
        return np.frombuffer(C.string_at(self.cptr, 8 * NWORDS), np.uint64).astype(np.int64)

    def drain(self):
        n = self.r.lib.retro_perf_drain(self.buf, 256); raw = self.buf.raw; out = []
        for k in range(n):
            o = k * SNAP_BYTES
            pc, ctx, vc, _ = struct.unpack_from('<4I', raw, o)
            out.append((pc, ctx, vc, np.frombuffer(raw, np.uint64, NWORDS, o + 16).astype(np.int64)))
        return out


def row(d):
    cyc = d[OFF['cyc']].reshape(NCTX, NB).sum(0)
    wall = int(d[OFF['now']][0])
    r = {'wall': wall, 'halted': int(d[OFF['halted']][0]), 'dma_blocked': int(d[OFF['blocked']][0])}
    for nm in ['inflate', 'vq_convert', 'vq_unpredict', 'vq_blit', 'adpcm', 'irq_dispatch', 'osd_font', 'memutil',
               'veneer', 'bios', 'irq_entry', 'rom_code', 'other', 'other_region']:
        r[nm] = int(cyc[BID[nm]])
    r['busy'] = wall - r['halted']
    rb = d[OFF['rd_b']].reshape(NCTX, 16).sum(0); wb = d[OFF['wr_b']].reshape(NCTX, 16).sum(0)
    rw = d[OFF['rd_w']].reshape(NCTX, 16).sum(0); ww = d[OFF['wr_w']].reshape(NCTX, 16).sum(0)
    for k, n in {2: 'ewram', 3: 'iwram', 5: 'pal', 6: 'vram', 8: 'rom'}.items():
        r[f'rd_{n}'] = int(rb[k]); r[f'wr_{n}'] = int(wb[k]); r[f'wait_{n}'] = int(rw[k] + ww[k])
    r['memwait'] = int(d[OFF['memwait']].sum()); r['fetchwait'] = int(d[OFF['fetchwait']].sum())
    r['fetch_rom'] = int(d[OFF['fetch_b']][8] + d[OFF['fetch_b']][9])
    r['dma1_bytes'] = int(d[OFF['dma_bytes']][1]); r['dma_cyc'] = int(d[OFF['dma_cyc']].sum())
    r['irqs'] = int(d[OFF['irqs']][0])
    vw = d[OFF['vram_wr_b']]
    r['vram_wr_active'], r['vram_wr_hblank'], r['vram_wr_vblank'] = int(vw[0]), int(vw[1]), int(vw[2])
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cart'); ap.add_argument('stream', type=int); ap.add_argument('out')
    ap.add_argument('--frames', type=int, default=0)
    ap.add_argument('--state', help='savestate prefix from emu/mkstate.py (default: make one)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rp = rom_path(a.cart); rom = open(rp, 'rb').read()
    c = hycart.find_containers(rom)[a.stream]
    s = hycart.Stream(rom, c['video'][0], c['video'][1])
    nfr = a.frames or s.frames
    ranges, ev, shift = build_ranges(rom)
    st = a.state
    if not st:
        st = os.path.join(a.out, 'start')
        import subprocess
        subprocess.run([sys.executable, os.path.join(HERE, '..', 'emu', 'mkstate.py'), rp, str(a.stream), '0', st], check=True)
    r = retro.Retro(rp, core=CORE); r.run(1)
    p = Perf(r, ranges, list(ev.items()))
    r.load_state(open(st + '.state', 'rb').read())
    r.lib.retro_perf_set_ctx(0); r.lib.retro_perf_reset()
    rows = []; last_blit = None; nblit = 0; t0 = time.time(); calls = {k: 0 for k in ev}
    first = None; last_vb = None; vrows = []
    end_at = None
    while r.frame_no < nfr * 5 + 4000 and (end_at is None or r.frame_no < end_at):
        r.run(1)
        if end_at is None and nblit >= nfr: end_at = r.frame_no + 6
        for pc, ctx, vc, v in p.drain():
            for k, e in ev.items():
                if pc == e: calls[k] += 1
            if pc == ev['irq'] and vc == 160:
                if last_vb is not None:
                    d = v - last_vb
                    cyc = d[OFF['cyc']].reshape(NCTX, NB).sum(0)
                    vrows.append((len(vrows), nblit, int(d[OFF['now']][0]), int(d[OFF['now']][0] - d[OFF['halted']][0]),
                                  int(cyc[BID['inflate']]), int(cyc[BID['vq_convert']] + cyc[BID['vq_unpredict']]),
                                  int(cyc[BID['vq_blit']]), int(cyc[BID['adpcm']]), int(cyc[BID['rom_code']])))
                last_vb = v
            if pc == ev['blit']:
                if last_blit is not None:
                    d = v - last_blit[0]
                    rr = row(d); rr['frame'] = nblit - 1; rr['blit_vcount'] = last_blit[1]
                    rows.append(rr)
                else:
                    first = v
                last_blit = (v, vc); nblit += 1
    final = p.counters()
    tot = row(last_blit[0] - first) if first is not None else {}
    tot.update(dict(cart=a.cart, stream=a.stream, frames_measured=len(rows), calls=calls, lost=int(r.lib.retro_perf_lost()),
                    shift=shift, seconds=time.time() - t0))
    with open(os.path.join(a.out, 'per_frame.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, list(rows[0])); w.writeheader(); w.writerows(rows)
    with open(os.path.join(a.out, 'per_vblank.csv'), 'w') as f:
        f.write('vblank,blits_before,wall,busy,inflate,vq_convert,vq_blit,adpcm,rom_code\n')
        for x in vrows: f.write(','.join(map(str, x)) + '\n')
    # steady state only: VBlanks after the first blit and up to the last one (no prefill, no teardown)
    vb = np.array([x[3] for x in vrows if 1 <= x[1] < nblit]) if vrows else np.zeros(1)
    tot.update(vblank_busy_max=int(vb.max()), vblank_busy_p99=float(np.percentile(vb, 99)), vblanks=len(vrows),
               vblanks_over_period=int((vb > 280896).sum()))
    json.dump(tot, open(os.path.join(a.out, 'totals.json'), 'w'), indent=1)
    print(json.dumps({k: tot[k] for k in ('frames_measured', 'wall', 'busy', 'lost', 'seconds')}))


if __name__ == '__main__':
    main()
