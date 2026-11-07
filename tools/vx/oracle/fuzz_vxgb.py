#!/usr/bin/env python3
"""Differential fuzzer for the VXGB paths: vxdec.c (libvxdec.so, VXGB mode) vs the Rev 5
player's ARM decoder (unicorn, uc_oracle_vxgb.py), on random but syntactically valid single
frames, with random reference/output memory and random nonzero-count grids.

Exercises what VXGB changes: residuals at 16x16 / 16x8 / 8x16 / 8x8 with all 32 CBP codes,
CAVLC coefficient blocks for every nC class (0-1, 2-3, 4-7 trees and the >= 8 FLC, chroma
U/V sharing one nC), every TotalCoeff / trailing-ones / total_zeros / run_before combination,
level prefixes up to 14, the 15-zero escape with 11-bit suffixes, suffixLength growth to 5,
all QPs 0..51, and the grid bookkeeping of every mode and split. The grids start with random
counts, so the never-written border entries (row -1, column -1) feed random nC values. The rest of the grammar (MC, intra) is generated as in fuzz.py.

  fuzz_vxgb.py --rom CART.gba [--cases N] [--seed S] [--resprob P]
Compares the whole EWRAM image after decoding (4 slots incl. padding, both nnz grids, MV
array) and the final bit position.
"""
import argparse
import ctypes
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from uc_oracle_vxgb import OracleGB, VXFILE, SLOT0, NZY, NZY_LEN, NZY_START, NZC, NZC_LEN, NZC_START, \
    MVARR, MV_LEN, IW_ROM  # noqa: E402

W, H = 240, 160
SY, SC = 0x41, 0x21
EW = 0x02000000


