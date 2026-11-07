#!/usr/bin/env python3
"""Differential fuzzing: the cart's own decoder routines (run in unicorn, emu/uc_fvmv.py)
against the C reference (libfvmvdec.so).

Video: each case fills all 256 KiB of EWRAM with random bytes (same in both), picks the
reference and output buffer addresses, and decodes one frame payload; the whole EWRAM must
then match.  Payload kinds:
  random  - random bit/colour/MV streams (exercises every mode, block size, edge and
            off-picture access, including vectors that cross rows and buffers)
  mutate  - a real frame from the cart with random bit flips in its bit stream
  real    - a real frame unchanged, on random memory
Audio: random and real 40-byte blocks through 0x03001b88 (synthesis) and 0x03001d18
(interpolation + quantiser), with state carried across blocks; the 256 int32 samples, the
1024 PCM bytes and all state words must match.

usage: fuzz.py [--vol N] [--video N] [--audio N] [--seed S]
"""
import argparse, ctypes as C, os, random, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'emu'))
from uc_fvmv import Oracle, ROMS  # noqa: E402

LIB = C.CDLL(os.path.join(HERE, '..', '..', 'reference', 'fvmv', 'libfvmvdec.so'))
LIB.fvmv_dec_size.restype = C.c_size_t
LIB.fvmv_ewram.restype = C.c_void_p
LIB.fvmv_ewram.argtypes = [C.c_void_p]
LIB.fvmv_decode_frame_at.argtypes = [C.c_void_p, C.c_char_p, C.c_uint32, C.c_uint32, C.c_uint32, C.c_uint32]
LIB.fvmv_audio_size.restype = C.c_size_t
LIB.fvmv_audio_synth.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p]
LIB.fvmv_audio_output.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_void_p]
LIB.fvmv_audio_init.argtypes = [C.c_void_p]
LIB.fvmv_init.argtypes = [C.c_void_p]

DATA = 0x09F00000            # payload placed in the (writable in unicorn) ROM window
EW = 0x02000000
STATS = {}


def real_frames(rom, n_max=4000):
    o = rom.find(b'FVMV')
    nf = struct.unpack_from('<I', rom, o + 4)[0]
    p = o + 0x20
    out = []
    for i in range(min(nf, n_max)):
        sz, _, _, _, vl = struct.unpack_from('<5I', rom, p)
        al = struct.unpack_from('<I', rom, p + 0x14 + vl)[0]
        out.append((rom[p + 0x14:p + 0x14 + vl], rom[p + 0x18 + vl:p + 0x18 + vl + al]))
        p += 16 + sz
    return out


def random_payload(rng):
    nb = rng.randrange(256, 12000) & ~3
    nc = rng.randrange(0, 8000) & ~1
    nm = rng.randrange(0, 3000)
    body = rng.randbytes(nb + nc + nm)
    v = struct.pack('<II', nb, nc) + body
    return v


def mutate(v, rng):
    nb = struct.unpack_from('<I', v, 0)[0]
    b = bytearray(v)
    for _ in range(rng.randrange(1, 6)):
        k = 8 + rng.randrange(max(nb, 1))
        if k < len(b):
            b[k] ^= 1 << rng.randrange(8)
    return bytes(b)


def video_case(o, rng, payload):
    # buffers anywhere in EWRAM, 2-byte aligned, at least 8 rows + 1 px from the ends
    lo, hi = EW + 8 * 480 + 2, EW + 0x40000 - 0x12c00 - 8 * 480 - 2
    if rng.random() < 0.5:   # the player's layout
        # (shifted up one buffer: the player's buffer 0 at 0x02000008 would make top-row
        # accesses leave EWRAM, which is open bus on the GBA and unmapped in unicorn)
        sel = rng.randrange(2)
        out, ref = (0x02012c08, 0x02025808) if sel else (0x02025808, 0x02012c08)
    else:
        out = rng.randrange(lo, hi) & ~3
        ref = rng.randrange(lo, hi) & ~1
    mem = rng.randbytes(0x40000)
    tail = rng.randbytes(0x8000)
    data = payload + tail
    o.set_ewram(mem)
    o.uc.mem_write(DATA, data)
    o.video(DATA, ref, out)
    a = o.ewram()
    d = C.create_string_buffer(LIB.fvmv_dec_size())
    LIB.fvmv_init(d)
    C.memmove(LIB.fvmv_ewram(d), mem, 0x40000)
    LIB.fvmv_decode_frame_at(d, data, len(payload), len(data), out, ref)
    b = C.string_at(LIB.fvmv_ewram(d), 0x40000)
    changed = sum(1 for i in range(0, 0x40000, 64) if a[i:i + 64] != mem[i:i + 64])
    STATS['changed'] = STATS.get('changed', 0) + changed
    mc = (C.c_uint64 * 128).from_address(C.addressof(d) + 0x40000 + 8)
    for z in range(16):
        for m in range(8):
            if mc[z * 8 + m]:
                STATS.setdefault('modes', set()).add((z, m))
    if a != b:
        diff = [i for i in range(0, 0x40000, 2) if a[i:i + 2] != b[i:i + 2]]
        return False, f'out={out:08x} ref={ref:08x} {len(diff)} halfwords differ, first at {EW + diff[0]:08x}'
    return True, ''


