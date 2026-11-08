#!/usr/bin/env python3
"""Python port of the public Sun/CCITT G.72x reference (g72x.c + g721.c), G.726-32 (G.721).
Written from the algorithm description; short (16-bit) storage semantics are applied where the
C code stores into `short` state fields."""

def s16(v):
    v &= 0xffff
    return v - 0x10000 if v & 0x8000 else v

POWER2 = [1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000]
QTAB_721 = [-124, 80, 178, 246, 300, 349, 400]
DQLNTAB = [-2048, 4, 135, 213, 273, 323, 373, 425, 425, 373, 323, 273, 213, 135, 4, -2048]
WITAB = [-12, 18, 41, 64, 112, 198, 355, 1122, 1122, 355, 198, 112, 64, 41, 18, -12]
FITAB = [0, 0, 0, 0x200, 0x200, 0x200, 0x600, 0xE00, 0xE00, 0x600, 0x200, 0x200, 0x200, 0, 0, 0]


def quan(val, table, size):
    for i in range(size):
        if val < table[i]:
            return i
    return size


def fmult(an, srn):
    anmag = an if an > 0 else ((-an) & 0x1FFF)
    anexp = quan(anmag, POWER2, 15) - 6
    if anmag == 0:
        anmant = 32
    elif anexp >= 0:
        anmant = anmag >> anexp
    else:
        anmant = anmag << -anexp
    wanexp = anexp + ((srn >> 6) & 0xF) - 13
    wanmant = (anmant * (srn & 0o77) + 0x30) >> 4
    retval = ((wanmant << wanexp) & 0x7FFF) if wanexp >= 0 else (wanmant >> -wanexp)
    return -retval if (an ^ srn) < 0 else retval


class State:
    def __init__(self):
        self.yl = 34816
        self.yu = 544
        self.dms = 0
        self.dml = 0
        self.ap = 0
        self.a = [0, 0]
        self.b = [0] * 6
        self.pk = [0, 0]
        self.dq = [32] * 6
        self.sr = [32, 32]
        self.td = 0

    def copy(self):
        s = State()
        s.__dict__.update({k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()})
        return s

    def __eq__(self, o):
        return self.__dict__ == o.__dict__


def predictor_zero(st):
    sezi = fmult(st.b[0] >> 2, st.dq[0])
    for i in range(1, 6):
        sezi += fmult(st.b[i] >> 2, st.dq[i])
    return sezi


def predictor_pole(st):
    return fmult(st.a[1] >> 2, st.sr[1]) + fmult(st.a[0] >> 2, st.sr[0])


def step_size(st):
    if st.ap >= 256:
        return st.yu
    y = st.yl >> 6
    dif = st.yu - y
    al = st.ap >> 2
    if dif > 0:
        y += (dif * al) >> 6
    elif dif < 0:
        y += (dif * al + 0x3F) >> 6
    return y


def quantize(d, y, table, size):
    dqm = abs(d)
    exp = quan(dqm >> 1, POWER2, 15)
    mant = ((dqm << 7) >> exp) & 0x7F
    dl = (exp << 7) + mant
    dln = dl - (y >> 2)
    i = quan(dln, table, size)
    if d < 0:
        return (size << 1) + 1 - i
    elif i == 0:
        return (size << 1) + 1
    return i


def reconstruct(sign, dqln, y):
    dql = dqln + (y >> 2)
    if dql < 0:
        return -0x8000 if sign else 0
    dex = (dql >> 7) & 15
    dqt = 128 + (dql & 127)
    dq = (dqt << 7) >> (14 - dex)
    return dq - 0x8000 if sign else dq


def _float(v):
    """dq/sr floating format for a positive magnitude"""
    exp = quan(v, POWER2, 15)
    return (exp << 6) + ((v << 6) >> exp)


