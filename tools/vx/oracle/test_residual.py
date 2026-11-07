#!/usr/bin/env python3
"""Differential test: reference/vx/decoder/residual.c vs the player's ARM residual code under unicorn.

Runs IWRAM 0x03005650 residual_block (-> 0x03005794 residual_4x4, escape 0x030059b4,
IDCT/add 0x03005a94.., DC-only 0x03005c9c..) on crafted bit-reader states, bitstreams,
predictions and dequant scales, and the same inputs through vx_residual() (ctypes).
Compares: final r1 (pos), r2 (cnt), r3 (buf), the whole Y and UV work area, and the
dequant scale words (the ARM must not have corrupted them).

Two generators:
  structured  -- a real encoder for the syntax (CBP ue(v), MPEG-4 intra TCOEF codes,
                 all three escape modes, DC-only and full blocks, all block sizes)
  random      -- uniformly random bits; cases where residual.c reports an impossible
                 stream (CBP index >= 64, coefficient position >= 32: the ARM would
                 corrupt its own tables) are skipped, everything else must match.

usage: test_residual.py [N_structured] [N_random] [seed]
"""
import ctypes, os, random, struct, subprocess, sys, tempfile

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import *

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYER = os.path.join(HERE, '..', '..', '..', 'reference', 'vx', 'player')
IWRAM = open(os.path.join(PLAYER, 'iwram.bin'), 'rb').read()
EWRAM = open(os.path.join(PLAYER, 'ewram.bin'), 'rb').read()

# ---------------------------------------------------------------- C side
def build_lib():
    out = os.path.join(tempfile.gettempdir() if not os.environ.get('TMPDIR') else os.environ['TMPDIR'],
                       'libvxresidual_%d.so' % os.getpid())
    subprocess.check_call(['gcc', '-O2', '-Wall', '-shared', '-fPIC', '-o', out,
                           os.environ.get('RESIDUAL_C', os.path.join(HERE, '..', '..', '..', 'reference', 'vx', 'decoder', 'residual.c'))])
    return ctypes.CDLL(out)

class VxBits(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('size', ctypes.c_size_t), ('pos', ctypes.c_size_t),
                ('buf', ctypes.c_uint32), ('cnt', ctypes.c_int)]

class VxQ(ctypes.Structure):
    _fields_ = [('scale', ctypes.c_int32 * 3), ('qp', ctypes.c_int)]

LIB = build_lib()
LIB.vx_residual.argtypes = [ctypes.POINTER(VxBits), ctypes.POINTER(VxQ), ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
LIB.vx_residual.restype = ctypes.c_int

# ---------------------------------------------------------------- ARM side
STREAM = 0x08000000; STREAM_SZ = 0x100000
YBASE = 0x02010000; UVBASE = 0x02018000
YOFF = 0x0100 + 0x20; UVOFF = 0x0100 + 0x20       # block origin inside the work areas
CTX = 0x02030100                                    # r0
RET = 0x03007f00
SP0 = 0x03007e00
AREA_Y = 20 * 256; AREA_UV = 12 * 256
SCALES = 0x03005770

COV = {0x03005828: 'full IDCT', 0x03005990: 'DC-only', 0x030059c4: 'esc mode1 (level+LMAX)',
       0x03005a18: 'esc mode2 (run+RMAX)', 0x03005a68: 'esc mode3 (FLC)',
       0x03005a94: 'add luma', 0x03005b2c: 'add U', 0x03005be4: 'add V',
       0x03005c9c: 'DC luma', 0x03005e30: 'DC U', 0x03006044: 'DC V', 0x030007c4: '1KiB refill boundary'}

class Arm:
    def __init__(self):
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.mem_map(0x02000000, 0x40000)
        uc.mem_map(0x03000000, 0x8000)
        uc.mem_map(STREAM, STREAM_SZ)
        uc.mem_map(0x08800000, 0x1000)
        uc.mem_write(0x02000000, EWRAM)
        uc.mem_write(0x03000000, IWRAM)
        ctx = bytearray(0x40)
        uc.mem_write(CTX - 8, struct.pack('<4I', YBASE, UVBASE, YOFF, UVOFF))
        uc.mem_write(CTX + 0x20, struct.pack('<I', 0x020010e4))
        # 0x030007c4: refill crossed a 1 KiB boundary of r1 -> the player re-maps the cart
        # window and wraps r1 in its 2 KiB ring. We model a linear stream: just return.
        uc.hook_add(UC_HOOK_CODE, lambda u, a, s, d: u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR)),
                    begin=0x030007c4, end=0x030007c4)
        # coverage counters for the interesting paths
        self.cov = {a: 0 for a in COV}
        def hit(u, a, sz, d): self.cov[a] += 1
        for a in COV:
            uc.hook_add(UC_HOOK_CODE, hit, begin=a, end=a)
        self.uc = uc

    def run(self, stream, pos, buf, cnt, scales, y, uv, w, h):
        uc = self.uc
        uc.mem_write(STREAM, bytes(STREAM_SZ))
        uc.mem_write(STREAM, stream)
        uc.mem_write(YBASE, y); uc.mem_write(UVBASE, uv)
        uc.mem_write(SCALES, struct.pack('<3i', *scales))
        uc.mem_write(SP0, struct.pack('<3I', 0x1111, 0x2222, RET))   # pop {fp, ip, pc}
        for r, v in ((UC_ARM_REG_R0, CTX), (UC_ARM_REG_R1, STREAM + pos), (UC_ARM_REG_R2, cnt & 0xffffffff),
                     (UC_ARM_REG_R3, buf), (UC_ARM_REG_R11, w), (UC_ARM_REG_R12, h),
                     (UC_ARM_REG_SP, SP0), (UC_ARM_REG_LR, 0)):
            uc.reg_write(r, v)
        uc.emu_start(0x03005650, RET, count=2_000_000)
        pc = uc.reg_read(UC_ARM_REG_PC)
        assert pc == RET, hex(pc)
        r2 = uc.reg_read(UC_ARM_REG_R2)
        return dict(pos=uc.reg_read(UC_ARM_REG_R1) - STREAM, cnt=r2 - (1 << 32) if r2 >> 31 else r2,
                    buf=uc.reg_read(UC_ARM_REG_R3), y=bytes(uc.mem_read(YBASE, AREA_Y)),
                    uv=bytes(uc.mem_read(UVBASE, AREA_UV)),
                    scales=struct.unpack('<3i', uc.mem_read(SCALES, 12)))

