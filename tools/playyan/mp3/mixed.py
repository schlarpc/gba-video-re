"""Mixed-block experiments (LAME never writes mixed blocks).
  python3 mixed.py craft IN.mp3 OUT.mp3 [first last]   set mixed_block_flag on every short-block
      granule of frames first..last (side-info bit edit; scalefactor layout then differs, which
      only changes what the main data decodes to)
  python3 mixed.py harness OUT.mp3   run the firmware decoder on the emulator core and report, per
      mixed granule, where the reorder's out-of-range reads land (0x2c52a source index starts at
      576: tmp[576..1115] = sp+2316..sp+4475 of that function's frame)"""
import sys, struct
from fwmp3 import FwMp3, frames

def side_bits(d, off):
    h = int.from_bytes(d[off:off + 4], 'big'); p = off + 4 + (0 if (h >> 16) & 1 else 2)
    mode = (h >> 6) & 3; nch = 1 if mode == 3 else 2
    pos = 9 + (5 if nch == 1 else 3) + 4 * nch
    out = []
    for gr in range(2):
        for ch in range(nch):
            q = pos + 12 + 9 + 8 + 4
            ws = (int.from_bytes(d[p:p + 32], 'big') >> (256 - q - 1)) & 1
            if ws:
                bt = (int.from_bytes(d[p:p + 32], 'big') >> (256 - q - 3)) & 3
                out.append((gr, ch, bt, p, q + 3))     # bit index of mixed flag
                pos = q + 1 + 2 + 1 + 10 + 9 + 3
            else:
                pos = q + 1 + 15 + 7 + 3
    return out

def craft(src, dst, first=0, last=10 ** 9):
    d = bytearray(open(src, 'rb').read()); n = 0
    for k, (off, ln, sr, mode) in enumerate(frames(bytes(d))):
        if not first <= k <= last: continue
        for gr, ch, bt, p, bit in side_bits(d, off):
            if bt == 2:
                d[p + bit // 8] |= 0x80 >> (bit % 8); n += 1
    open(dst, 'wb').write(d)
    print('mixed granules set:', n)

def harness(path):
    data = open(path, 'rb').read(); fr = frames(data)
    fw = FwMp3(); fw.load(data)
    seen = []
    def hook(m):
        sp = m.r[25]
        seen.append((sp, bytes(m.mv[sp + 2316:sp + 4476])))
    fw.set_hooks({0x2c5f2: hook})
    for k, (off, *_) in enumerate(fr[:40]):
        fw.frame(off)
        if seen:
            sp, raw = seen[0]
            w = struct.unpack('<540I', raw)
            print('frame', k, 'sp %#x' % sp, 'stack top (harness) %#x' % 0xF00000)
            print(' words 0..40:', ' '.join('%x' % x for x in w[:40]))
            print(' reads past the initial sp by', sp + 4476 - 0xF00000, 'bytes')
            break

if __name__ == '__main__':
    if sys.argv[1] == 'craft': craft(sys.argv[2], sys.argv[3], *map(int, sys.argv[4:6]))
    else: harness(sys.argv[2])
