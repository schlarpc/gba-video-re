#!/usr/bin/env python3
"""MPEG-4 Part 2 vector kernels of the Play-Yan video image (py_fw0) run in the VC01 emulator with
the vector model (vc01v.py), compared with independent references.  Usage: mp4_test.py [-v] [name...]

  idct          0x26002 (+ table loader 0x26450) vs fw_idct (AP-922 scaled IDCT written from the
                algorithm, vec/idct_ref.py); luma 4 blocks, chroma (x-offset r0=32), partial flags
  idct_sat      out-of-range IDCT inputs: vmin #2047 then a WRAPPING vshl #4 (op24 = plain shl, fixed
                by the 0x1FEA0 bitstream funnel shift), i.e. input = sx(min(c, 2047), 12)
  idct_ieee     IEEE-1180 accuracy of the same IDCT vs the float IDCT (L=H=256, 5, 300/-300 ranges)
  reorder       0x26800: 6 zig-zag-order blocks in memory -> macroblock layout in the VRF
  h263_mb       0x25f40: H.263 inter dequant of a macroblock in the VRF vs ffmpeg h263 inter
  h263_blk      0x25ed6: H.263 inter dequant of one block in memory
  h263_intra    0x25a28: H.263 intra dequant (DC * dc_scale, AC level*2Q +- qadd, level clip)
  mpeg_intra    0x26a9e (matrix load) + 0x25aa6: MPEG-quant intra (ISO 14496-2 7.4.4.2)
  mpeg_inter    0x26a9e + 0x26afa: MPEG-quant inter macroblock incl. saturation and mismatch control
  acpred        0x2589e / 0x25926: intra AC prediction (top row / left column) with QP rescaling
  memops        memcpy 0x29340, memset 0x26dbe, coefficient buffer clear/copy/load/store
  mbcopy        0x26e00: not-coded macroblock copy (luma + chroma read-modify-write)
  mc            0x2646e: luma + chroma half-pel prediction, both rounding controls, both stride cases
  mc_edge       0x38a8: MC with motion vectors pointing outside the picture (edge padding paths)
  mv4merge      0x270bc / 0x26a8c: 4-MV prediction assembly (conditional vld, offset registers)
  put           0x26f28: clamp 0..255 and 8-bit store of a macroblock (luma + interleaved chroma)
"""
import sys, random, math
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vc01, emu
from vc01v import VPU, s16
from idct_ref import fw_idct, float_idct

VERB = False
M = None


def mach():
    global M
    if M is None:
        M = emu.Machine('py_fw0'); M.cpu.vpu = VPU(M.cpu)
        M.call(0x26450)                         # IDCT prescale table + byte ramp (VRF)
    return M


def vp():
    return mach().cpu.vpu


LUMA = [(0, 0), (0, 8), (8, 0), (8, 8)]     # VRF (y, x) of the four 8x8 luma blocks (16-bit)
CHROMA = [(0, 32), (0, 40)]                 # U, V
BLK6 = LUMA + CHROMA


def put_block(y0, x0, b):
    for v in range(8):
        for u in range(8):
            vp().put16(y0 + v, x0 + u, b[v][u])


def get_block(y0, x0):
    return [[vp().get16(y0 + v, x0 + u) for u in range(8)] for v in range(8)]


def rblock(R, lo=-2048, hi=2047, p=None):
    p = R.choice([0.05, 0.2, 0.5, 1.0]) if p is None else p
    return [[R.randint(lo, hi) if R.random() < p else 0 for _ in range(8)] for _ in range(8)]


def t_idct():
    R = random.Random(11); ok = n = 0
    for t in range(60):
        bl = [rblock(R, *R.choice([(-2048, 2047), (-300, 300), (-20, 20)])) for _ in range(6)]
        flags = [1, 1, 1, 1] if t % 3 == 0 else [R.randrange(2) for _ in range(4)]
        for i in range(4):
            if not flags[i]:
                bl[i] = [[0] * 8 for _ in range(8)]
        for (y, x), b in zip(BLK6, bl):
            put_block(y, x, b)
        mach().call(0x26002, 0, *flags)
        mach().call(0x26002, 32, 1, 1, 0, 0)
        for (y, x), b in zip(BLK6, bl):
            n += 1; o = get_block(y, x); ok += o == fw_idct(b)
    return ok, n