def audio_run(o, rng, blocks):
    st = C.create_string_buffer(LIB.fvmv_audio_size())
    LIB.fvmv_audio_init(st)
    # zero the cart's audio state (IWRAM 0x03001b5c..0x03001b84, 0x03001d0c..0x03001d18)
    o.uc.mem_write(0x03001b5c, bytes(40))
    o.uc.mem_write(0x03001d0c, bytes(12))
    SRC, SYN, PCM = 0x09F80000, 0x02000000, 0x02001000
    bad = 0
    for i, blk in enumerate(blocks):
        o.uc.mem_write(SRC, blk)
        syn_u = o.audio(SRC, SYN)
        o.g.call(0x03001d18, (SYN, PCM, 1024), sp=0x03007e00)
        pcm_u = bytes(o.uc.mem_read(PCM, 1024))
        syn_c = (C.c_int32 * 256)()
        pcm_c = (C.c_int8 * 1024)()
        LIB.fvmv_audio_synth(st, blk, syn_c)
        LIB.fvmv_audio_output(st, syn_c, 256, pcm_c)
        st_u = bytes(o.uc.mem_read(0x03001b5c, 40)) + bytes(o.uc.mem_read(0x03001d0c, 12))
        st_c = st.raw[36:40] + st.raw[0:36] + st.raw[40:52]
        if syn_u != bytes(syn_c) or pcm_u != bytes(pcm_c) or st_u != st_c:
            bad += 1
            if bad < 5:
                print('  audio mismatch at block', i, syn_u == bytes(syn_c), pcm_u == bytes(pcm_c), st_u == st_c)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vol', type=int, default=1)
    ap.add_argument('--video', type=int, default=60)
    ap.add_argument('--audio', type=int, default=4000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--state', default=os.path.join(HERE, '..', '..', 'tmp', 'fvmv', 'cap3', 'p1e1', 'pre_decode0.state'))
    a = ap.parse_args()
    rng = random.Random(a.seed)
    rom = open(ROMS % a.vol, 'rb').read()
    o = Oracle(a.vol, a.state)
    frames = real_frames(rom)
    res = {}
    for i in range(a.video):
        kind = ('random', 'mutate', 'real')[i % 3]
        if kind == 'random':
            p = random_payload(rng)
        else:
            v = rng.choice(frames)[0]
            p = mutate(v, rng) if kind == 'mutate' else v
        ok, msg = video_case(o, rng, p)
        res.setdefault(kind, [0, 0])[0 if ok else 1] += 1
        if not ok:
            print('video', kind, 'case', i, 'MISMATCH', msg)
    for k, (g, b) in res.items():
        print(f'video {k}: {g} ok, {b} mismatch')
    print('video coverage: (size, mode) pairs hit:', len(STATS.get('modes', ())), 'of 88 possible;',
          '64-byte EWRAM lines changed per case:', STATS.get('changed', 0) // max(a.video, 1))
    # audio: random blocks, then a run of real blocks
    blocks = [rng.randbytes(40) for _ in range(a.audio // 2)]
    bad = audio_run(o, rng, blocks)
    print(f'audio random: {len(blocks) - bad} ok, {bad} mismatch')
    ab = [f[1][k:k + 40] for f in frames[:a.audio // 16] for k in range(0, len(f[1]), 40)]
    bad = audio_run(o, rng, ab)
    print(f'audio real: {len(ab) - bad} ok, {bad} mismatch')


if __name__ == '__main__':
    main()
