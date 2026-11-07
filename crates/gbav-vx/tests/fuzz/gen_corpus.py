#!/usr/bin/env python3
"""Differential fuzz corpus: random valid VX++ / VXGB frames run through the reference C
decoder (decoder/libvxdec.so via ctypes); tests/fuzz_c.rs replays every case in Rust and
compares the whole 256 KiB EWRAM image, the final bit position and the error count.

Generators (reused from the repo):
  VX++  decoder/fuzz.py Gen (every mode at every partition size, MVs leaving the picture, big
        se() values, intra at picture edges, QP changes), extended here with real residual
        coefficients: a VX++ TCOEF encoder built from the ROM's VLC LUT, all three escape
        modes, DC-only and full blocks, all CBPs, positions up to the 16th.
  VXGB  decoder/fuzz_vxgb.py Gen + Tables (CAVLC for every nC class, the level escape,
        suffixLength growth, all QPs, random nonzero-count grids).
Each case: 1..3 consecutive frames with random reference / output slot memory. A share of
cases ("garbage") replace the tail of a valid stream with random bits, which drives both
decoders through their error paths (they must still agree byte for byte).

  gen_corpus.py --lib cref/decoder/libvxdec.so --vxpp-rom SHREK.gba --vxgb-rom SHREK_REV5.gba \
                --cases 2000 --seed 1 --out vx_fuzz.bin

Build the C library from the repo's decoder/ (make ROM=<VX++ cart> VXGB_ROM=<VXGB cart>).
Corpus format (LE): b"VXFZ", u32 ncases; per case: u8 variant (0 VX++, 1 VXGB), u8 nframes,
u32 qp, u32 stream_len, stream, u32 nwrites, {u32 ewram_off, u32 len, bytes}*,
then the C result: 16-byte md5 of EWRAM, u32 bitpos, u32 errors.
"""
import argparse
import ctypes
import hashlib
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "decoder"))
sys.path.insert(0, os.path.join(REPO, "container"))
import fuzz as fz      # noqa: E402  (VX++ generator)
import fuzz_vxgb as fg  # noqa: E402  (VXGB generator + CAVLC tables)
import vxcart          # noqa: E402

W, H = 240, 160
EW = 0x02000000
SLOT0_PP, SLOT0_GB = 0x32C8, 0x2B48


# ----------------------------------------------------------------------------- VX++ residuals
class CoefCode:
    """Encoder view of the VX++ coefficient VLC (EWRAM 0x020010e4) and escape tables."""

    def __init__(self, rom):
        L = vxcart.player_layout(rom)
        eo = L["ewram"][0]
        lut = struct.unpack_from("<4096H", rom, eo + 0x10E4)
        self.lmax = rom[eo + 0x30E4:eo + 0x30E4 + 128]
        self.rmax = rom[eo + 0x3164:eo + 0x3164 + 128]
        io = L["iwram"][0]
        self.cbp = list(rom[io + 0x5610:io + 0x5610 + 64])
        cand = {}
        for idx, e in enumerate(lut):
            ln = e & 15
            lvl, run, last = (e >> 4) & 31, (e >> 9) & 63, e >> 15
            if lvl == 0 or ln < 2:
                continue
            pre = format(idx, "012b")[:ln - 1]
            if pre.startswith("0000011") or "0000011".startswith(pre):
                continue   # the escape prefix is tested before the LUT
            cand.setdefault((last, run, lvl), set()).add(pre)
        self.code = {}
        for k, pres in cand.items():
            for p in sorted(pres, key=len):
                # complete: every 12-bit index with this prefix decodes to the same entry
                ok = all(lut[int(p + format(x, "0%db" % (12 - len(p))), 2)] == lut[int(p + "0" * (12 - len(p)), 2)]
                         for x in range(1 << (12 - len(p)))) if len(p) < 12 else True
                if ok:
                    self.code[k] = p
                    break

    def put(self, bw, last, run, level):
        a = abs(level)
        s = 1 if level < 0 else 0
        c = self.code.get((last, run, a))
        if c is not None:
            bw.code(c); bw.put(s, 1); return "vlc"
        lm = self.lmax[last * 64 + run] if run < 64 else 0
        c = self.code.get((last, run, a - lm)) if a > lm else None
        if c is not None:
            bw.code("0000011" + "0" + c); bw.put(s, 1); return "esc1"
        rm = self.rmax[last * 64 + a] if a < 64 else 1
        c = self.code.get((last, run - rm, a)) if run >= rm else None
        if c is not None:
            bw.code("0000011" + "10" + c); bw.put(s, 1); return "esc2"
        bw.code("0000011" + "11"); bw.put(last, 1); bw.put(run, 6); bw.put(level & 0xFFF, 12)
        return "esc3"


