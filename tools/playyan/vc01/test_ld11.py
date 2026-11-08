#!/usr/bin/env python3
"""Regression test: 48-bit 0x2f op4..7 lane access, offset bits 1..0 (C core vs Python model vs spec).

  00 ldb  (rb + (o & ~1) + k, zero-extended)    01 stb
  10 ldsh (rb + (o & ~3) + k, sign-extended)    11 ldsb (rb + (o & ~3) + k, sign-extended)
11 was executed as a halfword store until 2026-11-08 (the AAC decoder work, mi_fw0 AAC ics_info 0x21b24:
`2f80 019f 4800` = ldsb r0, (r9 + 412)).  Run: python3 test_ld11.py  -> PASS / FAIL
"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cvc  # noqa: E402
sys.path.insert(0, cvc.ISA)
import vc01  # noqa: E402

BASE = 0x1000      # code
DATA = 0x8000      # rb value


def enc(op, rd, rb, o):
    h = 0x2f00 | (op << 5) | rd
    w1 = o & 0xffff
    w2 = ((o >> 16) & 0x7ff) | (rb << 11)
    return struct.pack('<3H', h, w1, w2)


def run_c(code, data, regs):
    c = cvc.CVC(size=0x10000)
    c.L.vc_set_align(c.p, 1)
    for i, b in enumerate(code):
        c.buf[BASE + i] = b
    for i, b in enumerate(data):
        c.buf[DATA + i] = b
    for k, v in regs.items():
        c.set_reg(k, v)
    c.set_reg(31, BASE)
    c.step()
    return c.r, bytes(c.buf[DATA:DATA + len(data)])


def run_py(code, data, regs):
    m = vc01.Mem()
    m.load(BASE, code)
    m.load(DATA, data)
    cpu = vc01.CPU(m)
    for k, v in regs.items():
        cpu.r[k] = v
    cpu.r[31] = BASE
    cpu.step()
    return cpu.r, bytes(m.read(DATA + i, 1) for i in range(len(data)))


def main():
    data = bytes([0x11, 0x22, 0x83, 0x44, 0x95, 0x66, 0xa7, 0x88] * 4)
    ok = True
    cases = []
    for op in (4, 5, 6, 7):
        k = op - 4
        for low, kind in ((0, 'ldb'), (1, 'stb'), (2, 'ldsh'), (3, 'ldsb')):
            o = 8 + low                       # offset 8 (+ the width/direction bits)
            regs = {2: DATA, 3: 0xdeadbeef}
            code = enc(op, 3, 2, o)
            if kind == 'ldb':
                want = data[(o & ~1) + k]
            elif kind == 'ldsb':
                want = data[(o & ~3) + k] - (256 if data[(o & ~3) + k] & 0x80 else 0)
            elif kind == 'ldsh':
                ea = ((o & ~3) + k) & ~1      # the bus ignores the low bit of a halfword access
                v = data[ea] | data[ea + 1] << 8
                want = v - 0x10000 if v & 0x8000 else v
            else:
                want = None
            rc, mc = run_c(code, data, regs)
            rp, mp = run_py(code, data, regs)
            if kind == 'stb':
                exp = bytearray(data); exp[(o & ~1) + k] = 0xef
                good = mc == bytes(exp) and mp == bytes(exp)
            else:
                good = rc[3] == want & 0xffffffff and rp[3] == want & 0xffffffff and mc == data and mp == data
            cases.append((op, kind, good))
            ok &= good
            if not good:
                print('FAIL op%d %s: C r3=%08x py r3=%08x want %s' % (op, kind, rc[3], rp[3], hex(want & 0xffffffff) if want is not None else 'store'))
    # vector store REP code 7 = 64 rows (mi_fw0 0x3d72: vst.8 H(0,0), (r0+0) rep +=r1, r1 = 64):
    # writes exactly 64 x 16 bytes at a 64-byte stride, nothing past base + 4096
    code = struct.pack('<3H', 0x2208, 0x0000, 0x01c1)
    for name in ('C', 'py'):
        if name == 'C':
            c = cvc.CVC(size=0x10000); c.L.vc_set_align(c.p, 1)
            for i, b in enumerate(code): c.buf[BASE + i] = b
            for i in range(0x2000): c.buf[0x4000 + i] = 0xAA
            c.set_reg(0, 0x4000); c.set_reg(1, 64); c.set_reg(31, BASE); c.step()
            mem = bytes(c.buf[0x4000:0x6000])
        else:
            m = vc01.Mem(); m.load(BASE, code); m.load(0x4000, b'\xaa' * 0x2000)
            cpu = vc01.CPU(m); cpu.r[0] = 0x4000; cpu.r[1] = 64; cpu.r[31] = BASE; cpu.step()
            mem = bytes(m.read(0x4000 + i, 1) for i in range(0x2000))
        rows = [mem[64 * k:64 * k + 16] for k in range(128)]
        good = all(r == bytes(16) for r in rows[:64]) and all(r == b'\xaa' * 16 for r in rows[64:])
        cases.append(('rep7', name, good)); ok &= good
        if not good:
            print('FAIL vst rep code 7 (%s): rows written %d' % (name, sum(1 for r in rows if r != b'\xaa' * 16)))
    print('%d cases, %s' % (len(cases), 'PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
