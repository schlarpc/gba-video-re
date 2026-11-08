#!/usr/bin/env python3
"""VC01 (Alphamosaic VideoCore 1) scalar ISA: decoder, disassembler text, and emulator core.

Encoding table with evidence: docs/playyan/vc01-isa.md.  Instruction stream: little-endian 16-bit words.
decode(mem_read16, addr) -> Insn ; Insn.text() ; CPU.step()

Status labels in comments: [C] confirmed by execution test, [P] probable (static evidence), [G] guess.
"""
import struct
from collections import namedtuple
from vc01v import vdecode, vfmt, VPU, VectorFault, EmuError  # vector (PPU) unit

CC = ['eq', 'ne', 'cs', 'cc', 'mi', 'pl', 'vs', 'vc', 'hi', 'ls', 'ge', 'lt', 'gt', 'le', '', 'nv']
REGN = {24: 'gp', 25: 'sp', 26: 'lr', 30: 'sr', 31: 'pc'}


def rn(r):
    return REGN.get(r, 'r%d' % r)


def sx(v, b):
    v &= (1 << b) - 1
    return v - (1 << b) if v & (1 << (b - 1)) else v


# 5-bit ALU op numbering (16-bit "110o oooo ssss dddd" and 32-bit 3-register form).
# rd = rd OP rs (2-operand) ; rd = ra OP rb (3-operand)
OP5 = {0: 'btst', 1: 'cmn', 2: 'bmask', 3: 'bic', 4: 'asr', 5: 'eor', 6: 'shl', 7: 'and',
       8: 'lsr', 9: 'op9', 10: 'mul', 11: 'rsub', 12: 'add', 13: 'sub', 14: 'mov', 15: 'or',
       16: 'cmp', 17: 'op17', 18: 'max', 19: 'min', 20: 'adds2', 21: 'adds4', 22: 'adds8', 23: 'adds16',
       24: 'neg', 25: 'msb', 26: 'exts8', 27: 'exts16', 28: 'brev', 29: 'op29', 30: 'abs', 31: 'not'}
# 4-bit immediate-ALU op (hb 0x60-0x77): op5 = 2*op4 except op4 8 -> cmp
OP4 = {0: 'btst', 1: 'bmask', 2: 'asr', 3: 'shl', 4: 'lsr', 5: 'mul', 6: 'add', 7: 'mov',
       8: 'cmp', 9: 'iop9', 10: 'iop10', 11: 'iop11'}
# 3-register "x4" group (2e8x/2e9x) op6
OPX = {0: 'mulhd.ss', 1: 'mulhd.su', 2: 'mulhd.us', 3: 'mulhd.uu', 4: 'divs', 5: 'div.su', 6: 'div.us', 7: 'divu',
       8: 'addsat', 9: 'subsat', 10: 'shlsat', 11: 'clipsh'}   # 0,3,4,7 [C/P]; rest = VC4 ops 40-43 order [G]
MEMW = ['ld', 'st', 'ldh', 'sth', 'ldb', 'stb', 'ldsh', 'ldsb']


class Insn:
    __slots__ = ('addr', 'size', 'words', 'op', 'a')

    def __init__(self, addr, size, words, op, **a):
        self.addr = addr; self.size = size; self.words = words; self.op = op; self.a = a

    def text(self):
        return fmt(self)

    def __repr__(self):
        return '%08x: %-16s %s' % (self.addr, ' '.join('%04x' % w for w in self.words), self.text())


def length(h):
    hb = h >> 8
    if hb == 0x2f:
        return 6
    if hb in (0x20, 0x21):      # vector 80-bit  [P] (overlap-conflict test, loop-back branches)
        return 10
    if hb in (0x22, 0x23):      # vector 48-bit  [P]
        return 6
    if hb < 0x20 or 0x28 <= hb <= 0x2e:
        return 4
    return 2


