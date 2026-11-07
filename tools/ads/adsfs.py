"""SFCD archive and .mmstr resource container parsing (ADS family)."""
import struct

def u32(b, o): return struct.unpack_from("<I", b, o)[0]
def u16(b, o): return struct.unpack_from("<H", b, o)[0]

def find_sfcd(rom, limit=0x200000):
    i = rom.find(b"SFCD", 0, limit)
    return None if i < 0 else i

def parse_sfcd(rom, base=None):
    if base is None:
        base = find_sfcd(rom)
    hdr_len, w2, n = u16(rom, base + 4), u16(rom, base + 6), u32(rom, base + 8)
    data = base + 8 + hdr_len
    ents, o = [], base + 12
    for _ in range(n):
        size, off = u32(rom, o), u32(rom, o + 8 - 4)
        e = rom.index(b"\0", o + 8)
        name = rom[o + 8:e].decode("latin1")
        o = (e + 4) & ~3
        ents.append(dict(name=name, size=size, off=off, rom=data + off))
    return dict(base=base, hdr_len=hdr_len, w2=w2, count=n, data=data, entries=ents, end=o)

def parse_mmstr(buf, off=0, size_in_bytes=False):
    total, cnt = u32(buf, off), u32(buf, off + 4)
    res, o = [], off + 8
    while o < off + total:
        w = u32(buf, o)
        typ, sz = w >> 28, (w & 0x0FFFFFFF)
        n = sz if size_in_bytes else sz * 4
        res.append(dict(type=typ, off=o + 4, size=n, hdr=w))
        o += 4 + n
    return dict(total=total, count_word=cnt, resources=res, end=o)

# substitution alphabet used inside .mmstr text and titles (filled in from the player code)
def deobf(bs):
    out = []
    for c in bs:
        if 1 <= c <= 10: out.append(chr(ord('0') + c - 1))
        elif 11 <= c <= 36: out.append(chr(ord('a') + c - 11))
        elif 37 <= c <= 62: out.append(chr(ord('A') + c - 37))
        elif c == 0xFF: out.append(' ')
        elif c == 0xFE: out.append('\n')
        else: out.append('{%02x}' % c)
    return "".join(out)
