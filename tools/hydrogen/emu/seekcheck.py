#!/usr/bin/env python3
"""Seek check: start the cart's PlayMovie at every chapter of episode 1 (cold start, the player's
own SeekToChapter) and compare the first N frames with hydec decoding from that chapter's segment."""
import sys, os, subprocess, csv, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..'))
import hycart
from carts import rom_path
code, stream, n, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
rom = open(rom_path(code), 'rb').read()
c = hycart.find_containers(rom)[stream]; s = hycart.Stream(rom, c['video'][0], c['video'][1])
res = []
for k, ch in enumerate(s.chapters):
    f0 = s.segments[ch['segment']]['frame']
    pre = f'{out}/{code}_{stream}_ch{k}'
    subprocess.run([sys.executable, os.path.join(HERE, 'capture.py'), rom_path(code), str(stream), pre, '--frames', str(n),
                    '--chapter', str(k)], capture_output=True, check=True)
    cap = [r['fb_md5'] for r in csv.DictReader(open(pre + '.frames.csv'))][:n]
    p = subprocess.run([os.path.join(HERE, '..', '..', '..', 'reference', 'hydrogen', 'hydec'), rom_path(code), '--stream', str(stream), '--start-frame', str(f0),
                        '--count', str(n), '--md5'], capture_output=True, text=True)
    dec = [l.split()[1] for l in p.stdout.splitlines()]
    ok = sum(a == b for a, b in zip(cap, dec))
    # audio: the player seeks to block frame // 12 and decodes (then discards) the remainder;
    # every ADPCM call after the seek must match a decode started at that block
    import ctypes as C, struct, hashlib
    sys.path.insert(0, os.path.join(HERE, '..')); import verify
    lib = C.CDLL(os.path.join(HERE, '..', '..', '..', 'reference', 'hydrogen', 'libhydec.so'))
    ao, al = c['audio']; nb = struct.unpack_from('<H', rom, ao + 14)[0]; po = ao + ((16 + 2 * nb + 3) & ~3)
    tab = struct.unpack_from(f'<{nb}H', rom, ao + 16)
    blk = f0 // 12
    a = verify._A(); buf = C.create_string_buffer(rom[po:ao + al + 64], al + 64)
    lib.mj_adpcm_init(C.byref(a), buf, al - (po - ao) + 64, 1)
    if blk: lib.mj_adpcm_seek(C.byref(a), C.c_size_t(4 * sum(tab[:blk])), C.c_uint32(blk))
    arows = list(csv.DictReader(open(pre + '.audio.csv')))
    # calls before the 4096-sample prefill are the seek's skip-decodes: the player patches the
    # sample store of the ADPCM loop to a NOP (mov r9,r9) for them, so their output is not written
    skip = sum(1371 + (i & 1) for i in range(f0 % 12))      # SoundPlayer::Seek's skip-decode, frame by frame
    acc = 0; first = 0
    while acc < skip: acc += int(arows[first]['n']); first += 1
    assert acc == skip, (acc, skip)
    aok = 0
    for i, r in enumerate(arows):
        kk = int(r['n']); o = (C.c_int8 * kk)(); lib.mj_adpcm_decode(C.byref(a), o, kk)
        if i >= first: aok += hashlib.md5(bytes(o)).hexdigest() == r['md5']
    arows = arows[first:]
    res.append(dict(chapter=k, segment=ch['segment'], frame=f0, ok=ok, n=len(cap), audio_ok=aok, audio_calls=len(arows),
                    audio_block=blk, skip_calls=first))
    print(code, stream, res[-1], flush=True)
json.dump(res, open(f'{out}/{code}_{stream}_seek.json', 'w'))
