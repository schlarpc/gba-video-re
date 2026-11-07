#!/usr/bin/env python3
"""Random-stream ENCODER for the Hydrogen DEFLATE variant (test-vector generator for fuzz.py).

Not a compressor: it emits valid streams that exercise every syntax element (stored blocks at
every bit alignment and parity, fixed blocks, dynamic blocks with all code-length codes,
all length and distance codes)."""
import random, struct, heapq

from hyinflate import LBASE, LEXT, DBASE, DEXT, CLORDER


class BW:
    """bit writer: MSB-first into 16-bit little-endian words"""
    def __init__(self):
        self.words = []; self.acc = 0; self.n = 0

    def put(self, v, k):
        for i in range(k - 1, -1, -1):
            self.acc = (self.acc << 1) | ((v >> i) & 1); self.n += 1
            if self.n == 16:
                self.words.append(self.acc); self.acc = 0; self.n = 0

    @property
    def pos(self):
        return len(self.words) * 16 + self.n

    def data(self):
        w = list(self.words)
        if self.n: w.append(self.acc << (16 - self.n))
        return b''.join(struct.pack('<H', x) for x in w)


def canon(lengths):
    mx = max(lengths)
    bl = [0] * (mx + 1)
    for l in lengths:
        if l: bl[l] += 1
    bl[0] = 0
    code = 0; nxt = [0] * (mx + 1)
    for b in range(1, mx + 1):
        code = (code + bl[b - 1]) << 1
        nxt[b] = code
    codes = {}
    for s, l in enumerate(lengths):
        if l: codes[s] = (nxt[l], l); nxt[l] += 1
    return codes


