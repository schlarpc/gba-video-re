#!/usr/bin/env python3
"""Pure-Python fixed-point model of the AAC IMDCT of mi_fw0 / fup_sec0 (0x97d6 N=2048, 0x99d8 N=256),
written from the firmware's algorithm; every stage is checked bit-exact against the emulator by
vec/aac_test.py (t_fft_stages, t_imdct_exact).  Data are complex int32; tables read from the image."""
import struct, os

import sys
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import workdir
FW = workdir.firmware(os.environ.get('AAC_IMG', 'mi_fw0'))
IMG = open(FW, 'rb').read()


def W32(x):
    return ((x + 2**31) % 2**32) - 2**31


def s16(x):
    x &= 0xffff
    return x - 0x10000 if x & 0x8000 else x


def sat16(x):
    return max(-32768, min(32767, x))


def M(x, w):
    """firmware 32x32 -> high-32 product (0xa5ec): xh*wh (vmul + vmulhd.ss) + the two cross partials
    xh*wl_u and wh*xl_u via vmulhn.su (op21: (p + 0x8000) >> 16); the lo*lo term is dropped."""
    xh, xl = x >> 16, x & 0xffff
    wh, wl = w >> 16, w & 0xffff
    return W32(xh * wh + ((xh * wl + 0x8000) >> 16) + ((wh * xl + 0x8000) >> 16))


def cmul(x, y, c, s):
    return W32(M(x, c) - M(y, s)), W32(M(x, s) + M(y, c))


def shl2_sat(x):
    """vlsr #14 clra acc / vshl #2 / vshls #2 acc: lo <<= 2 (wrap), hi = sat16(sat16(hi<<2) + (lo>>14))"""
    lo = x & 0xffff; hi = x >> 16
    nhi = sat16(sat16(hi << 2) + (lo >> 14))
    return W32((nhi << 16) | ((lo << 2) & 0xffff))


def h16(a, i):
    return struct.unpack_from('<h', IMG, a + 2 * i)[0]


def tw_block(addr):
    """twiddle block (128 bytes): 16 x (cos32, sin32) from [c lo][c hi][s lo][s hi]"""
    h = struct.unpack_from('<64h', IMG, addr)
    c = [W32(h[16 + t] << 16 | (h[t] & 0xffff)) for t in range(16)]
    s = [W32(h[48 + t] << 16 | (h[32 + t] & 0xffff)) for t in range(16)]
    return c, s