def memsel(sel, imm):
    """28-2b / 2d: selector (0..3) + imm16 -> (op, offset).  [P] from struct field cross-refs."""
    o = sx(imm, 16)
    if sel == 0:
        if imm & 1:
            return 'ldh', o & ~1        # unsigned: compared against 0xffff (loader 0xc8b98), often followed by exts16
        return ('st' if imm & 2 else 'ld'), o & ~3
    if sel == 1:
        return ('ldsh' if imm & 1 else 'sth'), o & ~1   # signed: followed by 'bmask #16' when unsigned needed
    return ('ldb', 'stb')[sel - 2], o


def decode(rd16, addr):
    h = rd16(addr)
    hb = h >> 8
    n = length(h)
    w = [h] + [rd16(addr + i) for i in range(2, n, 2)]
    I = lambda op, **k: Insn(addr, n, w, op, **k)
    if hb < 0x10:                                   # addcmpb [C]
        c = hb & 15; aa = (h >> 4) & 15; d = h & 15; h2 = w[1]; mode = h2 >> 14
        if mode in (0, 1):
            b = ('r', (h2 >> 10) & 15); off = sx(h2 & 0x3ff, 10)
        else:
            b = ('i', (h2 >> 8) & 63); off = sx(h2 & 0xff, 8)
        a_ = ('r', aa) if mode in (0, 2) else ('i', sx(aa, 4))
        return I('addcmpb', cc=c, rd=d, ra=a_, rb=b, target=addr + 2 * off)
    if hb < 0x20:                                   # bcc / bl [C]
        if h & 0x80:
            off = ((h >> 8 & 15) << 23) | (w[1] << 7) | (h & 0x7f)
            return I('bl', target=addr + 2 * sx(off, 27))
        off = (w[1] << 7) | (h & 0x7f)
        return I('b', cc=hb & 15, target=addr + 2 * sx(off, 23))
    if hb == 0x2f:                                  # 48-bit
        op = (h >> 5) & 7; rd = h & 31; imm = w[1] | w[2] << 16
        if op == 3:
            return I('movi', rd=rd, imm=imm)        # [C]
        if op in (0, 1):
            rb = w[2] >> 11; o = sx(((w[2] & 0x7ff) << 16) | w[1], 27)
            if op == 1:
                return I('addi3', rd=rd, rs=rb, imm=o)   # [C] lea
            # offset bits 1..0 = width/direction: 00 ld, 01 st, 10 ldh, 11 sth; address rb + (o & ~3)
            # [C gap fork: MPEG-4 bit reader 0x25eb2/0x20484 halfword loads, vec/gap_test.py bitreader 6/6 vs 0/6]
            return I('mem', w=('ld', 'st', 'ldh', 'sth')[o & 3], rd=rd, base=rb, idx=None, off=o & ~3, mode='')
        if op >= 4:     # byte access, lane = op-4 added to the (even) offset; offset bit0 = store  [P]
            rb = w[2] >> 11; o = sx(((w[2] & 0x7ff) << 16) | w[1], 27)
            # offset bits 1..0: 00 ldb, 01 stb at (o & ~1) + lane; 10 ldsh, 11 ldsb at (o & ~3) + lane
            # [C ldsh: MPEG-4 VLC sign bit py_fw0 0x204bc/0x204d0 -> all 13 Garage hashes in the LLE run;
            #  ldb [C loader]; stb [P]; ldsb [C 2026-11-08, AAC decoder work: mi_fw0 AAC ics_info 0x21b24 reads
            #  window_sequence back right after storing it, 0x3e90c/0xcee8; was decoded as sth and every AAC
            #  frame failed with error 10; signed because the FAAD2 fields are char-typed]]
            if o & 2:
                return I('mem', w=('ldsb' if o & 1 else 'ldsh'), rd=rd, base=rb, idx=None, off=(o & ~3) + op - 4, mode='')
            return I('mem', w=('stb' if o & 1 else 'ldb'), rd=rd, base=rb, idx=None, off=(o & ~1) + op - 4, mode='')
        return I('unk48', sub=op, rd=rd, imm=imm)
    if hb == 0x2e:
        op = (h >> 5) & 7; rd = h & 31
        if op <= 3:
            name = ['cmp', 'mul', 'add', 'mov'][op]
            return I('alui', alu=name, rd=rd, imm=sx(w[1], 16))
        if op in (4, 5):
            o6 = w[1] >> 10; rb = (w[1] >> 5) & 31; ra = w[1] & 31
            if op == 5 and o6 < 32:
                return I('alu3', alu=OP5[o6], rd=rd, ra=ra, rb=rb)
            return I('alu3', alu=OPX.get(o6, 'x%d_%d' % (op, o6)), rd=rd, ra=ra, rb=rb)
        return I('unk', what='2e op%d' % op)
    if hb == 0x2c:                                  # add rd, rs, #simm16 [P]
        return I('addi3', rd=h & 15, rs=(h >> 4) & 15, imm=sx(w[1], 16))
    if hb == 0x2d:
        m = (h >> 4) & 15; r = h & 15; o = w[1]
        if m in (12, 13):
            return I('addi3', rd=r, rs=24 if m == 12 else 25, imm=o)   # lea gp/sp [P]
        if m == 14:     # lea rd, (pc + sx16)  [P] G.726 table pointers (fup_sec0 0x380 -> dqlntab16 at 0x318)
            return I('addi3', rd=r, rs=31, imm=sx(o, 16))
        if m < 8:
            wn, oo = memsel(m & 3, o)
            return I('mem', w=wn, rd=r, base=24 if m < 4 else 25, idx=None, off=oo, mode='')
        return I('unk', what='2d m%d' % m)
    if 0x28 <= hb <= 0x2b:
        wn, oo = memsel(hb - 0x28, w[1])
        return I('mem', w=wn, rd=h & 15, base=(h >> 4) & 15, idx=None, off=oo, mode='')
    if hb in (0x20, 0x21, 0x22, 0x23):
        return I('vec', bits=n * 8, v=vdecode(w))
    if 0x24 <= hb <= 0x27:
        return I('unk', what='2x')
    if 0x30 <= hb <= 0x3f:
        if not h & 0x80:
            if hb == 0x3f:
                if h & 0x40:
                    return I('unk', what='3f hi')                  # 3f73 seen only as inter-function padding
                return I('addsp', imm=4 * sx(h & 0x3f, 6))          # [C]
            return I('b', cc=hb & 15, target=addr + 2 * sx(h & 0x7f, 7))   # [C]
        if (h >> 4) & 7 == 7:
            return I('setcc', cc=hb & 15, rd=h & 15)               # [P]
        return I('unk', what='3x hi')
    if 0x40 <= hb <= 0x5f:
        o = hb & 0x1f; x = (h >> 4) & 15; r = h & 15
        if x == 1:
            return I('mem', w='ld', rd=r, base=25, idx=None, off=4 * o, mode='')
        if x == 5:
            return I('mem', w='st', rd=r, base=25, idx=None, off=4 * o, mode='')
        if x == 9:
            return I('addi3', rd=r, rs=25, imm=4 * o)
        if x == 3:
            if o == 4 and r == 0: return I('rts')
            if o == 10 and r == 0: return I('ei')
            if o == 11 and r == 0: return I('di')
            if o == 24 and r == 0: return I('rti')
            if o == 18 and r == 0: return I('bkpt')   # 0x5230: breakpoint, executes as a no-op [LLE real-firmware run]
            if o == 3 and r == 0: return I('wfi')     # 0x4330: wait for interrupt (Nucleus idle loop) [LLE]
            if o == 2 and r == 0: return I('nop')
            if o == 6: return I('br', rd=r)
            if o == 7: return I('blr', rd=r)
            if o == 12: return I('switch', rd=r, w=1)
            if o == 13: return I('switch', rd=r, w=2)
            if o == 16: return I('tst', rd=r)
            if o == 19: return I('rdcfg', rd=r)
            if 20 <= o <= 23 and r == 0:   # 0x5430 vcbase := 0 ; 0x5530/5630/5730 vcbase += 16/32/48 (VRF column
                # base for operands with the offset flag) -- executed by the vector unit [C gap_test bilinear 18/18]
                return I('vec', v={'kind': 'cbase', 'name': 'vcbase', 'o': o})     # [G] reads a status/version word (loader tests bit 25)
            return I('unk', what='misc%d r%d' % (o, r))
        if not x & 1:   # 010o oooo iii0 dddd : lea rd, (pc&~3) + 4*(o:iii)   [C] (loader strings)
            return I('addi3', rd=r, rs=32, imm=((addr & ~3) + 4 * ((o << 3) | (x >> 1))) & 0xffffffff)
        return I('unk', what='4x x%x' % x)
    if 0x60 <= hb <= 0x77:
        op = (h >> 9) & 15; i = (h >> 4) & 31; r = h & 15
        name = OP4[op]
        imm = sx(i, 5) if name in ('add', 'mov', 'cmp', 'mul') else i   # mul signed [C MP3 e2e: py_fw1 0x2229a 6bc0 = mul r0,#-4]
        return I('alui', alu=name, rd=r, imm=imm)
    if 0x78 <= hb <= 0x7f:
        return I('mem', w=MEMW[hb & 7], rd=h & 15, base=(h >> 4) & 15, idx=None, off=0, mode='++')
    if 0x80 <= hb <= 0xbf:
        i = (h >> 11) & 7; wl = (h >> 8) & 7
        return I('mem', w=MEMW[wl], rd=h & 15, base=(h >> 4) & 15, idx=None if i == 7 else i, off=0, mode='')
    if 0xc0 <= hb <= 0xdf:
        return I('alu2', alu=OP5[hb - 0xc0], rd=h & 15, rs=(h >> 4) & 15)
    if hb in (0xe0, 0xf0):
        bb = (h >> 5) & 3; m = h & 31; base = [0, 6, 16, 24][bb]
        regs = [] if (bb == 3 and m == 15) else [(base + k) & 31 for k in range(m + 1)]
        return I('pop' if hb == 0xe0 else 'push', regs=regs, lrpc=bool(h & 0x80))
    if hb >= 0xe1:
        return I('mem', w='ld' if hb < 0xf0 else 'st', rd=h & 15, base=(h >> 4) & 15, idx=None,
                 off=4 * (hb & 15), mode='')
    return I('unk', what='?')


