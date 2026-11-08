"""Differential test: C reference vs the firmware decoder in the emulator, frame by frame.
python3 cmp.py FILE.mp3 [nframes] [img]"""
import sys, struct
from fwmp3 import FwMp3, frames
from cref import CMp3

def run(path, nf=None, img='py_fw1', verbose=True):
    data = open(path, 'rb').read()
    fr = frames(data)
    if nf: fr = fr[:nf]
    fw = FwMp3(img); fw.load(data)
    c = CMp3(img)
    bad = 0; first = None; stat = {}
    for k, (off, n, sr, mode) in enumerate(fr):
        rf, pf, _ = fw.frame(off)
        rc, pc, cn = c.frame(data[off:off + n + 4])
        stat[rc] = stat.get(rc, 0) + 1
        if list(pf) != pc and rc >= 0:
            bad += 1
            if first is None:
                first = k
                diff = [i for i in range(2304) if pf[i] != pc[i]]
                if verbose:
                    print('frame', k, 'rc', rc, 'ndiff', len(diff), 'first', diff[:5], [(pf[i], pc[i]) for i in diff[:5]])
    print('%s: %d frames, %d mismatching, first %s, rc %s' % (path.split('/')[-1], len(fr), bad, first, stat))
    return bad

if __name__ == '__main__':
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else 'py_fw1')