def update(code_size, y, wi, fi, dq, sr, dqsr, st):
    pk0 = 1 if dqsr < 0 else 0
    mag = dq & 0x7FFF
    ylint = st.yl >> 15
    ylfrac = (st.yl >> 10) & 0x1F
    thr1 = (32 + ylfrac) << ylint
    thr2 = (31 << 10) if ylint > 9 else thr1
    dqthr = (thr2 + (thr2 >> 1)) >> 1
    if st.td == 0:
        tr = 0
    elif mag <= dqthr:
        tr = 0
    else:
        tr = 1
    st.yu = s16(y + ((wi - y) >> 5))
    if st.yu < 544:
        st.yu = 544
    elif st.yu > 5120:
        st.yu = 5120
    st.yl += st.yu + ((-st.yl) >> 6)
    if tr == 1:
        st.a = [0, 0]
        st.b = [0] * 6
        a2p = 0
    else:
        pks1 = pk0 ^ st.pk[0]
        a2p = st.a[1] - (st.a[1] >> 7)
        if dqsr != 0:
            fa1 = st.a[0] if pks1 else -st.a[0]
            if fa1 < -8191:
                a2p -= 0x100
            elif fa1 > 8191:
                a2p += 0xFF
            else:
                a2p += fa1 >> 5
            if pk0 ^ st.pk[1]:
                if a2p <= -12160:
                    a2p = -12288
                elif a2p >= 12416:
                    a2p = 12288
                else:
                    a2p -= 0x80
            elif a2p <= -12416:
                a2p = -12288
            elif a2p >= 12160:
                a2p = 12288
            else:
                a2p += 0x80
        st.a[1] = s16(a2p)
        a0 = st.a[0] - (st.a[0] >> 8)
        if dqsr != 0:
            a0 = a0 + 192 if pks1 == 0 else a0 - 192
        a1ul = 15360 - a2p
        if a0 < -a1ul:
            a0 = -a1ul
        elif a0 > a1ul:
            a0 = a1ul
        st.a[0] = s16(a0)
        for c in range(6):
            b = st.b[c]
            b -= (b >> 9) if code_size == 5 else (b >> 8)
            if dq & 0x7FFF:
                b = b + 128 if (dq ^ st.dq[c]) >= 0 else b - 128
            st.b[c] = s16(b)
    st.dq[1:6] = st.dq[0:5]
    if mag == 0:
        st.dq[0] = 0x20 if dq >= 0 else s16(0xFC20)
    else:
        st.dq[0] = s16(_float(mag) if dq >= 0 else _float(mag) - 0x400)
    st.sr[1] = st.sr[0]
    if sr == 0:
        st.sr[0] = 0x20
    elif sr > 0:
        st.sr[0] = s16(_float(sr))
    elif sr > -32768:
        st.sr[0] = s16(_float(-sr) - 0x400)
    else:
        st.sr[0] = s16(0xFC20)
    st.pk[1] = st.pk[0]
    st.pk[0] = pk0
    if tr == 1:
        st.td = 0
    elif a2p < -11776:
        st.td = 1
    else:
        st.td = 0
    st.dms = s16(st.dms + ((fi - st.dms) >> 5))
    st.dml = s16(st.dml + (((fi << 2) - st.dml) >> 7))
    if tr == 1:
        st.ap = 256
    elif y < 1536:
        st.ap += (0x200 - st.ap) >> 4
    elif st.td == 1:
        st.ap += (0x200 - st.ap) >> 4
    elif abs((st.dms << 2) - st.dml) >= (st.dml >> 3):
        st.ap += (0x200 - st.ap) >> 4
    else:
        st.ap += (-st.ap) >> 4
    st.ap = s16(st.ap)


def g721_encoder(sl, st):
    """sl: 16-bit linear PCM sample (in_coding = linear)."""
    sl >>= 2
    sezi = predictor_zero(st)
    sez = sezi >> 1
    se = (sezi + predictor_pole(st)) >> 1
    d = sl - se
    y = step_size(st)
    i = quantize(d, y, QTAB_721, 7)
    dq = reconstruct(i & 8, DQLNTAB[i], y)
    sr = (se - (dq & 0x3FFF)) if dq < 0 else se + dq
    dqsr = sr + sez - se
    update(4, y, WITAB[i] << 5, FITAB[i], dq, sr, dqsr, st)
    return i


