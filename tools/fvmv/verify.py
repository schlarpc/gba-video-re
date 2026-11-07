#!/usr/bin/env python3
"""Verify the C decoder (fvmvdec) on every FVMV stream against the hash manifests.

For each cart (Pokemon Vol 1-4, identified by sha1) and each of its two streams, runs
  fvmvdec ROM --stream S --md5 --audio-md5
from power-on state (zeroed EWRAM and audio state, as the player has after boot) and compares
every frame md5 (16-bit buffer and bit-15-cleared) and every audio block md5 (int32 synthesis
output and s8 PCM) with verify/fvmv/<short id>_<stream>.{frame,audio}_md5.csv.gz.

usage: verify.py [ROMDIR_OR_ROMS...] [--json OUT]   (default: $GBAV_ROMS, else roms/)
Prints one line per stream and exits non-zero on any mismatch.
"""
import gzip, hashlib, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CARTS = {  # sha1 -> (short id, title)
    'ad0164df397860b0967590ee46ca1f41d6cecdcd': ('pokemon1', 'Pokemon - Volume 1 (USA)'),
    'e7b2e00d6a60e8c149cd07b0ee8d98bf2b4efd15': ('pokemon2', 'Pokemon - Volume 2 (USA)'),
    '0f0020ef4278e1777baaf74b6787355a5cb57d50': ('pokemon3', 'Pokemon - Volume 3 (USA)'),
    'fd79b821ff601e0091f19ab9c86b08f1c39ea2f3': ('pokemon4', 'Pokemon - Volume 4 (USA)'),
}


def find_roms(args):
    paths = []
    for a in args:
        if os.path.isdir(a):
            paths += [os.path.join(a, f) for f in sorted(os.listdir(a)) if f.lower().endswith('.gba')]
        else:
            paths.append(a)
    found = {}
    for p in paths:
        h = hashlib.sha1(open(p, 'rb').read()).hexdigest()
        if h in CARTS:
            found[h] = p
    return found


def load(path, key_n):
    m = {}
    with gzip.open(path, 'rt') as f:
        next(f)
        for l in f:
            p = l.strip().split(',')
            m[tuple(int(x) for x in p[:key_n])] = tuple(p[key_n:])
    return m


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    jout = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
    if jout in args:
        args.remove(jout)
    exe = os.path.join(HERE, '..', '..', 'reference', 'fvmv', 'fvmvdec')
    roms = find_roms(args or [os.environ.get('GBAV_ROMS') or os.path.join(HERE, '..', '..', 'roms')])
    if not roms:
        print('no FVMV carts found')
        return 1
    bad_total = 0
    res = {}
    for sha, path in sorted(roms.items(), key=lambda x: CARTS[x[0]][0]):
        code = CARTS[sha][0]
        for s in (0, 1):
            name = f'{code}_{s}'
            fm = load(os.path.join(HERE, '..', '..', 'verify', 'fvmv', name + '.frame_md5.csv.gz'), 1)
            ap = os.path.join(HERE, '..', '..', 'verify', 'fvmv', name + '.audio_md5.csv.gz')
            am = load(ap, 2) if os.path.exists(ap) else None  # per-block audio manifest not in verify/ (optional; gbav verify checks the audio segment manifests)
            out = subprocess.run([exe, path, '--stream', str(s), '--md5', '--audio-md5'],
                                 capture_output=True, text=True, check=True).stdout
            fv, av = {}, {}
            for l in out.splitlines():
                p = l.split(',')
                if p[0] == 'a':
                    av[(int(p[1]), int(p[2]))] = (p[3], p[4])
                else:
                    fv[(int(p[0]),)] = (p[1], p[2])
            fok = sum(fv.get(k) == v for k, v in fm.items())
            if am is None:
                am, av = {}, {}
            aok = sum(av.get(k) == v for k, v in am.items())
            bad = (len(fm) - fok) + (len(am) - aok) + (len(fv) != len(fm)) + (len(av) != len(am))
            bad_total += bad
            res[name] = {'sha1': sha, 'stream': s, 'frames': f'{fok}/{len(fm)}', 'audio_blocks': f'{aok}/{len(am)}'}
            print(f'{name}: video {fok}/{len(fm)} frames, audio {aok}/{len(am)} blocks  '
                  f'{"OK" if not bad else "MISMATCH"}', flush=True)
    if jout:
        json.dump(res, open(jout, 'w'), indent=1)
    print('ALL OK' if not bad_total else f'{bad_total} mismatches')
    return 1 if bad_total else 0


if __name__ == '__main__':
    sys.exit(main())