class BW:
    def __init__(self):
        self.bits = []

    def put(self, v, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def code(self, s):
        self.bits += [int(c) for c in s]

    def ue(self, v):
        v += 1
        n = v.bit_length()
        self.put(0, n - 1)
        self.put(v, n)

    def se(self, v):
        self.ue(2 * v - 1 if v > 0 else -2 * v)

    def bytes(self):
        b = self.bits + [0] * (-len(self.bits) % 16) + [0] * 64
        out = bytearray()
        for i in range(0, len(b), 16):
            hw = 0
            for k in range(16):
                hw = (hw << 1) | b[i + k]
            out += struct.pack("<H", hw)
        return bytes(out)


class Tables:
    """Encoder view of the ROM's CAVLC tables (complete codes only)."""

    def __init__(self, rom):
        iw = rom[IW_ROM:IW_ROM + 0x7CD0]
        b = lambda a: iw[a - 0x03000000]
        sb = lambda a: b(a) - 256 if b(a) > 127 else b(a)
        w32 = lambda a: struct.unpack_from("<I", iw, a - 0x03000000)[0]
        self.cbp = [b(0x03005E90 + i) for i in range(32)]
        self.cbp_idx = {v: i for i, v in enumerate(self.cbp)}
        tree = w32(0x03006910)
        self.ct = {}
        for cls, nC in ((0, 0), (1, 2), (2, 4)):
            lens = 0x03006120 + b(0x03006128 + nC)
            codes = {}
            for x in range(1 << 16):
                node, sym = b(0x03006120 + nC), 0
                for lvl in range(4):
                    sym = sb(tree + node * 16 + ((x >> (12 - 4 * lvl)) & 15))
                    if sym >= 0 or lvl == 3 or -sym >= 49:
                        break
                    node = -sym
                if sym < 0:
                    continue
                ln = b(lens + sym)
                codes.setdefault(sym, set()).add(format(x, "016b")[:ln])
            self.ct[cls] = {s: min(c, key=len) for s, c in codes.items() if len(c) == 1}
        self.tz = {}
        for tc in range(1, 16):
            self.tz[tc] = self._lut(iw, b(0x03006200 + tc - 1), w32(0x03006210 + 4 * (tc - 1)))
        self.rb = {}
        for zl in range(1, 7):
            self.rb[zl] = self._lut(iw, b(0x03006250 + zl - 1), w32(0x03006258 + 4 * (zl - 1)))
        self.thr = struct.unpack_from("<6I", iw, 0x6080)

    @staticmethod
    def _lut(iw, width, t):
        t -= 0x03000000
        codes = {}
        for x in range(1 << width):
            v = iw[t + 16 + x]
            codes.setdefault(v, set()).add(format(x, f"0{width}b")[:iw[t + v]])
        out = {}
        for v, cs in codes.items():
            for c in sorted(cs, key=len):   # a complete code: every peek it prefixes decodes to v
                if all(iw[t + 16 + x] == v for x in range(1 << width) if format(x, f"0{width}b").startswith(c)):
                    out[v] = c
                    break
        return out


class Gen:
    def __init__(self, rnd, tabs, grid, mvmax, semax, resprob):
        self.r, self.t, self.g = rnd, tabs, grid
        self.mvmax, self.semax, self.resprob = mvmax, semax, resprob
        self.bw = BW()
        self.stats = {}

    def st(self, k):
        self.stats[k] = self.stats.get(k, 0) + 1

    def sev(self):
        return self.r.randint(-8, 8) if self.r.random() < 0.6 else self.r.randint(-self.semax, self.semax)

    def mv_ok(self, mx, my):
        return -128 <= mx <= 127 and abs(mx) <= self.mvmax and abs(my) <= min(self.mvmax, 48)

    # ---------------------------------------------------------------- CAVLC 4x4 block
    def coefs(self):
        r = self.r
        k = r.random()
        tc = 0 if k < 0.1 else 16 if k < 0.15 else r.randint(1, 16)
        pos = sorted(r.sample(range(16), tc))
        c = [0] * 16
        big = r.random()
        for p in pos:
            q = r.random()
            if q < 0.45:
                v = 1
            elif q < 0.75:
                v = r.randint(2, 6)
            elif q < 0.9 or big < 0.5:
                v = r.randint(7, 40)
            else:
                v = r.randint(40, 2000)
            c[p] = -v if r.random() < 0.5 else v
        return c

    def block4x4(self, nC):
        t, bw, r = self.t, self.bw, self.r
        c = self.coefs()
        nzpos = [i for i in range(16) if c[i]]
        tc = len(nzpos)
        # trailing ones: any count <= 3 of consecutive +-1 at the high end is decodable
        maxt1 = 0
        for p in reversed(nzpos):
            if abs(c[p]) != 1 or maxt1 == 3:
                break
            maxt1 += 1
        t1 = r.randint(0, maxt1) if r.random() < 0.3 else maxt1
        if nC >= 8:
            bw.code("000011" if tc == 0 else format(((tc - 1) << 2) | t1, "06b"))
            self.st("ct_flc")
        else:
            cls = 0 if nC < 2 else 1 if nC < 4 else 2
            bw.code(t.ct[cls][t1 | tc << 2])
            self.st(f"ct{cls}")
        if tc == 0:
            return 0
        zl = 0
        if tc < 16:
            zl = nzpos[-1] + 1 - tc
            bw.code(t.tz[tc][zl])
        sl = 0
        levels = [c[p] for p in reversed(nzpos)]
        posr = list(reversed(nzpos))
        for i, v in enumerate(levels):
            if i < t1:
                bw.put(1 if v < 0 else 0, 1)
            else:
                lc = abs(v) - 1
                if lc >= (15 << sl):
                    if lc - (15 << sl) >= 2048:       # not codable: clamp the level
                        lc = (15 << sl) + 2047
                    bw.put(0, 15); bw.put(1, 1); bw.put(lc - (15 << sl), 11)
                    self.st("esc")
                else:
                    bw.put(0, lc >> sl); bw.put(1, 1)
                    if sl:
                        bw.put(lc & ((1 << sl) - 1), sl)
                    self.st(f"pfx{lc >> sl}")
                bw.put(1 if v < 0 else 0, 1)
                if lc > t.thr[sl]:
                    sl += 1
                self.st(f"sl{sl}")
            if i == tc - 1 or zl == 0:
                continue
            run = posr[i] - posr[i + 1] - 1
            if zl > 6:
                bw.code(format(7 - run, "03b") if run < 7 else "0" * (run - 4) + "1")
            else:
                bw.code(t.rb[zl][run])
            self.st(f"rb{min(zl, 7)}_{run}")
            zl -= run
        return tc

    def residual(self, w, h, nzy, nzc):
        g = self.g
        for by in range(0, h, 8):
            for bx in range(0, w, 8):
                ny = nzy + (by >> 2) * SY + (bx >> 2)
                nc = nzc + (by >> 3) * SC + (bx >> 3)
                idx = self.r.randrange(32)
                self.bw.ue(idx)
                cbp = self.t.cbp[idx]
                for k in range(4):
                    nn = ny + (k >> 1) * SY + (k & 1)
                    if cbp & (1 << k):
                        g[nn] = self.block4x4((g[nn - 1] + g[nn - SY] + 1) >> 1)
                    else:
                        g[nn] = 0
                if cbp & 0x10:
                    nC = (g[nc - 1] + g[nc - SC] + 1) >> 1
                    tu = self.block4x4(nC)
                    tv = self.block4x4(nC)
                    g[nc] = (tu + tv + 1) >> 1
                    self.st("chroma")
                else:
                    g[nc] = 0

    # ---------------------------------------------------------------- partition tree
    def block(self, w, h, pmx, pmy, mbmv, nzy, nzc):
        big = w >= 8 and h >= 8
        r = self.r
        while True:
            m = r.randrange(24 if big else 12)
            b = m % 12
            if m >= 12 and r.random() > self.resprob:
                continue
            if b == 1 and h == 2 or b == 2 and w == 2:
                continue
            if b in (0, 8, 10) and not self.mv_ok(pmx, pmy):
                continue
            if b in (1, 2) and r.random() < 0.3 and w * h <= 8:
                continue
            break
        self.bw.ue(m)
        self.st(f"{w}x{h}m{m}")
        if b in (0, 8, 10):
            mbmv[0] = (pmx, pmy)
        elif b in (3, 9, 11):
            while True:
                dx, dy = self.sev(), self.sev()
                if self.mv_ok(pmx + dx, pmy + dy):
                    break
            self.bw.se(dx); self.bw.se(dy)
            mbmv[0] = (pmx + dx, pmy + dy)
        elif b == 1:
            self.block(w, h // 2, pmx, pmy, mbmv, nzy, nzc)
            dy = (2 * SY, SC) if h == 16 else (0, 0)
            self.block(w, h // 2, pmx, pmy, mbmv, nzy + dy[0], nzc + dy[1])
        elif b == 2:
            self.block(w // 2, h, pmx, pmy, mbmv, nzy, nzc)
            dx = (2, 1) if w == 16 else (0, 0)
            self.block(w // 2, h, pmx, pmy, mbmv, nzy + dx[0], nzc + dx[1])
        elif b == 4:
            for _ in range(3):
                self.bw.se(self.sev())
        elif b == 5:
            while True:
                mx, my = r.randint(-self.mvmax, self.mvmax), r.randint(-self.mvmax, self.mvmax)
                if self.mv_ok(mx, my):
                    break
            self.bw.se(mx); self.bw.se(my)
            for _ in range(3):
                self.bw.se(self.sev())
        elif b == 6 and w >= 4 and h >= 4:
            self.bw.ue(r.randrange(4)); self.bw.ue(r.randrange(4))
        elif b == 7 and w >= 4 and h >= 4:
            for _ in range((w // 4) * (h // 4)):
                if r.random() < 0.4:
                    self.bw.put(1, 1)
                else:
                    self.bw.put(0, 1); self.bw.put(r.randrange(8), 3)
            self.bw.ue(r.randrange(4))
        if big:
            if m >= 12:
                self.residual(w, h, nzy, nzc)
            elif not (b == 1 and h == 16) and not (b == 2 and w == 16):
                for y in range(h // 4):
                    for x in range(w // 4):
                        self.g[nzy + y * SY + x] = 0
                for y in range(h // 8):
                    for x in range(w // 8):
                        self.g[nzc + y * SC + x] = 0

    def frame(self):
        mv = {}
        for my in range(H // 16):
            for mx in range(W // 16):
                def gmv(x, y):
                    return mv.get((x, y), (0, 0)) if 0 <= x < W // 16 and y >= 0 else (0, 0)
                a, b, c = gmv(mx - 1, my), gmv(mx, my - 1), gmv(mx + 1, my - 1)
                med = lambda p, q, s: sorted((p, q, s))[1]
                box = [(0, 0)]
                self.block(16, 16, med(a[0], b[0], c[0]), med(a[1], b[1], c[1]), box,
                           NZY_START - EW + my * 4 * SY + mx * 4, NZC_START - EW + my * 2 * SC + mx * 2)
                mv[(mx, my)] = box[0]
        return self.bw.bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True)
    ap.add_argument("--cases", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mvmax", type=int, default=40)
    ap.add_argument("--semax", type=int, default=300)
    ap.add_argument("--resprob", type=float, default=0.6, help="probability of keeping a residual mode")
    a = ap.parse_args()
    lib = ctypes.CDLL(os.path.join(HERE, "..", "..", "..", "reference", "vx", "decoder", "libvxdec.so"))
    lib.vx_new.restype = ctypes.c_void_p
    lib.vx_ewram.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.vx_ewram.argtypes = [ctypes.c_void_p]
    lib.vx_errors.restype = ctypes.c_ulong
    lib.vx_errors.argtypes = [ctypes.c_void_p]
    lib.vx_bitpos.restype = ctypes.c_uint32
    lib.vx_bitpos.argtypes = [ctypes.c_void_p]
    lib.vxgb_dec_init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
    lib.vx_decode_frame.argtypes = [ctypes.c_void_p]
    lib.vx_free.argtypes = [ctypes.c_void_p]
    lib.vxgb_supported.restype = ctypes.c_int
    if not lib.vxgb_supported():
        sys.exit("libvxdec.so was built without VXGB tables")
    o = OracleGB(a.rom)
    rom0 = bytearray(o.rom)
    tabs = Tables(o.rom)
    ewimg = bytes(rom0[0x8A00:0x8A00 + 0x2B48])
    fails = 0
    tot = {}
    lo, hi = SLOT0 - EW, MVARR - EW + MV_LEN
    for case in range(a.cases):
        rnd = random.Random(a.seed * 100003 + case)
        qp = rnd.randrange(52)
        mem = bytes(rnd.getrandbits(8) for _ in range(4 * 0xF000))
        gy = bytes(rnd.randint(0, 16) if rnd.random() < 0.7 else 0 for _ in range(NZY_LEN))
        gc = bytes(rnd.randint(0, 16) if rnd.random() < 0.7 else 0 for _ in range(NZC_LEN))
        grid = bytearray(0x40000)
        grid[NZY - EW:NZY - EW + NZY_LEN] = gy
        grid[NZC - EW:NZC - EW + NZC_LEN] = gc
        gen = Gen(rnd, tabs, grid, a.mvmax, a.semax, a.resprob)
        stream = gen.frame()
        for k, v in gen.stats.items():
            tot[k] = tot.get(k, 0) + v
        # ARM side
        rom = bytearray(rom0)
        rom[VXFILE + 0x14:VXFILE + 0x18] = struct.pack("<I", qp)
        rom[VXFILE + 0x38:VXFILE + 0x38 + len(stream)] = stream
        o.rom = bytes(rom)
        o.hdr = struct.unpack_from("<14I", o.rom, VXFILE)
        o.reset(None)
        o.uc.mem_write(SLOT0, mem)
        o.uc.mem_write(NZY, gy)
        o.uc.mem_write(NZC, gc)
        o.decode()
        arm = bytes(o.uc.mem_read(EW, 0x40000))
        arm_bit = o.bitpos()
        # C side
        d = lib.vx_new()
        lib.vxgb_dec_init(d, stream, len(stream), W, H, qp, ewimg, len(ewimg))
        ew = lib.vx_ewram(d)
        base = ctypes.addressof(ew.contents)
        ctypes.memmove(base + SLOT0 - EW, mem, len(mem))
        ctypes.memmove(base + NZY - EW, gy, len(gy))
        ctypes.memmove(base + NZC - EW, gc, len(gc))
        lib.vx_decode_frame(d)
        c = ctypes.string_at(ew, 0x40000)
        c_bit = lib.vx_bitpos(d)
        errs = lib.vx_errors(d)
        # the generator's own grid model must agree too
        model_ok = grid[NZY - EW:NZC - EW + NZC_LEN] == arm[NZY - EW:NZC - EW + NZC_LEN]
        if c[lo:hi] != arm[lo:hi] or c_bit != arm_bit or errs or not model_ok:
            fails += 1
            diff = [i for i in range(lo, hi) if c[i] != arm[i]]
            where = ""
            if diff:
                i = diff[0] + EW
                if i < NZY:
                    s, rr = divmod(i - SLOT0, 0xF000)
                    where = f"slot {s} +{rr:#x}"
                elif i < MVARR:
                    where = f"nnz grid {i:#x}"
                else:
                    where = f"MV array {i:#x}"
            print(f"case {case} (qp {qp}): FAIL {len(diff)} bytes differ, first {where}; bits C {c_bit} ARM {arm_bit};"
                  f" err {errs}; generator grid model {'ok' if model_ok else 'WRONG'}")
            if fails >= 3:
                break
        lib.vx_free(ctypes.c_void_p(d))
    keys = sorted(k for k in tot if not k[0].isdigit())
    print("coverage:", ", ".join(f"{k}={tot[k]}" for k in keys))
    print(f"{a.cases} cases, {fails} failures")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
