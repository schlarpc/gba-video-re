"""Whole-file headphone PCM of the C reference (reported frames only) -> md5 / raw file."""
import sys, hashlib, struct
import mp4min, cref
tr, fr = mp4min.audio_frames(sys.argv[1]); img = sys.argv[2] if len(sys.argv) > 2 else 'mi_fw0'
c = cref.CAAC(tr['dsi'], img)
h = hashlib.md5(); n = errs = und = 0
for raw in fr:
    r, pcm = c.frame(raw)
    if isinstance(r, tuple): errs += 1; continue
    if r < 0: und += 1; continue
    if r > 0: h.update(struct.pack('<2048h', *pcm)); n += 1
print(sys.argv[1].split('/')[-1], 'frames', len(fr), 'pcm_frames', n, 'errors', errs, 'undefined', und, 'md5', h.hexdigest())
