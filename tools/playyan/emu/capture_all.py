#!/usr/bin/env python3
"""Every capture behind verify/playyan (61 sets), then the manifests.

  capture_all.py [-j N] [--out DIR] [--only SUBSTR] [--list]

Runs capture.py for each file x cartridge in the matrix of verify/playyan/README.md, N at a time
(default 4; the captures are independent, and each is deterministic on its own), then
make_manifests.py --out DIR on all 61 tags (default DIR: $PY_CAPTURE_DIR/manifests).  Compare DIR
with verify/playyan by cmp.

Inputs: PY_ROMS, PY_MEDIA (Nintendo's files), and the test tones and chirp made by
make_test_audio.py in $PLAYYAN_WORK/media/test (PY_TEST_AUDIO overrides).  Logs go to
$PY_CAPTURE_DIR/log/<tag>.log.
"""
import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

EMU = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(EMU, '..'))
import workdir  # noqa: E402
CAP = os.environ.get('PY_CAPTURE_DIR') or os.path.join(workdir.WORK, 'capture')

GARAGE = ['AVOID', 'BAT', 'CREDIT', 'FIRE', 'FISH', 'INSECT', 'JUMP', 'KEEPER', 'MEMORY', 'NEKOROID', 'NOSE',
          'TRIPLE', 'WAVE']
TEST_AUDIO = ['tone_32000', 'tone_44100', 'tone_48000', 'chirp_44100']


def jobs():
    """(tag, build, kind, path, name on the card)"""
    test = os.environ.get('PY_TEST_AUDIO') or os.path.join(workdir.WORK, 'media', 'test')
    music = [('sample_music', workdir.media('playan_support_sample_music/sample_music.mp3'))]
    for s in TEST_AUDIO:
        p = os.path.join(test, s + '.mp3')
        if not os.path.exists(p):
            sys.exit('%s not found: run tools/playyan/emu/make_test_audio.py first' % p)
        music.append((s, p))
    video = [('sample_movie', workdir.media('playan_support_sample_movie/sample_movie.asf'), None)]
    video += [('garage_' + g, workdir.media('playan_garage_%s/%s.ASF' % (g.lower(), g)), 'X%s.ASF' % g) for g in GARAGE]
    mp4 = [('MP4sample_movie', workdir.media('play_yan_micro_support_mp4sample_movie/MP4sample_movie.mp4')),
           ('mp4sample_movie', workdir.media('mp4sample_movie.mp4'))]
    out = []
    for build in ('orig', 'v111', 'micro'):
        out += [('%s_%s' % (build, s), build, 'music', p, None) for s, p in music]
        out += [('%s_%s' % (build, s), build, 'video', p, n) for s, p, n in video]
        if build != 'orig':           # py_fw0 has no MP4 support
            out += [('%s_%s' % (build, s), build, 'video', p, None) for s, p in mp4]
    return out


def run(job):
    tag, build, kind, path, name = job
    cmd = [sys.executable, os.path.join(EMU, 'capture.py'), build, kind, path, '--tag', tag]
    if name:
        cmd += ['--name', name]
    os.makedirs(os.path.join(CAP, 'log'), exist_ok=True)
    with open(os.path.join(CAP, 'log', tag + '.log'), 'w') as log:
        rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=EMU)
    last = open(os.path.join(CAP, 'log', tag + '.log')).read().strip().splitlines()[-1:] or ['']
    print('%-28s rc=%d  %s' % (tag, rc, last[0][:160]), flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-j', type=int, default=4)
    ap.add_argument('--out')
    ap.add_argument('--only')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    js = [j for j in jobs() if not a.only or a.only in j[0]]
    if a.list:
        for j in js:
            print(*j)
        print(len(js), 'captures')
        return
    with ThreadPoolExecutor(a.j) as ex:
        rcs = list(ex.map(run, js))
    if any(rcs):
        sys.exit('%d captures failed' % sum(1 for r in rcs if r))
    out = a.out or os.path.join(CAP, 'manifests')
    subprocess.check_call([sys.executable, os.path.join(EMU, 'make_manifests.py'), '--out', out] + [j[0] for j in js])


if __name__ == '__main__':
    main()
