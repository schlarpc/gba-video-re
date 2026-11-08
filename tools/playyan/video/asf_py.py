"""Minimal ASF walker for the Python oracles: media-object payload bytes per stream (the files the
oracles use have one payload per object)."""
import struct


def objects(path, frags=None):
    """frags: optional list that receives (stream, object number, file offset, length) per fragment."""
    d = open(path, 'rb').read()
    hsize = struct.unpack_from('<Q', d, 16)[0]
    nobj = struct.unpack_from('<I', d, 24)[0]
    p = 30; psize = 0; streams = {}
    FP = bytes.fromhex('a1dcab8c47a9cf118ee400c00c205365')
    SP = bytes.fromhex('9107dcb7b7a9cf118ee600c00c205365')
    AUD = bytes.fromhex('409e69f84d5bcf11a8fd00805f5c442b')
    for _ in range(nobj):
        gid = d[p:p + 16]; sz = struct.unpack_from('<Q', d, p + 16)[0]
        if gid == FP:
            psize = struct.unpack_from('<I', d, p + 92)[0]
        elif gid == SP:
            flags = struct.unpack_from('<H', d, p + 72)[0]
            streams[flags & 0x7f] = 'audio' if d[p + 24:p + 40] == AUD else 'video'
        p += sz
    p = hsize + 50
    objs = {}
    while p + psize <= len(d):
        pk = d[p:p + psize]; q = 0
        if pk[0] & 0x80:
            q = 1 + (pk[0] & 15)
        lt, pf = pk[q], pk[q + 1]; q += 2
        def fld(t):
            nonlocal q
            n = [0, 1, 2, 4][t & 3]
            v = int.from_bytes(pk[q:q + n], 'little') if n else 0
            q += n
            return v
        multi = lt & 1
        plen = fld(lt >> 5); fld(lt >> 1); pad = fld(lt >> 3)
        plen = plen or psize
        q += 6
        end = plen - pad
        npay, plt = (pk[q] & 0x3f, pk[q] >> 6) if multi else (1, 0)
        if multi:
            q += 1
        for _ in range(npay):
            sn = pk[q] & 0x7f; q += 1
            num = fld(pf >> 4); off = fld(pf >> 2); rl = fld(pf)
            q += rl
            if multi:
                l = fld(plt)
            else:
                l = end - q
            objs.setdefault(sn, {}).setdefault(num, bytearray())
            objs[sn][num] += pk[q:q + l]
            if frags is not None:
                frags.append((sn, num, p + q, l))
            q += l
        p += psize
    return streams, objs


def audio_payloads(path):
    streams, objs = objects(path)
    a = [s for s, k in streams.items() if k == 'audio'][0]
    return [bytes(objs[a][k]) for k in sorted(objs[a])]
