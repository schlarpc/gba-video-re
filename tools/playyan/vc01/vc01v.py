#!/usr/bin/env python3
"""VC01 vector unit (PPU array): decoder, disassembly text and emulator -- unified model.

Merged from the per-codec kernel work (MP3, MPEG-4, AAC, pixel pipeline) and the G.726 work;
evidence and status per field/opcode in docs/playyan/vc01-isa.md (Vector instructions).  CFG holds switches for ablation.

VRF: 64 x 64 bytes P[y][x] (EP1320029).  16-bit lane at (y, x) = P[y][x] | P[y][x+16] << 8 (x mod 64);
8-bit lane = P[y][x] (zero-extended read, low byte written).  A 32-bit quantity is kept as two 16-bit
registers at x and x+32 of the same row (spec bit 5).
Register spec s (8 bits) = [h1 h0][m1 m0][n3..n0]:
   H: row y = h*16 + n, x = m*16, lanes x..x+15      V: column x = m*16 + n, rows h*16 .. +15
Instruction:
  h0 = 0010 00 C oooooo www   C compact (48-bit); www: bit2 D vertical, bit1 16-bit, bit0 D offset
  h1 = DDDDDDDD rrr AAAAA      rrr = offset register r0-r6 (7 = none): value v adds v & 63 to x and
                               (v >> 6) & 63 to y of the operands that request it
  h2 = AAAAA i bbbbbbbbbb      A10: bit9 A vertical, bit8 A offset (48-bit), bits 7..0 spec
  data, 48-bit: i=1: b = F PPP iiiiii ; i=0: b = B spec<<2 | B offset<<1 | B vertical
  data, 80-bit: h3 = [B field 15..10][A field 9..5][A offset reg 3..1][step 0]; h4 = [14 F][13..11 P]
                [10 CLRA][9 ACC][8..5 REP][4..3 SRU op][2..0 SRU reg]; imm16 = h3 & 0xfc00 | b
  memory: b = RRRR bbb sss (REP = 1 << R, base r[bbb], per-repetition stride r[sss], 7 = none)
"""

M32 = 0xffffffff


class EmuError(Exception):
    pass


class VectorFault(EmuError):
    """Unimplemented / not-understood vector instruction.  .addr, .words, .why"""
    def __init__(self, addr, words, why):
        self.addr = addr; self.words = tuple(words); self.why = why
        super().__init__('vector fault at %#x [%s]: %s' % (addr, ' '.join('%04x' % x for x in words), why))


def sx(v, b):
    v &= (1 << b) - 1
    return v - (1 << b) if v & (1 << (b - 1)) else v


def s16(v):
    return sx(v, 16)


def u16(v):
    return v & 0xffff


