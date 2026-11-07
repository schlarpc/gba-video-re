#!/usr/bin/env python3
"""Ground-truth capture for Hydrogen GBA Video carts in mGBA (libretro core with a PC-hook patch).

Boots the cart, and at the first pass through the player's top-level state dispatcher
(Thumb `ldrb r4,[r6]; adds r0,r4,#0; subs r0,#0x13; cmp r0,#7`) rewrites the state byte to
0x13 (PlayMovie), the chapter to -1 (start of stream) and the movie pointer to the chosen
container, so the cart's own PlayMovie() plays that stream from frame 0 with no menu input.

Per VQ blit (IWRAM blit entry, return address taken from LR) it records:
  vblank number, md5 of the 240x160 BGR555 framebuffer after the blit, md5 of the frame's
  index bytes, the block mode.
Per audio decode call (IWRAM ADPCM routine (ctx, dst, n)): vblank, n, md5 of the n output bytes.

Usage: capture.py ROM STREAM_INDEX OUT_PREFIX [--max-vblanks N] [--frames N]
Writes OUT_PREFIX.frames.csv and OUT_PREFIX.audio.csv (uncompressed; verify.py gzips manifests).
"""
import sys, os, struct, hashlib, ctypes as C, argparse, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import retro
import hycart

CORE = os.environ.get('HY_MGBA_CORE') or os.path.join(HERE, '..', '..', '..', 'tmp', 'mgba', 'hydrogen-cb', 'mgba_libretro.so')  # tools/mgba/mgba-prof.patch + mgba-cb-on-triage-prof.patch
SIG_DISPATCH = bytes.fromhex('3478201c13380728')
SIG_BLIT = bytes.fromhex('f05f2de90c4090e5004094e5844ea0e1a44ea0e1')
SIG_AUDIO = bytes.fromhex('f05f2de9345090e5054062e0344080e5')
BLOCKS = {0: 2400, 1: 2400, 2: 3180, 3: 3180, 4: 4240, 5: 4240, 6: 4800, 7: 4800}


def iwram_image(rom):
    t = struct.unpack_from('<I', rom, 0x18c)[0] + 0x18c
    for i in range(0, 0x30, 12):
        s, d, n = struct.unpack_from('<3I', rom, t + i)
        if d == 0x03000000: return s - 0x08000000, n
    raise ValueError('no IWRAM image')


def dispatcher(rom):
    """The player's top-level state dispatcher: `ldr rS,[pc,#i]; ldrb r4,[rS]` followed by either
    `adds r0,r4,#0; subs r0,#0x13; cmp r0,#7` (states 0x13..0x19) or `cmp r4,#7` (states 1..6).
    Returns (bp address of the ldrb, state byte address, movie-pointer address, PlayMovie state)."""
    import re
    for m in re.finditer(rb'([\x00-\xff][\x48-\x4f])([\x20-\x3f]\x78)(\x20\x1c\x13\x38\x07\x28|\x07\x2c)', rom[:0x40000], re.S):
        k = m.start()
        if k & 1: continue
        ldr = rom[k] | rom[k + 1] << 8; ldrb = rom[k + 2] | rom[k + 3] << 8
        rs = (ldr >> 8) & 7
        if (ldrb & 0xffc7) != 0x7804 or ((ldrb >> 3) & 7) != rs: continue
        lit = ((0x08000000 + k + 4) & ~3) + (ldr & 0xff) * 4
        state = struct.unpack_from('<I', rom, lit - 0x08000000)[0]
        play = 0x13 if m.group(3).startswith(b'\x20') else 1
        # function start: nearest preceding push {r4,r5,r6,lr} (b570); first ldr literal after it = globals base
        f = k - 2
        while not (rom[f + 1] == 0xb5 and rom[f] & 0x10): f -= 2      # push {r4,...,lr}
        g = f + 2
        while (rom[g] | rom[g + 1] << 8) & 0xf800 != 0x4800: g += 2   # first ldr rX,[pc,#i]
        l2 = rom[g] | rom[g + 1] << 8
        lit2 = ((0x08000000 + g + 4) & ~3) + (l2 & 0xff) * 4
        base = struct.unpack_from('<I', rom, lit2 - 0x08000000)[0]
        return 0x08000000 + k + 2, state, base + 4, play
    raise ValueError('dispatcher not found')


