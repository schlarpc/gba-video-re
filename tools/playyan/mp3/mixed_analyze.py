"""Analyse fw_mixed_cap.py captures: the 540 words each mixed granule's reorder reads past its
copy (tmp[576..1115]), per granule, and how they differ between granules and runs."""
import sys, struct, os
WORK = os.environ.get('MP3WORK', (os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')) + '/mp3work')
REC = 4096 + 128 + 1024

def granules(tag):
    b = open(os.path.join(WORK, 'mixcap_%s.bin' % tag), 'rb').read()
    out = []; cur = []; sp0 = None
    for i in range(len(b) // REC):
        r = b[i * REC:(i + 1) * REC]
        regs = struct.unpack_from('<32I', r, 4096)
        win = r[4096 + 128:]
        idx = regs[2]; sp = regs[25]
        val = struct.unpack_from('<I', win, 12)[0]
        if idx == 576 and cur:
            out.append((sp0, cur)); cur = []
        if idx == 576: sp0 = sp
        cur.append(val)
    if len(cur) == 540: out.append((sp0, cur))
    return out

if __name__ == '__main__':
    runs = {t: granules(t) for t in sys.argv[1:]}
    for t, gs in runs.items():
        print(t, 'granules', len(gs), 'sp', ['%#x' % sp for sp, _ in gs])
        for g, (sp, w) in enumerate(gs):
            print('  g%d first 32:' % g, ' '.join('%x' % x for x in w[:32]))
    base_t = sys.argv[1]; base = runs[base_t][0][1]
    for t, gs in runs.items():
        for g, (sp, w) in enumerate(gs):
            d = [i for i in range(540) if w[i] != base[i]]
            print('%s g%d vs %s g0: %d of 540 words differ; differing word indices (first 20): %s' % (t, g, base_t, len(d), d[:20]))