CFG = dict(
    discard=True,        # 16-bit horizontal D spec 0x10 = no write-back
    sru0='usum',         # SRU op 0: 'usum' (unsigned lane sum) | 'sum' | 'ppu0'  [MP3 synth needs usum; pixel passes either]
    astep='bit0',        # 80-bit A steps with REP: 'always' | 'bit0' | 'bit7'  [C AAC rep-r0 min/max reductions 0x9802]
    dstep='bit0',        # 80-bit D steps with REP: 'always' | 'bit0'  [C AAC 0x9802: h3 bit 0 = D and A step]
    bstep='bit10',       # 80-bit register B steps: 'bit10' | 'always'
    aplus=3,             # 80-bit A-field bits added to the A row/column (mask of f)
    memstride='row',     # stride when sss = 7: 'row' (16 bytes x lane size) | 'zero'
    vldoff='sx10',       # vld offset field: 'sx10' | 'a5' | 'u10'
    memalign=True,       # op0/op1 addresses rounded down to the row (16 bytes 8-bit, 32 bytes 16-bit) [C MP3 requant]
    op24='shl',          # 'shl' (C = bit out) | 'sat'  [C MPEG-4 0x1FEA0 funnel shift]
    op21='round',        # 'round' | 'floor'  [C IEEE-1180 IDCT, MP3 requant accuracy (floor: 7.4% off by up to 32768),
                         #  MP3 synth vs ISO float 1526 vs 1449/1536, pixel hashes; AAC kernels pass with either]
    acc_sat=True,        # not discriminated by any suite
    vstep16=True,        # 16-bit vertical REP steps 16-bit columns (byte col bits 4/5 swapped) [C AAC 0x9930]
    op6='or',            # 'or' | 'max'  [C MP3 requant 0x35182 or-in of carry bit; pixel; G.726 passes either]
    op9='sat8',          # 'sat8' (8-bit dest clamps 0..255, 16-bit wraps) | 'addsat' | 'add'
                         #  [C pixel 0..255 clamp; AAC 32-bit adds need 16-bit wrap]
    op16='floor',        # (a*b)>>8: 'floor' | 'round'  [C pixel]
    op17='satfloor',     # (a*b)>>8 saturated to dest width: 'satfloor' | 'satround' | 'wrap'  [gap]
    op20='round',        # 'floor' | 'round'  [C AAC iquant index round(|q|/8) (more accurate); pixel]
    op28='min',          # 'min' | 'clip'  [C AAC block-float with negative-dominant spectra: clip fails 11/20]
    cbase=True,          # operand offset flags add the VRF column base (scalar 0x54-0x57 30), see VPU._cb [gap]
    f0='noinc8w',        # 80-bit A field f == 0 (8-bit A): 'noinc8w' (no REP step iff D is 16-bit) | 'noinc8' | 'plain'  [pixel, gap]
)

# ------------------------------------------------------------------ opcodes
VOPNAME = {
    0: 'vld', 1: 'vst', 2: 'vld2', 3: 'vld3',
    4: 'vmov', 5: 'vand', 6: 'vor', 7: 'veor', 8: 'vadd', 9: 'vadds8', 10: 'vsub', 11: 'vsub11',
    12: 'vrsub', 13: 'vlt', 14: 'vmul', 15: 'vmuls', 18: 'vmulhd.ss', 19: 'vmulhd.su', 16: 'vmulh8', 17: 'vmulh8s', 20: 'vmulhn.uu',
    21: 'vmulhn.su', 23: 'vmulhdt.su', 24: 'vshl', 25: 'vshls', 26: 'vasr', 27: 'vlsr', 28: 'vmin',
    29: 'vmax', 30: 'vdist', 31: 'vtestmag', 34: 'vaddc', 35: 'vsubc', 36: 'vrsubc', 37: 'vclip',
    40: 'vaddsgn', 42: 'vlookup', 43: 'vsignshl', 44: 'vbitplanes',
}
COND = ['', '.never?', '.ifz', '.ifnz', '.ifn', '.ifnn', '.ifc', '.ifnc']
SRUNAME = ['usum', 'sum', 'imin', 'imax']


def vname(op):
    return VOPNAME.get(op, 'vop%d' % op)


_DW = [16]          # destination width of the instruction being executed (ops that saturate to it)


def _add(a, b, c):
    s = u16(a) + u16(b); return s, s >> 16


def _sub(a, b, c):
    return u16(a) - u16(b), int(u16(a) < u16(b))


def _rsub(a, b, c):
    return u16(b) - u16(a), int(u16(b) < u16(a))


def _addc(a, b, c):
    s = u16(a) + u16(b) + c; return s, s >> 16


def _subc(a, b, c):
    d = u16(a) - u16(b) - c; return d, int(d < 0)


def _rsubc(a, b, c):
    d = u16(b) - u16(a) - c; return d, int(d < 0)


def _sat(x):
    return (max(-32768, min(32767, x)), int(x > 32767 or x < -32768))


def _op24(a, b, c):
    if CFG['op24'] == 'sat':
        return _sat(a << (b & 31))[0], None
    r = u16(a) << (b & 15)
    return r, (r >> 16) & 1


def _op21(a, b, c):
    p = a * u16(b)
    return ((p + 0x8000) >> 16 if CFG['op21'] == 'round' else p >> 16), None


