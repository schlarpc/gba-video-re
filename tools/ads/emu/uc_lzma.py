#!/usr/bin/env python3
"""Unicorn oracle for the ADS player's LZMA decoder (SRMTHFG build bde72f6aba53).

Loads a savestate taken during playback, points the player's own LZMA object (vtable 0x03002AE8,
object 0x0300672C) at a blob, runs the cartridge's init (0x03000404) and decode (0x03000554 ->
0x03001C58) routines, and compares the output ring and the input bytes consumed with the C
decoder (lzma_dec.c via libads.so).

usage: uc_lzma.py real N        N real blobs (codebook + index) from all srmt streams
       uc_lzma.py fuzz N        N synthetic streams (lc=0 lp=0 pb=2, random data, encoder = xz)
       uc_lzma.py mutate N      N real blobs with random byte corruption (both sides lenient)
"""
import os, sys, struct, random, lzma, ctypes as C
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import carts, adsref, adsfs
from uc_gba import GBA

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/shots/srmt_ep1_play.state'
OBJ, INIT, DECODE = 0x0300672C, 0x03000404, 0x03000554
RING, MASK = 0x0200E3A0, 0x1FFFF
DESC = 0x0203FF00          # scratch for the init descriptor
BLOB = 0x09F00000          # scratch ROM area for synthetic blobs (overwrites unused cart space in unicorn only)
SP = 0x03007E00
PROBS = 0x02030000         # scratch EWRAM for a probability array large enough for lc+lp up to 6

lib = C.CDLL(os.path.join(HERE, '..', '..', '..', 'reference', 'ads', 'libads.so'))

class LzmaDec(C.Structure):
    _fields_ = [('in_', C.c_void_p), ('in_end', C.c_void_p), ('range', C.c_uint32), ('code', C.c_uint32),
                ('ring', C.c_void_p), ('ring_mask', C.c_uint32), ('pos', C.c_uint32),
                ('lc', C.c_uint), ('lp', C.c_uint), ('pb', C.c_uint), ('state', C.c_uint), ('rep', C.c_uint32 * 4),
                ('remain_len', C.c_uint32), ('max_dist', C.c_uint32), ('error', C.c_int), ('lenient', C.c_int),
                ('probs', C.c_void_p), ('nprobs', C.c_size_t)]
lib.lzma_probs_size.restype = C.c_size_t
lib.lzma_probs_size.argtypes = [C.c_uint, C.c_uint]
lib.lzma_init.argtypes = [C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.c_void_p, C.c_void_p, C.c_uint32, C.c_void_p, C.c_void_p]
lib.lzma_decode.argtypes = [C.c_void_p, C.c_uint32]

def c_decode(blob, chunks, lenient=False):
    us, par = struct.unpack_from('<II', blob)
    lc, lp, pb = (par >> 6) & 15, (par >> 3) & 7, par & 7
    probs = (C.c_uint16 * lib.lzma_probs_size(lc, lp))()
    ring = (C.c_uint8 * (MASK + 1))()
    buf = C.create_string_buffer(bytes(blob), len(blob))
    d = LzmaDec()
    lib.lzma_init(C.byref(d), lc, lp, pb, probs, ring, MASK, C.addressof(buf) + 8, C.addressof(buf) + len(blob))
    d.lenient = int(lenient)
    for n in chunks:
        lib.lzma_decode(C.byref(d), n)
    used = d.in_ - (C.addressof(buf) + 8)
    return bytes(ring), used, d.error

class Oracle:
    def __init__(self):
        self.g = GBA(STATE, carts.rom_path('srmt'))
        self.produced = 0
        self.big_probs = False
        self.base = {a: bytes(self.g.uc.mem_read(a, n)) for a, n in ((0x02000000, 0x40000), (0x03000000, 0x8000), (0x06000000, 0x18000))}

    def reset(self):
        for a, d in self.base.items(): self.g.uc.mem_write(a, d)

    def decode(self, blob_addr, us, chunks, blob_len=0):
        g = self.g; uc = g.uc
        self.reset()
        uc.mem_write(RING, b'\0' * (MASK + 1))
        # init descriptor as the player builds it (EWRAM 0x02002130): blob, blob length, ring base,
        # mode byte 2, ring size, start offset in the ring
        uc.mem_write(DESC, struct.pack('<IIIIII', blob_addr, blob_len, RING, 2, MASK + 1, 0))
        if self.big_probs:     # vtable+0x1C: set probability array (ptr, bytes); the cart's own fits lc+lp <= 1
            g.call(0x03000504, (OBJ, PROBS, 0x10000 * 2), sp=SP)
        g.call(INIT, (OBJ, DESC), sp=SP, max_insns=10_000_000)
        for n in chunks:
            g.call(DECODE, (OBJ, n, 0x7FFFFFFF), sp=SP, max_insns=200_000_000)
        ring = bytes(uc.mem_read(RING, MASK + 1))
        used = g.u32(OBJ + 0x28) - (blob_addr + 8)
        # the core finishes the match it is in even past the requested count; the overshoot is kept
        # as a negative count at obj+0x1C and taken off the next call
        over = g.u32(OBJ + 0x1C)
        over = (0x100000000 - over) if over & 0x80000000 else 0
        self.produced = sum(chunks) + over
        return ring, used

