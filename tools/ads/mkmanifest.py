#!/usr/bin/env python3
"""Turn emulator captures (emu/capture.py output) into hash-only manifests.

For every capture directory of a cart (boot, movie1..N) the streams it contains are identified
(identify.py), and for each stream two gzipped CSVs are written to manifests/:

  <cart>_<stream>.frame_md5.csv.gz  frame,md5     md5 of the 240x160 BGR555 framebuffer the
                                                  cartridge displayed for that stream frame
  <cart>_<stream>.audio_md5.csv.gz  block,samples,md5   md5 of the signed 8-bit samples the
                                                  cartridge's ADPCM routine produced for that
                                                  16458-sample block (the last block is cut where
                                                  the cartridge stopped decoding the track)

Only cartridge output goes into the manifests. The decoder is used to *locate* a stream inside a
capture (first frame md5 / audio anchor), never to fill in values.
usage: mkmanifest.py CART [CART...]
"""
import os, sys, csv, gzip, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carts
from util_pcm import locate
ADSDEC = os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec')
CAP = os.environ.get('ADS_CAP', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tmp', 'ads') + '/cap')
MAN = os.path.join(HERE, '..', '..', 'verify', 'ads')
BLOCK = 16458

def stream_names(cid):
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    names = []
    for l in out.splitlines():
        if l and l[0].isdigit():
            nm = l.split('mmstr@')[0].split(None, 1)[1].strip()
            names.append(nm)
    return names

def dec_md5(cid, s, n=None):
    a = [ADSDEC, carts.rom_path(cid), '--stream', s, '--md5', ] + carts.video_args(cid, s)
    if n: a += ['--count', str(n)]
    return [l.split()[1] for l in subprocess.run(a, capture_output=True, text=True, check=True).stdout.splitlines()]

def dec_pcm(cid, s, tmp):
    subprocess.run([ADSDEC, carts.rom_path(cid), '--stream', s, '--wav', tmp, '--count', '0'], capture_output=True, check=True)
    d = open(tmp, 'rb').read()[44:]
    os.remove(tmp)
    return bytes((b - 128) & 255 for b in d)

def safe(s): return s.replace(' ', '_')

def process(cid):
    os.makedirs(MAN, exist_ok=True)
    names = stream_names(cid)
    heads = {s: dec_md5(cid, s, 400) for s in names}
    summary = {}
    cdir = os.path.join(CAP, cid)
    for sub in sorted(os.listdir(cdir)):
        cap = os.path.join(cdir, sub)
        if not os.path.exists(os.path.join(cap, 'meta.json')): continue
        rows = list(csv.DictReader(open(os.path.join(cap, 'frames.csv'))))
        md5s = [r['fb_md5'] for r in rows]; emus = [int(r['emu']) for r in rows]
        pcm = open(os.path.join(cap, 'pcm.s8'), 'rb').read()
        acalls = [(int(r['pcm_off']), int(r['n']), int(r['emu'])) for r in csv.DictReader(open(os.path.join(cap, 'acalls.csv')))]
        # streams in this capture, in order of their first blit
        found = []
        for s in names:
            h = heads[s]
            # first-frame candidates: a capture position where the next 30 frames all match the head
            k = 0
            while k < len(md5s):
                try: k = md5s.index(h[0], k)
                except ValueError: break
                L = min(30, len(h))
                if md5s[k:k + L] == h[:L]: found.append((k, s)); break
                k += 1
        found.sort()
        import hashlib
        info = []
        for j, (k, s) in enumerate(found):
            end = found[j + 1][0] if j + 1 < len(found) else len(md5s)
            full = dec_md5(cid, s)
            seg = md5s[k:min(end, k + len(full))]
            a = dec_pcm(cid, s, os.path.join(cap, f'_t{j}.wav'))
            best = locate(pcm, a)
            info.append(dict(k=k, s=s, seg=seg, ast=best[0], alen=len(a)))
        for j, d in enumerate(info):
            s, seg, k, ast = d['s'], d['seg'], d['k'], d['ast']
            if s in summary and summary[s]['frames'] >= len(seg): continue   # keep the longest capture
            with gzip.open(os.path.join(MAN, f'{cid}_{safe(s)}.frame_md5.csv.gz'), 'wt') as f:
                f.write('frame,md5\n')
                for i, m in enumerate(seg): f.write(f'{i},{m}\n')
            # audio region: from the track start to where the cartridge stopped decoding it: the next
            # track's start in the same capture, or the last ADPCM call up to 30 VBlanks after the
            # stream's last displayed frame (the player decodes up to ~15 frames ahead)
            last_blit_emu = emus[k + len(seg) - 1] if seg else 0
            stop = max([o + n for o, n, e in acalls if e <= last_blit_emu + 30] + [0])
            if j + 1 < len(info) and info[j + 1]['ast'] > ast: stop = min(stop, info[j + 1]['ast'])
            region = pcm[ast:stop][:d['alen']] if ast >= 0 else b''
            with gzip.open(os.path.join(MAN, f'{cid}_{safe(s)}.audio_md5.csv.gz'), 'wt') as f:
                f.write('block,samples,md5\n')
                for b in range(0, len(region), BLOCK):
                    blk = region[b:b + BLOCK]
                    f.write(f'{b // BLOCK},{len(blk)},{hashlib.md5(blk).hexdigest()}\n')
            summary[s] = dict(capture=sub, first_blit=k, frames=len(seg), audio_off=ast, audio_samples=len(region))
            print(cid, s, summary[s], flush=True)
    json.dump(summary, open(os.path.join(MAN, f'{cid}.capture.json'), 'w'), indent=1)

if __name__ == '__main__':
    for c in sys.argv[1:]: process(c)