def fmt(i):
    a = i.a; op = i.op
    if op == 'addcmpb':
        f = lambda x: rn(x[1]) if x[0] == 'r' else '#%d' % x[1]
        return 'addcmpb%s %s, %s, %s, %#x' % (CC[a['cc']], rn(a['rd']), f(a['ra']), f(a['rb']), a['target'])
    if op == 'b':
        return 'b%s %#x' % (CC[a['cc']], a['target'])
    if op == 'bl':
        return 'bl %#x' % a['target']
    if op == 'movi':
        return 'mov %s, #%#x' % (rn(a['rd']), a['imm'])
    if op == 'addi3':
        s = 'add %s, %s, #%d' % (rn(a['rd']), rn(a['rs']), a['imm']) if abs(a['imm']) < 256 else \
            'add %s, %s, #%#x' % (rn(a['rd']), rn(a['rs']), a['imm'] & 0xffffffff if a['imm'] >= 0 else a['imm'])
        if a['rs'] == 31:
            s += '   ; =%#x' % ((i.addr + a['imm']) & 0xffffffff)
        if a['rs'] == 32:
            return 'lea %s, %#x' % (rn(a['rd']), a['imm'])
        return s
    if op == 'alui':
        return '%s %s, #%d' % (a['alu'], rn(a['rd']), a['imm'])
    if op == 'alu2':
        return '%s %s, %s' % (a['alu'], rn(a['rd']), rn(a['rs']))
    if op == 'alu3':
        return '%s %s, %s, %s' % (a['alu'], rn(a['rd']), rn(a['ra']), rn(a['rb']))
    if op == 'mem':
        if a['idx'] is not None:
            ea = '(%s+%s)' % (rn(a['base']), rn(a['idx']))
        elif a['off']:
            ea = '(%s%+d)' % (rn(a['base']), a['off'])
        else:
            ea = '(%s)' % rn(a['base'])
        return '%s %s, %s%s' % (a['w'], rn(a['rd']), ea, a['mode'])
    if op in ('push', 'pop'):
        r = a['regs']; s = []
        if r:
            s.append(rn(r[0]) if len(r) == 1 else '%s-%s' % (rn(r[0]), rn(r[-1])))
        if a['lrpc']:
            s.append('lr' if op == 'push' else 'pc')
        return '%s %s' % (op, ','.join(s))
    if op == 'addsp':
        return 'add sp, #%d' % a['imm']
    if op == 'setcc':
        return 'set%s %s' % (CC[a['cc']] or 'al', rn(a['rd']))
    if op == 'rdcfg':
        return 'rdcfg? %s' % rn(a['rd'])
    if op in ('br', 'blr', 'tst'):
        return '%s %s' % ({'br': 'b', 'blr': 'bl'}.get(op, op), rn(a['rd']))
    if op == 'switch':
        return 'switch%s %s' % ('.b' if a['w'] == 1 else '.h', rn(a['rd']))
    if op in ('rts', 'ei', 'di', 'rti', 'bkpt', 'wfi', 'nop'):
        return op
    if op == 'vec':
        return vfmt(a['v'])
    if op == 'unk48':
        return '?48 op%d %s, #%#x' % (a['sub'], rn(a['rd']), a['imm'])
    return '??' + (' ' + a.get('what', '') if a else '')


