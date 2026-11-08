"""Differential fuzzing: C reference vs firmware decoder on mutated MPEG-1 L3 frames.
python3 fuzz.py ITERS [seed]   (mutations: intensity-stereo mode_ext on joint frames, side-info
and main-data bit flips, random big_values/part2_3/global_gain/table_select fields)"""
import sys, random, glob, os, struct
from fwmp3 import FwMp3, frames
from cref import CMp3
WORK = os.environ.get('MP3WORK', (os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')) + '/mp3work')
FILES = sorted(glob.glob(os.path.join(WORK, 'gen/*.mp3'))) + \
    [os.path.join(os.environ.get('PY_MEDIA', ''), 'playan_support_sample_music', 'sample_music.mp3')]

def mutate(buf, fr, R, kind):
    off, n, sr, mode = fr
    b = bytearray(buf[off:off + n])
    crc = 0 if (b[1] & 1) else 2
    if kind == 'is':
        if mode == 1: b[3] = (b[3] & ~0x30) | (R.choice([1, 3]) << 4)
        elif mode != 3: b[3] = (b[3] & ~0xf0) | 0x40 | (R.choice([1, 3]) << 4)   # stereo -> joint + IS
    elif kind == 'side':
        for _ in range(R.randint(1, 3)):
            p = 4 + crc + R.randrange(17 if mode == 3 else 32); b[p] ^= 1 << R.randrange(8)
    elif kind == 'main':
        for _ in range(R.randint(1, 8)):
            p = 4 + crc + (17 if mode == 3 else 32) + R.randrange(max(1, n - 40)); b[p % n] ^= 1 << R.randrange(8)
    buf[off:off + n] = b

def main(iters, seed):
    R = random.Random(seed)
    cache = {}
    tot = bad = und = 0; kinds = {}
    for it in range(iters):
        f = R.choice(FILES)
        if f not in cache:
            d = open(f, 'rb').read(); cache[f] = (d, frames(d))
        data, fr = cache[f]
        k0 = R.randrange(max(1, len(fr) - 8)); win = fr[k0:k0 + 8]
        buf = bytearray(data)
        kind = R.choice(['is', 'is', 'side', 'main', 'main'])
        for j in range(len(win)):
            if j >= 2 and R.random() < 0.6: mutate(buf, win[j], R, kind)
        fw = FwMp3(); fw.load(bytes(buf)); c = CMp3()
        for (off, n, sr, mode) in win:
            rf, pf, _ = fw.frame(off)
            rc, pc, _ = c.frame(bytes(buf[off:off + n + 8]))
            tot += 1
            if rc < 0:
                und += 1; kinds[rc] = kinds.get(rc, 0) + 1; break   # state diverges legitimately
            if list(pf) != pc:
                bad += 1
                print('MISMATCH it %d file %s frame@%#x kind %s rc %d' % (it, os.path.basename(f), off, kind, rc))
                open(os.path.join(WORK, 'fuzz_fail_%d.bin' % it), 'wb').write(bytes(buf[win[0][0]:win[-1][0] + win[-1][1]]))
                break
    print('fuzz: %d frames, %d mismatches, %d not compared %s' % (tot, bad, und, kinds))
    return bad

if __name__ == '__main__':
    sys.exit(1 if main(int(sys.argv[1]) if len(sys.argv) > 1 else 50, int(sys.argv[2]) if len(sys.argv) > 2 else 1) else 0)
