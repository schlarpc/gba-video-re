import sys, glob, ctypes as C
import numpy, fwaac, mp4min, cref
for fn in sorted(glob.glob('out/fuzzfail_*.bin'), key=lambda x: int(x.split('_')[1].split('.')[0])):
    raw = open(fn, 'rb').read(); src = open(fn.replace('.bin', '.txt')).read()
    tr, fr = mp4min.audio_frames(src)
    f = fwaac.FwAAC(asc=tr['dsi']); c = cref.CAAC(tr['dsi'])
    r, info, pcm = f.frame(raw); cr, cp = c.frame(raw)
    exp = info[2] if r == 1 else ('err', C.c_int32(info[1]).value)
    print(fn, 'fw', r, exp, 'c', cr, 'ok' if cr == exp else 'DIFF', len(raw))
