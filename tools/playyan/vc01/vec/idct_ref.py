"""Reference IDCTs for the MPEG-4 kernel tests: the float IDCT, and the firmware's own fixed-point IDCT
(py_fw0 0x26002) written from its algorithm.  (ffmpeg's xvid IDCT, used for comparison during the
work, is not included: it is LGPL code; the firmware's IDCT is not it, see docs/playyan/decoders.md.)"""
import math
def i32(x): x &= 0xffffffff; return x - (1 << 32) if x >> 31 else x
def s16(x): x &= 0xffff; return x - 65536 if x >> 15 else x
def float_idct(b):
    C = lambda u: math.sqrt(0.5) if u == 0 else 1.0
    cs = [[C(u) * math.cos((2 * x + 1) * u * math.pi / 16) for u in range(8)] for x in range(8)]
    return [[sum(cs[y][v] * cs[x][u] * b[v][u] for v in range(8) for u in range(8)) / 4 for x in range(8)] for y in range(8)]


# ---- the firmware's IDCT (py_fw0 0x26002 + table 0xf240), written from the algorithm:
# AP-922 / xvid tan-butterfly in both directions on prescaled input, 16-bit arithmetic,
# MR(x, c) = (x * c + 0x8000) >> 16 with c an unsigned 16-bit constant (rounding multiply-high).
def MR(x, c): return s16((x * c + 0x8000) >> 16)
FW_TAN1, FW_TAN2, FW_TAN3, FW_SQRT2 = 0x32EC, 0x6A0A, 0xAB0E, 0xB505
def _s(k): return 1.0 if k == 0 else math.sqrt(2) * math.cos(min(k, 8 - k) * math.pi / 16) if k != 4 else 1.0
def fw_scale(u, v):
    """prescale factor (Q15 of s(u) s(v), s(k) = sqrt2 cos(k pi/16) for the xvid row-table pattern)"""
    S = [1.0, math.sqrt(2) * math.cos(math.pi / 16), math.sqrt(2) * math.cos(2 * math.pi / 16), math.sqrt(2) * math.cos(3 * math.pi / 16)]
    f = [S[0], S[1], S[2], S[3], S[0], S[3], S[2], S[1]]
    return int(f[u] * f[v] * 32768 + 0.5)
def fw_bfly(x):
    x = list(x); a = lambda p, q: s16(p + q); d = lambda p, q: s16(p - q)
    t8 = MR(x[1], FW_TAN1); t9 = MR(x[7], FW_TAN1); t9 = a(t9, x[1]); x[1] = d(t8, x[7])
    t8 = MR(x[3], FW_TAN3); x[7] = MR(x[5], FW_TAN3); x[3] = a(x[3], x[7]); t8 = d(x[5], t8)
    x[5] = d(t9, x[3]); t9 = a(t9, x[3]); x[3] = a(x[1], t8); x[1] = d(x[1], t8)
    t8 = MR(x[1], FW_SQRT2); x[7] = MR(x[5], FW_SQRT2); x[1] = a(x[7], t8); x[5] = d(x[7], t8)
    x[7] = MR(x[6], FW_TAN2); t8 = MR(x[2], FW_TAN2); x[2] = a(x[2], x[7]); x[6] = d(t8, x[6])
    t8 = d(x[0], x[4]); x[4] = a(x[0], x[4]); x[0] = a(t8, x[6]); x[6] = d(t8, x[6])
    t8 = a(x[4], x[2]); x[2] = d(x[4], x[2]); x[4] = d(x[2], x[3]); x[3] = a(x[2], x[3])
    x[2] = a(x[6], x[5]); x[5] = d(x[6], x[5]); x[6] = d(x[0], x[1]); x[1] = a(x[0], x[1])
    x[0] = a(t8, t9); x[7] = d(t8, t9)
    return x
def fw_idct(b):
    """8x8 block (rows of coefficients) -> 8x8 output, as the firmware computes it."""
    # pass 1 (VRF vertical registers = coefficient columns u, lanes = rows v): 1-D IDCT along u
    rows = []
    for v in range(8):
        x = [MR(s16(min(b[v][u], 2047) << 4), fw_scale(u, v)) for u in range(8)]
        rows.append(fw_bfly(x))
    # pass 2 (horizontal registers = rows, lanes = columns): 1-D IDCT along v, then x 1024 >> 16
    out = [[0] * 8 for _ in range(8)]
    for u in range(8):
        y = fw_bfly([rows[v][u] for v in range(8)])
        for v in range(8):
            out[v][u] = MR(y[v], 1024)
    return out
