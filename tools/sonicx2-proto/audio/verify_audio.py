#!/usr/bin/env python3
"""Verify the C audio decoder against the cart-derived hash manifests.
usage: verify_audio.py ROM [stream ...]   (default: intro ep103 ep104)"""
import gzip, hashlib, os, subprocess, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
MAN = os.path.join(HERE, '..', '..', '..', 'verify', 'hydrogen')
B = 16458

def main():
    rom = sys.argv[1]; streams = sys.argv[2:] or ['intro', 'ep103', 'ep104']
    exe = os.path.join(HERE, '..', '..', '..', 'reference', 'sonicx2-proto', 'audio', 'sx2adec')
    if not os.path.exists(exe):
        subprocess.check_call(['make', '-s', '-C', os.path.dirname(exe)])
    res = {}; bad = 0
    for s in streams:
        pcm = subprocess.run([exe, rom, '--stream', s, '--out', '/dev/stdout'], capture_output=True, check=True).stdout
        rows = gzip.open(os.path.join(MAN, f'sx2p_{s}.audio_md5.csv.gz'), 'rt').read().split('\n')[1:]
        ok = tot = 0
        for r in rows:
            if not r: continue
            k, n, h = r.split(','); k = int(k); n = int(n); tot += 1
            if hashlib.md5(pcm[k * B:k * B + n]).hexdigest() == h: ok += 1
            else:
                bad += 1
                if bad < 10: print(f'{s} block {k}: MISMATCH')
        res[s] = f'{ok}/{tot}'
        print(f'{s}: {ok}/{tot} blocks bit-exact ({len(pcm)} samples decoded)')
    print(json.dumps(res))
    return 1 if bad else 0
sys.exit(main())
