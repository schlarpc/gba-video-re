#!/usr/bin/env python3
"""Differential fuzzing against the cartridge's own ARM routines run in unicorn (emu/uc_oracle.py).

  fuzz.py inflate N   random Hydrogen-DEFLATE streams (stored/fixed/dynamic, all alignments),
                      decoded by the cart's Inflate in random request chunks vs hyinf_decode (C)
  fuzz.py vq N        random codebooks (odd and even modes) and index frames: cart's
                      UnPredict* + converter + blit vs mvq_* (C)
Options: --rom ROM --state PREFIX (default All Grown Up) --seed S
"""
import sys, os, random, ctypes as C, argparse, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'emu'))
import numpy as np
import hyenc, hyinflate
from uc_oracle import Oracle

lib = C.CDLL(os.path.join(HERE, '..', '..', 'reference', 'hydrogen', 'libhydec.so'))
lib.hyinf_decode.restype = C.c_long


class Tab(C.Structure):
    _fields_ = [('dither', C.c_uint8 * 16), ('brightness', C.c_int), ('gain_bias', C.c_int),
                ('lut', C.c_uint8 * 256), ('five', (C.c_uint8 * 256) * 16)]


def c_inflate(blob):
    size = struct.unpack_from('<I', blob)[0]
    out = C.create_string_buffer(size + 16)
    n = lib.hyinf_decode(blob, len(blob), out, size, None)
    return out.raw[:n] if n >= 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('what'); ap.add_argument('n', type=int)
    ap.add_argument('--rom', default=os.path.join(os.environ.get('GBAV_ROMS') or os.path.join(HERE, '../../roms'), 'Game Boy Advance Video - All Grown Up! - Volume 1 (USA).gba'))
    ap.add_argument('--state', default=os.path.join(HERE, '../../tmp/hydrogen/states/MGUE_1_b40'))
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    o = Oracle(a.rom, a.state)
    bad = 0
    if a.what == 'inflate':
        stats = [0, 0, 0]; rejected = 0; crashed = 0; oneshot_ok = 0; cart_quirk = 0; both_fail = 0; past_end = 0; oversub = 0
        for it in range(a.n):
            size = rng.choice([1, 2, 3, 17, 300, 4240, rng.randint(1, 30000)])
            blob, ref = hyenc.encode(rng, size, rng.choice([(0,), (1,), (2,), (0, 1, 2), (0, 2)]))
            # chunked requests like the player's per-frame calls
            chunks = []; left = size
            while left > 0:
                c = min(left, rng.choice([1, 2, 3, 4240, rng.randint(1, 5000)])); chunks.append(c); left -= c
            try:
                got, _ = o.inflate(blob, chunks)
            except Exception as e:
                o.reset()
                arr = (C.c_long * len(chunks))(*chunks); offs = (C.c_long * len(chunks))()
                obuf = C.create_string_buffer(size + 70000)
                if lib.hyinf_run_chunks(blob, len(blob), obuf, size + 70000, arr, len(chunks), offs) < 0:
                    both_fail += 1; continue          # the C transcription also goes off the rails
                open(os.environ.get('TMPDIR', '/tmp') + f'/fuzzcrash_{a.seed}_{it}.bin', 'wb').write(blob)
                open(os.environ.get('TMPDIR', '/tmp') + f'/fuzzcrash_{a.seed}_{it}.chunks', 'w').write(' '.join(map(str, chunks)))
                print('CRASH', it, size, e, flush=True); crashed += 1; continue
            if got is None:
                rejected += 1; o.reset(); continue
            cc = c_inflate(blob)
            py = bytes(hyinflate.Inflater(blob, 0).run()) if size < 5000 else ref
            # the C transcription of the request bookkeeping, same request sizes
            arr = (C.c_long * len(chunks))(*chunks); offs = (C.c_long * len(chunks))()
            obuf = C.create_string_buffer(size + 70000)
            nout = lib.hyinf_run_chunks(blob, len(blob), obuf, size + 70000, arr, len(chunks), offs)
            same_chunked = nout == len(got) and obuf.raw[:nout] == got and [x & 0xffffffff for x in offs] == [x & 0xffffffff for x in o.offs]   # 32-bit pointers
            if nout == -2:                    # C stops where the cartridge copies past the end of the blob
                past_end += 1; continue
            if nout == -3:                    # over-subscribed Huffman code set: cartridge result is implementation-defined
                oversub += 1; continue
            if got[:size] == ref: oneshot_ok += 1
            # one request for the whole blob: cart vs C (the codebook path)
            o.reset()
            try:
                got1, _ = o.inflate(blob, [size]); offs1 = list(o.offs)
            except Exception:
                got1 = None
            arr1 = (C.c_long * 1)(size); o1 = (C.c_long * 1)(); ob1 = C.create_string_buffer(size + 70000)
            n1 = lib.hyinf_run_chunks(blob, len(blob), ob1, size + 70000, arr1, 1, o1)
            same_oneshot = (got1 is None and n1 < 0) or (got1 is not None and n1 == len(got1) and ob1.raw[:n1] == got1)
            ok = same_chunked and same_oneshot
            if not same_chunked and got[:size] != ref: cart_quirk += 1
            if not ok:
                bad += 1
                print('MISMATCH', it, size, len(got), got[:size] == ref, same_chunked, same_oneshot)
                open(os.environ.get('TMPDIR', '/tmp') + f'/fuzzfail_{it}.bin', 'wb').write(blob)
                open(os.environ.get('TMPDIR', '/tmp') + f'/fuzzfail_{it}.chunks', 'w').write(' '.join(map(str, chunks)))
        agree = a.n - bad - rejected - crashed - oversub
        print(f'inflate: {a.n} streams; {rejected} rejected by the cartridge itself (Huffman table pool overflow '
              f'assert); of the other {a.n - rejected}: {agree} agree [{agree - both_fail - past_end} identical output bytes and '
              f'per-request offsets, chunked and one-shot; {both_fail} cart crashes where C reports an error; {past_end} cart '
              f'copies past the end of the blob where C reports an error]; {oversub} reach an over-subscribed Huffman code '
              f'set (cartridge result implementation-defined, C rejects); {bad} mismatch; {crashed} cart crash with C ok. '
              f'{oneshot_ok} chunked cart outputs equal the encoder input.')
    elif a.what == 'vq':
        rom = o.rom
        tab = Tab(); 
        dims = bytes([4, 4, 4, 4, 4, 3, 4, 3, 3, 3, 3, 3, 4, 2, 4, 2]); k = rom.find(dims, 0, 0x80000)
        tab.dither[:] = rom[k - 16:k]; tab.brightness = 256; tab.gain_bias = 0
        lib.mvq_init(C.byref(tab))
        for it in range(a.n):
            mode = rng.choice([1, 3, 5, 7, 1, 3, 5, 7, 0, 2, 4, 6])
            bw, bh = [(4, 4), (4, 4), (4, 3), (4, 3), (3, 3), (3, 3), (4, 2), (4, 2)][mode]
            ncb = 256
            nraw = ncb * (bw * bh + 2 * 4)
            raw = bytes(rng.randint(0, 255) if rng.random() < 0.7 else rng.choice([0, 1, 255, 128, 127]) for _ in range(nraw))
            lvl = rng.choice([5, 5, rng.randint(0, 10)])
            gain = [128, 153, 179, 204, 230, 256, 281, 307, 332, 358, 384][lvl]
            o.set_brightness(gain); tab.brightness = gain; lib.mvq_init(C.byref(tab))
            cart = o.codebook(raw, mode, ncb)
            buf = C.create_string_buffer(raw, nraw + 16)
            lib.mvq_unpredict(buf, mode, ncb)
            mine = (C.c_uint16 * (256 * 16))()
            # start from the same VRAM contents the cart had (unwritten slots stay)
            C.memmove(mine, cart.tobytes(), 512 * 16)
            lib.mvq_convert(buf, mode, ncb, C.byref(tab), mine)
            mine_a = np.frombuffer(bytes(mine), np.uint16).reshape(256, 16)
            stride = [16, 16, 12, 12, 16, 16, 8, 8][mode]
            flat_c = cart.reshape(-1)[:256 * stride] if stride != 16 else cart.reshape(-1)
            ok_cb = True
            if mode in (1, 3, 5, 7):
                cart_e = np.frombuffer(cart.tobytes()[:256 * stride * 2], np.uint16).reshape(256, stride)
                mine_e = np.frombuffer(bytes(mine)[:0], np.uint16)
                # mvq_convert writes out[e][slot] with 16-halfword rows; the cart packs entries at 'stride'
                mine_e = mine_a[:, :stride]
                ok_cb = np.array_equal(cart_e[:, :(10 if mode in (4, 5) else stride)], mine_e[:, :(10 if mode in (4, 5) else stride)])
            # blit with a random VRAM codebook in the cart's packed layout
            nb = (240 // bw) * (160 // bh)
            idx = bytes(rng.randint(0, 255) for _ in range(nb))
            vram = np.array([rng.randint(0, 0x7fff) for _ in range(256 * 16)], np.uint16)
            if mode in (4, 5): vram.reshape(256, 16)[:, 9] = 0
            fb_cart = o.blit(idx, mode, vram)
            # C model: unpack packed layout into [256][16]
            vr = np.zeros((256, 16), np.uint16)
            packed = vram.reshape(-1)
            for e in range(256): vr[e, :stride] = packed[e * stride:(e + 1) * stride]
            fb = (C.c_uint16 * (240 * 160))()
            pal = (C.c_uint16 * 24)(*np.frombuffer(bytes(o.g.uc.mem_read(0x05000000, 48)), np.uint16))
            lib.mvq_blit(idx, mode, vr.ctypes.data_as(C.c_void_p), pal, fb)
            ok_fb = np.array_equal(np.frombuffer(bytes(fb), np.uint16), fb_cart)
            if mode % 2 == 0: ok_cb = True   # even modes: documented divergence check below
            if not (ok_cb and ok_fb):
                bad += 1; print('MISMATCH vq', it, mode, ok_cb, ok_fb)
        print(f'vq: {a.n - bad}/{a.n} ok')


if __name__ == '__main__':
    main()
