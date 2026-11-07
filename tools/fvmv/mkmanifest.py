#!/usr/bin/env python3
"""Build hash-only manifests from mGBA whole-stream captures (emu/capture_fvmv.py output).
   <short id>_<stream>.frame_md5.csv.gz : frame,md5,md5_15   (decoder output buffer, 240x160 u16 LE;
                                     md5 = all 16 bits, md5_15 = bit 15 cleared)
   <short id>_<stream>.audio_md5.csv.gz : frame,block,md5_synth,md5_pcm (256 x int32 synthesis
                                     output; 1024 x s8 PCM written to the DMA ring)
usage: mkmanifest.py CAPDIR OUTDIR   (OUTDIR is normally verify/fvmv)"""
import gzip, os, sys
CODES = {v: f'pokemon{v}' for v in range(1, 5)}  # short ids of verify/carts.csv
cap, out = sys.argv[1], sys.argv[2]
for v in range(1, 5):
    for e in (1, 2):
        d = os.path.join(cap, f'p{v}e{e}')
        name = f'{CODES[v]}_{e - 1}'
        with gzip.open(os.path.join(d, 'frames.csv.gz'), 'rt') as f, \
                gzip.GzipFile(os.path.join(out, name + '.frame_md5.csv.gz'), 'wb', mtime=0) as g:
            g.write(b'frame,md5,md5_15\n')
            for l in f:
                if l.startswith('frame'):
                    continue
                p = l.strip().split(',')
                g.write(f'{p[0]},{p[4]},{p[5]}\n'.encode())
        with gzip.open(os.path.join(d, 'audio.csv.gz'), 'rt') as f, \
                gzip.GzipFile(os.path.join(out, name + '.audio_md5.csv.gz'), 'wb', mtime=0) as g:
            g.write(b'frame,block,md5_synth,md5_pcm\n')
            for l in f:
                if not l.startswith('frame'):
                    g.write(l.encode())
        print(name)
