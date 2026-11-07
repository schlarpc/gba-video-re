#!/usr/bin/env python3
"""Differential fuzzer: vxdec.c (libvxdec.so via ctypes) vs the player's ARM decoder (unicorn,
uc_oracle.py) on random but syntactically valid single frames, with random reference and
output-slot memory. Exercises every mode at every partition size, MVs pointing outside
the picture, large se() values (corner overflow / DC-offset clipping), intra prediction at
picture edges (reads of padding/neighbour memory), all intra 4x4 / 16x16 / chroma modes,
QP changes and residual calls (with CBP 0, i.e. ue index 0 -> cbp 0x00).

  fuzz.py [--cases N] [--seed S] [--mvmax M] [--semax E]
Compares: the whole EWRAM image after decoding (all 4 slots incl. padding, MV array) and the
final bit position. Prints the first failing case with its syntax trace.
"""
import argparse
import ctypes
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from uc_oracle import Oracle, SLOT0 as SLOT0_ADDR  # noqa: E402
SLOT0 = SLOT0_ADDR - 0x02000000

W, H = 240, 160


class BW:
    def __init__(self):
        self.bits = []

    def put(self, v, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

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


class Gen:
    def __init__(self, rnd, mvmax, semax, allow_res):
        self.r, self.mvmax, self.semax, self.allow_res = rnd, mvmax, semax, allow_res
        self.bw = BW()
        self.log = []

    def sev(self):
        r = self.r.random()
        if r < 0.6:
            return self.r.randint(-8, 8)
        return self.r.randint(-self.semax, self.semax)

    def mv_ok(self, mx, my):
        # |y| <= 48 keeps every reference read inside mapped EWRAM (slot 0 is ref2); below
        # 0x02000000 the GBA returns open-bus values, which neither side models.
        return -128 <= mx <= 127 and abs(mx) <= self.mvmax and abs(my) <= min(self.mvmax, 48)

    def block(self, w, h, pmx, pmy, mbmv):
        big = w >= 8 and h >= 8
        while True:
            m = self.r.randrange(24 if big else 12)
            b = m % 12
            if m >= 12 and not self.allow_res:
                continue
            if b == 1 and h == 2:
                continue
            if b == 2 and w == 2:
                continue
            if b in (0, 8, 10) and not self.mv_ok(pmx, pmy):
                continue
            if b in (1, 2) and self.r.random() < 0.3 and w * h <= 8:
                continue
            break
        self.bw.ue(m)
        self.log.append(f"{w}x{h} m{m}")
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
            self.block(w, h // 2, pmx, pmy, mbmv); self.block(w, h // 2, pmx, pmy, mbmv)
        elif b == 2:
            self.block(w // 2, h, pmx, pmy, mbmv); self.block(w // 2, h, pmx, pmy, mbmv)
        elif b == 4:
            for _ in range(3):
                self.bw.se(self.sev())
        elif b == 5:
            while True:
                mx, my = self.r.randint(-self.mvmax, self.mvmax), self.r.randint(-self.mvmax, self.mvmax)
                if self.mv_ok(mx, my):
                    break
            self.bw.se(mx); self.bw.se(my)
            for _ in range(3):
                self.bw.se(self.sev())
        elif b == 6 and w >= 4 and h >= 4:
            self.bw.ue(self.r.randrange(4)); self.bw.ue(self.r.randrange(4))
        elif b == 7 and w >= 4 and h >= 4:
            for _ in range((w // 4) * (h // 4)):
                if self.r.random() < 0.4:
                    self.bw.put(1, 1)
                else:
                    self.bw.put(0, 1); self.bw.put(self.r.randrange(8), 3)
            self.bw.ue(self.r.randrange(4))
        if m >= 12:
            for _ in range((w // 8) * (h // 8)):
                self.bw.ue(0)      # CBP index 0 -> no coded coefficients

    def frame(self):
        if self.r.random() < 0.5:
            self.bw.ue(self.r.randint(1, 47))
        else:
            self.bw.ue(0)
        mv = {}
        for my in range(H // 16):
            for mx in range(W // 16):
                def g(x, y):
                    return mv.get((x, y), (0, 0)) if 0 <= x < W // 16 and y >= 0 else (0, 0)
                a, b, c = g(mx - 1, my), g(mx, my - 1), g(mx + 1, my - 1)
                med = lambda p, q, r: sorted((p, q, r))[1]
                pmx, pmy = med(a[0], b[0], c[0]), med(a[1], b[1], c[1])
                box = [(0, 0)]
                self.block(16, 16, pmx, pmy, box)
                mv[(mx, my)] = box[0]
        return self.bw.bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mvmax", type=int, default=40)
    ap.add_argument("--semax", type=int, default=300)
    a = ap.parse_args()
    lib = ctypes.CDLL(os.path.join(HERE, "..", "..", "..", "reference", "vx", "decoder", "libvxdec.so"))
    lib.vx_new.restype = ctypes.c_void_p
    lib.vx_ewram.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.vx_ewram.argtypes = [ctypes.c_void_p]
    lib.vx_errors.restype = ctypes.c_ulong
    lib.vx_errors.argtypes = [ctypes.c_void_p]
    lib.vx_bitpos.restype = ctypes.c_uint32
    lib.vx_bitpos.argtypes = [ctypes.c_void_p]
    lib.vx_dec_init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                                ctypes.c_char_p, ctypes.c_size_t]
    lib.vx_decode_frame.argtypes = [ctypes.c_void_p]
    lib.vx_free.argtypes = [ctypes.c_void_p]
    o = Oracle()
    rom0 = bytearray(o.rom)
    ewimg = bytes(rom0[0x8a00:0x8a00 + 0x32c8])
    fails = 0
    for case in range(a.cases):
        rnd = random.Random(a.seed * 100003 + case)
        gen = Gen(rnd, a.mvmax, a.semax, True)
        stream = gen.frame()
        mem = bytes(rnd.getrandbits(8) for _ in range(4 * 0xF000))
        # ARM side
        rom = bytearray(rom0)
        rom[o.vx + 0x38:o.vx + 0x38 + len(stream)] = stream
        o.rom = bytes(rom)
        o.reset(None)
        o.uc.mem_write(0x02000000 + SLOT0, mem)
        y = o.decode()
        arm = bytes(o.uc.mem_read(0x02000000, 0x40000))
        arm_bit = o.bitpos()
        # C side
        d = lib.vx_new()
        lib.vx_dec_init(d, stream, len(stream), W, H, ewimg, len(ewimg))
        ew = lib.vx_ewram(d)
        ctypes.memmove(ctypes.addressof(ew.contents) + SLOT0, mem, len(mem))
        lib.vx_decode_frame(d)
        c = ctypes.string_at(ew, 0x40000)
        c_bit = lib.vx_bitpos(d)
        errs = lib.vx_errors(d)
        lo, hi = SLOT0, 0x3F2C8 + 0x318
        if c[lo:hi] != arm[lo:hi] or c_bit != arm_bit or errs:
            fails += 1
            diff = [i for i in range(lo, hi) if c[i] != arm[i]]
            where = ""
            if diff:
                i = diff[0]
                s, r = divmod(i - SLOT0, 0xF000)
                if s < 4 and r < 0xA000:
                    where = f"slot {s} Y ({r % 256},{r // 256})"
                elif s < 4:
                    where = f"slot {s} UV byte ({(r - 0xA000) % 256},{(r - 0xA000) // 256})"
                else:
                    where = f"MV array +{i - 0x3F2C8:#x}"
            print(f"case {case}: FAIL {len(diff)} bytes differ, first {where}; bits C {c_bit} ARM {arm_bit}; err {errs}")
            if fails >= 3:
                break
        lib.vx_free(ctypes.c_void_p(d))
    print(f"{a.cases} cases, {fails} failures")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