# lane function: f(a, b, cin) -> result | (result, carry_out or None); a, b signed 16-bit values
def _op9(a, b):
    v = a + b
    if CFG['op9'] == 'addsat':            # add saturating to the destination width [C pixel]
        return max(0, min(255, v)) if _DW[0] == 8 else max(-32768, min(32767, v))
    if CFG['op9'] == 'sat8':              # saturate only an 8-bit destination
        return max(0, min(255, v)) if _DW[0] == 8 else v
    return v


def _op28(a, b):
    if CFG['op28'] == 'clip':             # symmetric clamp to [-b-1, b] [pixel]
        return max(-b - 1, min(b, a))
    return min(a, b)


def _op16(a, b):
    return (a * b + (128 if CFG['op16'] == 'round' else 0)) >> 8


def _op17(a, b):
    """(a*b)>>8 saturated to the destination width (8-bit: 0..255) [P gap: brightness scaling
    py_fw0 0x27bea (gp+1632, 256 = neutral) and 0x283b0 intensity; rounding not discriminated]"""
    v = (a * b + (128 if CFG['op17'] == 'satround' else 0)) >> 8
    if CFG['op17'] == 'wrap':
        return v
    return max(0, min(255, v)) if _DW[0] == 8 else max(-32768, min(32767, v))


VOPS = {
    4: lambda a, b, c: b,
    5: lambda a, b, c: a & b,
    6: lambda a, b, c: (a | b) if CFG['op6'] == 'or' else max(a, b),
    7: lambda a, b, c: a ^ b,
    8: _add,
    9: lambda a, b, c: _op9(a, b),
    10: _sub,
    11: lambda a, b, c: a - b,
    12: _rsub,
    13: lambda a, b, c: int(a < b),
    14: lambda a, b, c: a * b,
    15: lambda a, b, c: _sat(a * b)[0],
    16: lambda a, b, c: _op16(a, b),
    17: lambda a, b, c: _op17(a, b),
    18: lambda a, b, c: (a * b) >> 16,
    19: lambda a, b, c: (a * u16(b)) >> 16,
    20: lambda a, b, c: ((u16(a) * u16(b)) + (0x8000 if CFG['op20'] == 'round' else 0)) >> 16,
    21: _op21,
    23: lambda a, b, c: int(a * u16(b) / 65536),
    24: _op24,
    25: lambda a, b, c: _sat(a << (b & 15)),
    26: lambda a, b, c: (a >> (b & 31), ((a >> ((b & 31) - 1)) & 1) if b & 31 else c),        # asr, C = last bit out [C MP3 requant]
    27: lambda a, b, c: (u16(a) >> (b & 31), ((u16(a) >> ((b & 31) - 1)) & 1) if b & 31 else c),  # lsr, C = last bit out
    28: lambda a, b, c: _op28(a, b),
    29: lambda a, b, c: max(a, b),
    30: lambda a, b, c: abs(a - b),
    31: lambda a, b, c: int(abs(a) >= b),
    34: _addc,
    35: _subc,
    36: _rsubc,
    37: lambda a, b, c: max(0, min(a, b)),
    40: lambda a, b, c: b + (a > 0) - (a < 0),
    43: lambda a, b, c: (a << b) if b >= 0 else (a >> -b),
    44: None,      # bitplanes: lane L = -1 if bit L of B else 0
    42: None,      # lookup (special-cased)
}
CONDF = {0: lambda n, z, c: True, 2: lambda n, z, c: z, 3: lambda n, z, c: not z,
         4: lambda n, z, c: n, 5: lambda n, z, c: not n, 6: lambda n, z, c: c, 7: lambda n, z, c: not c}


