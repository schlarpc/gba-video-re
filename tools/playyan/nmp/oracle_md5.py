"""md5 of the DSP's synthesis output (all calls, int16 LE, call order) for a file, plus per-1000-call
checkpoints: python3 oracle_md5.py FILE.mp3 OUT.json [max_calls]"""
import sys, json, hashlib, numpy as np, dump_pcm
pcm, addrs, dt = dump_pcm.dump(sys.argv[1], int(sys.argv[3]) if len(sys.argv) > 3 else 10 ** 9)
h = hashlib.md5(); cps = {}
for i, row in enumerate(pcm):
    h.update(row.astype('<i2').tobytes())
    if (i + 1) % 1000 == 0: cps[i + 1] = h.hexdigest()
json.dump({'calls': len(pcm), 'md5': h.hexdigest(), 'checkpoints': cps}, open(sys.argv[2], 'w'), indent=1)
np.save(sys.argv[2] + '.npy', pcm)
print(len(pcm), h.hexdigest(), round(dt))