# ---------------------------------------------------------------- emulator
M32 = 0xffffffff


# EmuError is defined in vc01v (shared with the vector unit) and re-exported here.


class Mem:
    def __init__(self):
        self.pages = {}
        self.mmio_read = None    # f(addr,size)->value or None
        self.mmio_write = None   # f(addr,size,value)->bool handled

    def _pg(self, a):
        p = self.pages.get(a >> 16)
        if p is None:
            p = self.pages[a >> 16] = bytearray(0x10000)
        return p

    def load(self, base, data):
        for i in range(0, len(data), 0x1000):
            chunk = data[i:i + 0x1000]
            a = base + i
            p = self._pg(a); o = a & 0xffff
            if o + len(chunk) <= 0x10000:
                p[o:o + len(chunk)] = chunk
            else:
                for j, b in enumerate(chunk):
                    self.w8(a + j, b)

    def r8(self, a):
        a &= M32
        return self._pg(a)[a & 0xffff]

    def w8(self, a, v):
        a &= M32
        self._pg(a)[a & 0xffff] = v & 0xff

    def read(self, a, n):
        a &= M32
        if self.mmio_read and 0x10000000 <= a < 0x20000000:
            v = self.mmio_read(a, n)
            if v is not None:
                return v
        o = a & 0xffff
        if o + n <= 0x10000:
            return int.from_bytes(self._pg(a)[o:o + n], 'little')
        return sum(self.r8(a + i) << (8 * i) for i in range(n))

    def write(self, a, n, v):
        a &= M32
        if self.mmio_write and 0x10000000 <= a < 0x20000000:
            if self.mmio_write(a, n, v):
                return
        o = a & 0xffff
        if o + n <= 0x10000:
            self._pg(a)[o:o + n] = (v & ((1 << (8 * n)) - 1)).to_bytes(n, 'little')
        else:
            for i in range(n):
                self.w8(a + i, v >> (8 * i))

    def r16(self, a):
        return self.read(a, 2)


