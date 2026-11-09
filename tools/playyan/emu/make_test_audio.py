#!/usr/bin/env python3
"""The test tones and the chirp of the Play-Yan music manifests (verify/playyan), bit for bit.

  make_test_audio.py [OUTDIR]     (default $PLAYYAN_WORK/media/test)

  tone_32000.mp3, tone_44100.mp3, tone_48000.mp3   12 s, left 1000 Hz, right 1500 Hz, peak 2334
                                                   (-23 dBFS), 64 kbit/s CBR joint stereo
  chirp_44100.mp3                                  10 s linear chirp 200 -> 2000 Hz, both channels,
                                                   peak 11000 (-9.5 dBFS), 128 kbit/s CBR joint stereo

All MPEG-1 Layer III (the Play-Yan plays only 32, 44.1 and 48 kHz).  The PCM is integer arithmetic
only: phase is an exact rational in 2^-32 cycles, the sine a 65,536-entry table computed with
integer Taylor series (no libm, no numpy maths, no ffmpeg lavfi), so every sample value is fixed.
The encoder is LAME 4.0 from a pinned nixpkgs revision (LAME=path overrides) with every option
given, no resampling, and no ID3 or Xing/LAME tag, so the files carry no version string or date.
The md5s below are checked after every run, so a different LAME build shows up as a mismatch
(LAME's SSE/MMX paths give the same bytes as its plain C path: checked with --noasm).
"""
import hashlib
import os
import subprocess
import sys

import numpy as np

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import workdir  # noqa: E402

NIXPKGS = 'github:NixOS/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504'   # lame 4.0
LAME_STORE = '/nix/store/d939x5bamfiaspx85mnf3c23vrsp9ckw-lame-4.0'

# stem: (rate, seconds, kbit/s, kind)
FILES = {
    'tone_32000': (32000, 12, 64, 'tone'),
    'tone_44100': (44100, 12, 64, 'tone'),
    'tone_48000': (48000, 12, 64, 'tone'),
    'chirp_44100': (44100, 10, 128, 'chirp'),
}
TONE_HZ = (1000, 1500)
TONE_AMP = 2334
CHIRP_HZ = (200, 2000)
CHIRP_AMP = 11000

# stem: (md5 of the s16le PCM, md5 of the .mp3, size of the .mp3)
EXPECTED = {
    'tone_32000': ('0d0981c3fa62d27648ae1b56f98e7a1d', '0a1e947f0ea839f0ac7650994da14f1b', 96480),
    'tone_44100': ('7428281e20d66ff4a30de5db6fe797c7', 'f8009132fa2dccd543872036f309a0cb', 96339),
    'tone_48000': ('37ac59fc3ce48c86fb85a934316a9172', '9fa402bb7736b5bafc11105dbb28dcff', 96192),
    'chirp_44100': ('75059966a92e819f15056610b898142f', 'd2ed5116f7045c555ebbdb9e3b286cd9', 160496),
}

TBITS = 16            # sine table: 2^16 entries per cycle
QBITS = 30            # table values in units of 2^-30


def sine_table():
    """round(sin(2 pi i / 2^TBITS) * 2^QBITS) for every i, in integers only."""
    P = 1 << 128      # fixed point for the series

    def atan_inv(x):  # atan(1/x) * P
        s, t, k, sign = 0, P // x, 1, 1
        while t:
            s += sign * (t // k)
            t //= x * x; k += 2; sign = -sign
        return s
    pi = 4 * (4 * atan_inv(5) - atan_inv(239))           # Machin
    n = 1 << TBITS
    q = n // 4
    quarter = []
    for i in range(q + 1):
        x = 2 * pi * i // n
        s, term, k = 0, x, 1
        while term:
            s += term
            term = -term * x // P * x // P // ((k + 1) * (k + 2))
            k += 2
        quarter.append((s * (1 << QBITS) + P // 2) // P)
    tab = quarter + quarter[q - 1:0:-1]                    # 0 .. pi
    tab += [-v for v in tab]                               # pi .. 2 pi
    assert len(tab) == n
    return tab


def pcm(stem, tab):
    rate, secs, _, kind = FILES[stem]
    n = rate * secs
    if kind == 'tone':
        chans = []
        for f in TONE_HZ:
            ph = [(f * k << 32) // rate & 0xFFFFFFFF for k in range(n)]
            chans.append([(TONE_AMP * tab[p >> (32 - TBITS)] + (1 << (QBITS - 1))) >> QBITS for p in ph])
    else:
        f0, f1 = CHIRP_HZ
        # cycles(t) = f0 t + (f1 - f0) t^2 / (2 T), t = k / rate
        den = 2 * secs * rate * rate
        ph = [((2 * f0 * secs * rate * k + (f1 - f0) * k * k) << 32) // den & 0xFFFFFFFF for k in range(n)]
        c = [(CHIRP_AMP * tab[p >> (32 - TBITS)] + (1 << (QBITS - 1))) >> QBITS for p in ph]
        chans = [c, c]
    return np.array(chans, dtype=np.int64).T.astype('<i2').tobytes()


def lame():
    p = os.environ.get('LAME')
    if p:
        return p
    p = os.path.join(LAME_STORE, 'bin', 'lame')
    if not os.path.exists(p):
        out = subprocess.check_output(['nix', 'build', '--no-link', '--print-out-paths', NIXPKGS + '#lame'], text=True)
        p = os.path.join(out.strip().splitlines()[-1], 'bin', 'lame')
    return p


def encode(exe, stem, raw, path):
    rate, _, kbps, _ = FILES[stem]
    khz = '%g' % (rate / 1000)
    cmd = [exe, '--silent', '--nohist', '-r', '-s', khz, '--bitwidth', '16', '--signed', '--little-endian',
           '-m', 'j', '-b', str(kbps), '--cbr', '--resample', khz, '-q', '2', '-t', '--noreplaygain',
           '--strictly-enforce-ISO', '-', path]
    subprocess.run(cmd, input=raw, check=True, env={'PATH': '/nonexistent', 'LC_ALL': 'C'})


def main():
    out = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else workdir.work('media', 'test')
    os.makedirs(out, exist_ok=True)
    exe = lame()
    ver = subprocess.run([exe, '--version'], capture_output=True, text=True).stdout.splitlines()[0]
    print('encoder:', exe, '|', ver)
    tab = sine_table()
    bad = 0
    for stem in FILES:
        raw = pcm(stem, tab)
        path = os.path.join(out, stem + '.mp3')
        encode(exe, stem, raw, path)
        b = open(path, 'rb').read()
        got = (hashlib.md5(raw).hexdigest(), hashlib.md5(b).hexdigest(), len(b))
        exp = EXPECTED[stem]
        st = 'ok' if got == exp else ('MISMATCH, expected %s' % (exp,) if exp else 'no expected value')
        bad += got != exp
        print('%-12s pcm %s  mp3 %s  %7d bytes  %s' % (stem, got[0], got[1], got[2], st))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