def real_blobs():
    rom = carts.load('srmt')
    for e in adsfs.parse_sfcd(rom)['entries']:
        if not e['name'].endswith('.mmstr'): continue
        for r in adsfs.parse_mmstr(rom, e['rom'])['resources']:
            if r['type'] != 1: continue
            v = adsref.parse_video(rom, r['off'], r['size'])
            for ch in v['chunks']:
                g = ch['B'][1] // max(1, ch['nfr']) if False else adsref.DIMS[ch['mode']]
                fs = (240 // g[0]) * (160 // g[1])
                yield ('A', ch['A'][0], rom[ch['A'][0]:ch['A'][0] + ch['A'][1]], None)
                yield ('B', ch['B'][0], rom[ch['B'][0]:ch['B'][0] + ch['B'][1]], fs)

def check(o, kind, addr, blob, chunks, lenient=False):
    us = struct.unpack_from('<I', blob)[0]
    r1, u1 = o.decode(addr, us, chunks, len(blob))
    n = o.produced
    r2, u2, err = c_decode(blob, [n], lenient)
    def take(r): return bytes(r[i & MASK] for i in range(n)) if n <= MASK + 1 else r
    ok = take(r1) == take(r2) and u1 == u2
    return ok, u1, u2, err

def main():
    mode, N = sys.argv[1], int(sys.argv[2])
    rnd = random.Random(1234)
    o = Oracle()
    stats = dict(ok=0, bad=0)
    if mode == 'real':
        blobs = list(real_blobs())
        rnd.shuffle(blobs)
        for kind, addr, blob, fs in blobs[:N]:
            us = struct.unpack_from('<I', blob)[0]
            if kind == 'A': chunks = [us]
            else:
                # the player decodes a frame (or a few) at a time; use frame-sized and odd pieces
                chunks = []; left = min(us, MASK + 1)
                while left > 0:
                    k = min(left, rnd.choice([fs, fs * 2, rnd.randint(1, fs)])); chunks.append(k); left -= k
            ok, u1, u2, err = check(o, kind, 0x08000000 + addr, blob, chunks)
            stats['ok' if ok else 'bad'] += 1
            if not ok: print('MISMATCH real', kind, hex(addr), u1, u2, err, flush=True)
    elif mode == 'fuzz':
        o.big_probs = True
        for i in range(N):
            # the cartridge's core hard-wires lc=0, lp=0, pb=2 (every stream uses them); see format-ads.md
            lc, lp, pb = 0, 0, 2
            n = rnd.randint(1, 60000)
            kind = rnd.random()
            if kind < 0.3: data = bytes(rnd.getrandbits(8) for _ in range(n))
            elif kind < 0.6: data = bytes(rnd.choice(b'\0\1\2\3abc') for _ in range(n))
            else:
                base = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(1, 4000)))
                data = (base * (n // len(base) + 1))[:n]
            enc = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{'id': lzma.FILTER_LZMA1, 'lc': lc, 'lp': lp, 'pb': pb,
                                                                         'dict_size': 1 << 16, 'preset': rnd.randint(0, 9)}])
            blob = struct.pack('<II', n, lc << 6 | lp << 3 | pb) + enc + b'\0' * 16
            o.g.uc.mem_write(BLOB, blob)
            chunks = [n] if rnd.random() < 0.5 else [rnd.randint(1, n) for _ in range(3)]
            chunks = [min(c, n) for c in chunks]
            if sum(chunks) > n: chunks = [n]
            ok, u1, u2, err = check(o, 'F', BLOB, blob, chunks)
            stats['ok' if ok else 'bad'] += 1
            if not ok: print('MISMATCH fuzz', i, lc, lp, pb, n, u1, u2, err, flush=True)
    elif mode == 'mutate':
        blobs = [b for b in real_blobs() if b[0] == 'A']
        for i in range(N):
            kind, addr, blob, fs = rnd.choice(blobs)
            b = bytearray(blob)
            for _ in range(rnd.randint(1, 8)):
                p = rnd.randint(13, len(b) - 1); b[p] = rnd.getrandbits(8)
            us = struct.unpack_from('<I', b)[0]
            o.g.uc.mem_write(BLOB, bytes(b) + b'\0' * 64)
            ok, u1, u2, err = check(o, 'M', BLOB, bytes(b) + b'\0' * 64, [us], lenient=True)
            stats['ok' if ok else 'bad'] += 1
            if not ok and err == 1: stats['overrun'] = stats.get('overrun', 0) + 1; stats['bad'] -= 1; continue
            if not ok: print('MISMATCH mutate', i, hex(addr), u1, u2, err, flush=True)
    print(mode, N, stats)

if __name__ == '__main__':
    main()
