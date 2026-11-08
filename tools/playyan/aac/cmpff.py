import numpy as np
import fwaac, mp4min, sys, numpy as np
tr, fr = mp4min.audio_frames(sys.argv[1]); ff = np.fromfile(sys.argv[2], '<i2')
d = fwaac.FwAAC(asc=tr['dsi']); N = int(sys.argv[3])
out = []
for k in range(N):
    r, info, pcm = d.frame(fr[k]); out.append(np.array(pcm[:info[2]], np.int32))
fw = np.concatenate(out)
for lag in range(-4096, 4097, 1024):
    a = fw[max(0, lag):]; b = ff[max(0, -lag):]; n = min(len(a), len(b))
    e = a[:n] - b[:n]
    print(lag, n, np.abs(e).max(), (e == 0).mean())
a = fw; b = ff[4096:]; n = min(len(a), len(b)); e = (a[:n] - b[:n]).reshape(-1, 2048)
for k in range(len(e)): print(k + 2, np.abs(e[k]).max(), (e[k] == 0).mean())
