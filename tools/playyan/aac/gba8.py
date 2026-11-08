import sys, gzip, hashlib, numpy as np, mp4min, cref
tr, fr = mp4min.audio_frames(sys.argv[1]); img = sys.argv[2]
c = cref.CAAC(tr['dsi'], img)
pcm = []
for raw in fr:
    r, p = c.frame(raw); pcm.append(np.array(p, np.int32))
pcm = np.concatenate(pcm).reshape(-1, 2)
rows = [l.split(',') for l in gzip.open(sys.argv[3], 'rt').read().split('\n')[1:] if l]
want = [r[3] for r in rows if r[1] == '1152']
def vol(s, v=28):
    p = s * v; return np.clip(np.where(p < 0, p + 31, p) >> 5, -32768, 32767)
L, R = vol(pcm[:, 0]), vol(pcm[:, 1])
def run(sh, last0, rate=44100, cmode=0):
    keep = []; c_ = 0; last = last0
    for i in range(len(L)):
        v = abs((c_ << sh) // rate); c_ += 1
        if c_ > rate: c_ = 0
        if v != last: last = v; keep.append(i)
    keep = np.array(keep)
    a = (R[keep] >> 8).astype(np.int8); b = (L[keep] >> 8).astype(np.int8)
    got = []
    for k in range(len(keep) // 576):
        got.append(hashlib.md5(a[576*k:576*k+576].tobytes() + b[576*k:576*k+576].tobytes()).hexdigest())
    return sum(g == w for g, w in zip(got, want)), len(got)
for sh in (13, 14):
    for last0 in (-1, 0):
        print(sh, last0, run(sh, last0), len(want))
