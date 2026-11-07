"""Bit-exact reimplementation of the player's YUV420 -> BGR555 blit (IWRAM 0x03000370,
ROM copy at file offset 0xBCC8+0x370). Clamp table lives at IWRAM 0x030001F0."""
import numpy as np

# Clamp/quantise table T[i] at 0x030001F0+i, extracted from IWRAM (index range used: -260..509).
_IW = None


def table(iwram=None):
    """Returns (T, base) with T a numpy uint8 array of IWRAM bytes 0x03000000..0x03000400."""
    global _IW
    if iwram is not None:
        _IW = np.frombuffer(iwram[:0x400], np.uint8)
    if _IW is None:
        import os
        rom = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shrek.gba"), "rb").read()
        # IWRAM image is copied from ROM 0xBCC8; the table area is static data in the image
        _IW = np.frombuffer(rom[0xBCC8:0xBCC8 + 0x400], np.uint8)
    return _IW, 0x1F0


def yuv_to_bgr555(slot, iwram=None):
    """slot: 0xF000 bytes (Y 256x160 stride 256, UV interleaved 256x80). Returns uint16[160,240]."""
    T, base = table(iwram)
    b = np.frombuffer(slot, np.uint8)
    Y = b[:0xA000].reshape(160, 256)[:, :240].astype(np.int32)
    UV = b[0xA000:0xF000].reshape(80, 256)[:, :240].astype(np.int32)
    u = UV[:, 0::2] - 128   # first byte of each pair -> blue
    v = UV[:, 1::2] - 128   # second byte -> red
    u = np.repeat(np.repeat(u, 2, 0), 2, 1)
    v = np.repeat(np.repeat(v, 2, 0), 2, 1)
    yy, xx = np.mgrid[0:160, 0:240]
    Yd = Y - 4 * ((xx + yy) & 1)            # checkerboard ordered dither
    R = T[base + 2 * v + Yd]
    B = T[base + 2 * u + Yd]
    G = T[base - (v + (u >> 1)) + Yd]
    return (R.astype(np.uint16) | (G.astype(np.uint16) << 5) | (B.astype(np.uint16) << 10))