def run_c(stream, pos, buf, cnt, scales, y, uv, w, h):
    sbuf = ctypes.create_string_buffer(bytes(stream), len(stream))
    bs = VxBits(ctypes.cast(sbuf, ctypes.c_void_p), len(stream), pos, buf, cnt)
    q = VxQ((ctypes.c_int32 * 3)(*scales), 0)
    yb = ctypes.create_string_buffer(bytes(y), len(y))
    ub = ctypes.create_string_buffer(bytes(uv), len(uv))
    rc = LIB.vx_residual(ctypes.byref(bs), ctypes.byref(q), ctypes.addressof(yb) + YOFF,
                         ctypes.addressof(ub) + UVOFF, w, h)
    return rc, dict(pos=bs.pos, cnt=bs.cnt, buf=bs.buf, y=yb.raw, uv=ub.raw, scales=tuple(scales))

# ---------------------------------------------------------------- encoder
LUT = struct.unpack('<4096H', EWRAM[0x10e4:0x30e4])
LMAX = EWRAM[0x30e4:0x3164]; RMAX = EWRAM[0x3164:0x31e4]
CBP = IWRAM[0x5610:0x5650]

def codebook():
    cb = {}
    for i, e in enumerate(LUT):
        ln = e & 15
        if (i >> 5) == 3 or ln == 1:
            continue
        n = ln - 1                       # codeword length without sign
        code = i >> (12 - n) if n <= 12 else i
        cb[(n, code)] = ((e >> 15), (e >> 9) & 63, (e >> 4) & 31)
    return [(n, c) + s for (n, c), s in sorted(cb.items())]
CODES = codebook()
assert len(CODES) == 102

