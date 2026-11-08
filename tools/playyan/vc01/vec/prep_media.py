#!/usr/bin/env python3
"""Inputs of the pixel-pipeline tests (pix_test.py, pix_e2e.py, pix_thumb.py, m4v_intra.py), made with
ffmpeg from the 13 Garage Game ASFs in $PY_MEDIA (playan_garage_<name>/<NAME>.ASF):

  $PLAYYAN_WORK/vec/m4v/<NAME>.m4v          the ASF's VOL header + first VOP (ffmpeg -c:v copy, dump_extra)
  $PLAYYAN_WORK/vec/frames/<NAME>_xvid.yuv  that frame decoded by ffmpeg -idct xvid (240x176 YUV420)

  prep_media.py [NAME ...]      (default: all 13)
"""
import os
import subprocess
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import workdir  # noqa: E402

NAMES = ['AVOID', 'BAT', 'CREDIT', 'FIRE', 'FISH', 'INSECT', 'JUMP', 'KEEPER', 'MEMORY', 'NEKOROID', 'NOSE',
         'TRIPLE', 'WAVE']


def main():
    m4v = workdir.work('vec', 'm4v')
    fr = workdir.work('vec', 'frames')
    for n in sys.argv[1:] or NAMES:
        src = workdir.media('playan_garage_%s/%s.ASF' % (n.lower(), n))
        subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-i', src, '-c:v', 'copy', '-bsf:v', 'dump_extra',
                               '-frames:v', '1', '-f', 'm4v', os.path.join(m4v, n + '.m4v')])
        subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-idct', 'xvid', '-i', src, '-frames:v', '1',
                               '-f', 'rawvideo', '-pix_fmt', 'yuv420p', os.path.join(fr, n + '_xvid.yuv')])
        print(n, 'ok')


if __name__ == '__main__':
    main()
