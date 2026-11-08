#!/usr/bin/env python3
"""The SD card folders run.py and the scenarios expect, as symlinks into $PY_MEDIA, in $PLAYYAN_WORK/sd/:

  orig/    sample_movie.asf, GARAGE/<the 13 Garage Game ASFs>, MUSIC/sample_music.mp3
  micro/   the same plus MP4sample_movie.mp4, play_yanmicro.ini (the key file), MUSIC/Sub/second_song.mp3
  nmp/     sample_music.mp3, Album/track2.mp3, stereo_L440_R1500.mp3
  stereo/  stereo_L440_R1500.mp3 (left 440 Hz, right 1500 Hz, 30 s; made with ffmpeg + libmp3lame)

The emulators turn a folder into a FAT16 image themselves (mkfs.fat + mtools).
  make_sd.py [orig|micro|nmp|stereo ...]      (default: all)
"""
import os
import shutil
import subprocess
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import workdir  # noqa: E402

GARAGE = ['AVOID', 'BAT', 'CREDIT', 'FIRE', 'FISH', 'INSECT', 'JUMP', 'KEEPER', 'MEMORY', 'NEKOROID', 'NOSE',
          'TRIPLE', 'WAVE']
MOVIE = 'playan_support_sample_movie/sample_movie.asf'
MUSIC = 'playan_support_sample_music/sample_music.mp3'
MP4 = 'play_yan_micro_support_mp4sample_movie/MP4sample_movie.mp4'
INI = 'play_yan_micro_keyfile_play_yanmicro/play_yanmicro.ini'


def stereo_mp3():
    p = os.path.join(workdir.work('cache'), 'stereo_L440_R1500.mp3')
    if not os.path.exists(p):
        subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=30',
                               '-f', 'lavfi', '-i', 'sine=frequency=1500:duration=30', '-filter_complex',
                               '[0][1]amerge=inputs=2', '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '128k', p])
    return p


def card(name):
    files = {}
    if name in ('orig', 'micro'):
        files['sample_movie.asf'] = workdir.media(MOVIE)
        for g in GARAGE:
            files['GARAGE/%s.ASF' % g] = workdir.media('playan_garage_%s/%s.ASF' % (g.lower(), g))
        files['MUSIC/sample_music.mp3'] = workdir.media(MUSIC)
    if name == 'micro':
        files['MP4sample_movie.mp4'] = workdir.media(MP4)
        files['play_yanmicro.ini'] = workdir.media(INI)
        files['MUSIC/Sub/second_song.mp3'] = workdir.media(MUSIC)
    if name == 'nmp':
        files['sample_music.mp3'] = workdir.media(MUSIC)
        files['Album/track2.mp3'] = workdir.media(MUSIC)
        files['stereo_L440_R1500.mp3'] = stereo_mp3()
    if name == 'stereo':
        files['stereo_L440_R1500.mp3'] = stereo_mp3()
    root = os.path.join(workdir.WORK, 'sd', name)
    shutil.rmtree(root, ignore_errors=True)
    for rel, src in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(os.path.abspath(src), p)
    print('%-7s %2d files -> %s' % (name, len(files), root))


if __name__ == '__main__':
    for n in sys.argv[1:] or ('orig', 'micro', 'nmp', 'stereo'):
        card(n)