LEVELSCALE = [(10, 13, 16), (11, 14, 18), (13, 16, 20), (14, 18, 23), (16, 20, 25), (18, 23, 29)]
def qscales(qp):
    return tuple(v << (qp // 6) for v in LEVELSCALE[qp % 6])

class BW:
    def __init__(self): self.bits = []
    def put(self, v, n):
        for k in range(n - 1, -1, -1): self.bits.append((v >> k) & 1)
    def ue(self, v):
        v += 1; n = v.bit_length(); self.put(0, n - 1); self.put(v, n)
    def halfwords(self):
        b = self.bits + [0] * ((-len(self.bits)) % 16)
        out = bytearray()
        for i in range(0, len(b), 16):
            hw = 0
            for k in range(16): hw = hw << 1 | b[i + k]
            out += struct.pack('<H', hw)
        return out

def enc_block(bw, rng):
    """emit one coded 4x4 block; positions kept < 16"""
    pos = 0
    dc_only = rng.random() < 0.2
    while True:
        mode = rng.choices([0, 1, 2, 3], [70, 10, 10, 10])[0] if pos < 10 else 3
        n, code, last, run, lev = rng.choice(CODES)
        if mode == 0:
            erun = run
        elif mode == 1:
            erun = run
        elif mode == 2:
            erun = run + RMAX[last * 64 + lev]
        else:
            last = rng.random() < 0.3 or pos >= 10; erun = rng.randrange(64) if rng.random() < 0.1 else rng.randrange(8)
        if dc_only:
            erun = 0; last = 1
            if mode in (0, 1):
                cand = [c for c in CODES if c[2] == 1 and c[3] == 0]
                n, code, last, run, lev = rng.choice(cand)
            elif mode == 2:
                cand = [c for c in CODES if c[2] == 1 and c[3] + RMAX[64 + c[4]] == 0]
                if not cand: mode = 3
                else: n, code, last, run, lev = rng.choice(cand)
        if pos + erun > 15:
            continue
        sign = rng.randrange(2)
        if mode == 0:
            bw.put(code, n); bw.put(sign, 1)
        elif mode == 1:
            bw.put(3, 7); bw.put(0, 1); bw.put(code, n); bw.put(sign, 1)
        elif mode == 2:
            bw.put(3, 7); bw.put(2, 2); bw.put(code, n); bw.put(sign, 1)
        else:
            bw.put(3, 7); bw.put(3, 2); bw.put(int(last), 1); bw.put(erun, 6)
            bw.put(rng.randrange(-2048, 2048) & 0xfff, 12)
        pos += erun + 1
        if last or pos >= 16:
            if not last:      # force a terminating symbol at pos 15 is not always possible;
                return False  # caller discards the stream
            return True

def enc_residual(rng, w, h):
    bw = BW()
    for _ in range((w // 8) * (h // 8)):
        idx = rng.randrange(64) if rng.random() < 0.8 else rng.choice([0, 1, 2, 7])
        bw.ue(idx)
        cbp = CBP[idx]
        for b in range(6):
            if cbp >> b & 1:
                if not enc_block(bw, rng):
                    return None
    return bw

# ---------------------------------------------------------------- driver
def make_case(rng, structured):
    w, h = rng.choice([(16, 16), (16, 8), (8, 16), (8, 8)])
    if structured:
        bw = None
        while bw is None:
            bw = enc_residual(rng, w, h)
        payload = bw.bits
    else:
        payload = [rng.randrange(2) for _ in range(rng.choice([64, 512, 4096, 16384]))]
    # bit reader state: pos even, cnt 0..15, valid = cnt+16 bits, buf left aligned.
    cnt = rng.randrange(16)
    lead = rng.randrange(16) * 16          # junk halfwords before the reader position
    bits = [rng.randrange(2) for _ in range(lead)] + payload + [rng.randrange(2) for _ in range(32 + rng.randrange(64))]
    # the next bit to consume is bit `lead`; pos*8 - (cnt+16) == lead  -> pos*8 = lead + cnt + 16
    need = lead + cnt + 16
    pad = (-need) % 16                     # pos must be a whole halfword: shift junk
    bits = [rng.randrange(2) for _ in range(pad)] + bits
    lead += pad; need += pad
    pos = need // 8
    buf = 0
    for k in range(cnt + 16): buf = buf << 1 | bits[lead + k]
    buf <<= 32 - (cnt + 16)
    bw = BW(); bw.bits = bits
    stream = bytes(bw.halfwords())
    if rng.random() < 0.85:
        qp = rng.randrange(48); scales = qscales(qp)
    else:
        scales = tuple(rng.randrange(-40000, 40000) for _ in range(3))
    y = bytes(rng.randrange(256) for _ in range(AREA_Y))
    uv = bytes(rng.randrange(256) for _ in range(AREA_UV))
    if rng.random() < 0.3:   # saturated predictions exercise the clip
        y = bytes(rng.choice([0, 1, 254, 255]) for _ in range(AREA_Y))
    return (stream, pos, buf, cnt, scales, y, uv, w, h)

def main():
    ns = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    nr = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    rng = random.Random(seed)
    arm = Arm()
    stats = dict(ok=0, skipped=0, fail=0, bits=0)
    for i in range(ns + nr):
        case = make_case(rng, i < ns)
        rc, c = run_c(*case)
        if rc < 0:
            assert i >= ns, 'structured stream rejected by residual.c (rc=%d)' % rc
            stats['skipped'] += 1
            continue
        a = arm.run(*case)
        bad = [k for k in ('pos', 'cnt', 'buf', 'y', 'uv', 'scales') if a[k] != c[k]]
        if bad:
            stats['fail'] += 1
            if stats['fail'] <= 5:
                print('MISMATCH case %d (%s) w=%d h=%d: %s' % (i, 'struct' if i < ns else 'random',
                      case[7], case[8], bad))
                for k in bad:
                    if k in ('y', 'uv'):
                        d = [j for j in range(len(a[k])) if a[k][j] != c[k][j]]
                        print('  %s first diffs' % k, [(j // 256, j % 256, a[k][j], c[k][j]) for j in d[:8]])
                    else:
                        print('  %s arm=%r c=%r' % (k, a[k], c[k]))
        else:
            stats['ok'] += 1
            stats['bits'] += (c['pos'] * 8 - (c['cnt'] + 16)) - (case[1] * 8 - (case[3] + 16))
    for a, n in arm.cov.items():
        print('  coverage %-24s %8d' % (COV[a], n))
    print('structured=%d random=%d: ok=%d skipped(impossible)=%d FAIL=%d  (%d bits decoded)' %
          (ns, nr, stats['ok'], stats['skipped'], stats['fail'], stats['bits']))
    sys.exit(1 if stats['fail'] else 0)

if __name__ == '__main__':
    main()
