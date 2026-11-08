"""Dump the NMP DSP's decoded PCM per granule and channel, straight from its synthesis routine
(2:81B0, which writes 576 int16 per call), for any MP3:

    python3 dump_pcm.py FILE.mp3 OUT.npy [max_granules]

OUT.npy: int16 array [n_calls, 576] in call order (granule 0 ch 0, granule 0 ch 1, ...) plus
OUT.npy.json with the output addresses.  This is the oracle for the nmp model."""
import sys, json, time, numpy as np
import nmpo
SYN, SYN_RET = 0x281b0, 0x282be
def dump(path, maxg=10 ** 9):
    o = nmpo.Oracle(path)
    out = []; addrs = []
    t0 = time.time()
    while len(out) < maxg:
        if o.run_until([SYN], max_cycles=3 * 10 ** 9) is None: break
        a = o.acc(0) & 0xFFFF
        if o.run_until([SYN_RET]) is None: break
        out.append(o.d(a, 576)); addrs.append(a)
    return np.array(out, dtype=np.uint16).view(np.int16), addrs, time.time() - t0
if __name__ == '__main__':
    pcm, addrs, dt = dump(sys.argv[1], int(sys.argv[3]) if len(sys.argv) > 3 else 10 ** 9)
    np.save(sys.argv[2], pcm)
    json.dump({'addrs': addrs}, open(sys.argv[2] + '.json', 'w'))
    print('calls', len(pcm), 'secs', round(dt, 1))
