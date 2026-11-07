#!/usr/bin/env python3
"""Reference decoder for the Hydrogen DEFLATE variant ("hyCompressionManager::Inflate").

Blob = u32 LE uncompressed size, then a bit stream read MSB-first out of 16-bit little-endian words.
Blocks: 2-bit type (0 stored, 1 fixed Huffman, 2 dynamic Huffman, 3 end). No BFINAL bit.
"""
import struct

LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
LEXT = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]
DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097,
         6145, 8193, 12289, 16385, 24577]
DEXT = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]
CLORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]


class Bits:
    def __init__(self, data, pos):
        self.d = data; self.p = pos; self.buf = 0; self.n = 0  # buf holds n bits, MSB-aligned in a python int

    def need(self, k):
        while self.n < k:
            w = self.d[self.p] | (self.d[self.p + 1] << 8); self.p += 2
            self.buf = (self.buf << 16) | w; self.n += 16

    def get(self, k):
        if k == 0: return 0
        self.need(k)
        self.n -= k
        v = (self.buf >> self.n) & ((1 << k) - 1)
        self.buf &= (1 << self.n) - 1
        return v

    def peek(self, k):
        self.need(k)
        return (self.buf >> (self.n - k)) & ((1 << k) - 1)


def build(lengths):
    """canonical code -> {(len, code): sym}"""
    mx = max(lengths) if lengths else 0
    bl = [0] * (mx + 2)
    for l in lengths:
        if l: bl[l] += 1
    code = 0; nxt = [0] * (mx + 2)
    for b in range(1, mx + 1):
        code = (code + bl[b - 1]) << 1 if b > 1 else 0
        nxt[b] = code
    # standard: code = (code + bl[bits-1]) << 1 with bl[0]=0
    code = 0; bl[0] = 0
    for b in range(1, mx + 1):
        code = (code + bl[b - 1]) << 1
        nxt[b] = code
    t = {}
    for s, l in enumerate(lengths):
        if l:
            t[(l, nxt[l])] = s; nxt[l] += 1
    return t, mx


def dec_sym(br, tab):
    t, mx = tab
    code = 0
    for l in range(1, mx + 1):
        code = (code << 1) | br.get(1)
        if (l, code) in t: return t[(l, code)]
    raise ValueError('bad code')


FIXED_LIT = build([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8)


class Inflater:
    """Streaming decoder: out() yields the whole uncompressed stream; tracks block info."""
    def __init__(self, data, off):
        self.size = struct.unpack_from('<I', data, off)[0]
        self.br = Bits(data, off + 4)
        self.blocks = []

    def run(self):
        br = self.br; out = bytearray()
        while True:
            bt = br.get(2)
            self.blocks.append((bt, len(out)))
            if bt == 3:
                break
            if bt == 0:
                ln = br.get(16)
                # player: if >=8 bits buffered, first byte comes from the bit buffer; rest of buffer dropped;
                # then raw copy from the halfword pointer; an odd tail byte is read with an 8-bit bit read.
                k = 0
                if br.n >= 8 and ln > 0:  # TODO verify against cart
                    out.append(br.get(8)); k = 1
                br.buf = 0; br.n = 0
                rem = ln - k
                ev = rem & ~1
                out += br.d[br.p:br.p + ev]; br.p += ev
                if rem & 1:
                    out.append(br.get(8))
                continue
            if bt == 1:
                lit = FIXED_LIT; dist = None
            else:
                hlit = br.get(5) + 257; hdist = br.get(5) + 1; hclen = br.get(4) + 4
                cl = [0] * 19
                for i in range(hclen): cl[CLORDER[i]] = br.get(3)
                clt = build(cl)
                lens = []
                while len(lens) < hlit + hdist:
                    s = dec_sym(br, clt)
                    if s < 16: lens.append(s)
                    elif s == 16: lens += [lens[-1]] * (br.get(2) + 3)
                    elif s == 17: lens += [0] * (br.get(3) + 3)
                    else: lens += [0] * (br.get(7) + 11)
                lit = build(lens[:hlit]); dist = build(lens[hlit:])
            while True:
                s = dec_sym(br, lit)
                if s < 256: out.append(s); continue
                if s == 256: break
                s -= 257
                L = LBASE[s] + br.get(LEXT[s])
                d = br.get(5) if dist is None else dec_sym(br, dist)
                D = DBASE[d] + br.get(DEXT[d])
                for i in range(L): out.append(out[-D])
        self.out = out
        return out
