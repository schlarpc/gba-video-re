"""Differential fuzz: mutated frames (bit flips, truncation, random tails) of real AAC streams through
the firmware decoder (emulator) and the C reference; compares return code, reported samples and PCM.
Frames whose C result is -100 (behaviour depends on unmodelled memory) are counted separately."""
import ctypes as C
import numpy, sys, random, os
import fwaac, mp4min, cref
R = random.Random(int(os.environ.get('SEED', '1')))
files = sys.argv[1:-1]; N = int(sys.argv[-1])
ok = bad = undef = 0
for fn in files:
    tr, fr = mp4min.audio_frames(fn)
    f = fwaac.FwAAC(asc=tr['dsi']); c = cref.CAAC(tr['dsi'])
    hist = []
    for it in range(N):
        raw = bytearray(fr[R.randrange(len(fr))])
        m = R.random()
        if m < 0.5:
            for _ in range(R.randint(1, 4)):
                i = R.randrange(len(raw) * 8); raw[i >> 3] ^= 0x80 >> (i & 7)
        elif m < 0.7: raw = raw[:R.randint(1, len(raw))]
        elif m < 0.85: raw = raw[:R.randint(1, len(raw))] + bytes(R.randrange(256) for _ in range(R.randint(1, 64)))
        else: raw = bytearray(R.randrange(256) for _ in range(R.randint(2, 400)))
        raw = bytes(raw)
        hist.append(raw)
        try:
            r, info, pcm = f.frame(raw)
        except RuntimeError as e:
            print('emulator stop', e); f = fwaac.FwAAC(asc=tr['dsi']); c = cref.CAAC(tr['dsi']); hist = []; continue
        cr, cp = c.frame(raw)
        exp = info[2] if r == 1 else ('err', C.c_int32(info[1]).value)
        if cr in (-2, -3, -4): undef += 1; f = fwaac.FwAAC(asc=tr['dsi']); c = cref.CAAC(tr['dsi']); hist = []; continue
        if cr != exp or (r == 1 and list(pcm[:2048]) != cp):
            bad += 1
            if bad <= 10: print(fn, it, 'fw', r, info, 'c', cr, raw[:24].hex())
            open('out/fuzzfail_%d.bin' % bad, 'wb').write(raw); __import__('pickle').dump(hist, open('out/fuzzhist_%d.pkl' % bad, 'wb')); open('out/fuzzfail_%d.txt' % bad, 'w').write(fn)
            f = fwaac.FwAAC(asc=tr['dsi']); c = cref.CAAC(tr['dsi']); hist = []
        else: ok += 1
print('fuzz ok', ok, 'bad', bad, 'undefined', undef)
