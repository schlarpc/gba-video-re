#!/usr/bin/env python3
"""Ground-truth manifests from $PY_CAPTURE_DIR/cap/<tag>/ captures (capture.py) -> verify/playyan/.

  make_manifests.py [TAG ...]        (default: every capture that has info.json)

Per capture <build>_<stem> (build = the cartridge image that decoded it: py_fw1, fup_sec1, mi_fw1,
py_fw0, fup_sec0, mi_fw0):
  <build>_<stem>.frame_md5.csv.gz      frame,gba_cycle,bgr555_md5        240x160 frames as the GBA reads
                                       them (u16 LE BGR555, 76800 bytes, row-major, after the bridge)
  <build>_<stem>.thumb_md5.csv.gz      thumb,bgr555_md5                   60x40 thumbnails (4800 bytes)
  <build>_<stem>.audio_md5.csv.gz      block,pcm_frame,frames,rate,md5    headphone PCM per firmware
                                       buffer (s16 LE, L/R interleaved, as the AC'97 DMA played it)
  <build>_<stem>.audio8_md5.csv.gz     block,bytes,gba_cycle,md5          GBA 8-bit blocks (s8: rec[4]/2
                                       right-channel samples then the same number of left)
  <build>_<stem>.audio_seg_md5.csv.gz  segment,first_block,blocks,md5     headphone PCM in ~30 s segments
  <build>_<stem>.audio8_seg_md5.csv.gz segment,first_block,blocks,md5     8-bit blocks in ~30 s segments
Segment md5 = md5 of the concatenated bytes of its blocks (the repo's make-segments convention).
"""
import csv, glob, gzip, hashlib, io, json, os, sys
import numpy as np

HERE = os.environ.get('PY_CAPTURE_DIR', os.path.join(os.environ.get('PLAYYAN_WORK', ''), 'emu', 'final'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'verify', 'playyan')

DECODER = {('orig', 'music'): 'py_fw1', ('v111', 'music'): 'fup_sec1', ('micro', 'music'): 'mi_fw1',
           ('orig', 'video'): 'py_fw0', ('v111', 'video'): 'fup_sec0', ('micro', 'video'): 'mi_fw0'}


def wcsv(path, header, rows):
    b = io.StringIO()
    b.write(header + '\n')
    for r in rows:
        b.write(','.join(str(x) for x in r) + '\n')
    with gzip.GzipFile(path, 'wb', mtime=0) as f:
        f.write(b.getvalue().encode())


def segments(chunks, per_seg):
    """chunks: list of bytes; per_seg: blocks per segment"""
    out = []
    for s in range(0, len(chunks), per_seg):
        part = chunks[s:s + per_seg]
        out.append((len(out), s, len(part), hashlib.md5(b''.join(part)).hexdigest()))
    return out


def one(capdir):
    info = json.load(open(os.path.join(capdir, 'info.json')))
    build = DECODER[(info['build'], info['kind'])]
    stem = os.path.splitext(info['name'])[0]
    if stem.startswith('X') and 'garage' in os.path.basename(capdir):
        stem = stem[1:]          # Garage ASFs were renamed X<NAME>.ASF so the GBA plays them as video
    base = os.path.join(OUT, '%s_%s' % (build, stem))
    res = dict(tag=os.path.basename(capdir), build=build, file=os.path.basename(info['file']), md5=info['md5'])
    # frames
    fr = list(csv.DictReader(open(os.path.join(capdir, 'frames.csv'))))
    if fr:
        raw = open(os.path.join(capdir, 'frames.bin'), 'rb').read()
        rows = [(i, r['gba_cycle'], hashlib.md5(raw[i * 0x12C00:(i + 1) * 0x12C00]).hexdigest()) for i, r in enumerate(fr)]
        wcsv(base + '.frame_md5.csv.gz', 'frame,gba_cycle,bgr555_md5', rows)
        res['frames'] = len(rows)
    th = list(csv.DictReader(open(os.path.join(capdir, 'thumbs.csv'))))
    if th:
        raw = open(os.path.join(capdir, 'thumbs.bin'), 'rb').read()
        rows = [(i, hashlib.md5(raw[i * 0x12C0:(i + 1) * 0x12C0]).hexdigest()) for i in range(len(th))]
        wcsv(base + '.thumb_md5.csv.gz', 'thumb,bgr555_md5', rows)
        res['thumbs'] = len(rows)
    # headphone PCM per firmware buffer
    hb = list(csv.DictReader(open(os.path.join(capdir, 'hp_blocks.csv'))))
    rate = info.get('ac_rate')
    if hb:
        hp = np.fromfile(os.path.join(capdir, 'hp.s16'), np.int16)
        chunks, rows = [], []
        for r in hb:
            p, nb = int(r['pcm_frame']), int(r['bytes'])
            seg = hp[2 * p:2 * p + nb // 2]
            if len(seg) * 2 < nb:
                break           # last buffer cut off by the end of the capture
            b = seg.tobytes()
            chunks.append(b)
            rows.append((len(rows), p, nb // 4, rate, hashlib.md5(b).hexdigest()))
        wcsv(base + '.audio_md5.csv.gz', 'block,pcm_frame,frames,rate,md5', rows)
        fpb = (sum(int(r[2]) for r in rows) / len(rows)) if rows else 1
        per = max(1, int(round(30 * (rate or 44100) / fpb)))
        wcsv(base + '.audio_seg_md5.csv.gz', 'segment,first_block,blocks,md5', segments(chunks, per))
        res['hp_blocks'] = len(rows)
    # 0x2BA-byte blocks are the UI click (0x2000 OPERATION_SOUND) of the key press that started playback
    a8 = [r for r in csv.DictReader(open(os.path.join(capdir, 'a8.csv'))) if int(r['bytes']) != 0x2BA]
    if a8:
        raw = open(os.path.join(capdir, 'a8.bin'), 'rb').read()
        chunks, rows = [], []
        for i, r in enumerate(a8):
            o, n = int(r['offset']), int(r['bytes'])
            b = raw[o:o + n]
            chunks.append(b)
            rows.append((i, n, r['gba_cycle'], hashlib.md5(b).hexdigest()))
        wcsv(base + '.audio8_md5.csv.gz', 'block,bytes,gba_cycle,md5', rows)
        spb = sum(int(r[1]) for r in rows) / 2 / len(rows)
        gba_rate = 16384 if info['kind'] == 'music' else 8192
        per = max(1, int(round(30 * gba_rate / spb)))
        wcsv(base + '.audio8_seg_md5.csv.gz', 'segment,first_block,blocks,md5', segments(chunks, per))
        res['a8_blocks'] = len(rows)
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    tags = sys.argv[1:] or sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(HERE, 'cap', '*', 'info.json')))
    allres = []
    tags = [t for t in tags if not t.startswith('x_')]     # x_*: pacing experiments, not ground truth
    for t in tags:
        r = one(os.path.join(HERE, 'cap', t))
        allres.append(r)
        print(r)
    json.dump(allres, open(os.path.join(HERE, 'manifests.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