class CPU:
    def __init__(self, mem=None):
        self.m = mem or Mem()
        self.r = [0] * 32
        self.N = self.Z = self.C = self.V = 0
        self.cache = {}
        self.icount = 0
        self.trace = None
        self.used = set()      # opcode keys executed (for coverage of tests)
        self.ie = 0
        self.vec_nop = False
        self.vpu = None        # VPU instance, created on the first vector instruction
        self.cfg = 0
        self.wfi = 0

    # condition evaluation (VC4 semantics: C = borrow for cmp)
    def cond(self, c):
        N, Z, C, V = self.N, self.Z, self.C, self.V
        return [Z, not Z, C, not C, N, not N, V, not V,
                (not C) and (not Z), C or Z, N == V, N != V,
                (not Z) and N == V, Z or N != V, True, False][c]

    def setflags_sub(self, a, b):
        a &= M32; b &= M32
        r = (a - b) & M32
        self.N = r >> 31; self.Z = int(r == 0); self.C = int(a < b)
        self.V = int(((a ^ b) & (a ^ r)) >> 31 & 1)

    def setflags_add(self, a, b):
        a &= M32; b &= M32
        s = a + b; r = s & M32
        self.N = r >> 31; self.Z = int(r == 0); self.C = int(s > M32)
        self.V = int((~(a ^ b) & (a ^ r)) >> 31 & 1)

    def alu(self, name, d, s):
        """d = current rd value, s = operand; returns new value or None (flags-only)"""
        d &= M32; s &= M32
        if name == 'mov': return s
        if name == 'add': return d + s
        if name == 'sub': return d - s
        if name == 'rsub': return s - d
        if name == 'and': return d & s
        if name == 'or': return d | s
        if name == 'mul': return d * s
        if name == 'shl': return d << (s & 31)
        if name == 'lsr': return d >> (s & 31)
        if name == 'asr': return sx(d, 32) >> (s & 31)
        if name == 'neg': return -s
        if name == 'eor': return d ^ s
        if name == 'msb': return s.bit_length() - 1
        if name == 'exts16': return sx(s, 16)
        if name == 'exts8': return sx(s, 8)
        if name == 'bic': return d & ~s
        if name == 'abs': return abs(sx(s, 32))
        if name == 'brev': return int('{:032b}'.format(s)[::-1], 2)
        if name == 'max': return max(sx(d, 32), sx(s, 32))
        if name == 'min': return min(sx(d, 32), sx(s, 32))
        if name.startswith('adds'): return d + (s << {'adds2': 1, 'adds4': 2, 'adds8': 3, 'adds16': 4}[name])
        if name == 'not': return ~s
        if name == 'bmask': return d & ((1 << (s & 31)) - 1) if s & 31 else 0
        if name == 'cmp': self.setflags_sub(d, s); return None
        if name == 'cmn': self.setflags_add(d, s); return None
        if name == 'btst': self.Z = int(not (d >> (s & 31)) & 1); return None
        if name == 'divu': return d // s if s else M32
        if name == 'divs':
            a, b = sx(d, 32), sx(s, 32)
            if b == 0: return M32
            q = abs(a) // abs(b)
            return q if (a < 0) == (b < 0) else -q
        if name == 'mulhd.uu': return (d * s) >> 32
        if name == 'mulhd.ss': return (sx(d, 32) * sx(s, 32)) >> 32
        if name == 'mulhd.su': return (sx(d, 32) * s) >> 32
        if name == 'mulhd.us': return (d * sx(s, 32)) >> 32
        raise EmuError('alu op %s not implemented' % name)

    def fetch(self, pc):
        i = self.cache.get(pc)
        if i is None:
            i = self.cache[pc] = decode(self.m.r16, pc)
        return i

    def step(self):
        r = self.r
        pc = r[31]
        i = self.fetch(pc)
        a = i.a; op = i.op
        if self.trace:
            self.trace(self, i)
        self.icount += 1
        npc = (pc + i.size) & M32
        key = op if op not in ('alu2', 'alu3', 'alui', 'mem') else op + ':' + (a.get('alu') or a.get('w'))
        if op == 'vec':
            key = 'vec:' + a['v']['name']
        self.used.add(key)
        if op == 'addcmpb':
            ra = a['ra']; rb = a['rb']
            v = (r[a['rd']] + (r[ra[1]] if ra[0] == 'r' else ra[1])) & M32
            r[a['rd']] = v
            cmpv = r[rb[1]] if rb[0] == 'r' else rb[1]
            sv = (self.N, self.Z, self.C, self.V)
            self.setflags_sub(v, cmpv)      # VC4: addcmpb does not change flags -> restore
            t = self.cond(a['cc'])
            self.N, self.Z, self.C, self.V = sv
            if t:
                npc = a['target'] & M32
        elif op == 'b':
            if self.cond(a['cc']):
                npc = a['target'] & M32
        elif op == 'bl':
            r[26] = npc; npc = a['target'] & M32
        elif op == 'movi':
            r[a['rd']] = a['imm'] & M32
        elif op == 'addi3':
            base = pc if a['rs'] == 31 else 0 if a['rs'] == 32 else r[a['rs']]
            r[a['rd']] = (base + a['imm']) & M32
        elif op == 'alui' or op == 'alu2':
            s = a['imm'] if op == 'alui' else r[a['rs']]
            v = self.alu(a['alu'], r[a['rd']], s)
            if v is not None:
                r[a['rd']] = v & M32
        elif op == 'alu3':
            v = self.alu(a['alu'], r[a['ra']], r[a['rb']])
            if v is not None:
                r[a['rd']] = v & M32
        elif op == 'mem':
            base = pc if a['base'] == 31 else r[a['base']]
            w = a['w']
            size = {'ld': 4, 'st': 4, 'ldh': 2, 'sth': 2, 'ldsh': 2, 'ldb': 1, 'stb': 1, 'ldsb': 1}[w]
            ea = base + a['off']
            if a['idx'] is not None:
                ea += r[a['idx']] * size
            ea &= M32
            if size > 1:            # the bus ignores the low address bits of halfword/word accesses [LLE]
                ea &= ~(size - 1)
            if w.startswith('st'):
                self.m.write(ea, size, r[a['rd']])
            else:
                v = self.m.read(ea, size)
                if w in ('ldsh', 'ldsb'):
                    v = sx(v, 8 * size) & M32
                r[a['rd']] = v
            if a['mode'] == '++':
                r[a['base']] = (r[a['base']] + size) & M32
        elif op == 'push':      # VC4 stm order: highest register at the lowest address, lr on top [LLE]
            regs = a['regs'][::-1] + ([26] if a['lrpc'] else [])
            sp = (r[25] - 4 * len(regs)) & M32
            for k, x in enumerate(regs):
                self.m.write(sp + 4 * k, 4, r[x])
            r[25] = sp
        elif op == 'pop':
            regs = a['regs'][::-1] + ([31] if a['lrpc'] else [])
            sp = r[25]
            for k, x in enumerate(regs):
                v = self.m.read(sp + 4 * k, 4)
                if x == 31:
                    npc = v
                else:
                    r[x] = v
            r[25] = (sp + 4 * len(regs)) & M32
        elif op == 'rts':
            npc = r[26]
        elif op == 'br':
            npc = r[a['rd']]
        elif op == 'blr':
            r[26] = npc; npc = r[a['rd']]
        elif op == 'addsp':
            r[25] = (r[25] + a['imm']) & M32
        elif op == 'setcc':
            r[a['rd']] = int(self.cond(a['cc']))
        elif op == 'tst':
            self.setflags_sub(r[a['rd']], 0)
        elif op == 'switch':
            idx = r[a['rd']]
            tbl = npc
            if a['w'] == 1:
                off = sx(self.m.read(tbl + idx, 1), 8)
            else:
                off = sx(self.m.read(tbl + 2 * idx, 2), 16)
            npc = (tbl + 2 * off) & M32
        elif op in ('nop', 'bkpt'):
            pass
        elif op == 'wfi':
            self.wfi = 1
        elif op == 'rti':       # [P] VC4 semantics: sr = *sp++; pc = *sp++
            self.sr = self.m.read(r[25], 4); npc = self.m.read(r[25] + 4, 4); r[25] = (r[25] + 8) & M32
        elif op == 'rdcfg':
            r[a['rd']] = self.cfg
        elif op == 'vec':
            if not self.vec_nop:
                if self.vpu is None:
                    self.vpu = VPU(self)
                self.vpu.execute(i)
        elif op == 'ei':
            self.ie = 1
        elif op == 'di':
            self.ie = 0
        else:
            raise EmuError('unimplemented %s at %#x: %s' % (op, pc, i.text()))
        if r[31] != pc:         # an instruction wrote pc as its destination: that is a jump [LLE: mov pc,#imm]
            npc = r[31]
        r[31] = npc


def disasm(data, base, start, end):
    rd = lambda a: struct.unpack_from('<H', data, a - base)[0] if 0 <= a - base < len(data) - 1 else 0
    a = start; out = []
    while a < end:
        i = decode(rd, a); out.append(i); a += i.size
    return out
