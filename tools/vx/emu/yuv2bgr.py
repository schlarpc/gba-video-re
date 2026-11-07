"""Bit-exact reimplementation of the player's YUV420 -> BGR555 blit (IWRAM 0x03000370,
ROM copy at the IWRAM image + 0x370). Clamp table lives at IWRAM 0x030001F0. Its upper end
reads the blitter's own `mov sl,#height` / `mov sl,#width` (0x38c / 0x394), which player_run
patches per stream, so the result depends on the picture size (see reference/vx/decoder/vxdec.c)."""
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
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "..", "..", "tools", "vx", "container"))
        import vxcart
        rom = open(os.path.join(here, "..", "..", "..", "shrek.gba"), "rb").read()
        off = vxcart.player_layout(rom)["iwram"][0]   # the table area is static data in the image
        _IW = np.frombuffer(rom[off:off + 0x400], np.uint8)
    return _IW, 0x1F0


def yuv_to_bgr555(slot, iwram=None, width=240, height=160):
    """slot: 0xF000 bytes (Y 256x160 stride 256, UV interleaved 256x80). Returns uint16[height,width]."""
    T, base = table(iwram)
    T = T.copy()
    T[0x38C:0x390] = np.frombuffer((0xE3A0A000 | height).to_bytes(4, "little"), np.uint8)
    T[0x394:0x398] = np.frombuffer((0xE3A0A000 | width).to_bytes(4, "little"), np.uint8)
    b = np.frombuffer(slot, np.uint8)
    Y = b[:0xA000].reshape(160, 256)[:height, :width].astype(np.int32)
    UV = b[0xA000:0xF000].reshape(80, 256)[:height // 2, :width].astype(np.int32)
    u = UV[:, 0::2] - 128   # first byte of each pair -> blue
    v = UV[:, 1::2] - 128   # second byte -> red
    u = np.repeat(np.repeat(u, 2, 0), 2, 1)
    v = np.repeat(np.repeat(v, 2, 0), 2, 1)
    yy, xx = np.mgrid[0:height, 0:width]
    Yd = Y - 4 * ((xx + yy) & 1)            # checkerboard ordered dither
    R = T[base + 2 * v + Yd].astype(np.uint32)
    B = T[base + 2 * u + Yd].astype(np.uint32)
    G = T[base - (v + (u >> 1)) + Yd].astype(np.uint32)
    # The blitter adds the fields (T can exceed 31 past the clamp range) into one 32-bit
    # store per pixel pair, so carries cross fields and run from the even pixel into the odd one.
    px = R + (G << 5) + (B << 10)
    word = (px[:, 0::2] + (px[:, 1::2] << 16)) & 0xFFFFFFFF
    out = np.empty((height, width), np.uint16)
    out[:, 0::2] = word & 0xFFFF
    out[:, 1::2] = word >> 16
    return out
