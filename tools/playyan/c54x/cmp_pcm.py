"""compare captured headphone PCM (wav) against a reference decode (ffmpeg s16le) - alignment + SNR"""
import sys, wave, numpy as np
def load_wav(p):
    w = wave.open(p); return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2).astype(np.float64), w.getframerate()
def compare(emu, ref, maxoff=20000, seglen=20000):
    start = int(np.argmax(np.abs(emu[:, 0]) > 50))
    seg = emu[start:start + seglen, 0]
    # coarse alignment by FFT cross-correlation
    n = 1 << int(np.ceil(np.log2(len(seg) + maxoff + seglen)))
    R = np.fft.rfft(ref[:maxoff + seglen, 0], n); S = np.fft.rfft(seg, n)
    cc = np.fft.irfft(R * np.conj(S), n)[:maxoff]
    off = int(np.argmax(cc))   # ref[off + k] ~ emu[start + k]
    lag = off - start          # ref index = emu index + lag
    a0 = max(0, -lag); n_ = min(len(emu) - a0, len(ref) - (a0 + lag))
    e = emu[a0:a0 + n_]; r = ref[a0 + lag:a0 + lag + n_]
    act = np.abs(r).max(axis=1) > 0
    res = {}
    for ch in (0, 1):
        d = e[:, ch] - r[:, ch]
        res[ch] = dict(snr=10 * np.log10(np.sum(r[:, ch] ** 2) / max(np.sum(d ** 2), 1e-9)),
                       maxerr=float(np.abs(d).max()), exact=float(np.mean(d == 0)), rms=float(np.sqrt(np.mean(d ** 2))))
    return lag, n_, res
if __name__ == '__main__':
    emu, sr = load_wav(sys.argv[1])
    ref = np.fromfile(sys.argv[2], dtype=np.int16).reshape(-1, 2).astype(np.float64)
    lag, n, res = compare(emu, ref)
    print('emu %d frames @%d Hz; aligned lag %d, compared %d frames (%.2f s)' % (len(emu), sr, lag, n, n / sr))
    for ch in (0, 1):
        r = res[ch]; print('  ch%d: SNR %.2f dB  rms err %.2f  max err %.0f  bit-exact %.2f%%' % (ch, r['snr'], r['rms'], r['maxerr'], 100 * r['exact']))
