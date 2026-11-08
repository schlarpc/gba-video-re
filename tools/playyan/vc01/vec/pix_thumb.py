#!/usr/bin/env python3
"""Run the py_fw0 thumbnail path (scale 0x2749a mode 2 + colour conversion) on a decoded YUV420 frame
and hash the 60x40 result like the GBA does (protocol.md section 9)."""
import sys, struct, os
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import emu
from vc01v import VPU
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import workdir
FR = workdir.work('vec', 'frames') + '/'   # <NAME>_xvid.yuv from vec/prep_media.py
HASH = {'INSECT':0x4D5E5AEB,'AVOID':0xF8CB9008,'BAT':0xD911FE57,'CREDIT':0x02F86041,'JUMP':0x748C5BAF,'FIRE':0x0D78A9FE,
        'KEEPER':0x3FEBEFE5,'MEMORY':0x90119AE6,'NEKOROID':0xD839D809,'NOSE':0x86D3BA78,'FISH':0x5741DE47,
        'TRIPLE':0xD5C0A40B,'WAVE':0xE7DCD82B}

def ghash(px):
    h = 0
    for p in px:
        h = (h + p) & 0xffffffff
        h = ((h << 1) | (h >> 31)) & 0xffffffff
    return h

def mach():
    m = emu.Machine('py_fw0')
    m.cpu.vpu = VPU(m.cpu)
    for a, code in ((0x293f2, [0x6e10, 0x4430]), (0x29432, [0x6e00, 0x4430])):   # VRF semaphore stubs
        for k, h in enumerate(code): m.m.write(a + 2 * k, 2, h)
    return m

def desc(m, typ, w, h, pitch, size, ptr):
    b = bytearray(24); b[0] = typ
    struct.pack_into('<5I', b, 4, w, h, pitch, size, ptr)
    return m.alloc(b)

def thumbnail(m, yuv, W=240, H=176, swapuv=False, dw=60, dh=40, mode=2):
    ys = W * H; cs = ys // 4
    Y, U, V = yuv[:ys], yuv[ys:ys + cs], yuv[ys + cs:ys + 2 * cs]
    if swapuv: U, V = V, U
    hp = (H + 15) & ~15
    buf = bytes(Y) + bytes(W * (hp - H)) + bytes(U) + bytes(V) + bytes(256)
    sp = m.alloc(buf)
    src = desc(m, 3, W, H, W, len(buf) - 256, sp)
    dpitch = 2 * ((dw + 15) & ~15)
    dp = m.alloc(bytes(dpitch * dh + 256))
    dst = desc(m, 1, dw, dh, dpitch, dpitch * dh, dp)
    m.call(0x2749a, dst, src, mode, maxsteps=50_000_000)
    return [m.m.read(dp + y * dpitch + 2 * x, 2) for y in range(dh) for x in range(dw)]

if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if '=' not in a]
    cfg = dict(a.split('=') for a in sys.argv[1:] if '=' in a)
    swap = cfg.pop('swapuv', '0') == '1'; idct = cfg.pop('idct', 'xvid'); post = cfg.pop('post', '')
    names = args or sorted(HASH)
    ok = 0
    for n in names:
        m = mach()
        px = thumbnail(m, open(FR + '%s_%s.yuv' % (n, idct), 'rb').read(), swapuv=swap)
        if post == '555':
            px = [(p & 31) | ((p >> 6) & 31) << 5 | ((p >> 11) & 31) << 10 for p in px]
        if post == 'swap555':
            px = [((p >> 11) & 31) | ((p >> 6) & 31) << 5 | (p & 31) << 10 for p in px]
        h = ghash(px); ok += h == HASH[n]
        print('%-9s %08x %08x %s' % (n, h, HASH[n], 'MATCH' if h == HASH[n] else ''), flush=True)
    print('matched %d/%d' % (ok, len(names)))

def save_png(px, path, w=60, h=40, fmt='565'):
    from PIL import Image
    im = Image.new('RGB', (w, h))
    for i, p in enumerate(px):
        if fmt == '565':
            r, g, b = (p & 31) << 3, ((p >> 5) & 63) << 2, ((p >> 11) & 31) << 3
        else:
            r, g, b = (p & 31) << 3, ((p >> 5) & 31) << 3, ((p >> 10) & 31) << 3
        im.putpixel((i % w, i // w), (r, g, b))
    im.resize((w * 4, h * 4)).save(path)
