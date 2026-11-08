#!/usr/bin/env python3
"""py_fw0 0x1FEA0 (= mi_fw0/fup_sec0 0x344C0): MPEG-4 bitstream window expansion, the routine that
gates video in the real-firmware run.  It reads the stream pointer from the global at 0x20480,
expands 16 x 15 big-endian halfwords into every 16-bit window at each bit offset and writes
table[j][k] (16-bit, at 0x1dd80 + 0x1e0*it + 32*j + 2*k) = stream bits [16*(15*it+j-1)+k, +16).
Checks op24 (vshl, wrapping, as the funnel-shift partner of vlsr under ACC), ACC/CLRA, 8-bit byte
moves (byte swap), vld2/vld3 unaligned pair, vst vertical REP with a stride register.
Usage: bitstream_test.py [img]  (py_fw0 default)"""
import sys, os, random, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import emu
IMG = {'py_fw0': (0x1fea0, 0x20480, 0x1dd80), 'mi_fw0': (0x344c0, None, None), 'fup_sec0': (0x344c0, None, None)}


def main():
    img = sys.argv[1] if len(sys.argv) > 1 else 'py_fw0'
    fn, gptr, tab = IMG[img]
    m = emu.Machine(img)
    R = random.Random(1)
    ok = n = 0
    for t in range(12):
        stream = bytes(R.randrange(256) for _ in range(16 * 2 * 16 + 64))
        sa = 0xd00002 + 2 * R.randrange(8)               # halfword-aligned stream position
        m.m.load(sa - 2, stream)
        m.m.write(gptr, 4, sa)
        m.call(fn, 0x1234)
        bits = int.from_bytes(stream, 'big'); nb = 8 * len(stream)
        good = True
        for it in range(16):
            for j in range(15):
                for k in range(16):
                    pos = 16 * (15 * it + j) + k                    # stream starts at sa - 2
                    want = (bits >> (nb - pos - 16)) & 0xffff
                    got = m.m.read(tab + 0x1e0 * it + 32 * j + 2 * k, 2)
                    if got != want:
                        good = False
        ok += good; n += 1
        good_ptr = m.m.read(gptr, 4) == sa + 16 * 30
        ok += good_ptr; n += 1
    print('bitstream_1fea0  %d/%d  %s' % (ok, n, 'PASS' if ok == n else 'FAIL'))


if __name__ == '__main__':
    main()
