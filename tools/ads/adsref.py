"""Python reference model of the ADS video path (container, LZMA layer, codebook, blit).
Slow; used to develop and cross-check the C decoder."""
import struct, lzma
import adsfs

DIMS = {0: (4, 4), 1: (4, 4), 2: (4, 3), 3: (4, 3), 4: (3, 3), 5: (3, 3), 6: (4, 2), 7: (4, 2)}
CROWS = {0: 1, 1: 4, 2: 1, 3: 3, 4: 1, 5: 2, 6: 1, 7: 2}
# output pixel slot for each codebook luma byte (generic 4-wide path); 3x3 path is linear
POS44 = [0, 1, 4, 5, 2, 3, 6, 7, 8, 9, 12, 13, 10, 11, 14, 15]
POS43 = [0, 1, 4, 5, 2, 3, 6, 7, 8, 9, 10, 11]
DITHER = [7, 3, 6, 2, 1, 5, 0, 4, 6, 2, 7, 3, 0, 4, 1, 5]

def u32(b, o): return struct.unpack_from('<I', b, o)[0]

def parse_video(buf, off, size):
    w0, magic, nchunks = struct.unpack_from('<III', buf, off)
    p = off + 12
    z = buf.index(b'\0', p); title = adsfs.deobf(buf[p:z]); p += ((z - p) + 4) & ~3
    chaps = []
    for _ in range(w0 & 0xff):
        ci = struct.unpack_from('<H', buf, p)[0]
        z = buf.index(b'\0', p + 2); chaps.append((ci, adsfs.deobf(buf[p + 2:z]))); p += 2 + (((z - p - 2) + 4) & ~3)
    p = (p + 3) & ~3
    chunks = []
    for _ in range(nchunks):
        a, b = struct.unpack_from('<II', buf, p)
        sa, sb = (b & 0x1fff) * 4, (b >> 13) * 4
        chunks.append(dict(off=p, w0=a, w1=b, nfr=a >> 16, mode=a & 7, ncb=(a >> 6) & 0x3ff,
                           A=(p + 8, sa), B=(p + 8 + sa, sb)))
        p += 8 + sa + sb
    return dict(w0=w0, frames=(w0 >> 8) & 0xffff, magic=magic, title=title, chapters=chaps, chunks=chunks, end=p)

def unlzma(blob):
    us, par = struct.unpack_from('<II', blob, 0)
    lc, lp, pb = (par >> 6) & 15, (par >> 3) & 7, par & 7
    d = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{'id': lzma.FILTER_LZMA1, 'lc': lc, 'lp': lp, 'pb': pb, 'dict_size': 1 << 24}])
    return d.decompress(blob[8:], max_length=us)

def unpredict(cb, mode, count):
    bw, bh = DIMS[mode]
    cb = bytearray(cb)
    n = count * bw * bh
    acc = 0
    for i in range(n): acc = (acc + cb[i]) & 255; cb[i] = acc
    nc = 2 * CROWS[mode] * count
    acc = 0
    for i in range(n, n + nc): acc = (acc + cb[i]) & 255; cb[i] = acc
    return bytes(cb)

def clamp_default(v):
    return 0 if v < 0 else 255 if v > 255 else v

def five(k, v):
    return min(31, (v + DITHER[k]) >> 3)

def s8(x): return x - 256 if x >= 128 else x

def convert(cb, mode, count, clamp=clamp_default):
    """codebook (YCbCr, unpredicted) -> count entries of 16 BGR555 halfwords (32 bytes)"""
    bw, bh = DIMS[mode]
    n = count * bw * bh
    out = [0] * (count * 16)
    def px(y, cbv, crv, k):
        r = five(k, clamp(y + 2 * crv)); g = five(k, clamp(y - (cbv >> 1) - crv)); b = five(k, clamp(y + 2 * cbv))
        return r | g << 5 | b << 10
    if mode in (4, 5):
        cbp, crp = n, n + 2 * count
        for e in range(count):
            for j in range(9):
                c = 0 if j < 6 else 1
                out[e * 16 + j] = px(cb[e * 9 + j], s8(cb[cbp + 2 * e + c]), s8(cb[crp + 2 * e + c]), j)
            out[e * 16 + 9] = 0
        return out
    pos = POS43 if mode in (2, 3) else POS44
    cbp = n; crp = n + n // 4
    li = 0; ci = 0
    for e in range(count):
        for r in range(bh):
            for j in range(4):
                k = pos[r * 4 + j]
                out[e * 16 + k] = px(cb[li], s8(cb[cbp + ci]), s8(cb[crp + ci]), k)
                li += 1
            ci += 1
    return out

def blit(idx, rgb, mode, pal=b'\0' * 48):
    """-> 240*160 list of BGR555"""
    fb = [0] * (240 * 160)
    if mode in (2, 3):
        for by in range(53):
            for bx in range(60):
                e = idx[by * 60 + bx]
                for y in range(3):
                    for x in range(4):
                        fb[(by * 3 + y) * 240 + bx * 4 + x] = rgb[e * 16 + y * 4 + x]
        pw = struct.unpack('<24H', pal[:48])
        for x in range(240): fb[159 * 240 + x] = pw[x % 24]
    elif mode in (0, 1):
        for by in range(40):
            for bx in range(60):
                e = idx[by * 60 + bx]
                for y in range(4):
                    for x in range(4):
                        fb[(by * 4 + y) * 240 + bx * 4 + x] = rgb[e * 16 + y * 4 + x]
    else:
        raise NotImplementedError(mode)
    return fb

def frames(buf, v):
    """yield (frame_no, chunk_index, idx_plane, rgb) for a parsed video resource"""
    fno = 0
    for ci, ch in enumerate(v['chunks']):
        A = buf[ch['A'][0]:ch['A'][0] + ch['A'][1]]; B = buf[ch['B'][0]:ch['B'][0] + ch['B'][1]]
        cb = unpredict(unlzma(A), ch['mode'], ch['ncb'])
        rgb = convert(cb, ch['mode'], ch['ncb'])
        ix = unlzma(B)
        bw, bh = DIMS[ch['mode']]
        fs = (240 // bw) * (160 // bh)
        for k in range(ch['nfr']):
            yield fno, ci, ix[k * fs:(k + 1) * fs], rgb
            fno += 1