class BW(fz.BW):
    def code(self, s):
        self.bits += [int(c) for c in s]


class GenPP(fz.Gen):
    """fuzz.py's VX++ generator with coded residual coefficients instead of CBP 0 only."""

    def __init__(self, rnd, mvmax, semax, coef, resprob):
        super().__init__(rnd, mvmax, semax, True)
        self.bw = BW()
        self.coef, self.resprob, self.stats = coef, resprob, {}

    def st(self, k):
        self.stats[k] = self.stats.get(k, 0) + 1

    def block4x4(self):
        r = self.r
        n = r.choice((1, 1, 2, 3, 4, 6, 8, 12, 16)) if r.random() < 0.9 else r.randint(1, 16)
        pos = sorted(r.sample(range(16), n))
        prev = -1
        for i, p in enumerate(pos):
            q = r.random()
            lvl = r.randint(1, 3) if q < 0.6 else r.randint(4, 30) if q < 0.85 else r.randint(31, 2047)
            if r.random() < 0.5:
                lvl = -lvl
            self.st(self.coef.put(self.bw, int(i == n - 1), p - prev - 1, lvl))
            prev = p
        self.st("dc_only" if pos == [0] else "full")

    def block(self, w, h, pmx, pmy, mbmv):
        # same grammar as fuzz.Gen.block; residual part replaced
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
            self.block(w, h // 2, pmx, pmy, mbmv); self.block(w, h // 2, pmx, pmy, mbmv)
        elif b == 2:
            self.block(w // 2, h, pmx, pmy, mbmv); self.block(w // 2, h, pmx, pmy, mbmv)
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
        if m >= 12:
            for _ in range((w // 8) * (h // 8)):
                idx = r.randrange(64)
                self.bw.ue(idx)
                cbp = self.coef.cbp[idx]
                for k in range(6):
                    if cbp & (1 << k):
                        self.block4x4()


# ----------------------------------------------------------------------------- C side
class CLib:
    def __init__(self, path):
        lib = self.lib = ctypes.CDLL(path)
        lib.vx_new.restype = ctypes.c_void_p
        lib.vx_ewram.restype = ctypes.POINTER(ctypes.c_uint8)
        lib.vx_ewram.argtypes = [ctypes.c_void_p]
        lib.vx_errors.restype = ctypes.c_ulong
        lib.vx_errors.argtypes = [ctypes.c_void_p]
        lib.vx_bitpos.restype = ctypes.c_uint32
        lib.vx_bitpos.argtypes = [ctypes.c_void_p]
        lib.vx_dec_init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_char_p, ctypes.c_size_t]
        lib.vxgb_dec_init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
        lib.vx_decode_frame.argtypes = [ctypes.c_void_p]
        lib.vx_free.argtypes = [ctypes.c_void_p]
        lib.vxgb_supported.restype = ctypes.c_int
        if not lib.vxgb_supported():
            sys.exit("libvxdec.so was built without VXGB tables")

    def run(self, vxgb, qp, stream, writes, nframes, ewimg):
        lib = self.lib
        d = lib.vx_new()
        if vxgb:
            lib.vxgb_dec_init(d, stream, len(stream), W, H, qp, ewimg, len(ewimg))
        else:
            lib.vx_dec_init(d, stream, len(stream), W, H, ewimg, len(ewimg))
        ew = lib.vx_ewram(d)
        base = ctypes.addressof(ew.contents)
        for off, data in writes:
            ctypes.memmove(base + off, data, len(data))
        for _ in range(nframes):
            lib.vx_decode_frame(d)
        out = (hashlib.md5(ctypes.string_at(ew, 0x40000)).digest(), lib.vx_bitpos(d), lib.vx_errors(d))
        lib.vx_free(ctypes.c_void_p(d))
        return out


def garble(stream, r):
    """Keep a valid prefix, randomise the rest (error paths)."""
    b = bytearray(stream)
    cut = r.randrange(2, max(3, len(b) - 8)) & ~1
    for i in range(cut, len(b)):
        b[i] = r.getrandbits(8)
    return bytes(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--vxpp-rom", required=True)
    ap.add_argument("--vxgb-rom", required=True)
    ap.add_argument("--cases", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--garbage", type=float, default=0.1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    c = CLib(a.lib)
    rom_pp = open(a.vxpp_rom, "rb").read()
    rom_gb = open(a.vxgb_rom, "rb").read()
    coef = CoefCode(rom_pp)
    tabs = fg.Tables(rom_gb)
    ew_pp = rom_pp[0x8A00:0x8A00 + 0x32C8]
    ew_gb = rom_gb[0x8A00:0x8A00 + 0x2B48]
    out = [b"VXFZ", struct.pack("<I", a.cases)]
    stats, kinds = {}, {}
    for case in range(a.cases):
        r = random.Random(a.seed * 1000003 + case)
        vxgb = case % 2
        nframes = r.choice((1, 1, 2, 3))
        mem = bytes(r.getrandbits(8) for _ in range(4 * 0xF000))
        if vxgb:
            qp = r.randrange(52)
            gy = bytes(r.randint(0, 16) if r.random() < 0.7 else 0 for _ in range(fg.NZY_LEN))
            gc = bytes(r.randint(0, 16) if r.random() < 0.7 else 0 for _ in range(fg.NZC_LEN))
            grid = bytearray(0x40000)
            grid[fg.NZY - EW:fg.NZY - EW + fg.NZY_LEN] = gy
            grid[fg.NZC - EW:fg.NZC - EW + fg.NZC_LEN] = gc
            g = fg.Gen(r, tabs, grid, 40, 300, 0.6)
            for _ in range(nframes):
                stream = g.frame()
            writes = [(SLOT0_GB, mem), (fg.NZY - EW, gy), (fg.NZC - EW, gc)]
            ewimg = ew_gb
        else:
            qp = 0
            g = GenPP(r, 40, 300, coef, 0.5)
            for _ in range(nframes):
                stream = g.frame()
            writes = [(SLOT0_PP, mem)]
            ewimg = ew_pp
        for k, v in g.stats.items():
            if not k[0].isdigit():
                stats[k] = stats.get(k, 0) + v
        kind = ("vxgb" if vxgb else "vxpp")
        if r.random() < a.garbage:
            stream = garble(stream, r)
            kind += "-garbage"
        kinds[kind] = kinds.get(kind, 0) + 1
        md5, bit, err = c.run(vxgb, qp, stream, writes, nframes, ewimg)
        rec = [struct.pack("<BBII", vxgb, nframes, qp, len(stream)), stream, struct.pack("<I", len(writes))]
        for off, data in writes:
            rec += [struct.pack("<II", off, len(data)), data]
        rec += [md5, struct.pack("<II", bit, min(err, 0xFFFFFFFF))]
        out += rec
        if case % 200 == 199:
            print(f"{case + 1} cases", file=sys.stderr)
    open(a.out, "wb").write(b"".join(out))
    print("cases:", kinds)
    print("coverage:", ", ".join(f"{k}={stats[k]}" for k in sorted(stats)))


if __name__ == "__main__":
    main()