def g721_decoder(i, st, clamp=True):
    """returns linear PCM (sr << 2); firmware clamps to int16, Sun reference does not."""
    i &= 0x0f
    sezi = predictor_zero(st)
    sez = sezi >> 1
    se = (sezi + predictor_pole(st)) >> 1
    y = step_size(st)
    dq = reconstruct(i & 0x08, DQLNTAB[i], y)
    sr = (se - (dq & 0x3FFF)) if dq < 0 else se + dq
    dqsr = sr - se + sez
    update(4, y, WITAB[i] << 5, FITAB[i], dq, sr, dqsr, st)
    out = sr << 2
    if clamp:
        out = max(-32768, min(32767, out))
    return out


if __name__ == '__main__':
    import math
    st = State(); st2 = State(); err = 0; n = 0
    for k in range(4000):
        x = int(12000 * math.sin(k * 0.05) + 3000 * math.sin(k * 0.71))
        c = g721_encoder(x, st)
        y = g721_decoder(c, st2)
        err += (x - y) ** 2; n += x * x
    print('SNR %.1f dB' % (10 * math.log10(n / err)))


# ---- G.723 24 and 40 kbit/s (Sun g723_24.c / g723_40.c); witab values are used unshifted there
RATES = {
    24: dict(bits=3, size=3, code_size=3,
             qtab=[8, 218, 331],
             dqln=[-2048, 135, 273, 373, 373, 273, 135, -2048],
             wi=[-128, 960, 4384, 18624, 18624, 4384, 960, -128],
             fi=[0, 0x200, 0x400, 0xE00, 0xE00, 0x400, 0x200, 0]),
    32: dict(bits=4, size=7, code_size=4, qtab=QTAB_721, dqln=DQLNTAB,
             wi=[w << 5 for w in WITAB], fi=FITAB),
    40: dict(bits=5, size=15, code_size=5, srmask=0x7FFF,   # g723_40.c masks dq with 0x7FFF
             qtab=[-122, -16, 68, 139, 198, 250, 298, 339, 378, 413, 445, 475, 502, 528, 553],
             dqln=[-2048, -66, 28, 104, 169, 224, 274, 318, 358, 395, 429, 459, 488, 514, 539, 566,
                   566, 539, 514, 488, 459, 429, 395, 358, 318, 274, 224, 169, 104, 28, -66, -2048],
             wi=[448, 448, 768, 1248, 1280, 1312, 1856, 3200, 4512, 5728, 7008, 8960, 11456, 14080,
                 16928, 22272, 22272, 16928, 14080, 11456, 8960, 7008, 5728, 4512, 3200, 1856, 1312,
                 1280, 1248, 768, 448, 448],
             fi=[0, 0, 0, 0, 0, 0x200, 0x200, 0x200, 0x200, 0x200, 0x400, 0x600, 0x800, 0xA00, 0xC00,
                 0xC00, 0xC00, 0xC00, 0xA00, 0x800, 0x600, 0x400, 0x200, 0x200, 0x200, 0x200, 0x200,
                 0, 0, 0, 0, 0]),
}


def encoder(rate, sl, st):
    t = RATES[rate]
    sl >>= 2
    sezi = predictor_zero(st)
    sez = sezi >> 1
    se = (sezi + predictor_pole(st)) >> 1
    d = sl - se
    y = step_size(st)
    i = quantize(d, y, t['qtab'], t['size'])
    dq = reconstruct(i & (1 << (t['bits'] - 1)), t['dqln'][i], y)
    sr = (se - (dq & t.get('srmask', 0x3FFF))) if dq < 0 else se + dq
    dqsr = sr + sez - se
    update(t['code_size'], y, t['wi'][i], t['fi'][i], dq, sr, dqsr, st)
    return i


def decoder(rate, i, st, clamp=True):
    t = RATES[rate]
    i &= (1 << t['bits']) - 1
    sezi = predictor_zero(st)
    sez = sezi >> 1
    se = (sezi + predictor_pole(st)) >> 1
    y = step_size(st)
    dq = reconstruct(i & (1 << (t['bits'] - 1)), t['dqln'][i], y)
    sr = (se - (dq & t.get('srmask', 0x3FFF))) if dq < 0 else se + dq
    dqsr = sr - se + sez
    update(t['code_size'], y, t['wi'][i], t['fi'][i], dq, sr, dqsr, st)
    out = sr << 2
    return max(-32768, min(32767, out)) if clamp else out
