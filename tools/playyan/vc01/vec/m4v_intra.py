#!/usr/bin/env python3
"""Minimal MPEG-4 Part 2 (simple profile) I-VOP decoder in Python, with a pluggable IDCT.

Used as the front end of the thumbnail end-to-end check (pix_test.py t_garage: with the firmware's
IDCT run in the emulator, the thumbnails hash to the GBA ROM's Garage Game table).  During the work it
was bit-exact against ffmpeg -idct xvid with a port of that IDCT (not included: LGPL).
decode(path) -> (W, H, Y, U, V) with planar uint8 lists; dequantised coefficient blocks are
passed to IDCT(block8x8) -> 8x8 ints (before clamping)."""
import re, sys, os
# ISO/IEC 14496-2 / H.263 standard tables, as committed in reference/playyan/video/m4v_tables.h
# (tools/playyan/video/gen_std_tables.py)
HDR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..', 'reference', 'playyan', 'video', 'm4v_tables.h')
_T = open(HDR).read()


def carr(name):
    m = re.search(r'\b' + re.escape(name) + r'\s*(\[[^\]]*\])+\s*=\s*\{(.*?)\};', _T, re.S)
    return [int(x, 0) for x in re.findall(r'-?0x[0-9a-fA-F]+|-?\d+', m.group(2))]


def pairs(name):
    v = carr(name)
    return list(zip(v[0::2], v[1::2]))


ZIGZAG = carr('ZIGZAG')
ALT_H = carr('ALT_HORIZ')
ALT_V = carr('ALT_VERT')
YDC = carr('Y_DC_SCALE')
CDC = carr('C_DC_SCALE')
DCTHR = carr('DC_THRESHOLD')
INTRA_VLC = pairs('INTRA_VLC')
INTRA_RUN = carr('INTRA_RUN')
INTRA_LEVEL = carr('INTRA_LEVEL')
RL_N, RL_LAST = 102, 67
DC_LUM = pairs('DC_LUM')
DC_CHR = pairs('DC_CHROM')
MCBPC = pairs('INTRA_MCBPC')
CBPY = pairs('CBPY')

MAX_LEVEL = [[0] * 65 for _ in range(2)]; MAX_RUN = [[0] * 65 for _ in range(2)]
for _i in range(RL_N):
    _l = int(_i >= RL_LAST)
    r, lv = INTRA_RUN[_i], INTRA_LEVEL[_i]
    MAX_LEVEL[_l][r] = max(MAX_LEVEL[_l][r], lv); MAX_RUN[_l][lv] = max(MAX_RUN[_l][lv], r)


def mkvlc(tab):
    return {(bits, code): i for i, (code, bits) in enumerate(tab)}


V_INTRA = mkvlc(INTRA_VLC); V_DCL = mkvlc(DC_LUM); V_DCC = mkvlc(DC_CHR)
V_MCBPC = mkvlc(MCBPC); V_CBPY = mkvlc(CBPY)


class Bits:
    def __init__(self, data, pos=0):
        self.d = data; self.p = pos

    def get(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1); self.p += 1
        return v

    def show(self, n):
        p = self.p; v = self.get(n); self.p = p; return v

    def sget(self, n):
        v = self.get(n)
        return v - (1 << n) if v >> (n - 1) else v

    def vlc(self, tab, maxbits=13):
        code = 0
        for n in range(1, maxbits + 1):
            code = (code << 1) | self.get(1)
            if (n, code) in tab:
                return tab[(n, code)]
        raise ValueError('bad vlc at %d' % self.p)

    def align(self):
        self.p = (self.p + 7) & ~7


def find_start(d, code, pos=0):
    i = d.find(bytes([0, 0, 1, code]), pos)
    return i


