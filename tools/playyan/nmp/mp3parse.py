"""MPEG-1 Layer III bitstream parsing (ISO 11172-3): frames, side info, scalefactors, Huffman.
Huffman trees from PDMP3 (public domain, ref/PDMP3/pdmp3.c), standard tables only."""
import re
import os
WORK = os.environ.get('PLAYYAN_WORK', '')  # the local emulator tree (not in this repository)
_src = open(os.environ.get('PDMP3_C', WORK + '/emu/nmp_lle/ref/PDMP3/pdmp3.c')).read()  # PDMP3 (public domain)
_tab = _src[_src.index('g_huffman_table[] = {'):]
_tab = _tab[:_tab.index('};')]
_tab = re.sub(r'//[^\n]*', '', _tab)
HT = [int(x, 16) for x in re.findall(r'0x[0-9a-fA-F]+', _tab)]
_main = _src[_src.index('g_huffman_main [34] = {'):]
_main = _main[:_main.index('};')]
HMAIN = []
for m in re.finditer(r'\{(NULL|g_huffman_table\s*(?:\+(\d+))?)\s*,\s*(\d+),\s*(\d+)\s*\}', _main):
    off = None if m.group(1) == 'NULL' else int(m.group(2) or 0)
    HMAIN.append((off, int(m.group(3)), int(m.group(4))))
assert len(HMAIN) == 34

