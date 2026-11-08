#!/usr/bin/env python3
"""Re-code an I-VOP-only MPEG-4 SP stream (H.263 quant, no resync markers) so that its intra DC
coefficients go through the AC (TCOEF) VLC: set intra_dc_vlc_thr to index THR in every VOP and
re-emit the macroblocks, coding the DC differential as the first TCOEF event whenever the running
QP is >= the threshold (the decoder's choice: QP after the MB's dquant ('after') or before it
('before')). Nothing else changes; the result decodes to the same pictures.

usage: dcvlc_transcode.py IN.asf OUT.asf THR_INDEX after|before"""
import os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from asf_py import objects

T = open(os.path.join(HERE, '..', '..', '..', 'crates', 'gbav-playyan', 'src', 'm4v_tables.rs')).read()


def tab(name):
    m = re.search(r'pub const %s: \[(\(u8, u8\)|\(u16, u16\)|u8); (\d+)\] = \[(.*?)\];' % name, T)
    body = m.group(3)
    if m.group(1).startswith('('):
        return [tuple(map(int, x)) for x in re.findall(r'\((\d+), (\d+)\)', body)]
    return list(map(int, re.findall(r'\d+', body)))


INTRA_MCBPC, CBPY, DC_LUM, DC_CHROM = tab('INTRA_MCBPC'), tab('CBPY'), tab('DC_LUM'), tab('DC_CHROM')
INTRA_VLC, RUN, LEVEL = tab('INTRA_VLC'), tab('INTRA_RUN'), tab('INTRA_LEVEL')
DCTHR = tab('DC_THRESHOLD')
LAST = 67
CROSS = [0]
MAXL = [[0] * 65 for _ in range(2)]; MAXR = [[0] * 65 for _ in range(2)]
for i in range(102):
    l = int(i >= LAST)
    MAXL[l][RUN[i]] = max(MAXL[l][RUN[i]], LEVEL[i]); MAXR[l][LEVEL[i]] = max(MAXR[l][LEVEL[i]], RUN[i])
ENC = {(int(i >= LAST), RUN[i], LEVEL[i]): INTRA_VLC[i] for i in range(102)}


class R:
    def __init__(s, d, p): s.b = ''.join(format(x, '08b') for x in d); s.p = p

    def get(s, n):
        v = int(s.b[s.p:s.p + n] or '0', 2) if n else 0; s.p += n; return v

    def vlc(s, t):
        for n in range(1, 14):
            c = int(s.b[s.p:s.p + n], 2)
            for i, (code, ln) in enumerate(t):
                if ln == n and code == c:
                    s.p += n; return i
        raise ValueError('vlc at %d' % s.p)


class W:
    def __init__(s): s.b = []

    def put(s, v, n):
        if n: s.b.append(format(v & ((1 << n) - 1), '0%db' % n))

    def code(s, cl):
        s.put(cl[0], cl[1])

    def bits(s): return ''.join(s.b)


def parse_vol(d):
    i = d.find(b'\x00\x00\x01\x20'); r = R(d, (i + 4) * 8)
    r.get(9)
    if r.get(1): r.get(7)
    if r.get(4) == 15: r.get(16)
    if r.get(1):
        r.get(3)
        if r.get(1): r.get(79)
    r.get(3); res = r.get(16)
    return max(1, (res - 1).bit_length())


def events(r, start):
    ev = []; i = start - 1
    while True:
        idx = r.vlc(INTRA_VLC)
        if idx == 102:
            if r.get(1) == 0:
                k = r.vlc(INTRA_VLC); l = int(k >= LAST); run = RUN[k]; lv = LEVEL[k] + MAXL[l][run]; last = l
                lv = -lv if r.get(1) else lv
            elif r.get(1) == 0:
                k = r.vlc(INTRA_VLC); l = int(k >= LAST); lv0 = LEVEL[k]; run = RUN[k] + MAXR[l][lv0] + 1; last = l
                lv = -lv0 if r.get(1) else lv0
            else:
                last = r.get(1); run = r.get(6); r.get(1); v = r.get(12); lv = v - 4096 if v & 0x800 else v; r.get(1)
        else:
            last = int(idx >= LAST); run = RUN[idx]; lv = -LEVEL[idx] if r.get(1) else LEVEL[idx]
        ev.append((last, run, lv)); i += run + 1
        if last:
            return ev


def put_events(w, ev):
    for last, run, lv in ev:
        key = (last, run, abs(lv))
        if key in ENC:
            w.code(ENC[key]); w.put(1 if lv < 0 else 0, 1)
        else:
            assert lv and -2047 <= lv <= 2047
            w.code(INTRA_VLC[102]); w.put(3, 2); w.put(last, 1); w.put(run, 6); w.put(1, 1); w.put(lv, 12); w.put(1, 1)


