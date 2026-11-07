#!/usr/bin/env python3
"""Verify the Sonic X Vol 2 prototype decoders against the hash manifests captured from the cart.

  video: sx2dec --md5 for every stream vs verify/hydrogen/sx2p_<stream>.frame_md5.csv.gz
         (md5 of the VRAM bitmap rows the VQ blit writes, captured in mGBA at the blit's return)
  audio: delegated to audio/verify_audio.py (verify/hydrogen/sx2p_<stream>.audio_md5.csv.gz)

usage: verify.py ROM [--video-only | --audio-only] [--hydec PATH] [--json OUT]
  --hydec PATH  verify the shared Hydrogen-family decoder (reference/hydrogen/hydec) instead of
                sx2dec: its raw frames are hashed over the same lines as the manifest
Exit status 0 only if every frame and every audio block matches.
"""
import sys, os, gzip, csv, subprocess, json

HERE = os.path.dirname(os.path.abspath(__file__))
SHA1 = '00257021d9c4444e96d96331fb6eb07e756f53cc'
# stream index in sx2dec --list order (ROM position) -> manifest name
STREAMS = [(0, 'ep103'), (1, 'ep104'), (2, 'intro')]


def check_rom(path):
    import hashlib
    h = hashlib.sha1(open(path, 'rb').read()).hexdigest()
    if h != SHA1:
        sys.exit(f'{path}: sha1 {h} is not the Sonic X Vol 2 prototype ({SHA1})')


def frames_hydec(hydec, rom, idx, lines_list):
    import hashlib
    p = subprocess.Popen([hydec, rom, '--stream', str(idx), '--out', '/dev/stdout'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    got = []
    while True:
        fb = p.stdout.read(240 * 160 * 2)
        if not fb: break
        lines = lines_list[len(got)] if len(got) < len(lines_list) else 160
        got.append(hashlib.md5(fb[:lines * 480]).hexdigest())
    p.wait()
    return got


def verify_video(rom, hydec=None):
    exe = os.path.join(HERE, '..', '..', 'reference', 'sonicx2-proto', 'sx2dec')
    if not os.path.exists(exe):
        subprocess.check_call(['make', '-C', os.path.dirname(exe), 'sx2dec'])
    res = {}
    for idx, name in STREAMS:
        man = os.path.join(HERE, '..', '..', 'verify', 'hydrogen', f'sx2p_{name}.frame_md5.csv.gz')
        rows = list(csv.DictReader(gzip.open(man, 'rt')))
        want = [r['vram_md5'] for r in rows]
        if hydec:
            got = frames_hydec(hydec, rom, idx, [int(r['lines']) for r in rows])
        else:
            out = subprocess.run([exe, rom, '--stream', str(idx), '--md5'], capture_output=True, text=True, check=True).stdout
            got = [l.split(',')[3] for l in out.splitlines()]
        ok = sum(a == b for a, b in zip(got, want))
        bad = [i for i, (a, b) in enumerate(zip(got, want)) if a != b][:10]
        res[name] = {'frames': len(want), 'decoded': len(got), 'ok': ok, 'first_bad': bad}
        print(f'video {name} ({"hydec" if hydec else "sx2dec"}): {ok}/{len(want)} frames match' + (f' (decoded {len(got)})' if len(got) != len(want) else '') + (f' first bad {bad}' if bad else ''))
    return res


def verify_audio(rom):
    va = os.path.join(HERE, 'audio', 'verify_audio.py')
    if not os.path.exists(va):
        print('audio: audio/verify_audio.py missing'); return None
    p = subprocess.run([sys.executable, va, rom], capture_output=True, text=True)
    print(p.stdout.strip())
    if p.returncode: print(p.stderr.strip())
    return {'returncode': p.returncode, 'output': p.stdout}


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    rom = a[0]; check_rom(rom)
    res = {}
    hyd = a[a.index('--hydec') + 1] if '--hydec' in a else None
    if '--audio-only' not in a: res['video'] = verify_video(rom, hyd)
    if '--video-only' not in a: res['audio'] = verify_audio(rom)
    if '--json' in a: json.dump(res, open(a[a.index('--json') + 1], 'w'), indent=1)
    good = all(v['ok'] == v['frames'] == v['decoded'] for v in res.get('video', {}).values())
    if res.get('audio') is not None: good &= res['audio']['returncode'] == 0
    sys.exit(0 if good else 1)