def fft16_ad4a(re, im):
    """0xad4a, per 16-point block: radix-2 DIT, stages d=1, 2 (-i on elements 3 mod 4), twiddles from the
    32-bit tables 0xa9c0 (before d=4) / 0xaa40 (before d=8) applied to (x<<2 saturating) with M, skipped
    where the sin high half is 0."""
    re = list(re); im = list(im)

    def bfly(d):
        nr, ni = re[:], im[:]
        for t in range(16):
            if (t // d) % 2 == 0:
                nr[t] = W32(re[t] + re[t + d]); ni[t] = W32(im[t] + im[t + d])
            else:
                nr[t] = W32(re[t - d] - re[t]); ni[t] = W32(im[t - d] - im[t])
        re[:], im[:] = nr, ni
    bfly(1)
    for t in range(3, 16, 4):
        re[t], im[t] = im[t], W32(-re[t])
    bfly(2)
    for d, tab in ((4, 0xa9c0), (8, 0xaa40)):
        c, s = tw_block(tab)
        for t in range(16):
            if h16(tab, 48 + t) == 0:
                continue
            re[t], im[t] = cmul(shl2_sat(re[t]), shl2_sat(im[t]), c[t], s[t])
        bfly(d)
    return re, im


def radix2(A, B, tw):
    """0xaace: per block pair, t = cmul(b<<2 (wrap), w); a' = a + t; b' = a - t.  A, B: lists of
    (re[16], im[16]); tw: twiddle table address (one 128-byte block per block pair)."""
    for k in range(len(A)):
        c, s = tw_block(tw + 128 * k)
        ar, ai = A[k]; br, bi = B[k]
        tr = []; ti = []
        for t in range(16):
            x, y = cmul(W32(br[t] << 2), W32(bi[t] << 2), c[t], s[t])
            tr.append(x); ti.append(y)
        A[k] = ([W32(a + b) for a, b in zip(ar, tr)], [W32(a + b) for a, b in zip(ai, ti)])
        B[k] = ([W32(a - b) for a, b in zip(ar, tr)], [W32(a - b) for a, b in zip(ai, ti)])


def fft64(blocks):
    """0xab46 (16-point FFT per block) + 0xafe4 (two 0xaac0 span-1 stages, one span-2 stage)."""
    blocks = [fft16_ad4a(*b) for b in blocks]
    for p in range(2):
        A = [blocks[2 * p]]; B = [blocks[2 * p + 1]]; radix2(A, B, 0xbd80); blocks[2 * p], blocks[2 * p + 1] = A[0], B[0]
    A = blocks[0:2]; B = blocks[2:4]; radix2(A, B, 0xbe00); blocks[0:2] = A; blocks[2:4] = B
    return blocks


def fft512(blocks):
    """0x9cf6: blocks = 32 x (re[16], im[16]) in memory order (already bit-reversed by 0x9c84)."""
    blocks = [fft16_ad4a(*b) for b in blocks]
    for q in range(4):                                  # 0xbd20 on each 8-block quarter
        Q = blocks[8 * q:8 * q + 8]
        for p in range(4):                              # 0xaac0: span 1 block, table 0xbd80
            A = [Q[2 * p]]; B = [Q[2 * p + 1]]; radix2(A, B, 0xbd80); Q[2 * p], Q[2 * p + 1] = A[0], B[0]
        for h in (0, 4):                                # span 2 blocks, table 0xbe00
            A = Q[h:h + 2]; B = Q[h + 2:h + 4]; radix2(A, B, 0xbe00); Q[h:h + 2] = A; Q[h + 2:h + 4] = B
        A = Q[0:4]; B = Q[4:8]; radix2(A, B, 0xbf00); Q[0:4] = A; Q[4:8] = B
        blocks[8 * q:8 * q + 8] = Q
    for h in (0, 16):                                   # span 8 blocks, table 0xb920
        A = blocks[h:h + 8]; B = blocks[h + 8:h + 16]; radix2(A, B, 0xb920)
        blocks[h:h + 8] = A; blocks[h + 8:h + 16] = B
    A = blocks[0:16]; B = blocks[16:32]; radix2(A, B, 0xb120); blocks[0:16] = A; blocks[16:32] = B
    return blocks


# ---------------------------------------------------------------- IMDCT (0x97d6 / 0x99d8)
def msb(v):
    return v.bit_length() - 1


def shift32(x, n):
    """0xa7dc / 0xa858: n > 0: left shift with the high half saturating (vshls + acc);
    n < 0: (x + 2^(-n-1)) >> -n (32-bit add with carry, funnel shift)."""
    if n > 0:
        lo = x & 0xffff; hi = x >> 16
        nhi = sat16(sat16(hi << n) + (lo >> (16 - n)))
        return W32((nhi << 16) | ((lo << n) & 0xffff))
    if n < 0:
        m = -n
        return W32(W32(x + (1 << (m - 1))) >> m)
    return x


def P16(c, v):
    """0xa73e partial product: u16 coefficient c times int32 v, high 32 bits: (v>>16)*c + rnd16(c*lo)"""
    return (v >> 16) * c + ((c * (v & 0xffff) + 0x8000) >> 16)


def twiddle16(re, im, c, s):
    return W32(P16(c, re) - P16(s, im)), W32(P16(s, re) + P16(c, im))


def u16tab(addr, n):
    return list(struct.unpack_from('<%dH' % n, IMG, addr))


def bitrev(k, bits):
    return int('{:0{w}b}'.format(k, w=bits)[::-1], 2)


LONG = dict(N=2048, ctab=0xa180, stab=0x9d80, bf=4, r8=-8)    # 0x97d6: r8 = arg - 8, r7 = msb - 4
SHORT = dict(N=256, ctab=0xb0a0, stab=0xb020, bf=6, r8=-5)    # 0x99d8: r8 = arg - 5, r7 = msb - 6


def imdct_stages(X, arg=10, P=LONG):
    """returns dict of intermediate complex arrays (lists of (re, im)) and the shift values."""
    N = P['N']; N2 = N // 2; N4 = N // 4
    st = {}
    z = [(X[2 * k], X[2 * k + 1]) for k in range(N4)]
    st['load'] = z
    Mx = max([0] + [max(v >> 16, ~(v >> 16)) for p in z for v in p])
    r7 = msb(Mx) - P['bf']
    st['r7'] = r7
    z = [(shift32(a, -r7), shift32(b, -r7)) for a, b in z]
    st['scaled'] = z
    z = [(W32(-z[k][0]), z[N4 - 1 - k][1]) for k in range(N4)]
    st['pre_in'] = z
    c = u16tab(P['ctab'], N4); s = u16tab(P['stab'], N4)
    z = [twiddle16(a, b, c[k], s[k]) for k, (a, b) in enumerate(z)]
    z = [(a, W32(-b)) for a, b in z]
    st['pretw'] = z
    nb = N4.bit_length() - 1
    z = [z[bitrev(k, nb)] for k in range(N4)]
    st['bitrev'] = z
    if N4 == 512:
        bl = fft512([([z[16 * b + t][0] for t in range(16)], [z[16 * b + t][1] for t in range(16)]) for b in range(32)])
    else:
        bl = fft64([([z[16 * b + t][0] for t in range(16)], [z[16 * b + t][1] for t in range(16)]) for b in range(4)])
    z = [(bl[k >> 4][0][k & 15], bl[k >> 4][1][k & 15]) for k in range(N4)]
    st['fft'] = z
    z = [(a, W32(-b)) for a, b in z]
    z = [twiddle16(a, b, c[k], s[k]) for k, (a, b) in enumerate(z)]
    st['posttw'] = z
    st['r0'] = r7 + arg + P['r8']
    z = [(shift32(a, st['r0']), shift32(b, st['r0'])) for a, b in z]
    st['final'] = z
    st['out'] = reorder(z, N)
    return st


def reorder(Z, N):
    """firmware output reordering (FAAD2 mdct.c shape; RE/IM roles and signs as the firmware's conj
    convention gives them), Z = N/4 complex -> N int32."""
    N2, N4, N8 = N // 2, N // 4, N // 8
    RE = [z[0] for z in Z]; IM = [z[1] for z in Z]
    out = [0] * N
    for j in range(N8):
        out[2 * j] = W32(-RE[N8 + j]); out[2 * j + 1] = W32(-IM[N8 - 1 - j])
        out[N4 + 2 * j] = IM[j]; out[N4 + 2 * j + 1] = RE[N4 - 1 - j]
        out[N2 + 2 * j] = IM[N8 + j]; out[N2 + 2 * j + 1] = RE[N8 - 1 - j]
        out[N2 + N4 + 2 * j] = RE[j]; out[N2 + N4 + 2 * j + 1] = IM[N4 - 1 - j]
    return out


def imdct(X, N, arg=10):
    """whole firmware IMDCT: X = N/2 int32 spectral values -> N int32 outputs."""
    return imdct_stages(X, arg, LONG if N == 2048 else SHORT)['out']


# ---------------------------------------------------------------- filter bank (0x21bb2) and PCM (0x975e)
def wmul(a, w):
    """0x6840: 2 * ((a >> 16) * w + rnd16(w * (a & 0xffff))), w int16 window (Q15); the low partial
    product goes through op21 (round to nearest)"""
    return W32(2 * ((a >> 16) * w + ((w * (a & 0xffff) + 0x8000) >> 16)))


def ifilter_bank(seq, win, freq, T):
    """0x21bb2 (FAAD2 ifilter_bank structure on the firmware's vector helpers).
    seq: 0 ONLY_LONG, 1 LONG_START, 2 EIGHT_SHORT, 3 LONG_STOP; freq: 1024 int32 spectral values
    (8 x 128 for EIGHT_SHORT); T: 2048 int32 time buffer = [output 1024 | overlap 1024], updated in
    place; win: dict of int16 windows 'Lprev', 'Lrev' (1024), 'Sprev', 'S', 'Srev' (128) as selected by
    window_shape / window_shape_prev from the fb pointer tables (fb+16692/16700/16708/16716).
    Returns T."""
    T[0:1024] = T[1024:2048]                                     # 0x1216 copy overlap to the front
    if seq in (0, 1, 3):
        t = imdct(freq[:1024], 2048)
        if seq == 3:
            t[0:448] = [0] * 448                                 # 0x11ec
            t[448:576] = [wmul(a, w) for a, w in zip(t[448:576], win['Sprev'])]
        else:
            t[0:1024] = [wmul(a, w) for a, w in zip(t[0:1024], win['Lprev'])]
        T[0:1024] = [W32(a + b) for a, b in zip(t[0:1024], T[0:1024])]      # 0x123e
        if seq == 1:
            T[1024:1472] = t[1024:1472]                          # 0x1216 (448)
            T[1472:1600] = [wmul(a, w) for a, w in zip(t[1472:1600], win['Srev'])]
            T[1600:2048] = [0] * 448
        else:
            T[1024:2048] = [wmul(a, w) for a, w in zip(t[1024:2048], win['Lrev'])]
        return T
    T[1024:2048] = [0] * 1024                                    # 0x6a1e x 4
    for w in range(8):
        t = imdct(freq[128 * w:128 * w + 128], 256)
        wp = win['Sprev'] if w == 0 else win['S']
        t[0:128] = [wmul(a, x) for a, x in zip(t[0:128], wp)]
        o = 448 + 128 * w
        T[o:o + 128] = [W32(a + b) for a, b in zip(t[0:128], T[o:o + 128])]
        T[o + 128:o + 256] = [W32(b + wmul(a, x)) for a, x, b in zip(t[128:256], win['Srev'], T[o + 128:o + 256])]
    T[1600:2048] = [0] * 448
    return T


def pcm16(x):
    """0x975e / 0x945a output: clip16((x + 512) >> 10)"""
    return max(-32768, min(32767, (x + 512) >> 10))
