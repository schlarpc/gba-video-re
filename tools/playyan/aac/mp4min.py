"""Minimal MP4 reader for the oracle tools: tracks with handler, stsd entry, sample offsets/sizes."""
import struct

CONTAINERS = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'edts', b'dinf', b'udta'}


def boxes(d, p, end):
    while p + 8 <= end:
        sz, t = struct.unpack_from('>I4s', d, p)
        h = 8
        if sz == 1:
            sz = struct.unpack_from('>Q', d, p + 8)[0]; h = 16
        elif sz == 0:
            sz = end - p
        yield t, p + h, p + sz
        p += sz


def esds_dsi(d, p, end):
    """DecoderSpecificInfo bytes from an esds box payload (after version/flags)."""
    p += 4
    def desc(p):
        tag = d[p]; p += 1; n = 0
        for _ in range(4):
            b = d[p]; p += 1; n = n << 7 | (b & 0x7f)
            if not b & 0x80: break
        return tag, p, n
    tag, p, n = desc(p)            # ES_Descriptor
    flags = d[p + 2]; p += 3
    if flags & 0x80: p += 2
    if flags & 0x40: p += 1 + d[p]
    if flags & 0x20: p += 2
    tag, p, n = desc(p)            # DecoderConfigDescriptor
    oti = d[p]; p += 13
    tag, p, n = desc(p)            # DecSpecificInfo
    return oti, bytes(d[p:p + n])


def tracks(d):
    out = []
    for t, s, e in boxes(d, 0, len(d)):
        if t != b'moov': continue
        for t2, s2, e2 in boxes(d, s, e):
            if t2 != b'trak': continue
            tr = {}
            def walk(s, e):
                for t, a, b in boxes(d, s, e):
                    if t in CONTAINERS: walk(a, b)
                    elif t == b'hdlr': tr['hdlr'] = d[a + 8:a + 12]
                    elif t == b'mdhd':
                        v = d[a]; tr['timescale'] = struct.unpack_from('>I', d, a + (20 if v else 12))[0]
                    elif t == b'stsd':
                        et = d[a + 12:a + 16]; tr['fmt'] = et; base = a + 16
                        if et == b'mp4a':
                            tr['channels'], = struct.unpack_from('>H', d, base + 16)
                            tr['rate'] = struct.unpack_from('>I', d, base + 24)[0] >> 16
                            q = base + 28
                        else:
                            q = base + 78
                        for t3, a3, b3 in boxes(d, q, b):
                            if t3 == b'esds': tr['oti'], tr['dsi'] = esds_dsi(d, a3, b3)
                    elif t == b'stsz':
                        ss, n = struct.unpack_from('>II', d, a + 4)
                        tr['sizes'] = [ss] * n if ss else list(struct.unpack_from('>%dI' % n, d, a + 12))
                    elif t == b'stco':
                        n, = struct.unpack_from('>I', d, a + 4); tr['co'] = list(struct.unpack_from('>%dI' % n, d, a + 8))
                    elif t == b'co64':
                        n, = struct.unpack_from('>I', d, a + 4); tr['co'] = list(struct.unpack_from('>%dQ' % n, d, a + 8))
                    elif t == b'stsc':
                        n, = struct.unpack_from('>I', d, a + 4)
                        tr['stsc'] = [struct.unpack_from('>III', d, a + 8 + 12 * i) for i in range(n)]
            walk(s2, e2)
            # sample offsets
            offs = []; si = 0; co = tr.get('co', []); stsc = tr.get('stsc', [])
            for ci, c in enumerate(co):
                spc = 0
                for k, (first, n, _) in enumerate(stsc):
                    if ci + 1 >= first: spc = n
                o = c
                for _ in range(spc):
                    if si >= len(tr.get('sizes', [])): break
                    offs.append(o); o += tr['sizes'][si]; si += 1
            tr['offsets'] = offs
            out.append(tr)
    return out


def audio_frames(path):
    d = open(path, 'rb').read()
    for tr in tracks(d):
        if tr.get('fmt') == b'mp4a':
            return tr, [d[o:o + n] for o, n in zip(tr['offsets'], tr['sizes'])]
    raise ValueError('no mp4a track')
