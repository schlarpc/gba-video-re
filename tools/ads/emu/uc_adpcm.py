#!/usr/bin/env python3
"""Unicorn oracle for the Majesco ADPCM decoder.

Runs the player's own audio driver (Thumb, EWRAM: block header parser + bit reader) and sample
loop (ARM, IWRAM) on real audio payloads and on random bit streams, and compares every output
sample with reference/majesco/adpcm.c (via libads.so).

Builds: SRMTHFG (bde72f6aba53, halfword-paired output to VRAM) and CN Limited (e0080e16dac3,
byte output). usage: uc_adpcm.py BUILD real|random N
"""
import os, sys, struct, random, ctypes as C
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import carts, adsfs
from uc_gba import GBA

CFG = {
    # cart, savestate during playback, audio ctx, driver(ctx,out,n) thumb, scratch payload, scratch out
    'srmt': dict(state=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/shots/srmt_ep1_play.state',
                 ctx=0x03002DF4, driver=0x02008654),
    'cn-limited': dict(state=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/oracle/cn-limited_play.state',
                       ctx=0x03002E10, driver=0x020033E4),
}
PAYLOAD = 0x09F00000
OUT = 0x02030000
SP = 0x03007E00
lib = C.CDLL(os.path.join(HERE, '..', '..', '..', 'reference', 'ads', 'libads.so'))

class MjAdpcm(C.Structure):
    _fields_ = [('buf', C.c_void_p), ('nbits_total', C.c_size_t), ('pos', C.c_size_t), ('overrun', C.c_int),
                ('coding', C.c_int), ('step', C.c_int32), ('prev', C.c_int32), ('h0', C.c_int32), ('h1', C.c_int32),
                ('f0', C.c_int32), ('f1', C.c_int32), ('f2', C.c_int32), ('s1', C.c_uint8), ('s2', C.c_uint8),
                ('ctx', C.c_uint8), ('tab', C.c_uint8 * 64), ('left', C.c_uint32), ('block', C.c_uint32)]
lib.mj_adpcm_init.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.c_int]
lib.mj_adpcm_decode.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
lib.mj_adpcm_decode.restype = C.c_size_t

def c_decode(payload, n):
    buf = C.create_string_buffer(payload, len(payload))
    a = MjAdpcm()
    lib.mj_adpcm_init(C.byref(a), buf, len(payload), 0)
    out = (C.c_int8 * n)()
    got = lib.mj_adpcm_decode(C.byref(a), out, n)
    return bytes(C.string_at(out, got))

class Oracle:
    def __init__(self, build):
        self.cfg = CFG[build]
        self.g = GBA(self.cfg['state'], carts.rom_path(build))
        self.base = {a: bytes(self.g.uc.mem_read(a, n)) for a, n in ((0x02000000, 0x40000), (0x03000000, 0x8000), (0x06000000, 0x18000))}

    def decode(self, payload, n, chunk):
        g = self.g; uc = g.uc
        for a, d in self.base.items(): uc.mem_write(a, d)
        uc.mem_write(PAYLOAD, payload + b'\0' * 64)
        ctx = self.cfg['ctx']
        w = lambda off, v: uc.mem_write(ctx + off, struct.pack('<I', v & 0xffffffff))
        # fresh bit reader on the payload, no block started (as the player's seek/open does)
        w(0x1C, 0); w(0x20, 0); w(0x24, 0); w(0x28, 0); w(0x2C, PAYLOAD); w(0x30, PAYLOAD); w(0x34, 0)
        done = 0
        while done < n:
            k = min(chunk, n - done)
            g.call(self.cfg['driver'], (ctx, OUT + done, k), sp=SP, thumb=True, max_insns=100_000_000)
            done += k
        return bytes(uc.mem_read(OUT, n))

def real_payloads(build):
    rom = carts.load(build)
    for e in adsfs.parse_sfcd(rom)['entries']:
        if not e['name'].endswith('.mmstr'): continue
        for r in adsfs.parse_mmstr(rom, e['rom'])['resources']:
            if r['type'] != 2: continue
            nb = struct.unpack_from('<H', rom, r['off'] + 14)[0]
            po = r['off'] + ((16 + 2 * nb + 3) & ~3)
            yield e['name'], rom[po:r['off'] + r['size']]

def main():
    build, mode, N = sys.argv[1], sys.argv[2], int(sys.argv[3])
    o = Oracle(build)
    rnd = random.Random(99)
    stats = dict(ok=0, bad=0, samples=0)
    if mode == 'real':
        for name, pl in list(real_payloads(build))[:N]:
            n = min(3 * 16458 + 1234, len(pl) * 2)      # three blocks and a bit of the fourth
            n -= n & 1
            a = o.decode(pl[:200000], n, 4096)
            b = c_decode(pl[:200000], n)
            ok = a == b
            stats['ok' if ok else 'bad'] += 1; stats['samples'] += n
            if not ok: print('MISMATCH real', name, next(i for i in range(n) if a[i] != b[i]), flush=True)
    else:
        for i in range(N):
            ln = rnd.randint(64, 12000) & ~3
            pl = bytearray(rnd.getrandbits(8) for _ in range(ln))
            pl[3] &= 0x7f            # first bit (bit 31 of word 0) = 0: a full block header
            n = min(rnd.randint(2, 40000), ln * 2) & ~1
            a = o.decode(bytes(pl), n, rnd.choice([n, 4096, 1372]))
            b = c_decode(bytes(pl) + b'\0' * 64, n)
            ok = a == b
            stats['ok' if ok else 'bad'] += 1; stats['samples'] += n
            if not ok: print('MISMATCH random', i, ln, n, next((j for j in range(n) if a[j] != b[j]), None), flush=True)
    print(build, mode, N, stats)

if __name__ == '__main__':
    main()
