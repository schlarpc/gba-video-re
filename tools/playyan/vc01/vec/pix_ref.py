#!/usr/bin/env python3
"""Python reference of the py_fw0 thumbnail path as derived from the vector code:
crop/fit (0x2749a mode 2) -> 4:1 box scaler (vertical 0x28f4e, horizontal 0x2868e) -> YUV420->RGB565 (0x284a0).
Variant knobs are module globals so the hash search can try alternatives quickly."""
OP16 = 'floor'     # (a*b)>>8 ; 'round': (a*b+128)>>8
OP21 = 'round'     # (a*u16(b) + 0x8000)>>16 [confirmed by the Garage hashes] ; 'floor'
HSUM = 'perlane'   # horizontal box: sum of per-lane (p*recip)>>16 ; 'total': (sum*recip)>>16
FMT = '565'

def op16(a, b):
    return (a * b + (128 if OP16 == 'round' else 0)) >> 8

def op21(a, b):
    return (a * b + (0x8000 if OP21 == 'round' else 0)) >> 16

def sat(v, lo, hi): return lo if v < lo else hi if v > hi else v

RECIP = [0, 65536, 32768, 21845, 16384, 13107, 10923, 9362, 8192, 7282, 6554, 5958, 5461, 5041, 4681, 4369, 4096]

def box(plane, W, y0, rows, n):
    """plane: bytes W wide; take rows y0..y0+rows*n, n:1 in both directions"""
    out = []
    for oy in range(rows):
        v = [op21(sum(plane[(y0 + n * oy + i) * W + x] for i in range(n)), RECIP[n]) for x in range(W)]
        for ox in range(W // n):
            if HSUM == 'perlane':
                s = sum(op21(v[n * ox + j], RECIP[n]) for j in range(n))
            else:
                s = op21(sum(v[n * ox + j] for j in range(n)), RECIP[n])
            out.append(min(255, s))
    return out

def convert(Y, U, V, w, h):
    px = []
    for y in range(h):
        for x in range(w):
            yy = op16(Y[y * w + x] - 16, 298)
            u = U[(y // 2) * (w // 2) + x // 2] - 128; v = V[(y // 2) * (w // 2) + x // 2] - 128
            g = sat(op16(u, -100) + op16(v, -200), -32768, 32767)
            lo = sat(yy + op16(u, 408), 0, 255); hi = sat(yy + op16(v, 516), 0, 255); gg = sat(yy + g, 0, 255)
            px.append((lo >> 3) | ((hi & 0xf8) << 8) | ((gg >> 2) << 5))
    return px

def thumbnail(yuv, W=240, H=176, swapuv=False):
    ys = W * H; cs = ys // 4
    Y, U, V = yuv[:ys], yuv[ys:ys + cs], yuv[ys + cs:ys + 2 * cs]
    if swapuv: U, V = V, U
    Yt = box(Y, W, 8, 40, 4); Ut = box(U, W // 2, 4, 20, 4); Vt = box(V, W // 2, 4, 20, 4)
    return convert(Yt, Ut, Vt, 60, 40)


def rgb565_to_bgr555(p):
    """GBA-side view: the cartridge sends RGB565 (R = bits 15..11); the Garage hashes match after
    this field swap.  The swap is done by the cartridge bridge on the WIN_R1 pixel window, not by the
    GBA: the GBA hashes (0x0C019410) and blits the raw window halfwords (docs/playyan/protocol.md 8.2; emu/win1_test.py)."""
    return ((p >> 11) & 31) | ((p >> 6) & 31) << 5 | (p & 31) << 10