# ------------------------------------------------------------------ decode / text
def vdecode(w):
    """Decode a vector instruction (3 or 5 halfwords) into a field dict (additive superset of the
    G.726-era fields)."""
    h0, h1, h2 = w[0], w[1], w[2]
    op = (h0 >> 3) & 63
    v = dict(cmpt=(h0 >> 9) & 1, op=op, www=h0 & 7, name=vname(op), D=h1 >> 8, dfl=(h1 >> 5) & 7,
             A=((h1 & 0x1f) << 5) | (h2 >> 11), i=(h2 >> 10) & 1, lo=h2 & 0x3ff,
             vert=bool(h0 & 4), w16=bool(h0 & 2),
             imm=None, brow=None, bscal=None, setf=0, cond=0, rep=1, repcode=0, sru=None, h3=None, h4=None,
             acc=0, clra=0)
    if len(w) == 5:
        v['h3'], v['h4'] = h3, h4 = w[3], w[4]
    if op <= 3:
        v['kind'] = 'mem'
        v['rb'] = (h2 >> 3) & 7
        v['sreg'] = h2 & 7
        v['mform'] = h2 & 7
        v['repcode'] = (h2 >> 6) & 15
        # REP code 7 on vld/vst = 64, the largest REPn of the patent (US20030159023: n in 2,4,8,16,32,48,
        # 64); 1 << 7 = 128 wrapped the 64-row VRF and stored the spectrum twice (mi_fw0 0x3d72, AAC short
        # windows: 75/3201 frames garbage with 128, 0/3201 off by > 2 LSB vs ffmpeg with 64) [C AAC e2e]
        v['rep'] = 64 if v['repcode'] == 7 else 1 << v['repcode']
        if v['h4'] is not None:
            v['cond'] = (v['h4'] >> 11) & 7
        return v
    v['kind'] = 'data'
    if v['cmpt']:
        if v['i']:
            v['imm'] = sx(h2 & 63, 6); v['setf'] = (h2 >> 9) & 1; v['cond'] = (h2 >> 6) & 7
        else:
            v['brow'] = (h2 & 0x3ff) >> 2
    else:
        h3, h4 = v['h3'], v['h4']
        if v['i']:
            v['imm'] = s16((h3 & 0xfc00) | (h2 & 0x3ff))
        elif h3 & 0x8000:
            v['bscal'] = (h3 >> 12) & 7
        else:
            v['brow'] = (h2 & 0x3ff) >> 2
        v['aplus'] = (h3 >> 5) & 7
        v['setf'] = (h4 >> 14) & 1; v['cond'] = (h4 >> 11) & 7
        v['clra'] = (h4 >> 10) & 1; v['acc'] = (h4 >> 9) & 1
        v['repcode'] = (h4 >> 5) & 15
        v['rep'] = 1 << v['repcode']
        so, sr = (h4 >> 3) & 3, h4 & 7
        if sr != 7:
            v['sru'] = (so, sr)
    return v


def spec_yx(s, vert):
    n = s & 15; hi = s >> 6; mid = (s >> 4) & 3
    if vert:
        return hi << 4, (mid << 4) | n
    return (hi << 4) | n, mid << 4


def _r(spec, vert, w16=True, off=None):
    y, x = spec_yx(spec, vert)
    s = '%s%s(%d,%d)' % ('V' if vert else 'H', 'X' if w16 else '', y, x)
    if off is not None and off != 7:
        s += '+r%d' % off
    return s


def operand_text(v):
    """(D, A, B) operand strings."""
    dfl = v['dfl']; w16 = v['w16']; lo = v['lo']
    if v['cmpt']:
        D = _r(v['D'], v['vert'], w16, dfl if v['www'] & 1 else None)
        A = _r(v['A'] & 0xff, bool(v['A'] & 0x200), w16, dfl if v['A'] & 0x100 else None)
        if v['imm'] is not None:
            B = '#%d' % v['imm']
        else:
            B = _r(lo >> 2, bool(lo & 1), w16, dfl if lo & 2 else None)
        return D, A, B
    h3 = v['h3']; f = (h3 >> 5) & 31; A10 = v['A']; aoff = (h3 >> 1) & 7
    D = _r(v['D'], v['vert'], w16, dfl)
    if f & 16:
        y = ((A10 >> 6) & 3) << 4 | (f & 15)
        A = 'H(%d,%d)%s' % (y, A10 & 63, '+r%d' % aoff if aoff != 7 else '')
    else:
        av = bool(A10 & 0x200)
        y, x = spec_yx(A10 & 0xff, av)
        if av:
            x += f & CFG['aplus']
        else:
            y += f & CFG['aplus']
        A = '%s%s(%d,%d)%s' % ('V' if av else 'H', 'X' if f & 8 else '', y, x, '+r%d' % aoff if aoff != 7 else '')
    if v['imm'] is not None:
        B = '#%d' % v['imm']
    elif v['bscal'] is not None:
        B = 'r%d' % v['bscal']
    else:
        bf = h3 >> 10
        B = _r(lo >> 2, bool(lo & 1), bool(bf & 2), (bf >> 2) & 7) + ('' if bf & 1 else '!')
    return D, A, B