def fdct(p):
    C = [[(math.sqrt(0.125) if u == 0 else 0.5) * math.cos((2 * x + 1) * u * math.pi / 16) for x in range(8)] for u in range(8)]
    return [[sum(C[v][y] * C[u][x] * p[y][x] for y in range(8) for x in range(8)) for u in range(8)] for v in range(8)]


def ieee(L, H, nblk, sign=1, seed=1):
    R = random.Random(seed); pe = 0; se = [0] * 64; sq = [0] * 64; xv = 0
    for t in range(nblk // 4):
        bl = []
        for _ in range(4):
            p = [[sign * R.randint(-L, H) for _ in range(8)] for _ in range(8)]
            F = fdct(p)
            bl.append([[max(-2048, min(2047, int(math.floor(F[v][u] + 0.5)))) for u in range(8)] for v in range(8)])
        for (y, x), b in zip(LUMA, bl):
            put_block(y, x, b)
        mach().call(0x26002, 0, 1, 1, 1, 1)
        for (y, x), b in zip(LUMA, bl):
            o = get_block(y, x); f = float_idct(b)
            for v in range(8):
                for u in range(8):
                    ref = max(-256, min(255, int(math.floor(f[v][u] + 0.5))))
                    e = max(-256, min(255, o[v][u])) - ref
                    pe = max(pe, abs(e)); se[8 * v + u] += e; sq[8 * v + u] += e * e
    N = nblk
    return dict(peak=pe, ppmse=max(sq) / N, omse=sum(sq) / N / 64, ppme=max(abs(x) for x in se) / N,
                ome=abs(sum(se)) / N / 64)


def sx12(c):
    c &= 0xfff
    return c - 0x1000 if c & 0x800 else c


def t_idct_sat():
    """out-of-range coefficients (MPEG-intra dequant leaves e.g. +-4095/-4096 unsaturated): the IDCT
    input stage is vmin #2047 then vshl #4 with 16-bit wrap-around (op24 is a plain shift: the MPEG-4
    bitstream unpacker 0x1FEA0 funnel-shifts arbitrary stream words with it).  So the effective input is
    sx(min(c, 2047), 12) -- low inputs < -2048 wrap; valid streams never reach this.  The earlier
    reference (clip to [-2048, 2047]) assumed saturation and was not independent evidence."""
    R = random.Random(19); ok = n = 0
    for t in range(30):
        bl = [rblock(R, -2048, 2047) for _ in range(4)]
        for b in bl:
            for k in range(3):
                b[R.randrange(8)][R.randrange(8)] = R.choice([R.randint(-4096, -2049), R.randint(2048, 4095)])
        for (y, x), b in zip(LUMA, bl):
            put_block(y, x, b)
        mach().call(0x26002, 0, 1, 1, 1, 1)
        for (y, x), b in zip(LUMA, bl):
            n += 1; ok += get_block(y, x) == fw_idct([[sx12(min(2047, c)) for c in row] for row in b])
    return ok, n


def t_idct_ieee():
    ok = n = 0
    for L, H, sgn in ((256, 255, 1), (5, 5, 1), (300, 300, 1), (256, 255, -1)):
        r = ieee(L, H, 2000 if not VERB else 10000, sgn)
        # IEEE-1180 limits except the overall mean error (ome <= 0.0015), which the firmware's
        # round-half-up multiply-high misses (~0.005; docs/playyan/vc01-isa.md) -- reported, not required
        good = r['peak'] <= 1 and r['ppmse'] <= 0.06 and r['omse'] <= 0.02 and r['ppme'] <= 0.015 and r['ome'] <= 0.01
        if VERB or not good:
            print('   IEEE-1180 L=%d H=%d sign=%d %s' % (L, H, sgn, {k: round(v, 4) for k, v in r.items()}))
        n += 1; ok += good
    return ok, n


def t_reorder():
    R = random.Random(3); ok = n = 0
    m = mach(); buf = m.alloc(bytes(768))
    for t in range(20):
        blk = [[R.randint(-2048, 2047) for _ in range(64)] for _ in range(6)]
        for b in range(6):
            for j in range(64):
                m.m.write(buf + 128 * b + 2 * j, 2, blk[b][j])
        m.call(0x26800, buf)
        for b, (y, x) in enumerate(BLK6):
            n += 1; ok += get_block(y, x) == [blk[b][8 * v:8 * v + 8] for v in range(8)]
    return ok, n


def h263_inter_ref(b, Q):
    qadd = (Q - 1) | 1
    return [[s16(l * 2 * Q + (qadd if l > 0 else -qadd)) if l else 0 for l in row] for row in b]


def t_h263_mb():
    R = random.Random(5); ok = n = 0
    for t in range(40):
        Q = R.randint(1, 31)
        bl = [rblock(R, -255, 255) if R.random() < 0.8 else rblock(R, -2048, 2047) for _ in range(6)]
        for (y, x), b in zip(BLK6, bl):
            put_block(y, x, b)
        mach().call(0x25f40, Q)
        for (y, x), b in zip(BLK6, bl):
            n += 1; o = get_block(y, x); r = h263_inter_ref(b, Q); ok += o == r
            if VERB and o != r:
                print('   Q', Q, [(v, u, b[v][u], o[v][u], r[v][u]) for v in range(8) for u in range(8) if o[v][u] != r[v][u]][:4])
    return ok, n


def t_h263_blk():
    R = random.Random(6); ok = n = 0
    m = mach(); buf = m.alloc(bytes(128))
    for t in range(80):
        Q = R.randint(1, 31); b = rblock(R, -255, 255)
        for v in range(8):
            for u in range(8):
                m.m.write(buf + 16 * v + 2 * u, 2, b[v][u])
        m.call(0x25ed6, Q, buf)
        o = [[s16(m.m.read(buf + 16 * v + 2 * u, 2)) for u in range(8)] for v in range(8)]
        n += 1; ok += o == h263_inter_ref(b, Q)
    return ok, n


def t_h263_intra():
    """0x25a28(blk, clipped_levels_out, dc_scale, Q): levels clipped to [-2048,2047] (stored to the
    second buffer), DC *= dc_scale, AC: level*2Q +- ((Q-1)|1)."""
    R = random.Random(7); ok = n = 0
    m = mach(); buf = m.alloc(bytes(128)); buf2 = m.alloc(bytes(128))
    for t in range(80):
        Q = R.randint(1, 31); dcs = R.randint(8, 46)
        L = [R.randint(-3000, 3000) if R.random() < 0.05 else (R.randint(-127, 127) if R.random() < 0.4 else 0) for _ in range(64)]
        L[0] = R.randint(1, 254)
        for j in range(64):
            m.m.write(buf + 2 * j, 2, L[j])
        m.call(0x25a28, buf, buf2, dcs, Q)
        o = [s16(m.m.read(buf + 2 * j, 2)) for j in range(64)]
        c = [s16(m.m.read(buf2 + 2 * j, 2)) for j in range(64)]
        Lc = [max(-2048, min(2047, l)) for l in L]
        qadd = (Q - 1) | 1
        ref = [s16(Lc[0] * dcs)] + [s16(l * 2 * Q + (qadd if l > 0 else -qadd)) if l else 0 for l in Lc[1:]]
        n += 1; ok += o == ref and c == Lc
    return ok, n


def tdiv(a, b):
    return -((-a) // b) if a < 0 else a // b


def mpeg_ref(L, W, Q, intra, dcs=8, sat=True):
    F = []
    for j in range(64):
        l = L[j]
        if intra and j == 0:
            F.append(l * dcs); continue
        k = 0 if intra else (l > 0) - (l < 0)
        F.append(tdiv((2 * l + k) * W[j] * Q, 16) if l else 0)
    if sat:
        F = [max(-2048, min(2047, f)) for f in F]
    if sum(F) % 2 == 0:
        F[63] += -1 if F[63] & 1 else 1
    return F


def load_matrices(Wi, We):
    m = mach()
    a = m.alloc(bytes(Wi)); b = m.alloc(bytes(We))
    m.call(0x26a9e, a, b)


def t_mpeg_intra():
    """0x25aa6(blk, clipped_out, dc_scale, Q).  Firmware adjustment: no saturation of the result
    (coefficients outside [-2048,2047] are clamped later by the IDCT's vmin #2047 + saturating
    shift); so compare exactly where the spec value is in range, else require |F| >= 2048 with the
    right sign; mismatch control checked only when nothing saturates."""
    R = random.Random(8); ok = n = 0
    m = mach(); buf = m.alloc(bytes(128)); buf2 = m.alloc(bytes(128))
    for t in range(80):
        Wi = [R.randint(8, 255) for _ in range(64)]; We = [R.randint(8, 255) for _ in range(64)]
        load_matrices(Wi, We)
        Q = R.randint(1, 31); dcs = R.randint(8, 46)
        L = [R.randint(-2048, 2047) if R.random() < 0.05 else (R.randint(-40, 40) if R.random() < 0.4 else 0) for _ in range(64)]
        L[0] = R.randint(1, 254)
        for j in range(64):
            m.m.write(buf + 2 * j, 2, L[j])
        m.call(0x25aa6, buf, buf2, dcs, Q)
        o = [s16(m.m.read(buf + 2 * j, 2)) for j in range(64)]
        raw = mpeg_ref(L, Wi, Q, True, dcs, sat=False)
        insat = all(-2048 <= x <= 2047 for x in raw)
        good = all(o[j] == raw[j] if -2048 <= raw[j] <= 2047 else
                   (o[j] >= 2048 if raw[j] > 0 else o[j] <= -2048) for j in range(64 if insat else 63))
        n += 1; ok += good
        if VERB and not good:
            print('   ', [(j, L[j], o[j], raw[j]) for j in range(64) if o[j] != raw[j]][:6])
    return ok, n


def t_mpeg_inter():
    """0x26afa(Q, cbp-pairs): MPEG-quant inter dequant of the macroblock in the VRF, saturation and
    per-block mismatch control (0x26c02).  r1 bits: 1:0 chroma U,V ; 3:2 bottom luma ; 5:4 top luma."""
    R = random.Random(9); ok = n = 0
    for t in range(40):
        Wi = [R.randint(8, 255) for _ in range(64)]; We = [R.randint(8, 255) for _ in range(64)]
        load_matrices(Wi, We)
        Q = R.randint(1, 31)
        bl = [rblock(R, -60, 60) if R.random() < 0.85 else rblock(R, -2048, 2047) for _ in range(6)]
        for (y, x), b in zip(BLK6, bl):
            put_block(y, x, b)
        mach().call(0x26afa, Q, 0x3f)
        for (y, x), b in zip(BLK6, bl):
            o = sum(get_block(y, x), [])
            r = mpeg_ref(sum(b, []), We, Q, False)
            n += 1; ok += o == r
            if VERB and o != r:
                print('   ', [(j, sum(b, [])[j], o[j], r[j]) for j in range(64) if o[j] != r[j]][:6])
    return ok, n


def t_mc():
    m = mach()
    R = random.Random(4); ok = n = 0
    for W in (176, 192):                         # chroma stride = 8 mod 16 and 0 mod 16 paths
        H = 144
        Y = [[R.randrange(256) for _ in range(W)] for _ in range(H)]
        U = [[R.randrange(256) for _ in range(W // 2)] for _ in range(H // 2)]
        V = [[R.randrange(256) for _ in range(W // 2)] for _ in range(H // 2)]
        FY, FU, FV = 0x00200000, 0x00210000, 0x00218000
        for y in range(H):
            m.m.load(FY + y * W, bytes(Y[y]))
        for y in range(H // 2):
            m.m.load(FU + y * W // 2, bytes(U[y])); m.m.load(FV + y * W // 2, bytes(V[y]))

        def hp(P, x2, y2, rc):
            x, y = x2 >> 1, y2 >> 1; fx, fy = x2 & 1, y2 & 1
            a = P[y][x]; b = P[y][x + fx]; c = P[y + fy][x]; d = P[y + fy][x + fx]
            if fx and fy:
                return (a + b + c + d + 2 - rc) >> 2
            if fx or fy:
                return (a + (b if fx else c) + 1 - rc) >> 1
            return a
        for t in range(30):
            mbx = R.randrange(1, W // 16 - 2); mby = R.randrange(1, H // 16 - 2)
            mvx = R.randint(-30, 30); mvy = R.randint(-30, 30); rc = R.randrange(2)
            sp = emu.STACK
            m.m.write(sp, 4, mvy & 0xffffffff); m.m.write(sp + 4, 4, 16 * mbx); m.m.write(sp + 8, 4, 16 * mby)
            m.call(0x2646e, FY, W, 1 - rc, FU, FV, mvx)
            cx = (mvx >> 1) | (mvx & 1); cy = (mvy >> 1) | (mvy & 1)
            refs = [([[hp(Y, 2 * (16 * mbx + i) + mvx, 2 * (16 * mby + j) + mvy, rc) for i in range(16)] for j in range(16)], 32, 0, 16),
                    ([[hp(U, 2 * (8 * mbx + i) + cx, 2 * (8 * mby + j) + cy, rc) for i in range(8)] for j in range(8)], 32, 32, 8),
                    ([[hp(V, 2 * (8 * mbx + i) + cx, 2 * (8 * mby + j) + cy, rc) for i in range(8)] for j in range(8)], 32, 40, 8)]
            for ref, y0, x0, s in refs:
                o = [[vp().get16(y0 + j, x0 + i) for i in range(s)] for j in range(s)]
                n += 1; ok += o == ref
    return ok, n


def t_put():
    """0x26f28(frame, stride, x, y?, ...): clamp the reconstructed macroblock to 0..255 and store it."""
    m = mach(); R = random.Random(12); ok = n = 0
    for t in range(40):
        W = (176, 192)[t & 1]
        FY, FU, FV = 0x00300000, 0x00310000, 0x00318000
        for y in range(144):
            m.m.load(FY + y * W, bytes(W))
        for y in range(72):
            m.m.load(FU + y * W // 2, bytes(W // 2)); m.m.load(FV + y * W // 2, bytes(W // 2))
        vals = {}
        for (y0, x0) in BLK6:
            b = [[R.randint(-300, 600) for _ in range(8)] for _ in range(8)]
            put_block(y0, x0, b); vals[(y0, x0)] = b
        mbx = R.randrange(0, W // 16); mby = R.randrange(0, 144 // 16)
        m.call(0x26f28, FY, W, 16 * mbx, 16 * mby, FU, FV)
        cl = lambda v: max(0, min(255, v))
        good = True
        for (y0, x0), b in vals.items():
            for v in range(8):
                for u in range(8):
                    if x0 < 32:
                        a = FY + (16 * mby + y0 + v) * W + 16 * mbx + x0 + u
                    else:
                        a = (FU if x0 == 32 else FV) + (8 * mby + v) * (W // 2) + 8 * mbx + u
                    good &= m.m.read(a, 1) == cl(b[v][u])
        n += 1; ok += good
    return ok, n


def t_mc_edge():
    """0x38a8(ctx, mvx, mvy, 1-rounding_control): MC with unrestricted motion vectors: blocks reaching
    outside the picture are built with edge replication (0x25b7c..0x25e8a), stored (0x25b5a) and
    interpolated (0x2646e).  Reference: half-pel interpolation on the edge-padded reference picture."""
    m = mach(); R = random.Random(14); ok = n = 0
    for W, H in ((176, 144), (192, 144), (160, 112)):
        Y = [[R.randrange(256) for _ in range(W)] for _ in range(H)]
        U = [[R.randrange(256) for _ in range(W // 2)] for _ in range(H // 2)]
        V = [[R.randrange(256) for _ in range(W // 2)] for _ in range(H // 2)]
        FB = 0x00400000; uoff = W * H; voff = W * H + W * H // 4
        for y in range(H):
            m.m.load(FB + y * W, bytes(Y[y]))
        for y in range(H // 2):
            m.m.load(FB + uoff + y * W // 2, bytes(U[y])); m.m.load(FB + voff + y * W // 2, bytes(V[y]))
        ctx = m.alloc(bytes(0x200)); tl = m.alloc(bytes(32 * 40)); tu = m.alloc(bytes(16 * 24)); tv = m.alloc(bytes(16 * 24))
        for off, val in ((0x184, W), (0x188, H), (0x194, uoff), (0x198, voff), (0x1e8, FB), (0x1ec, tl), (0x1f0, tu), (0x1f4, tv)):
            m.m.write(ctx + off, 4, val)

        def pel(P, x, y):
            return P[min(max(y, 0), len(P) - 1)][min(max(x, 0), len(P[0]) - 1)]

        def hp(P, x2, y2, rc):
            x, y = x2 >> 1, y2 >> 1; fx, fy = x2 & 1, y2 & 1
            a = pel(P, x, y); b = pel(P, x + fx, y); c = pel(P, x, y + fy); d = pel(P, x + fx, y + fy)
            if fx and fy:
                return (a + b + c + d + 2 - rc) >> 2
            if fx or fy:
                return (a + (b if fx else c) + 1 - rc) >> 1
            return a
        for t in range(60):
            mbx = R.choice([0, 1, W // 16 - 1, W // 16 - 2, R.randrange(W // 16)])
            mby = R.choice([0, 1, H // 16 - 1, H // 16 - 2, R.randrange(H // 16)])
            mvx = R.randint(-32, 31); mvy = R.randint(-32, 31); rc = R.randrange(2)   # vop_fcode 1
            m.m.write(ctx + 0x1c8, 4, 16 * mbx); m.m.write(ctx + 0x1cc, 4, 16 * mby)
            m.call(0x38a8, ctx, mvx, mvy, 1 - rc)
            cx = (mvx >> 1) | (mvx & 1); cy = (mvy >> 1) | (mvy & 1)
            refs = [([[hp(Y, 2 * (16 * mbx + i) + mvx, 2 * (16 * mby + j) + mvy, rc) for i in range(16)] for j in range(16)], 32, 0, 16),
                    ([[hp(U, 2 * (8 * mbx + i) + cx, 2 * (8 * mby + j) + cy, rc) for i in range(8)] for j in range(8)], 32, 32, 8),
                    ([[hp(V, 2 * (8 * mbx + i) + cx, 2 * (8 * mby + j) + cy, rc) for i in range(8)] for j in range(8)], 32, 40, 8)]
            for k, (ref, y0, x0, s) in enumerate(refs):
                o = [[vp().get16(y0 + j, x0 + i) for i in range(s)] for j in range(s)]
                n += 1; ok += o == ref
                if VERB and o != ref:
                    print('   W', W, 'mb', mbx, mby, 'mv', mvx, mvy, 'plane', k, o[0][:6], ref[0][:6])
    return ok, n


RECIP = 0x2c61c      # u16 table round(65536 / q) in the image (q = 1: 65535)


def rdiv_fw(a, b):
    """MPEG-4 '//' (round to nearest, half away from zero) as the firmware computes it: the
    quotient truncated toward zero is bumped by sign(quotient) when |remainder| >= ceil(b / 2), so a
    result that truncates to 0 is never rounded up to +-1 (firmware deviation)."""
    q = int(a / b); r = a - q * b
    if q and abs(r) >= b - (b >> 1):
        q += 1 if q > 0 else -1
    return q


def t_acpred():
    """0x2589e (top row) and 0x25926 (left column) intra AC prediction:
    QF[i] += (pred[i] * QP_pred) // QP_cur, or pred[i] unscaled when the QPs are equal (r4 = 0)."""
    m = mach(); R = random.Random(15); ok = n = 0
    blk = m.alloc(bytes(128)); prd = m.alloc(bytes(128))
    for t in range(200):
        B = [R.randint(-200, 200) for _ in range(64)]; P = [R.randint(-300, 300) for _ in range(64)]
        qp, qc = R.randint(1, 31), R.randint(1, 31); flag = R.randrange(2) if t % 2 else 1
        for j in range(64):
            m.m.write(blk + 2 * j, 2, B[j]); m.m.write(prd + 2 * j, 2, P[j])
        left = t >= 100
        m.call(0x25926 if left else 0x2589e, blk, prd, qp, qc, flag, RECIP)
        o = [s16(m.m.read(blk + 2 * j, 2)) for j in range(64)]
        ref = list(B)
        for i in range(8):
            j = 8 * i if left else i
            ref[j] = s16(B[j] + (rdiv_fw(P[j] * qp, qc) if flag else P[j]))
        n += 1; ok += o == ref
    return ok, n


def t_memops():
    """0x29340 memcpy(dst, src, n) (vld2/vld3 unaligned + vst.8 / vld+vst 16-bit), 0x26dbe
    memset0(p, n) (32-byte vst of a zeroed 16-bit row), 0x25a08 (clear the 6-block coefficient
    buffer: one zero row stored REP times), 0x25b3c (copy two coefficient buffers),
    0x270f8 / 0x2710a (macroblock coefficients memory <-> VRF)."""
    m = mach(); R = random.Random(16); ok = n = 0
    A = 0x00500000
    for t in range(60):
        src = A + R.randrange(0, 64); dst = A + 0x2000 + R.randrange(0, 64) if t % 3 else A + 0x2000 + (src & 31)
        ln = R.choice([R.randrange(0, 40), R.randrange(0, 600), 512])
        data = bytes(R.randrange(256) for _ in range(ln + 64))
        m.m.load(src, data); m.m.load(dst - 32, bytes(R.randrange(256) for _ in range(ln + 96)))
        before = bytes(m.m.read(dst - 32 + i, 1) for i in range(ln + 96))
        m.call(0x29340, dst, src, ln)
        after = bytes(m.m.read(dst - 32 + i, 1) for i in range(ln + 96))
        g = after == before[:32] + data[:ln] + before[32 + ln:]
        n += 1; ok += g
        if VERB and not g:
            print('   memcpy', hex(dst), hex(src), ln)
    for t in range(30):
        p = A + 0x4000 + R.randrange(0, 64); ln = R.randrange(64, 700)
        m.m.load(p - 32, bytes(R.randrange(1, 256) for _ in range(ln + 96)))
        before = bytes(m.m.read(p - 32 + i, 1) for i in range(ln + 96))
        m.call(0x26dbe, p, ln)
        after = bytes(m.m.read(p - 32 + i, 1) for i in range(ln + 96))
        # firmware quirk: the 32-byte loop is a do-while, so when the aligned middle part is empty it
        # still clears 32 bytes and the tail loop then starts 32 bytes later (lengths >= 64 tested)
        head = (32 - (p & 31)) & 31
        z = ln + (32 if ((ln - head) & ~31) == 0 else 0)
        exp = bytearray(before); exp[32:32 + z] = bytes(z)
        g = after == bytes(exp[:len(after)])
        n += 1; ok += g
        if VERB and not g:
            print('   memset', hex(p), ln, [i - 32 for i in range(ln + 96) if after[i] != (before[:32] + bytes(ln) + before[32 + ln:])[i]][:8])
    buf = m.alloc(bytes(1024)); buf2 = m.alloc(bytes(1024))
    for t in range(10):
        m.m.load(buf, bytes(R.randrange(256) for _ in range(1024)))
        m.call(0x25a08, buf)
        n += 1; ok += all(m.m.read(buf + i, 1) == 0 for i in range(768)) and m.m.read(buf + 768, 4) != 0x12345678
        a = bytes(R.randrange(256) for _ in range(768)); b = bytes(R.randrange(256) for _ in range(768))
        m.m.load(buf, a); m.m.load(buf + 768 - 768, a)
        src0, src1, dst0, dst1 = buf, buf2, A + 0x6000, A + 0x7000
        m.m.load(src1, b)
        m.cpu.r[8] = dst1; m.cpu.r[9] = src1
        m.call(0x25b3c, dst0, src0)
        n += 1; ok += bytes(m.m.read(dst0 + i, 1) for i in range(768)) == a
        blk = [[R.randint(-2048, 2047) for _ in range(64)] for _ in range(6)]
        raw = b''.join(int(x & 0xffff).to_bytes(2, 'little') for x in sum(blk, []))
        m.m.load(buf, raw); m.call(0x270f8, buf)
        # memory layout of the MB buffer = VRF layout: luma 16 rows x 16 coeffs, then chroma 8 rows x (U|V)
        good = all(vp().get16(r, c) == s16(int.from_bytes(raw[32 * r + 2 * c:32 * r + 2 * c + 2], 'little')) for r in range(16) for c in range(16))
        good &= all(vp().get16(r, 32 + c) == s16(int.from_bytes(raw[512 + 32 * r + 2 * c:512 + 32 * r + 2 * c + 2], 'little')) for r in range(8) for c in range(16))
        m.m.load(buf2, bytes(768)); m.call(0x2710a, buf2)
        good &= bytes(m.m.read(buf2 + i, 1) for i in range(768)) == raw
        n += 1; ok += good
        if VERB and not good:
            print('   mb load/store')
    return ok, n


def t_mbcopy():
    """0x26e00(src, dst, luma_off, stride, luma_size, chroma_off): copy a not-coded macroblock
    (16x16 luma, two 8x8 chroma blocks merged into 16-byte-aligned rows of the destination)."""
    m = mach(); R = random.Random(17); ok = n = 0
    for W, H in ((176, 144), (192, 144), (160, 128)):
        S, D = 0x00600000, 0x00680000
        size = W * H * 3 // 2
        src = bytes(R.randrange(256) for _ in range(size)); dst = bytes(R.randrange(256) for _ in range(size))
        for t in range(12):
            m.m.load(S, src); m.m.load(D, dst)
            mbx, mby = R.randrange(W // 16), R.randrange(H // 16)
            lo = 16 * mby * W + 16 * mbx; co = 8 * mby * (W // 2) + 8 * mbx
            m.call(0x26e00, S, D, lo, W, W * H, co)
            exp = bytearray(dst)
            for y in range(16):
                exp[lo + y * W: lo + y * W + 16] = src[lo + y * W: lo + y * W + 16]
            for pl in (W * H, W * H + W * H // 4):
                for y in range(8):
                    a = pl + co + y * (W // 2)
                    exp[a:a + 8] = src[a:a + 8]
            got = bytes(m.m.read(D + i, 1) for i in range(size))
            n += 1; ok += got == bytes(exp)
    return ok, n


def t_mv4merge():
    """0x270bc(buf, off, keep_left) / 0x26a8c(luma, chroma): 4-MV assembly of the prediction.
    0x270bc stores 8 rows of the prediction at VRF (32 + off>>6, off & 63) as bytes to buf (stride 16);
    with keep_left the left 8 bytes of each row come from buf (conditional vld, lanes 0-7).
    0x26a8c loads the assembled 16x16 luma / 8x16 chroma prediction back as 8-bit rows."""
    m = mach(); R = random.Random(18); ok = n = 0
    buf = m.alloc(bytes(512)); cbuf = m.alloc(bytes(256))
    for t in range(20):
        preds = []
        exp = bytearray(256)
        for k, (off, keep) in enumerate(((0, 0), (0, 1), (512, 0), (512, 1))):
            P = [[R.randrange(256) for _ in range(16)] for _ in range(16)]
            for y in range(16):
                for x in range(16):
                    vp().put16(32 + y, x, P[y][x])
            m.call(0x270bc, buf + (128 if off else 0), off, keep)
            y0 = off >> 6
            for r in range(8):
                row = list(exp[(y0 + r) * 16:(y0 + r) * 16 + 16])
                newrow = (row[:8] if keep else P[y0 + r][:8]) + P[y0 + r][8:16]
                exp[(y0 + r) * 16:(y0 + r) * 16 + 16] = bytes(newrow)
        got = bytes(m.m.read(buf + i, 1) for i in range(256))
        n += 1; ok += got == bytes(exp)
        C = [[R.randrange(256) for _ in range(16)] for _ in range(8)]
        for y in range(8):
            for x in range(16):
                vp().put16(32 + y, 32 + x, C[y][x])
        m.call(0x270bc, cbuf, 32, 0)
        n += 1; ok += bytes(m.m.read(cbuf + i, 1) for i in range(128)) == bytes(sum(C, []))
        m.cpu.r[9] = 0
        m.call(0x26a8c, buf, cbuf)
        good = all(vp().get8(32 + y, x) == exp[16 * y + x] for y in range(16) for x in range(16))
        good &= all(vp().get8(32 + y, 32 + x) == C[y][x] for y in range(8) for x in range(16))
        n += 1; ok += good
    return ok, n


TESTS = [t_idct, t_idct_sat, t_idct_ieee, t_reorder, t_h263_mb, t_h263_blk, t_h263_intra, t_mpeg_intra, t_mpeg_inter, t_acpred, t_memops, t_mbcopy, t_mc, t_mc_edge, t_mv4merge, t_put]

if __name__ == '__main__':
    args = sys.argv[1:]
    if '-v' in args:
        VERB = True; args.remove('-v')
    tot = [0, 0]
    for t in TESTS:
        name = t.__name__[2:]
        if args and name not in args:
            continue
        try:
            p, n = t()
        except Exception as e:
            import traceback; traceback.print_exc(); p, n = 0, 1
        print('%-12s %5d/%-5d %s' % (name, p, n, 'PASS' if p == n else 'FAIL'))
        tot[0] += p; tot[1] += n
    print('total %d/%d' % tuple(tot))
