#!/usr/bin/env python3
"""Reference model of the Majesco VQ codebook conversion (YUV -> dithered RGB555) and blit."""
import numpy as np

DIMS = [(4, 4), (4, 4), (4, 3), (4, 3), (3, 3), (3, 3), (4, 2), (4, 2)]


def dither_tables(dither):
    """T[k][v] -> 5-bit, k = position 0..15"""
    T = np.zeros((16, 256), np.uint8)
    for k in range(16):
        for v in range(256):
            T[k, v] = 31 if (v >> 3) >= 31 else (v >> 3) + (dither[k] < (v & 7))
    return T


def clamp_lut(brightness=256, extra=0):
    lut = np.array([min(255, (i * (brightness + extra)) >> 8) for i in range(256)], np.int32)
    def C(x):
        x = np.asarray(x)
        return np.where(x < 0, 0, np.where(x > 255, lut[255], lut[np.clip(x, 0, 255)]))
    return C


def unpredict(a):
    """running byte sum (mod 256) over the whole plane"""
    return (np.cumsum(np.frombuffer(bytes(a), np.uint8).astype(np.int64)) & 0xff).astype(np.uint8)


def codebook(raw, mode, n, dither, pos_tab, brightness=256):
    """raw = inflated codebook bytes. returns uint16 array [256][16] in the cart's VRAM entry layout
    (entry stride 16 halfwords for 4x4/3x3, 12 for 4x3, 8 for 4x2)."""
    bw, bh = DIMS[mode]
    nl = n * bw * bh
    Y = unpredict(raw[:nl]).astype(np.int32)
    nch = (2 if mode in (4, 5) else bh)
    U = unpredict(raw[nl:nl + 2 * n * nch]).view(np.int8).astype(np.int32)
    V = U[n * nch:]; U = U[:n * nch]
    T = dither_tables(dither); C = clamp_lut(brightness)
    stride = {0: 16, 1: 16, 2: 12, 3: 12, 4: 16, 5: 16, 6: 8, 7: 8}[mode]
    out = np.zeros((256, stride), np.uint16)
    for e in range(n):
        for r in range(bh):
            for c in range(bw):
                if mode in (4, 5):
                    pos = r * 4 + c; slot = r * 3 + c; ci = 0 if r < 2 else 1
                else:
                    pos = pos_tab[r * 4 + c]; slot = pos; ci = r
                y = Y[e * bw * bh + r * bw + c]
                u = U[e * nch + ci]; v = V[e * nch + ci]
                R = T[pos][C(y + 2 * v)]; G = T[pos][C(y - (u >> 1) - v)]; B = T[pos][C(y + 2 * u)]
                out[e, slot] = int(R) | (int(G) << 5) | (int(B) << 10)
    return out