def vfmt(v):
    if v['kind'] == 'cbase':
        return 'vcbase #0' if v['o'] == 20 else 'vcbase +=%d' % (16 * (v['o'] - 20))
    w16 = v['w16']
    name = v['name'] + ('' if w16 else '.8')
    if v['kind'] == 'mem':
        base = 'r%d' % v['rb']
        if v['op'] == 1:
            off = 32 * v['D'] + 16 * v['i']
            if v['cmpt']:
                reg = _r(v['A'] & 0xff, bool(v['A'] & 0x200), w16)
            else:
                reg = operand_text(dict(v, kind='data', imm=0))[1]
        else:
            off = 16 * ((sx(v['A'], 10) << 1) | v['i'])
            if not v['cmpt']:
                off += 2 * (v['h3'] >> 13)
            o = (v['h4'] >> 5) & 7 if not v['cmpt'] else 7
            reg = _r(v['D'], v['vert'], w16, o)
        s = '%s %s, (%s%+d)' % (name, reg, base, off)
        if v['rep'] > 1:
            s += ' rep%d' % v['rep']
        if v['sreg'] != 7:
            s += ' +=r%d' % v['sreg']
        if v['cond']:
            s = s.replace(name, name + COND[v['cond']], 1)
        return s
    D, A, B = operand_text(v)
    if v['D'] == 0x10 and w16 and not v['vert']:
        D = '-'
    s = '%s%s %s, %s, %s' % (name, COND[v['cond']], D, A, B)
    if v['setf']:
        s += ' setf'
    if v['repcode'] == 7 and not v['cmpt']:
        s += ' rep r0'
    elif v['rep'] > 1:
        s += ' rep%d' % v['rep']
    if v['clra']:
        s += ' clra'
    if v['acc']:
        s += ' acc'
    if v['sru']:
        s += ' %s r%d' % (SRUNAME[v['sru'][0]], v['sru'][1])
    return s


# ------------------------------------------------------------------ emulator
def _bx(c):
    c &= 63
    return (c & ~0x30) | ((c >> 1) & 0x10) | ((c << 1) & 0x20)


class Opd:
    """register operand: corner (y, x), vertical, 16-bit, steps with REP"""
    __slots__ = ('y', 'x', 'v', 'w', 'inc')

    def __init__(self, y, x, v, w, inc=True):
        self.y = y & 63; self.x = x & 63; self.v = v; self.w = w; self.inc = inc

    def lanes(self, k):
        y, x = self.y, self.x
        if not self.inc:
            k = 0
        if self.v:
            if self.w and CFG['vstep16']:   # 16-bit columns step in 16-bit-column space: 0..15 -> 32..47 [C AAC 0x9930]
                xb = _bx(_bx(x) + k)
                return [((y + i) & 63, xb) for i in range(16)]
            return [((y + i) & 63, (x + k) & 63) for i in range(16)]
        return [((y + k) & 63, (x + i) & 63) for i in range(16)]


def opd(s, vert, w16, off, inc=True):
    y, x = spec_yx(s, vert)
    return Opd(y + ((off >> 6) & 63), x + (off & 63), vert, w16, inc)


