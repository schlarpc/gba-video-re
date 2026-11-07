#!/usr/bin/env python3
"""Compare an emulator capture (emu/capture.py) with adsdec output for one or more streams.
usage: compare.py CART CAPDIR STREAM [STREAM...]   (streams in the order the cart played them)"""
import sys, os, subprocess, csv, hashlib, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import carts
from util_pcm import locate
ADSDEC = os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec')
BLOCK = 16458

def dec_md5(rom, stream, extra=()):
    out = subprocess.run([ADSDEC, rom, '--stream', stream, '--md5'] + list(extra), capture_output=True, text=True, check=True).stdout
    return [l.split()[1] for l in out.splitlines()]

def dec_pcm(rom, stream, tmp):
    subprocess.run([ADSDEC, rom, '--stream', stream, '--wav', tmp, '--count', '0'], capture_output=True, check=True)
    return bytes((b - 128) & 255 for b in open(tmp, 'rb').read()[44:])

def compare(cid, cap, streams, quiet=False):
    rom = carts.rom_path(cid)
    caps = [r['fb_md5'] for r in csv.DictReader(open(os.path.join(cap, 'frames.csv')))]
    pcm = open(os.path.join(cap, 'pcm.s8'), 'rb').read()
    res = []
    vpos = 0; apos = 0
    for s in streams:
        mine = dec_md5(rom, s, carts.video_args(cid, s))
        # locate the stream's first frame in the capture (the cartridge may show other blits first)
        try: st = caps.index(mine[0], vpos)
        except ValueError: st = None
        r = dict(stream=s, frames=len(mine))
        if st is None:
            r.update(video='not found'); res.append(r); continue
        seg = caps[st:st + len(mine)]
        ok = sum(a == b for a, b in zip(seg, mine))
        bad = [i for i, (a, b) in enumerate(zip(seg, mine)) if a != b]
        r.update(cap_first_blit=st, captured=len(seg), video_ok=ok, video_bad_first=bad[:10])
        vpos = st + len(seg)
        a = dec_pcm(rom, s, os.path.join(cap, f'_tmp_{s.replace(" ", "_")}.wav'))
        # anchor on the first non-silent 1024-sample window of the decoded track
        K = next((i for i in range(0, max(1, len(a) - 1024), 256) if len(set(a[i:i + 1024])) > 8), 0)
        ast, n = locate(pcm, a, max(0, apos - 64))
        if ast < 0:
            r.update(audio='not found')
        else:
            nb = (len(a) + BLOCK - 1) // BLOCK
            full = n // BLOCK
            r.update(cap_audio_off=ast, audio_samples=len(a), audio_match_prefix=n,
                     audio_blocks_full_ok=full, audio_blocks=nb,
                     audio_cart_end=None)
            apos = ast + n
        res.append(r)
    if not quiet:
        for r in res: print(json.dumps(r))
    return res

if __name__ == '__main__':
    compare(sys.argv[1], sys.argv[2], sys.argv[3:])