def addresses(rom):
    o, n = iwram_image(rom)
    img = rom[o:o + n]
    d, state, movie, play = dispatcher(rom)
    b = img.find(SIG_BLIT); a = img.find(SIG_AUDIO)
    assert b > 0 and a > 0, (b, a)
    return dict(dispatch=d, state=state, movie=movie, play=play, blit=0x03000000 + b, audio=0x03000000 + a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rom'); ap.add_argument('stream', type=int); ap.add_argument('out')
    ap.add_argument('--max-vblanks', type=int, default=0)
    ap.add_argument('--frames', type=int, default=0)
    ap.add_argument('--chapter', type=int, default=-1, help='start at this chapter (the player\'s own seek)')
    ap.add_argument('--states', default=None, help='dir: save a savestate after every K-th blit (with --state-every)')
    ap.add_argument('--state-every', type=int, default=0)
    a = ap.parse_args()
    rom = open(a.rom, 'rb').read()
    conts = hycart.find_containers(rom)
    cont = conts[a.stream]
    s = hycart.Stream(rom, cont['video'][0], cont['video'][1])
    nfr = a.frames or s.frames
    ad = addresses(rom)
    r = retro.Retro(a.rom, core=CORE)
    L = r.lib
    CB = C.CFUNCTYPE(None, C.c_int, C.POINTER(C.c_uint32))
    arr = (C.c_uint32 * 8).in_dll(L, 'retro_bp_addr')
    nbp = C.c_int.in_dll(L, 'retro_bp_n')
    st = dict(inj=0, frames=[], audio=[], pend=None, apend=None, done=False, err=None)
    ff = open(a.out + '.frames.csv', 'w'); fa = open(a.out + '.audio.csv', 'w')
    ff.write('blit,vblank,fb_md5,idx_md5,mode\n'); fa.write('call,vblank,n,md5\n')

    def cb(i, R):
        try:
            if i == 0:
                if st['inj'] == 0:
                    r.write(ad['state'], bytes([ad['play']])); r.write(ad['state'] + 4, struct.pack('<i', a.chapter))
                    r.write(ad['movie'], struct.pack('<I', 0x08000000 + cont['container']))
                    st['inj'] = r.frame_no
                else:
                    st['done'] = True     # PlayMovie returned to the dispatcher
            elif i == 1:                  # blit entry: r0 = StreamBase
                ctx = R[0]
                ip = struct.unpack('<I', r.read(ctx + 0x1030, 4))[0]
                seg = struct.unpack('<I', r.read(ctx + 0xc, 4))[0]
                mode = struct.unpack('<I', r.read(seg, 4))[0] & 7
                st['pend'] = (r.frame_no, hashlib.md5(r.read(ip, BLOCKS[mode])).hexdigest(), mode)
                arr[2] = R[14] & ~1
            elif i == 2:
                if st['pend'] is None: return
                vb, im, mode = st['pend']; st['pend'] = None; arr[2] = 0xffffffff
                fb = r.read(0x06000000, 240 * 160 * 2)
                k = len(st['frames'])
                st['frames'].append(1)
                ff.write(f'{k},{vb},{hashlib.md5(fb).hexdigest()},{im},{mode}\n')
                if len(st['frames']) >= nfr: st['done'] = True
            elif i == 3:                  # audio entry (ctx, dst, n)
                st['apend'] = (r.frame_no, R[1], R[2])
                arr[4] = R[14] & ~1
            elif i == 4:
                if st['apend'] is None: return
                vb, dst, n = st['apend']; st['apend'] = None; arr[4] = 0xffffffff
                k = len(st['audio']); st['audio'].append(1)
                fa.write(f'{k},{vb},{n},{hashlib.md5(r.read(dst, n)).hexdigest()}\n')
        except Exception as e:  # noqa
            import traceback; traceback.print_exc(); st['err'] = e; st['done'] = True

    cbf = CB(cb)
    C.c_void_p.in_dll(L, 'retro_bp_cb').value = C.cast(cbf, C.c_void_p).value
    arr[0] = ad['dispatch']; arr[1] = ad['blit']; arr[2] = 0xffffffff; arr[3] = ad['audio']; arr[4] = 0xffffffff
    nbp.value = 5
    C.c_int.in_dll(L, 'retro_prof_on').value = 0
    t0 = time.time()
    maxvb = a.max_vblanks or (nfr * 5 + 3000)
    while not st['done'] and r.frame_no < maxvb:
        r.run(60)
    ff.close(); fa.close()
    print(f'{os.path.basename(a.rom)} stream {a.stream}: {len(st["frames"])}/{nfr} blits, {len(st["audio"])} audio calls, '
          f'injected at vblank {st["inj"]}, ended at {r.frame_no}, {time.time() - t0:.0f}s, addrs ' +
          ' '.join(f'{k}={v:#x}' for k, v in ad.items()))
    return 1 if st['err'] else 0


if __name__ == '__main__':
    sys.exit(main())