class Decoder:
    def __init__(self, data, idct):
        self.d = data; self.idct = idct
        self.vol()

    def vol(self):
        d = self.d
        i = next(i for i in range(len(d) - 4) if d[i:i + 3] == b'\0\0\1' and 0x20 <= d[i + 3] <= 0x2f)
        b = Bits(d, (i + 4) * 8)
        b.get(1); b.get(8)
        if b.get(1):
            self.verid = b.get(4); b.get(3)
        else:
            self.verid = 1
        ar = b.get(4)
        if ar == 15: b.get(16)
        if b.get(1):
            b.get(2); b.get(1)
            if b.get(1): b.get(15); b.get(1); b.get(15); b.get(1); b.get(15); b.get(1); b.get(3); b.get(11); b.get(1); b.get(15); b.get(1)
        shape = b.get(2); assert shape == 0
        b.get(1); res = b.get(16); b.get(1)
        self.tib = max(1, (res - 1).bit_length())
        if b.get(1): b.get(self.tib)
        b.get(1); self.W = b.get(13); b.get(1); self.H = b.get(13); b.get(1)
        assert b.get(1) == 0, 'interlaced'
        b.get(1)                                     # obmc_disable
        sprite = b.get(1) if self.verid == 1 else b.get(2); assert sprite == 0
        if b.get(1): b.get(4); b.get(4)              # not_8_bit
        self.mpeg_quant = b.get(1)
        assert not self.mpeg_quant, 'MPEG quant matrices not implemented'
        if self.verid != 1: b.get(1)                 # quarter_sample
        assert b.get(1) == 1, 'complexity estimation'
        self.resync = not b.get(1)
        self.dp = b.get(1); assert not self.dp, 'data partitioning'
        if self.verid != 1:
            assert not b.get(1); b.get(1)
        assert b.get(1) == 0, 'scalability'

    def vop(self):
        d = self.d
        i = find_start(d, 0xb6)
        b = Bits(d, (i + 4) * 8)
        t = b.get(2); assert t == 0, 'not an I-VOP'
        while b.get(1): pass
        b.get(1); b.get(self.tib); b.get(1)
        assert b.get(1) == 1, 'vop not coded'
        self.dcthr = DCTHR[b.get(3)]
        self.q = b.get(5)
        return b

    def decode(self):
        b = self.vop()
        W, H = self.W, self.H
        mbw, mbh = (W + 15) // 16, (H + 15) // 16
        self.Y = [0] * (mbw * 16 * mbh * 16); self.U = [0] * (mbw * 8 * mbh * 8); self.V = [0] * (mbw * 8 * mbh * 8)
        # per 8x8 block position: dc (dequantised), ac row/col (quantised levels), slice id, qscale
        self.dcv = {}; self.acv = {}; self.slice = {}; self.qs = {}
        sl = 0
        mb = 0
        while mb < mbw * mbh:
            if self.resync and mb and self.is_resync(b):
                b.get(17)
                nb = (mbw * mbh - 1).bit_length()
                mb = b.get(nb)
                self.q = b.get(5)
                if b.get(1):                      # HEC
                    while b.get(1): pass
                    b.get(1); b.get(self.tib); b.get(1); b.get(2); b.get(3)
                sl += 1
            self.mb(b, mb % mbw, mb // mbw, sl)
            mb += 1
        cw = mbw * 8
        Y = [self.Y[y * mbw * 16 + x] for y in range(H) for x in range(W)]
        U = [self.U[y * cw + x] for y in range(H // 2) for x in range(W // 2)]
        V = [self.V[y * cw + x] for y in range(H // 2) for x in range(W // 2)]
        return W, H, Y, U, V

    def is_resync(self, b):
        p = b.p
        if p & 7 == 0:
            # stuffing must precede; check aligned resync marker
            pass
        # next byte-aligned position: stuffing bits '0111..' then 17-bit marker
        q = (p + 8) & ~7 if True else p
        nbits = q - p
        if nbits == 0:
            nbits = 8; q = p + 8
        save = b.p
        stuff = b.get(nbits)
        ok = stuff == (1 << (nbits - 1)) - 1 and b.show(17) == 1
        if ok:
            return True
        b.p = save
        return False

    def mb(self, b, mx, my, sl):
        while True:
            c = b.vlc(V_MCBPC)
            if c != 8: break
        cbpc = c & 3; mtype = 3 + (c >> 2)
        acpred = b.get(1)
        cbpy = b.vlc(V_CBPY)
        if mtype == 4:
            self.q = max(1, min(31, self.q + [-1, -2, 1, 2][b.get(2)]))
        cbp = cbpy << 2 | cbpc
        for n in range(6):
            coded = (cbp >> (5 - n)) & 1
            if n < 4:
                bx, by, comp = 2 * mx + (n & 1), 2 * my + (n >> 1), 0
            else:
                bx, by, comp = mx, my, n - 3
            blk = self.block(b, n, bx, by, comp, coded, acpred, sl)
            out = self.idct(blk)
            if comp == 0:
                P, stride = self.Y, ((self.W + 15) // 16) * 16
            else:
                P, stride = (self.U if comp == 1 else self.V), ((self.W + 15) // 16) * 8
            for y in range(8):
                for x in range(8):
                    P[(by * 8 + y) * stride + bx * 8 + x] = max(0, min(255, out[y][x]))

    def block(self, b, n, bx, by, comp, coded, acpred, sl):
        q = self.q
        scale = YDC[q] if comp == 0 else CDC[q]
        key = lambda x, y: (comp, x, y)
        def dcat(x, y):
            k = key(x, y)
            if k in self.dcv and self.slice[k] == sl:
                return self.dcv[k]
            return 1024
        a = dcat(bx - 1, by); bb = dcat(bx - 1, by - 1); c = dcat(bx, by - 1)
        if abs(a - bb) < abs(bb - c):
            pred, direc = c, 1
        else:
            pred, direc = a, 0
        blk = [0] * 64
        use_vlc = q < self.dcthr
        assert use_vlc, 'intra_dc_vlc_thr path not implemented'
        size = b.vlc(V_DCL if comp == 0 else V_DCC)
        if size == 0:
            lv = 0
        else:
            v = b.get(size)
            lv = v if v >> (size - 1) else v - (1 << size) + 1
            if size > 8:
                b.get(1)
        lv += (pred + (scale >> 1)) // scale
        dq = lv * scale
        dq = 0 if dq < 0 else 2047 if dq > 2047 else dq
        self.dcv[key(bx, by)] = dq; self.slice[key(bx, by)] = sl; self.qs[key(bx, by)] = q
        blk[0] = lv
        if coded:
            scan = (ALT_V if direc == 0 else ALT_H) if acpred else ZIGZAG
            i = 0
            while True:
                idx = b.vlc(V_INTRA)
                if idx == RL_N:                         # escape
                    if b.get(1) == 0:                   # first escape: level offset
                        idx = b.vlc(V_INTRA)
                        last = int(idx >= RL_LAST); run = INTRA_RUN[idx]; level = INTRA_LEVEL[idx]
                        level += MAX_LEVEL[last][run]
                    elif b.get(1) == 0:                 # second escape: run offset
                        idx = b.vlc(V_INTRA)
                        last = int(idx >= RL_LAST); run = INTRA_RUN[idx]; level = INTRA_LEVEL[idx]
                        run += MAX_RUN[last][level] + 1
                    else:                               # third escape: fixed length
                        last = b.get(1); run = b.get(6); b.get(1); level = b.sget(12); b.get(1)
                        i += run + 1
                        blk[scan[i]] = level
                        if last: break
                        continue
                    if b.get(1): level = -level
                else:
                    last = int(idx >= RL_LAST); run = INTRA_RUN[idx]; level = INTRA_LEVEL[idx]
                    if b.get(1): level = -level
                i += run + 1
                blk[scan[i]] = level
                if last: break
        # AC prediction (quantised levels), then store row/col for neighbours
        if acpred:
            if direc == 0:
                k = key(bx - 1, by)
                if k in self.acv and self.slice[k] == sl:
                    src = self.acv[k][0]; qs = self.qs[k]
                    for j in range(1, 8):
                        v = src[j] if qs == q else rdiv(src[j] * qs, q)
                        blk[j * 8] += v
            else:
                k = key(bx, by - 1)
                if k in self.acv and self.slice[k] == sl:
                    src = self.acv[k][1]; qs = self.qs[k]
                    for j in range(1, 8):
                        v = src[j] if qs == q else rdiv(src[j] * qs, q)
                        blk[j] += v
        self.acv[key(bx, by)] = ([0] + [blk[j * 8] for j in range(1, 8)], [0] + [blk[j] for j in range(1, 8)])
        # dequantise (H.263 method)
        out = [0] * 64
        out[0] = lv * scale
        qmul, qadd = 2 * q, (q - 1) | 1
        for j in range(1, 64):
            l = blk[j]
            if l:
                v = l * qmul + qadd if l > 0 else l * qmul - qadd
                out[j] = v
        return [out[r * 8:r * 8 + 8] for r in range(8)]


def rdiv(a, b):
    return (a + (b >> 1)) // b if a >= 0 else -((-a + (b >> 1)) // b)


def decode(path, idct):
    return Decoder(open(path, 'rb').read(), idct).decode()


if __name__ == '__main__':
    # m4v_intra.py NAME...: decode $PLAYYAN_WORK/vec/m4v/NAME.m4v with the firmware IDCT model and compare
    # with ffmpeg's frame (vec/prep_media.py); the difference is the IDCT, not the parser
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    import workdir
    from idct_ref import fw_idct
    D = workdir.work('vec')
    for n in sys.argv[1:]:
        W, H, Y, U, V = decode(os.path.join(D, 'm4v', n + '.m4v'), fw_idct)
        ref = open(os.path.join(D, 'frames', n + '_xvid.yuv'), 'rb').read()
        mine = bytes(Y + U + V)
        diff = sum(a != b for a, b in zip(mine, ref))
        print(n, W, H, 'bytes differing from ffmpeg -idct xvid:', diff, 'of', len(ref))
