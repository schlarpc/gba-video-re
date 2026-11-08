#!/usr/bin/env python3
"""G.726 oracle: run the Play-Yan video firmware's ASF audio payload decoder (py_fw0 0x1234e:
state init 0x122e4, MSB-first unpacking, decoders 0x3e6 / 0x5ae / 0x7d6 / 0xa56) in the VC01
emulator and write what it produces, for the Rust test `g726_vs_firmware` (PY_G726_ORACLE=file)
and the C reference (reference/playyan/g726/test).

usage: oracle.py OUT [ASF]   -- random payloads at 16/24/32/40 kbit/s, plus the first audio
payloads of ASF (G.726-32) if given.  OUT format, one case per line:
  bits hex(payload) hex(int16 samples, little-endian)   (the stream state carries across lines
  until a line with bits 0 resets it)."""
import sys, random, struct
import os
WORK = os.environ.get('PLAYYAN_WORK', '')  # the local emulator tree (not in this repository)
ISA = WORK + '/firmware/isa'
sys.path.insert(0, ISA)
import g726_vemu as V
import emu

DECODE, INIT = 0x1234e, 0x122e4


def asf_audio_payloads(path, n):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'video'))
    import subprocess, os
    # use the Rust parser through a tiny shim would be nicer; parse with a minimal python ASF walker
    from asf_py import audio_payloads
    return audio_payloads(path)[:n]


def main():
    out = open(sys.argv[1], 'w')
    m = V.machine('py_fw0')
    ctx = m.alloc(bytes(128)); inb = m.alloc(bytes(4096)); outb = m.alloc(bytes(4096 * 8 * 4 // 2 + 64)); stb = m.alloc(bytes(16))
    R = random.Random(726)

    def run_stream(bits, payloads):
        m.call(INIT, ctx)
        m.m.write(ctx + 92, 4, 0x726dec)
        m.m.write(ctx + 96, 4, bits)
        out.write('0 - -\n')
        for p in payloads:
            n = len(p) * 8 // bits
            m.m.load(inb, p)
            m.m.write(ctx + 100, 4, n)
            m.call(DECODE, ctx, inb, outb, stb)
            words = struct.unpack('<%dI' % n, bytes(m.m.read(outb + k, 1) for k in range(4 * n)))
            assert all((w & 0xffff) == (w >> 16) for w in words), 'mono words not duplicated'
            pcm = struct.pack('<%dh' % n, *[((w & 0xffff) ^ 0x8000) - 0x8000 for w in words])
            out.write('%d %s %s\n' % (bits, p.hex(), pcm.hex()))
            out.flush()

    for bits in (2, 3, 4, 5):
        for seed in range(2):
            pl = []
            for k in range(12):
                L = R.choice([3, 15, 30, 60, 120])
                if R.random() < 0.3:      # bursts of extreme codes drive the adaptation hard
                    pl.append(bytes(R.choice([0x00, 0xff, 0x77, 0x88, 0x7f]) for _ in range(L)))
                else:
                    pl.append(bytes(R.randrange(256) for _ in range(L)))
            run_stream(bits, pl)
    if len(sys.argv) > 2:
        run_stream(4, asf_audio_payloads(sys.argv[2], 40))
    print('wrote', sys.argv[1])


if __name__ == '__main__':
    main()