def transcode_vop(d, tib, nmb, thr_idx, mode):
    j = d.find(b'\x00\x00\x01\xb6'); r = R(d, (j + 4) * 8)
    assert r.get(2) == 0, 'not an I-VOP'
    while r.get(1): pass
    r.get(1 + tib + 1)
    assert r.get(1) == 1
    hdr_end = r.p
    old_thr = DCTHR[r.get(3)]; q = r.get(5)
    w = W(); w.b.append(r.b[(j + 4) * 8:hdr_end]); w.put(thr_idx, 3); w.put(q, 5)
    thr = DCTHR[thr_idx]
    for mb in range(nmb):
        while True:
            c = r.vlc(INTRA_MCBPC)
            if c != 8: break
        cbpc = c & 3; dq = c >= 4
        acp = r.get(1); cbpy = r.vlc(CBPY)
        qb = q
        dqv = r.get(2) if dq else None
        if dq: q = max(1, min(31, q + [-1, -2, 1, 2][dqv])); CROSS.append(q)
        use_dc_old = q < old_thr
        blocks = []
        for n in range(6):
            coded = (cbpy << 2 | cbpc) >> (5 - n) & 1
            dcsize = dcbits = None; ev = []
            if use_dc_old:
                dcsize = r.vlc(DC_LUM if n < 4 else DC_CHROM)
                dcbits = r.get(dcsize) if dcsize else 0
                if dcsize > 8: r.get(1)
                ev = events(r, 1) if coded else []
                diff = 0 if dcsize == 0 else (dcbits if dcbits >> (dcsize - 1) else dcbits - (1 << dcsize) + 1)
            else:
                ev = events(r, 0) if coded else []
                diff = 0
                if ev and ev[0][1] == 0:
                    diff = ev[0][2]; ev = ev[1:]
                elif ev:
                    ev = [(ev[0][0], ev[0][1] - 1, ev[0][2])] + ev[1:]
            blocks.append((diff, ev))
        qsel = q if mode == 'after' else qb
        if (q < thr) != (qb < thr):
            CROSS[0] += 1
        use_dc_new = qsel < thr
        # re-emit
        nb = []
        for diff, ev in blocks:
            if use_dc_new:
                nb.append(('vlc', diff, ev, bool(ev)))
            else:
                if diff:
                    full = [(0 if ev else 1, 0, diff)] + ev
                elif ev:
                    full = [(ev[0][0], ev[0][1] + 1, ev[0][2])] + ev[1:]
                else:
                    full = []
                if full and not ev:
                    full = [(1, 0, diff)]
                nb.append(('ac', diff, full, bool(full)))
        ncbp = 0
        for k, b in enumerate(nb):
            ncbp |= int(b[3]) << (5 - k)
        w.code(INTRA_MCBPC[(ncbp & 3) + (4 if dq else 0)]); w.put(acp, 1); w.code(CBPY[ncbp >> 2])
        if dq: w.put(dqv, 2)
        for k, (kind, diff, ev, coded) in enumerate(nb):
            if kind == 'vlc':
                tbl = DC_LUM if k < 4 else DC_CHROM
                if diff == 0:
                    w.code(tbl[0])
                else:
                    size = abs(diff).bit_length()
                    w.code(tbl[size]); w.put(diff if diff > 0 else diff + (1 << size) - 1, size)
                    if size > 8: w.put(1, 1)
                if ev: put_events(w, ev)
            elif coded:
                put_events(w, ev)
    bits = w.bits()
    bits += '0' + '1' * ((-len(bits) - 1) % 8)
    return d[:j + 4] + int(bits, 2).to_bytes(len(bits) // 8, 'big')


def main():
    inp, outp, thr_idx, mode = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    streams, objs = objects(inp)
    v = [s for s, k in streams.items() if k == 'video'][0]
    raw = open(inp, 'rb').read()
    i = raw.find(b'\x00\x00\x01\x20')
    e = min([x for x in (raw.find(b'\x00\x00\x01\xb2', i + 4), raw.find(b'\x00\x00\x01\xb6', i + 4)) if x > 0] + [i + 64])
    vol = raw[i:e]
    tib = parse_vol(vol)
    import struct
    w = 240; h = 176
    nmb = (w // 16) * (h // 16)
    es = bytearray(vol)
    for k in sorted(objs[v]):
        es += transcode_vop(bytes(objs[v][k]), tib, nmb, thr_idx, mode)
    with tempfile.TemporaryDirectory() as td:
        m = os.path.join(td, 'v.m4v'); open(m, 'wb').write(es)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-r', '30', '-f', 'm4v', '-i', m, '-i', inp, '-map', '0:v', '-map', '1:a',
                        '-c', 'copy', '-vtag', 'MP4S', outp], check=True)
    print('wrote', outp, 'dquant crossings of the threshold:', CROSS[0], 'dquant MBs', len(CROSS) - 1, 'qp range', (min(CROSS[1:]), max(CROSS[1:])) if len(CROSS) > 1 else None)


if __name__ == '__main__':
    main()
