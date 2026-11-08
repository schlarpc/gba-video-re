#!/usr/bin/env python3
"""Find the Play-Yan VC01 firmware images in the user's files (shared by the table generators).
Sources: the Play-Yan ROM (fw0/fw1 raw at 0x29710 / 0x5B258), the Play-Yan Micro ROM (LZ77 chunk
lists at 0xE7898 / 0xE7D34), playan.fup (sections, check bytes stripped), or a raw image.
Images are identified by md5 only."""
import hashlib, sys

IMAGES = {
    '19cee6751b736a086819dd352d57f99f': 'py_fw0', '904dbf90365f8ac1cdfb507f04d0a302': 'py_fw1',
    '09df03a96a904fb472ad733c03e33eb3': 'mi_fw0', '390ce952b1c269930628f2ca27ae3511': 'mi_fw1',
    'cdb23583f909d7920f4f37d6e9a91f2e': 'fup_sec0', '3951130fb9c128bf3035852b51e2a732': 'fup_sec1',
}


def lz77(src):
    assert src[0] == 0x10
    size = src[1] | src[2] << 8 | src[3] << 16
    out = bytearray(); p = 4
    while len(out) < size:
        flags = src[p]; p += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if not (flags >> bit) & 1:
                out.append(src[p]); p += 1
            else:
                b0, b1 = src[p], src[p + 1]; p += 2
                n = (b0 >> 4) + 3; d = ((b0 & 15) << 8 | b1) + 1
                for _ in range(n):
                    out.append(out[-d])
    return bytes(out[:size])


def candidates(b):
    for o, n in ((0x29710, 0x31B48), (0x5B258, 0x3F66C)):
        yield b[o:o + n]
    for lst in (0xE7898, 0xE7D34):
        try:
            img = bytearray(); p = lst
            while True:
                ptr = int.from_bytes(b[p:p + 4], 'little')
                if ptr == 0:
                    break
                img += lz77(b[ptr - 0x0C000000:]); p += 4
                if len(img) > 0x100000:
                    break
            yield bytes(img)
        except Exception:
            pass
    if b.startswith(b'Nintendo Playan firmware update program'):
        for e in range(b[0x3E]):
            ent = b[0x40 + 16 * e:0x50 + 16 * e]
            off = int.from_bytes(ent[0:4], 'little'); size = int.from_bytes(ent[4:8], 'little')
            out = bytearray(); p = off
            while len(out) < size:
                out += b[p:p + min(0x200, size - len(out))]; p += 0x201
            yield bytes(out)
    if len(b) <= 0x100000:
        yield b


def images(paths):
    found = {}
    for path in paths:
        b = open(path, 'rb').read()
        for c in candidates(b):
            name = IMAGES.get(hashlib.md5(c).hexdigest())
            if name and name not in found:
                found[name] = c
    return found


def table(img, off, n, md5):
    t = img[off:off + n]
    if hashlib.md5(t).hexdigest() != md5:
        sys.exit('table at 0x%x: md5 mismatch' % off)
    return t
