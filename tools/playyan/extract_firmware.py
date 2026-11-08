#!/usr/bin/env python3
"""Extract the cartridges' firmware images from your own dumps into $PLAYYAN_WORK/firmware/extract/.

  extract_firmware.py [--roms DIR] [--force]

Sources in DIR (default $PY_ROMS, then $GBAV_ROMS): Play-Yan (Japan).gba, Play-Yan Micro (Japan).gba,
Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba and playan.fup, under those names. A missing source
only skips its images. Each image is written only if its sha1 is the one in docs/playyan/firmware.md:

  py_fw0, py_fw1, py_fwldr   Play-Yan: raw in the ROM (docs/playyan/firmware.md 1.1)
  mi_fw0, mi_fw1, mi_fwldr   Play-Yan Micro: GBA-BIOS LZ77 chunk lists (1.2)
  fup_sec0, fup_sec1         playan.fup v1.11: its two sections, check bytes removed (1.3)
  nmp_boottable              Nintendo MP3 Player: the C54x boot table at ROM 0x0A8F34 (6)

The VC01 tools load images by these names; the loaders are the images as stored (the GBA uploads
bytes 0x200.. to VC 0xC0200, which the tools do themselves).
"""
import argparse
import hashlib
import os
import struct
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import workdir  # noqa: E402

SHA1 = {
    'py_fw0': 'ce983ce7bac58ff438806247702438ec7b97d742',
    'py_fw1': 'ede92003b9aec9d18d17707d95cb61bb21444922',
    'py_fwldr': 'e351f78d491c60c0ef080475ff255f4519fc82b5',
    'mi_fw0': 'f7533448ece8db54718ed290110168831a9c3a3e',
    'mi_fw1': '30c85e025df51bbc1e52120e788d6784967bbfdf',
    'mi_fwldr': '5800cbb96a93f30067e778483f2e126e5d9536a2',
    'fup_sec0': '084ad575a77dc8a2cbab876b7feb4c1548562a58',
    'fup_sec1': 'f04e7558b07cca2b72ef6a558e3aadc0339e0ef5',
    'nmp_boottable': 'd0daecfbb18db148df0b291f8cb8d40bfcca2662',
}
FUP_SHA1 = '79289f11d2c1b85d6f8363000d3c7916fdc2ada7'


def lz77(src, p):
    """GBA BIOS LZ77 (SWI 0x11) stream at src[p:]."""
    hdr = struct.unpack_from('<I', src, p)[0]
    if hdr & 0xFF != 0x10:
        raise ValueError('not LZ77 at 0x%x' % p)
    size = hdr >> 8
    out = bytearray()
    p += 4
    while len(out) < size:
        flags = src[p]
        p += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                x = src[p] << 8 | src[p + 1]
                p += 2
                n, disp = (x >> 12) + 3, (x & 0xFFF) + 1
                for _ in range(n):
                    out.append(out[-disp])
            else:
                out.append(src[p])
                p += 1
    return bytes(out[:size])


def chunk_list(rom, table):
    """A NUL-terminated list of ROM pointers (0x0C000000-based) to LZ77 chunks, decoded and joined."""
    img = bytearray()
    while True:
        ptr = struct.unpack_from('<I', rom, table)[0]
        table += 4
        if not ptr:
            return bytes(img)
        img += lz77(rom, ptr - 0x0C000000)


def fup_sections(fup):
    """playan.fup: 0x40-byte header (entry count at +0x3E), 16-byte entries (offset, size, ...), data in
    0x200-byte blocks each followed by a check byte."""
    if not fup.startswith(b'Nintendo Playan firmware update program'):
        raise ValueError('not playan.fup')
    for e in range(fup[0x3E]):
        off, size = struct.unpack_from('<II', fup, 0x40 + 16 * e)
        out = bytearray()
        p = off
        while len(out) < size:
            out += fup[p:p + min(0x200, size - len(out))]
            p += 0x201
        yield bytes(out)


def sources(romdir):
    def read(key):
        p = os.path.join(romdir, workdir.ROM_NAMES[key])
        if not os.path.exists(p):
            print('  (no %s)' % p)
            return None
        return open(p, 'rb').read()

    py = read('orig')
    if py:
        py = py[:0x100000]
        for name, off, n in (('py_fw0', 0x29710, 0x31B48), ('py_fw1', 0x5B258, 0x3F66C), ('py_fwldr', 0x9A8C4, 0x123C0)):
            yield name, py[off:off + n]
    mi = read('micro')
    if mi:
        mi = mi[:0x100000]
        for name, table in (('mi_fw0', 0xE7898), ('mi_fw1', 0xE7D34), ('mi_fwldr', 0xE8178)):
            yield name, chunk_list(mi, table)
    fup = read('fup')
    if fup:
        if hashlib.sha1(fup).hexdigest() != FUP_SHA1:
            print('  playan.fup: sha1 %s is not v1.11 (%s); trying anyway' % (hashlib.sha1(fup).hexdigest(), FUP_SHA1))
        for i, sec in enumerate(fup_sections(fup)):
            yield 'fup_sec%d' % i, sec
    nmp = read('nmp')
    if nmp:
        yield 'nmp_boottable', nmp[0xA8F34:0xA8F34 + 0xF730 * 2]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--roms', default=workdir.roms_dir())
    ap.add_argument('--force', action='store_true', help='rewrite images that are already there')
    a = ap.parse_args()
    os.makedirs(workdir.FIRMWARE, exist_ok=True)
    print('sources: %s\noutput:  %s' % (a.roms, workdir.FIRMWARE))
    bad = 0
    got = set()
    for name, data in sources(a.roms):
        h = hashlib.sha1(data).hexdigest()
        out = os.path.join(workdir.FIRMWARE, name + '.bin')
        if h != SHA1[name]:
            print('  %-14s sha1 %s, expected %s: NOT written' % (name, h, SHA1[name]))
            bad += 1
            continue
        got.add(name)
        if os.path.exists(out) and not a.force and hashlib.sha1(open(out, 'rb').read()).hexdigest() == h:
            print('  %-14s ok (already there)' % name)
            continue
        with open(out, 'wb') as f:
            f.write(data)
        print('  %-14s %7d bytes  sha1 %s' % (name, len(data), h))
    missing = sorted(set(SHA1) - got)
    if missing:
        print('missing: %s' % ', '.join(missing))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
