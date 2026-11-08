import ctypes as C
import os
import numpy, sys
import fwaac, mp4min, cref
def frames_of(path):
    if path.endswith('.mp4') or path.endswith('.m4a'):
        tr, fr = mp4min.audio_frames(path); return tr['dsi'], fr
    raise ValueError(path)
if __name__ == '__main__':
    asc, fr = frames_of(sys.argv[1]); N = int(sys.argv[2]) if len(sys.argv) > 2 else len(fr)
    import os
    img = os.environ.get('AAC_IMG', 'mi_fw0')
    f = fwaac.FwAAC(img, asc=asc); c = cref.CAAC(asc, img)
    bad = 0
    for k in range(min(N, len(fr))):
        r, info, pcm = f.frame(fr[k])
        fpcm = list(pcm[:2048])
        cr, cpcm = c.frame(fr[k])
        exp_ret = info[2] if r == 1 else ('err', C.c_int32(info[1]).value)
        if (fpcm != cpcm and r == 1) or cr != exp_ret:
            bad += 1
            if bad <= 5:
                import numpy as np
                a = np.array(fpcm or [0]*2048); b = np.array(cpcm or [0]*2048)
                print('frame', k, 'fw ret', r, info, 'c ret', cr, 'maxdiff', abs(a - b).max(), 'eq', (a == b).mean())
    print('frames', min(N, len(fr)), 'mismatch', bad)