class VPU:
    def __init__(self, cpu):
        self.cpu = cpu
        self.P = [bytearray(64) for _ in range(64)]
        self.N = [0] * 16; self.Z = [0] * 16; self.C = [0] * 16
        self.acc = [0] * 16
        self.log = None          # optional list of (addr, v)
        self.cbase = 0           # VRF column base (scalar 0x5430 = 0, 0x5530/5630/5730 += 16/32/48), see _cb [gap]

    # --- raw access (also used by tests)
    def get16(self, y, x):
        y &= 63; x &= 63
        return s16(self.P[y][x] | self.P[y][(x + 16) & 63] << 8)

    def put16(self, y, x, val):
        y &= 63; x &= 63
        self.P[y][x] = val & 0xff; self.P[y][(x + 16) & 63] = (val >> 8) & 0xff

    def rcell(self, y, x, w16):          # pixel-fork test accessors (byte coordinates)
        return self.get16(y, x) if w16 else self.P[y & 63][x & 63]

    def wcell(self, y, x, w16, val):
        if w16:
            self.put16(y, x, val)
        else:
            self.P[y & 63][x & 63] = val & 0xff

    def get8(self, y, x):
        return self.P[y & 63][x & 63]

    def put8(self, y, x, val):
        self.P[y & 63][x & 63] = val & 0xff

    def rdo(self, o, k):
        if o.w:
            return [self.get16(y, x) for y, x in o.lanes(k)]
        return [self.P[y][x] for y, x in o.lanes(k)]

    def wro(self, o, k, vals, mask=None):
        for L, (y, x) in enumerate(o.lanes(k)):
            if mask is None or mask[L]:
                if o.w:
                    self.put16(y, x, vals[L])
                else:
                    self.P[y][x] = vals[L] & 0xff

    def fault(self, i, why):
        raise VectorFault(i.addr, i.words, why)

    def reg(self, n):
        return 0 if n == 7 else self.cpu.r[n]

    # --- operand decode
    def afield(self, v, off, inc):
        """80-bit A operand (also the 80-bit vst source)."""
        h3 = v['h3']; f = (h3 >> 5) & 31; A10 = v['A']
        if f & 16:     # byte-address mode: 8-bit, horizontal, any byte column
            return Opd((((A10 >> 6) & 3) << 4 | (f & 15)) + ((off >> 6) & 63), (A10 & 63) + (off & 63),
                       False, False, inc)
        av = bool(A10 & 0x200)
        y, x = spec_yx(A10 & 0xff, av)
        # f == 0: 8-bit operand; with a 16-bit D it does not step with REP (chroma doubling 0x284a0
        # vsub VX(16,32), V(16,0) rep2) [pixel]; with an 8-bit D it steps (RGB888 brightness 0x1893e) [gap]
        if f == 0 and (CFG['f0'] == 'noinc8' or (CFG['f0'] == 'noinc8w' and v['w16'])):
            return Opd(y + ((off >> 6) & 63), x + (off & 63), av, False, False)
        if av:
            x += f & CFG['aplus']
        else:
            y += f & CFG['aplus']
        return Opd(y + ((off >> 6) & 63), x + (off & 63), av, bool(f & 8), inc)

    def operands(self, v):
        lo = v['lo']; w16 = v['w16']
        if v['cmpt']:
            o = self.reg(v['dfl'])
            D = opd(v['D'], v['vert'], w16, o if v['www'] & 1 else 0)
            A = opd(v['A'] & 0xff, bool(v['A'] & 0x200), w16, o if v['A'] & 0x100 else 0)
            B = opd(lo >> 2, bool(lo & 1), w16, o if lo & 2 else 0) if v['brow'] is not None else None
            return D, A, B
        h3 = v['h3']
        st = h3 & 1
        dinc = True if CFG['dstep'] == 'always' else bool(st)
        ainc = {'always': True, 'bit0': bool(st), 'bit7': bool(h3 & 0x80)}[CFG['astep']]
        D = self._cb(opd(v['D'], v['vert'], w16, self.reg(v['dfl']), dinc), v['www'] & 1)
        A = self._cb(self.afield(v, self.reg((h3 >> 1) & 7), ainc), v['A'] & 0x100)
        B = None
        if v['brow'] is not None:
            bf = h3 >> 10
            binc = bool(bf & 1) if CFG['bstep'] == 'bit10' else True
            B = self._cb(opd(lo >> 2, bool(lo & 1), bool(bf & 2), self.reg((bf >> 2) & 7), binc), lo & 2)
        return D, A, B

    def _cb(self, o, flag):
        """VRF column base (scalar 0x5430 = 0, 0x5530/0x5630/0x5730 += 16/32/48 mod 64): added to the
        column of an operand whose offset flag is set (www bit0 = D, A10 bit8 = A, B bit1 = B) in the
        80-bit form and in memory ops; in the compact ALU form the flag selects the offset register
        instead.  [gap: bilinear scalers 0x28afa.. (loads only), RGB888 brightness 0x17fcc (sliding
        64-byte window: loads, the 0x1893e multiply and stores all base-relative)]"""
        if flag and self.cbase and CFG['cbase']:
            o.x = (o.x + self.cbase) & 63
        return o

    # --- execution
    def execute(self, i):
        v = i.a['v']
        if self.log is not None:
            self.log.append((i.addr, v))
        if v['kind'] == 'mem':
            return self.mem(i, v)
        if v['kind'] == 'cbase':          # scalar words 0x5430..0x5730 (decoded by vc01.py / test patch)
            self.cbase = 0 if v['o'] == 20 else (self.cbase + 16 * (v['o'] - 20)) & 63
            return
        r = self.cpu.r
        op = v['op']
        fn = VOPS.get(op, 'missing')
        if fn == 'missing':
            self.fault(i, 'vector opcode %d not implemented' % op)
        cond = CONDF.get(v['cond'])
        if cond is None:
            self.fault(i, 'vector condition %d' % v['cond'])
        D, A, B = self.operands(v)
        _DW[0] = 16 if D.w else 8
        rep = v['rep']
        if op == 42:      # lookup uses the memory-op format: D offset reg = h4 bits 7..5, no REP [C AAC iquant]
            o = self.reg((v['h4'] >> 5) & 7) if not v['cmpt'] else 0
            D = opd(v['D'], v['vert'], v['w16'], o)
            rep = 1
        if not v['cmpt'] and v['repcode'] == 7 and op != 42:
            rep = r[0]                                   # REP r0 [P AAC]
        if v['clra']:
            self.acc = [0] * 16
        for k in range(rep):
            av = self.rdo(A, k)
            if v['imm'] is not None:
                bv = [v['imm']] * 16
            elif v['bscal'] is not None:
                bv = [s16(r[v['bscal']])] * 16
            else:
                bv = self.rdo(B, k)
            act = [cond(self.N[L], self.Z[L], self.C[L]) for L in range(16)]
            cy = [None] * 16
            if op == 44:
                out = [-1 if (bv[L] >> L) & 1 else 0 for L in range(16)]
            elif op == 42:          # lookup: lane = mem16[r(base) + 2 * acc]  [P AAC]
                base = r[(v['lo'] >> 3) & 7]
                out = [s16(self.cpu.m.read((base + 2 * (self.acc[L] & 0xffff)) & M32, 2)) for L in range(16)]
            else:
                out = []
                for L in range(16):
                    res = fn(av[L], bv[L], self.C[L])
                    if type(res) is tuple:
                        res, cy[L] = res
                    out.append(s16(res))
            if v['acc']:
                for L in range(16):
                    if act[L]:
                        t = self.acc[L] + out[L]
                        self.acc[L] = max(-32768, min(32767, t)) if CFG['acc_sat'] else s16(t)
                out = [self.acc[L] if act[L] else out[L] for L in range(16)]
            if not D.w:
                out = [x & 0xff for x in out]
            if not (CFG['discard'] and D.w and not D.v and v['D'] == 0x10):
                self.wro(D, k, out, act)
            if v['setf']:
                for L in range(16):
                    if act[L]:
                        self.N[L] = int(out[L] < 0) if D.w else (out[L] >> 7) & 1
                        self.Z[L] = int(out[L] == 0)
                        if cy[L] is not None:
                            self.C[L] = cy[L]
            if v['sru']:
                so, reg = v['sru']
                sel = [L for L in range(16) if act[L]]
                if so == 0:
                    if CFG['sru0'] == 'usum':
                        res = sum((out[L] & 0xffff) if D.w else out[L] for L in sel)
                    elif CFG['sru0'] == 'sum':
                        res = sum(out[L] for L in sel)
                    else:
                        res = out[0] if act[0] else 0
                elif so == 1:
                    res = sum(out[L] for L in sel)
                elif not sel:
                    res = -1
                elif so == 2:
                    res = min(sel, key=lambda L: (out[L], L))
                else:
                    res = max(sel, key=lambda L: (out[L], L))
                r[reg] = res & M32

    def mem(self, i, v):
        r = self.cpu.r; m = self.cpu.m; w = i.words
        op = v['op']; lo = v['lo']
        rep = v['rep']
        base = r[v['rb']]
        sreg = v['sreg']
        act = None
        if op == 1:
            addr = base + 32 * v['D'] + 16 * v['i']
            if v['cmpt']:
                o = self._cb(opd(v['A'] & 0xff, bool(v['A'] & 0x200), v['w16'], 0), v['A'] & 0x100)
            else:
                f = (v['h3'] >> 5) & 31
                o = self._cb(self.afield(v, self.reg((v['h3'] >> 1) & 7), f != 8 or bool(v['h3'] & 0x10)), v['A'] & 0x100)   # no step only for f == 8 with h3 bit 4 clear (0x010e zero fills; 0x0114 AAC 0x9930 and 0x0202 MPEG-4 0x270d8 step)
        else:
            a = {'sx10': sx(v['A'], 10), 'a5': v['A'] & 31, 'u10': v['A']}[CFG['vldoff']]
            addr = base + 16 * ((a << 1) | v['i'])
            if v['cmpt']:
                o = self._cb(opd(v['D'], v['vert'], v['w16'], 0), v['www'] & 1)
            else:
                addr += 2 * (w[3] >> 13)
                h4 = v['h4']
                o = self._cb(opd(v['D'], v['vert'], v['w16'], self.reg((h4 >> 5) & 7), bool(w[3] & 0x11) or rep == 1),
                             v['www'] & 1)
                if v['cond']:
                    cond = CONDF.get(v['cond'])
                    act = [cond(self.N[L], self.Z[L], self.C[L]) for L in range(16)]
        n = 2 if o.w else 1
        if sreg != 7:
            stride = r[sreg]
        else:
            stride = 16 * n if CFG['memstride'] == 'row' else 0
        for k in range(rep):
            a = (addr + k * stride) & M32
            if CFG['memalign'] and op in (0, 1):
                a &= ~(16 * n - 1)      # aligned to the row size (32 bytes for 16-bit) [C MP3 requant output alignment]
            if op == 1:
                for L, val in enumerate(self.rdo(o, k)):
                    m.write((a + n * L) & M32, n, val)
            else:
                vals = [m.read((a + n * L) & M32, n) for L in range(16)]
                if op == 3:     # unaligned pair, second half: only lanes past the 16-lane block [C MP3 requant/memcpy]
                    blk = 16 * n
                    msk = [((a + n * L) // blk) != (a // blk) for L in range(16)]
                    act = msk if act is None else [p and q for p, q in zip(act, msk)]
                self.wro(o, k, vals, act)


class _CellRow:
    """vb[y][c]: 16-bit 'cell' view used by vec/aac_test.py (cell c = byte column with bits 4 and 5
    swapped, i.e. the AAC fork's register numbering on the byte-addressed VRF)."""
    def __init__(s, vpu, y): s.vpu = vpu; s.y = y
    @staticmethod
    def bx(c): return (c & ~0x30) | ((c >> 1) & 0x10) | ((c << 1) & 0x20)
    def __getitem__(s, c): return s.vpu.get16(s.y, s.bx(c)) & 0xffff
    def __setitem__(s, c, val): s.vpu.put16(s.y, s.bx(c), val)


VPU.vb = property(lambda self: [_CellRow(self, y) for y in range(64)])
