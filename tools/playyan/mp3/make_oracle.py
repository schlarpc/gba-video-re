"""Hash manifests from the firmware oracle (the music image's own decoder 0x222f2 and speaker
routine 0x3185e on the C VC01 core): per frame md5 of the 1152 x 2 int16 PCM, and per GBA block
md5 of ring A || ring B (576 + 576 bytes) at volume 28 (the default user level 18 x 56 / 36).
python3 make_oracle.py OUTDIR FILE.mp3...   -> OUTDIR/<name>.mp3_pcm_md5.csv, .gba_md5.csv"""
import sys, os, struct, hashlib
from fwmp3 import FwMp3, frames

def gba_blocks(d, rate, vol, words_iter):
    gp = d.gp
    def wr(off, v): struct.pack_into('<I', d.mv, gp + off, v & 0xffffffff)
    wr(1520, 0); struct.pack_into('<I', d.mv, gp + 37748, 1); wr(1424, 0)
    wr(1660, vol)
    for o in (1412, 1564, 1416, 1420): wr(o, 0)
    sent = []
    d.set_hooks({0x33670: lambda m: (sent.append(bytes(m.mv[m.r[2]:m.r[2] + m.r[3]])), m.r.__setitem__(31, m.r[26]))})
    d.call(0x33ff6, rate)     # stream start: counters, last = f(old rate = 0), rate
    W = d.malloc(4 * 1152)
    for ws in words_iter:
        d.mv[W:W + 4608] = ws
        d.call(0x3185e, W, 1152)
    return sent

def main(out, files):
    os.makedirs(out, exist_ok=True)
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        data = open(f, 'rb').read(); fr = frames(data)
        d = FwMp3(); d.load(data)
        rows = []; pcms = []
        for k, (off, n, sr, mode) in enumerate(fr):
            r, pcm, _ = d.frame(off)
            b = struct.pack('<2304h', *pcm); pcms.append(b)
            rows.append('%d,%d,%d,%s' % (k, off, n, hashlib.md5(b).hexdigest()))
        open(os.path.join(out, name + '.mp3_pcm_md5.csv'), 'w').write('frame,offset,len,md5\n' + '\n'.join(rows) + '\n')
        g = FwMp3()
        blocks = gba_blocks(g, fr[0][2], 28, pcms)
        open(os.path.join(out, name + '.gba_md5.csv'), 'w').write(
            'block,md5\n' + ''.join('%d,%s\n' % (i, hashlib.md5(b).hexdigest()) for i, b in enumerate(blocks)))
        print(name, len(rows), 'frames', len(blocks), 'gba blocks')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