BITRATE = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
RATES = [44100, 48000, 32000]
RATES_V = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
BITRATE_LSF = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
SFB_LONG = {44100: [0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 52, 62, 74, 90, 110, 134, 162, 196, 238, 288, 342, 418, 576],
            48000: [0, 4, 8, 12, 16, 20, 24, 30, 36, 42, 50, 60, 72, 88, 106, 128, 156, 190, 230, 276, 330, 384, 576],
            32000: [0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 54, 66, 82, 102, 126, 156, 194, 240, 296, 364, 448, 550, 576],
            22050: [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
            24000: [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 114, 136, 162, 194, 232, 278, 332, 394, 464, 540, 576],
            16000: [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
            11025: [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
            12000: [0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576],
            8000: [0, 12, 24, 36, 48, 60, 72, 88, 108, 132, 160, 192, 232, 280, 336, 400, 476, 566, 568, 570, 572, 574, 576]}
SFB_SHORT = {44100: [0, 4, 8, 12, 16, 22, 30, 40, 52, 66, 84, 106, 136, 192],
             48000: [0, 4, 8, 12, 16, 22, 28, 38, 50, 64, 80, 100, 126, 192],
             32000: [0, 4, 8, 12, 16, 22, 30, 42, 58, 78, 104, 138, 180, 192],
             22050: [0, 4, 8, 12, 18, 24, 32, 42, 56, 74, 100, 132, 174, 192],
             24000: [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 136, 180, 192],
             16000: [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192],
             11025: [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192],
             12000: [0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192],
             8000: [0, 8, 16, 24, 36, 52, 72, 96, 124, 160, 162, 164, 166, 192]}
LSF_NSF = [[[6, 5, 5, 5], [9, 9, 9, 9], [6, 9, 9, 9]],
           [[6, 5, 7, 3], [9, 9, 12, 6], [6, 9, 12, 6]],
           [[11, 10, 0, 0], [18, 18, 0, 0], [15, 18, 0, 0]],
           [[7, 7, 7, 0], [12, 12, 12, 0], [6, 15, 12, 0]],
           [[6, 6, 6, 3], [12, 9, 9, 6], [6, 12, 9, 6]],
           [[8, 8, 5, 0], [15, 12, 9, 0], [6, 18, 9, 0]]]
SLEN = [(0, 0), (0, 1), (0, 2), (0, 3), (3, 0), (1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3), (4, 2), (4, 3)]
PRETAB = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 3, 3, 3, 2, 0]

class Bits:
    def __init__(self, data, pos=0): self.d = data; self.p = pos
    def get(self, n):
        v = 0
        for _ in range(n):
            byte = self.d[self.p >> 3] if (self.p >> 3) < len(self.d) else 0
            v = v << 1 | (byte >> (7 - (self.p & 7))) & 1; self.p += 1
        return v

def skip_id3(data):
    p = 0
    while data[p:p + 3] == b'ID3':
        sz = (data[p + 6] & 0x7f) << 21 | (data[p + 7] & 0x7f) << 14 | (data[p + 8] & 0x7f) << 7 | (data[p + 9] & 0x7f)
        p += 10 + sz + (10 if data[p + 5] & 0x10 else 0)
    return p

def frames(data):
    p = skip_id3(data)
    while p + 4 <= len(data):
        h = int.from_bytes(data[p:p + 4], 'big')
        ver = (h >> 19) & 3
        if (h >> 21) & 0x7FF != 0x7FF or ver == 1 or (h >> 17) & 3 != 1:
            p += 1; continue
        bri = (h >> 12) & 15; sri = (h >> 10) & 3
        if bri in (0, 15) or sri == 3: p += 1; continue
        pad = (h >> 9) & 1; rate = RATES_V[ver][sri]
        size = (144000 * BITRATE[bri] // rate if ver == 3 else 72000 * BITRATE_LSF[bri] // rate) + pad
        yield dict(pos=p, h=h, crc=not (h >> 16) & 1, mode=(h >> 6) & 3, modeext=(h >> 4) & 3, rate=rate, size=size,
                   lsf=ver != 3, data=data[p:p + size])
        p += size

def side_info(fr):
    d = fr['data']; b = Bits(d, 32 + (16 if fr['crc'] else 0))
    nch = 1 if fr['mode'] == 3 else 2
    lsf = fr.get('lsf', False)
    si = dict(nch=nch, main_data_begin=b.get(8 if lsf else 9), lsf=lsf)
    b.get((1 if nch == 1 else 2) if lsf else (5 if nch == 1 else 3))
    si['scfsi'] = [[0] * 4 for _ in range(nch)] if lsf else [[b.get(1) for _ in range(4)] for _ in range(nch)]
    gr = []
    for g in range(1 if lsf else 2):
        chs = []
        for c in range(nch):
            gc = dict(part2_3_length=b.get(12), big_values=b.get(9), global_gain=b.get(8),
                      scalefac_compress=b.get(9 if lsf else 4), window_switching=b.get(1))
            if gc['window_switching']:
                gc['block_type'] = b.get(2); gc['mixed'] = b.get(1)
                gc['table_select'] = [b.get(5), b.get(5), 0]
                gc['subblock_gain'] = [b.get(3) for _ in range(3)]
                gc['region0_count'] = 8 if (gc['block_type'] == 2 and not gc['mixed']) else 7
                gc['region1_count'] = 36
            else:
                gc['block_type'] = 0; gc['mixed'] = 0
                gc['table_select'] = [b.get(5) for _ in range(3)]
                gc['subblock_gain'] = [0, 0, 0]
                gc['region0_count'] = b.get(4); gc['region1_count'] = b.get(3)
            gc['preflag'] = 0 if lsf else b.get(1)
            gc['scalefac_scale'] = b.get(1); gc['count1table'] = b.get(1)
            chs.append(gc)
        gr.append(chs)
    si['gr'] = gr
    si['hdr_bytes'] = 4 + (2 if fr['crc'] else 0) + ((9 if nch == 1 else 17) if lsf else (17 if nch == 1 else 32))
    return si

UNREAD_MAX = -1

def lsf_mixed(b, slen, t2, area, ch, rate):
    """2:9143.. the MPEG-2 mixed-block scalefactor reader, as the firmware does it.  Per channel a
    62-word area (long[0..22], short[23 + band + 13*window]) that persists across granules:
    * long bands 0..5 (read with slen0, or zeroed if slen0 = 0), then (nr0 - 6)/3 short bands with
      slen0 (or zeroed) - the short band counter CONTINUES the long counter, so they land at band 6;
    * groups k = 1..3: nr[k]/3 short bands at slen[k]; at the first group with slen[k] = 0 the
      firmware instead zeroes (nr[1]+nr[2]+nr[3])/3 bands from the current band on, reads no
      further groups, and the writes run past band 12 into the next window row and beyond the area;
    * finally band 12 is zeroed in all windows.
    Returns (sfl, sfs) as the scale-index builder reads them: long 0..5, short [band][window] =
    area[23 + band + 13*window] for band 3..12 (bands 3..5 keep whatever the area held)."""
    nr = LSF_NSF[t2][2]; base = 62 * ch
    def put(i, v):
        if 0 <= base + i < len(area): area[base + i] = v
    if slen[0]:
        for j in range(6): put(j, b.get(slen[0]))
    else:
        for j in range(6): put(j, 0)
    band = 6
    for _ in range((nr[0] - 6) // 3 if nr[0] - 6 >= 3 else 0):
        for w in range(3): put(23 + band + 13 * w, b.get(slen[0]) if slen[0] else 0)
        band += 1
    for k in range(1, 4):
        if slen[k] == 0:
            for kk in range(1, 4):
                if nr[kk] >= 3:
                    for _ in range(nr[kk] // 3):
                        for w in range(3): put(23 + band + 13 * w, 0)
                        band += 1
            break
        if nr[k] >= 3:
            for _ in range(nr[k] // 3):
                for w in range(3): put(23 + band + 13 * w, b.get(slen[k]))
                band += 1
    for w in range(3): put(35 + 13 * w, 0)
    sfl = [0] * 22; sfs = [[0] * 3 for _ in range(13)]
    for j in range(6): sfl[j] = area[base + j]
    def at(j): return area[base + j] if base + j < len(area) else 0
    if rate == 8000:
        # the builder walks the reorder's band list: long bands 0..2 (lines 0..35), then lines 36..71
        # as a 12-wide short band using area band 3, then short bands 3..12 using area band b + 1
        # (band 12 reads area[36 + 13w], i.e. the next window's band 0 and, for w = 2, word 62)
        sfs = [[at(23 + bb + 1 + 13 * w) for w in range(3)] for bb in range(13)]
        return sfl, sfs, [at(26 + 13 * w) for w in range(3)]
    for bb in range(3, 13):
        for w in range(3): sfs[bb][w] = at(23 + bb + 13 * w)
    return sfl, sfs
ZERO_SLEN_MAX = -1

def lsf_scalefactors(b, gc, is_right, area=None, ch=0, rate=0):
    """ISO 13818-3 scalefactors: returns (sfl[22], sfs[13][3]) and sets gc['preflag']"""
    sf = gc['scalefac_compress']
    if is_right:
        gc['intensity_scale'] = sf & 1; sf >>= 1; pre = 0
        if sf < 180: slen = [sf // 36, (sf % 36) // 6, (sf % 36) % 6, 0]; t2 = 3
        # FIRMWARE DEVIATION (2:90A3 / 2:908F): no -180 / -244, and % 63 / % 15 / % 3 instead of the
        # ISO % 64 / % 16 / % 4 (ISO 13818-3: x = isfc-180: (x%64)>>4, (x%16)>>2, x%4)
        elif sf < 244: slen = [(sf % 63) >> 4, (sf % 15) >> 2, sf % 3, 0]; t2 = 4
        else: slen = [sf // 3, sf % 3, 0, 0]; t2 = 5
    elif sf < 400: slen = [(sf >> 4) // 5, (sf >> 4) % 5, (sf % 16) >> 2, sf % 4]; t2 = 0; pre = 0
    elif sf < 500: sf -= 400; slen = [(sf >> 2) // 5, (sf >> 2) % 5, sf % 4, 0]; t2 = 1; pre = 0
    else: sf -= 500; slen = [sf // 3, sf % 3, 0, 0]; t2 = 2; pre = 1
    gc['preflag'] = pre
    short = gc['window_switching'] and gc['block_type'] == 2
    t1 = (2 if gc['mixed'] else 1) if short else 0
    if short and gc['mixed']:
        if is_right: raise NotImplementedError('MPEG-2 mixed block with intensity stereo')
        sfl, sfs, *pseudo = lsf_mixed(b, slen, t2, area, ch, rate)
        gc['_pseudo'] = pseudo[0] if pseudo else None
        return sfl, sfs
    vals = []; maxes = []
    for k in range(4):
        for _ in range(LSF_NSF[t2][t1][k]): vals.append(b.get(slen[k])); maxes.append((1 << slen[k]) - 1 if slen[k] else ZERO_SLEN_MAX)
    gc['_vals_max'] = maxes
    sfl = [0] * 22; sfs = [[0] * 3 for _ in range(13)]
    base = 62 * ch
    if short:
        for j, v in enumerate(vals):
            if j // 3 < 13: sfs[j // 3][j % 3] = v
        if area is not None:
            for bb in range(12):
                for w in range(3): area[base + 23 + bb + 13 * w] = sfs[bb][w]
            for w in range(3): area[base + 35 + 13 * w] = 0
    else:
        for j, v in enumerate(vals[:22]): sfl[j] = v
        if area is not None:
            for j in range(len(vals[:22])): area[base + j] = vals[j]
    # illegal intensity position per band (2^slen - 1); bands without scalefactors: UNREAD_MAX
    ml = [UNREAD_MAX] * 22; msh = [[UNREAD_MAX] * 3 for _ in range(13)]
    if short and not gc['mixed']:
        for j, v in enumerate(maxes):
            if j // 3 < 13: msh[j // 3][j % 3] = v
    elif not short:
        for j, v in enumerate(maxes[:22]): ml[j] = v
    gc['is_maxl'] = ml; gc['is_maxs'] = msh
    return sfl, sfs

def huff(b, tab, end):
    off, tlen, lin = HMAIN[tab]
    if off is None: return 0, 0
    point = 0; left = 32
    while True:
        e = HT[off + point]
        if e & 0xff00 == 0:
            x, y = (e >> 4) & 15, e & 15; break
        if b.get(1):
            while HT[off + point] & 0xff >= 250: point += HT[off + point] & 0xff
            point += HT[off + point] & 0xff
        else:
            while HT[off + point] >> 8 >= 250: point += HT[off + point] >> 8
            point += HT[off + point] >> 8
        left -= 1
        if left == 0 or point >= tlen: x = y = 0; break
    if lin and x == 15: x += b.get(lin)
    if x and b.get(1): x = -x
    if lin and y == 15: y += b.get(lin)
    if y and b.get(1): y = -y
    return x, y

def count1(b, tab):
    if tab == 1:
        v = 15 - b.get(4)
    else:
        off = 2742; point = 0
        while True:
            e = HT[off + point]
            if e & 0xff00 == 0: v = e & 15; break
            if b.get(1):
                while HT[off + point] & 0xff >= 250: point += HT[off + point] & 0xff
                point += HT[off + point] & 0xff
            else:
                while HT[off + point] >> 8 >= 250: point += HT[off + point] >> 8
                point += HT[off + point] >> 8
    out = []
    for k in (3, 2, 1, 0):
        q = (v >> k) & 1
        if q and b.get(1): q = -q
        out.append(q)
    return out

class Decoder:
    """bitstream front end: yields per granule a list of channels with is[], scalefactors, side info"""
    def __init__(self):
        self.res = bytearray()
        self.prev_sf = [[0] * 22 for _ in range(2)]
        # 0x8061: two 62-word scalefactor areas and word 124 (0x80DD, the frame's main_data_begin),
        # which channel 1's mixed reader always overwrites (band 13 window 2) before the 8 kHz
        # builder reads it; writes past it (channel 0's side info) are dead by then
        self.lsf_area = [0] * (62 * 2 + 1)
    def frame(self, fr):
        si = side_info(fr)
        main = fr['data'][si['hdr_bytes']:]
        start = len(self.res) - si['main_data_begin']
        ok = start >= 0
        self.res += main
        buf = bytes(self.res)
        self.res = self.res[-4096:] if len(self.res) > 4096 else self.res
        if not ok: return si, None
        b = Bits(buf, start * 8)
        rate = fr['rate']; out = []
        for g in range(len(si['gr'])):
            chs = []
            for c in range(si['nch']):
                gc = si['gr'][g][c]
                p0 = b.p
                sl1, sl2 = SLEN[gc['scalefac_compress'] & 15]
                sfl = [0] * 22; sfs = [[0] * 3 for _ in range(13)]
                if si['lsf']:
                    sfl, sfs = lsf_scalefactors(b, gc, c == 1 and fr['mode'] == 1 and fr['modeext'] & 1, self.lsf_area, c, fr['rate'])
                elif gc['window_switching'] and gc['block_type'] == 2:
                    if gc['mixed']:
                        for s in range(8): sfl[s] = b.get(sl1)
                        for s in range(3, 12):
                            for w in range(3): sfs[s][w] = b.get(sl1 if s < 6 else sl2)
                    else:
                        for s in range(12):
                            for w in range(3): sfs[s][w] = b.get(sl1 if s < 6 else sl2)
                else:
                    bands = [(0, 6), (6, 11), (11, 16), (16, 21)]
                    for k, (lo, hi) in enumerate(bands):
                        if g == 1 and si['scfsi'][c][k]:
                            for s in range(lo, hi): sfl[s] = self.prev_sf[c][s]
                        else:
                            for s in range(lo, hi): sfl[s] = b.get(sl1 if k < 2 else sl2)
                    self.prev_sf[c] = list(sfl)
                end = p0 + gc['part2_3_length']
                isv = [0] * 576
                bv = 2 * gc['big_values']
                if gc['window_switching']:
                    r1 = (36 if not si['lsf'] else (96 if rate == 8000 else 48)) if (gc['mixed'] and gc['block_type'] == 2) else (((72 if rate == 8000 else 36) if gc['block_type'] == 2 else 36) if not si['lsf'] or gc['block_type'] == 2 else (108 if rate == 8000 else 54)); r2 = 576
                else:
                    sfb = SFB_LONG[rate]
                    r1 = sfb[gc['region0_count'] + 1]; r2 = sfb[min(gc['region0_count'] + gc['region1_count'] + 2, 22)]
                i = 0
                while i < bv:
                    tab = gc['table_select'][0 if i < r1 else (1 if i < r2 else 2)]
                    isv[i], isv[i + 1] = huff(b, tab, end); i += 2
                # a quad starting at 574 is decoded and its first two values kept
                while i < 576 and b.p < end:
                    q = count1(b, gc['count1table'])
                    for k in range(4):
                        if i + k < 576: isv[i + k] = q[k]
                    i += 4
                if b.p > end:
                    i -= 4
                    for k in range(4):
                        if i + k < 576: isv[i + k] = 0
                i = min(i, 576)
                b.p = end
                chs.append(dict(gc=gc, isv=isv, nz=i, sfl=sfl, sfs=sfs))
            out.append(chs)
        return si, out

MIXED_LONG_LINES = lambda rate: 72 if rate == 8000 else 36

def scale_index(ch, rate):
    """per-line exponent in quarter steps (the requantiser's s): gain - 210 - 8*subblock - shift*sf"""
    gc = ch['gc']; s = [0] * 576
    mult = 2 * (1 + gc['scalefac_scale'])
    g = gc['global_gain'] - 210
    if gc['window_switching'] and gc['block_type'] == 2:
        sfb = SFB_SHORT[rate]; i = 0; b0 = 0
        if gc['mixed'] and gc.get('_pseudo') is not None:
            # 8 kHz MPEG-2.5 mixed (see lsf_mixed): long 0..35, a 12-wide short band, bands 3..12
            for b in range(3):
                sf = ch['sfl'][b] + gc['preflag'] * PRETAB[b]
                for i in range(SFB_LONG[rate][b], SFB_LONG[rate][b + 1]): s[i] = g - mult * sf
            i = 36
            for win in range(3):
                for k in range(12):
                    s[i] = g - 8 * gc['subblock_gain'][win] - mult * gc['_pseudo'][win]; i += 1
            for b in range(3, 13):
                for win in range(3):
                    for k in range(sfb[b + 1] - sfb[b]):
                        s[i] = g - 8 * gc['subblock_gain'][win] - mult * ch['sfs'][b][win]; i += 1
            return s
        if gc['mixed']:
            sl = SFB_LONG[rate]; L = MIXED_LONG_LINES(rate)
            nl = sl.index(L)
            for b in range(nl):
                sf = ch['sfl'][b] + gc['preflag'] * PRETAB[b]
                for i in range(sl[b], sl[b + 1]): s[i] = g - mult * sf
            i = L; b0 = sfb.index(L // 3)
        for b in range(b0, 13):
            w_ = sfb[b + 1] - sfb[b]
            for win in range(3):
                sf = ch['sfs'][b][win] if b < 12 else 0
                for k in range(w_):
                    s[i] = g - 8 * gc['subblock_gain'][win] - mult * sf; i += 1
    else:
        sfb = SFB_LONG[rate]
        for b in range(22):
            sf = (ch['sfl'][b] + (gc['preflag'] * PRETAB[b])) if b < 21 else 0
            for i in range(sfb[b], sfb[b + 1]): s[i] = g - mult * sf
    return s
