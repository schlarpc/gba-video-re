"""Build test MP3s with LAME (dev tool): python3 gen_media.py OUTDIR"""
import numpy as np, wave, subprocess, sys, os
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
LAME = os.environ.get('LAME', 'lame')
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get('MP3WORK', WORK + '/mp3work'), 'gen')
os.makedirs(out, exist_ok=True)
rng = np.random.default_rng(1)

def wav(path, sr, ch, secs=4.0, kind='mix'):
    n = int(sr * secs); t = np.arange(n) / sr
    sig = []
    for c in range(ch):
        f0 = 220 * (c + 1)
        s = 0.3 * np.sin(2 * np.pi * (f0 + 2000 * t / secs) * t)        # sweep
        s += 0.05 * rng.standard_normal(n)                                # noise
        if kind in ('mix', 'trans'):                                      # clicks -> short blocks
            for p in range(0, n, sr // (8 if kind == 'trans' else 3)):
                s[p:p + 40] += rng.uniform(-0.9, 0.9, size=min(40, n - p))
        if kind == 'loud':
            s = np.clip(s * 6, -1, 1)
        sig.append(s)
    if ch == 2 and kind == 'wide':
        sig[1] = -sig[0] * 0.9 + 0.1 * sig[1]
    a = (np.clip(np.stack(sig, 1), -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr); w.writeframes(a.tobytes())

CASES = [
    ('mono44_128', 44100, 1, 'mix', ['-m', 'm', '-b', '128']),
    ('js48_192', 48000, 2, 'mix', ['-m', 'j', '-b', '192']),
    ('ms32_64', 32000, 2, 'trans', ['-m', 'f', '-b', '64', '--resample', '32']),
    ('st44_320', 44100, 2, 'loud', ['-m', 's', '-b', '320']),
    ('st44_32', 44100, 2, 'mix', ['-m', 's', '-b', '32', '--resample', '44.1']),
    ('vbr44', 44100, 2, 'trans', ['-V', '2']),
    ('vbr48mono', 48000, 1, 'trans', ['-V', '6']),
    ('wide44', 44100, 2, 'wide', ['-m', 'j', '-b', '96']),
    ('crc44', 44100, 2, 'mix', ['-p', '-b', '160']),
]
for name, sr, ch, kind, opts in CASES:
    w = os.path.join(out, name + '.wav')
    wav(w, sr, ch, kind=kind)
    subprocess.run([LAME, '--quiet', '--noreplaygain', '-t'] + opts + [w, os.path.join(out, name + '.mp3')], check=True)
    os.remove(w)
print(sorted(os.listdir(out)))