def huff_lengths(freq, limit=15):
    """lengths from frequencies (classic Huffman, then crude limit by flattening)"""
    syms = [s for s, f in enumerate(freq) if f]
    L = [0] * len(freq)
    if len(syms) == 1:
        L[syms[0]] = 1; return L
    while True:
        h = [(freq[s], i, [s]) for i, s in enumerate(syms)]
        heapq.heapify(h); k = len(h)
        depth = {s: 0 for s in syms}
        while len(h) > 1:
            a = heapq.heappop(h); b = heapq.heappop(h)
            for s in a[2] + b[2]: depth[s] += 1
            heapq.heappush(h, (a[0] + b[0], k, a[2] + b[2])); k += 1
        if max(depth.values()) <= limit:
            for s in syms: L[s] = depth[s]
            return L
        freq = [(f + 1) // 2 + (1 if f else 0) for f in freq]


def lcode(n):
    for i in range(28, -1, -1):
        if n >= LBASE[i]:
            if i == 28 and n != 258: continue
            if n - LBASE[i] < (1 << LEXT[i]) or i == 28: return i, n - LBASE[i]
    raise ValueError(n)


def dcode(d):
    for i in range(29, -1, -1):
        if d >= DBASE[i]: return i, d - DBASE[i]


def gen_tokens(rng, out, n):
    """append n output bytes as a token list [(lit) | (len, dist)] given history out"""
    toks = []
    while n > 0:
        if len(out) > 0 and n >= 3 and rng.random() < 0.5:
            L = min(n, rng.choice([3, 4, 5, 10, 11, 18, 35, 67, 130, 257, 258, rng.randint(3, 258)]))
            D = rng.randint(1, min(len(out), 32768))
            if rng.random() < 0.3: D = min(len(out), rng.choice([1, 2, 5, 7, 24577, 32768]))
            for i in range(L): out.append(out[-D])
            toks.append((L, D)); n -= L
        else:
            b = rng.choice([rng.randint(0, 255), 0, 255, 0x41]); out.append(b); toks.append((b,)); n -= 1
    return toks


def encode(rng, size, block_types=(0, 1, 2)):
    bw = BW(); out = bytearray()
    while len(out) < size:
        bt = rng.choice(block_types)
        n = min(size - len(out), rng.choice([1, 2, 3, 7, 100, 1000, rng.randint(1, 5000)]))
        bw.put(bt, 2)
        if bt == 0:
            n = min(n, 0xffff)
            payload = bytes(rng.randint(0, 255) for _ in range(n))
            bw.put(n, 16)
            pending = (-bw.pos) % 16
            i = 0
            if n and pending >= 8:
                bw.put(payload[0], 8); i = 1; pending -= 8
            bw.put(0, pending)                     # dropped bits (reader discards them)
            rest = payload[i:]
            ev = len(rest) & ~1
            for k in range(0, ev, 2):              # raw memory copy = byte-swapped words in bit order
                bw.put(rest[k + 1], 8); bw.put(rest[k], 8)
            if len(rest) & 1: bw.put(rest[-1], 8)   # odd tail via an 8-bit read
            out += payload
            continue
        toks = gen_tokens(rng, out, n)
        if bt == 1:
            lit = canon([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8); dist = None
        else:
            lf = [0] * 286; df = [0] * 30
            lf[256] = 1
            for t in toks:
                if len(t) == 1: lf[t[0]] += 1
                else: lf[257 + lcode(t[0])[0]] += 1; df[dcode(t[1])[0]] += 1
            # sprinkle random extra symbols so the tables vary
            for _ in range(rng.randint(0, 20)): lf[rng.randint(0, 285)] += rng.randint(0, 3)
            if not any(df): df[rng.randint(0, 29)] = 1
            if sum(1 for f in df if f) == 1: df[(df.index(max(df)) + 1) % 30] += 1
            ll = huff_lengths(lf); dl = huff_lengths(df)
            hlit = max(257, max(i for i, l in enumerate(ll) if l) + 1)
            hdist = max(1, max(i for i, l in enumerate(dl) if l) + 1)
            seq = ll[:hlit] + dl[:hdist]
            # run-length encode code lengths
            rl = []; i = 0
            while i < len(seq):
                v = seq[i]; j = i
                while j < len(seq) and seq[j] == v: j += 1
                run = j - i
                if v == 0 and run >= 3:
                    while run >= 11: k = min(run, 138); rl.append((18, k - 11)); run -= k; i += k
                    if run >= 3: rl.append((17, run - 3)); i += run; run = 0
                    while run: rl.append((0,)); run -= 1; i += 1
                elif run >= 4 and rng.random() < 0.8:
                    rl.append((v,)); i += 1; run -= 1
                    while run >= 3: k = min(run, 6); rl.append((16, k - 3)); run -= k; i += k
                    while run: rl.append((v,)); run -= 1; i += 1
                else:
                    rl.append((v,)); i += 1
            cf = [0] * 19
            for t in rl: cf[t[0]] += 1
            if sum(1 for f in cf if f) == 1: cf[(cf.index(max(cf)) + 1) % 19] += 1
            cl = huff_lengths(cf, 7)
            hclen = max(4, max(k + 1 for k in range(19) if cl[CLORDER[k]]))
            bw.put(hlit - 257, 5); bw.put(hdist - 1, 5); bw.put(hclen - 4, 4)
            for k in range(hclen): bw.put(cl[CLORDER[k]], 3)
            cc = canon(cl)
            for t in rl:
                c, l = cc[t[0]]; bw.put(c, l)
                if t[0] == 16: bw.put(t[1], 2)
                elif t[0] == 17: bw.put(t[1], 3)
                elif t[0] == 18: bw.put(t[1], 7)
            lit = canon(ll); dist = canon(dl)
        for t in toks:
            if len(t) == 1:
                c, l = lit[t[0]]; bw.put(c, l)
            else:
                li, le = lcode(t[0]); c, l = lit[257 + li]; bw.put(c, l); bw.put(le, LEXT[li])
                di, de = dcode(t[1])
                if dist is None: bw.put(di, 5)
                else: c, l = dist[di]; bw.put(c, l)
                bw.put(de, DEXT[di])
        c, l = lit[256]; bw.put(c, l)
    bw.put(3, 2)
    return struct.pack('<I', len(out)) + bw.data(), bytes(out)
