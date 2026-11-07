"""Cartridge layout helpers shared by the tools: where the player's RAM images are, and
which VX++ streams a ROM holds. Nothing here is specific to one cartridge.

The boot loader (ROM 0x200) finds program i by summing the size table at ROM 0x8e4 from
ROM 0xa00. Program 2 is the movie player. Its 0x44-byte load header lists the sections as
(dst, src_start, src_end) triples with src addresses linked at 0x02000000 = the program's
ROM offset, so the IWRAM image's ROM offset follows from the header instead of being a
constant (it is 0xbcc8 on every VX++ cartridge seen so far).
"""
import hashlib
import struct

MAGIC = b'VX++'
PROG_TABLE = 0x8e4
PROG_BASE = 0xa00
PLAYER_PROG = 2

# Player builds seen so far: sha1 of the player program image (ROM 0x8a00, 0x10000 bytes).
KNOWN_PLAYERS = {
    '7324ebf35a10601b8c09bcff008b76144e2734cb': 'VX++ player (Shrek, Shark Tale, Shrek 2 Rev 6; Shrek + Shark Tale Rev 5)',
}

# Cartridges checked against the emulator (docs/carts in README.md). sha1 of the whole ROM.
KNOWN_CARTS = {
    '95d612057682b7b886441d387b167ba2493e49ef': 'Shrek (USA) Rev 6',
    '9bc5b60793b8a6de3b80eb2c213cd125e1fa468e': 'Shark Tale (USA) Rev 6',
    '3bcb4acc5da539bc1b91c9537bf7ffa081ded1d4': 'Shrek 2 (USA) Rev 6',
    'f23390b7a62c605fbce6730addc29d381488e076': 'Shrek + Shark Tale (USA) Rev 5',
}


def player_layout(rom):
    """ROM offsets of the player program and its EWRAM/IWRAM images.
    Returns dict(prog, ewram=(rom_off, size), iwram=(rom_off, size), entry)."""
    sizes = struct.unpack_from('<3I', rom, PROG_TABLE)
    prog = PROG_BASE + sum(sizes[:PLAYER_PROG])
    w = struct.unpack_from('<17I', rom, prog)
    secs = {}
    for k in range(2, 16, 3):
        dst, s0, s1 = w[k], w[k + 1], w[k + 2]
        if dst == 0x02000000:            # the EWRAM section is (dst, size_end, entry): src = prog itself
            secs['ewram'] = (prog, s0 - 0x02000000)
        elif s1 > s0:
            secs.setdefault({0x03: 'iwram', 0x06: 'vram'}.get(dst >> 24, hex(dst)), (prog + s0 - 0x02000000, s1 - s0))
    if 'ewram' not in secs or 'iwram' not in secs:
        raise ValueError('cannot parse the player load header at ROM 0x%x' % prog)
    return dict(prog=prog, prog_size=sizes[PLAYER_PROG], ewram=secs['ewram'], iwram=secs['iwram'], entry=w[16])


def player_sha1(rom):
    L = player_layout(rom)
    return hashlib.sha1(rom[L['prog']:L['prog'] + L['prog_size']]).hexdigest()


def find_streams(rom):
    """Header offsets of all VX++ streams: the magic on a 0x200 boundary (the cart reads
    512-byte sectors) with plausible dimensions."""
    out, i = [], -1
    while True:
        i = rom.find(MAGIC, i + 1)
        if i < 0:
            return out
        if i % 0x200:
            continue
        n, w, h = struct.unpack_from('<3I', rom, i + 4)
        if 0 < n < 1 << 20 and 0 < w <= 240 and 0 < h <= 160 and not (w | h) & 15:
            out.append(i)


def parse_offset(s):
    return int(s, 0)


def pick_stream(rom, stream=None):
    """stream: None (first), a header offset, or a small index into find_streams()."""
    st = find_streams(rom)
    if not st:
        raise ValueError('no VX++ stream in this file')
    if stream is None:
        return st[0]
    if stream < len(st):
        return st[stream]
    if stream not in st:
        raise ValueError('no VX++ stream at 0x%x (streams: %s)' % (stream, ', '.join(hex(x) for x in st)))
    return stream
