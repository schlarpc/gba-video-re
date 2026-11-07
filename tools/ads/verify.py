#!/usr/bin/env python3
"""Verify the ADS decoder (adsdec) against the emulator hash manifests in manifests/.

For every manifest stream: decode the whole video stream and compare the md5 of every
framebuffer with the cartridge's; decode the whole audio track and compare every 16458-sample
block (the last one only as far as the cartridge decoded it).
usage: verify.py [CART...] [--json OUT]       (default: every cart that has manifests)
"""
import os, sys, gzip, csv, json, hashlib, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carts
ADSDEC = os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec')
MAN = os.path.join(HERE, '..', '..', 'verify', 'ads')
BLOCK = 16458

def streams_of(cid):
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    res = {}
    for l in out.splitlines():
        if l and l[0].isdigit():
            nm = l.split('mmstr@')[0].split(None, 1)[1].strip()
            res[nm.replace(' ', '_')] = dict(name=nm, frames=int(l.split(' frames')[0].split()[-1]),
                                            blocks=int(l.split(' blocks')[0].split()[-1]))
    return res

def verify_stream(cid, sid, info):
    rom = carts.rom_path(cid)
    r = dict(cart=cid, stream=sid, frames=info['frames'], blocks=info['blocks'])
    fm = os.path.join(MAN, f'{cid}_{sid}.frame_md5.csv.gz')
    if os.path.exists(fm):
        want = [row['md5'] for row in csv.DictReader(gzip.open(fm, 'rt'))]
        out = subprocess.run([ADSDEC, rom, '--stream', info['name'], '--md5'] + carts.video_args(cid, info['name']), capture_output=True, text=True).stdout
        got = [l.split()[1] for l in out.splitlines()]
        bad = [i for i, (a, b) in enumerate(zip(want, got)) if a != b]
        r.update(video_captured=len(want), video_ok=len(want) - len(bad), video_bad=bad[:20], video_decoded=len(got))
    am = os.path.join(MAN, f'{cid}_{sid}.audio_md5.csv.gz')
    if os.path.exists(am):
        rows = list(csv.DictReader(gzip.open(am, 'rt')))
        with tempfile.NamedTemporaryFile(suffix='.wav') as t:
            subprocess.run([ADSDEC, rom, '--stream', info['name'], '--wav', t.name, '--count', '0'], capture_output=True)
            pcm = bytes((b - 128) & 255 for b in open(t.name, 'rb').read()[44:])
        ok = 0; bad = []
        for row in rows:
            b, n = int(row['block']), int(row['samples'])
            if hashlib.md5(pcm[b * BLOCK:b * BLOCK + n]).hexdigest() == row['md5']: ok += 1
            else: bad.append(b)
        r.update(audio_blocks_captured=len(rows), audio_ok=ok, audio_bad=bad[:20],
                 audio_samples_captured=sum(int(x['samples']) for x in rows))
    return r

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    jout = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
    if jout in args: args.remove(jout)
    cids = args or sorted({f.split('_')[0] for f in os.listdir(MAN) if f.endswith('.frame_md5.csv.gz')}
                          & set(carts.CARTS))
    res = []; allok = True
    for cid in cids:
        for sid, info in streams_of(cid).items():
            if not os.path.exists(os.path.join(MAN, f'{cid}_{sid}.frame_md5.csv.gz')): continue
            r = verify_stream(cid, sid, info)
            ok = r.get('video_ok') == r.get('video_captured') == r['frames'] and r.get('audio_ok') == r.get('audio_blocks_captured')
            allok &= ok
            print(f"{'OK  ' if ok else 'FAIL'} {cid:13s} {sid:14s} video {r.get('video_ok')}/{r.get('video_captured')} of {r['frames']}  "
                  f"audio {r.get('audio_ok')}/{r.get('audio_blocks_captured')} blocks of {r['blocks']}", flush=True)
            res.append(r)
    if jout: json.dump(res, open(jout, 'w'), indent=1)
    sys.exit(0 if allok else 1)

if __name__ == '__main__':
    main()
