#!/usr/bin/env python3
"""Internal-consistency oracle: the YUV420->RGB converter 0x2730e has four code paths
(r2/r3 flags -> 0x29174 plain, 0x291c4, 0x29264, 0x290ee; vertical-register variants 0x28662/0x28590).
Their outputs must be geometric transforms of each other."""
import sys, random
import os
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import pix_thumb as P

def run(m, Y, U, V, W, H, r2, r3, dw, dh):
    hp = (H + 15) & ~15
    buf = bytes(Y) + bytes(W * (hp - H)) + bytes(U) + bytes(V) + bytes(256)
    sp = m.alloc(buf); src = P.desc(m, 3, W, H, W, len(buf) - 256, sp)
    dpitch = 2 * ((dw + 15) & ~15)
    dp = m.alloc(bytes(dpitch * (dh + 16) + 256)); dst = P.desc(m, 1, dw, dh, dpitch, dpitch * dh, dp)
    m.call(0x2730e, dst, src, r2, r3, maxsteps=20_000_000)
    return [[m.m.read(dp + y * dpitch + 2 * x, 2) for x in range(dw)] for y in range(dh)]

if __name__ == '__main__':
    R = random.Random(3); W = H = 32
    Y = [R.randrange(256) for _ in range(W * H)]; U = [R.randrange(256) for _ in range(W * H // 4)]; V = [R.randrange(256) for _ in range(W * H // 4)]
    base = run(P.mach(), Y, U, V, W, H, 0, 0, W, H)
    T = lambda a: [list(x) for x in zip(*a)]
    FH = lambda a: [r[::-1] for r in a]
    FV = lambda a: a[::-1]
    cands = {'id': base, 'T': T(base), 'FH': FH(base), 'FV': FV(base), 'R90': FH(T(base)), 'R270': FV(T(base)), 'R180': FV(FH(base))}
    for r2, r3 in ((0, 1), (1, 0), (1, 1)):
        out = run(P.mach(), Y, U, V, W, H, r2, r3, W, H)
        best = max(cands, key=lambda k: sum(a == b for ra, rb in zip(out, cands[k]) for a, b in zip(ra, rb)))
        n = sum(a == b for ra, rb in zip(out, cands[best]) for a, b in zip(ra, rb))
        print('r2=%d r3=%d best=%s match %d/%d' % (r2, r3, best, n, W * H))

def show(out, ref):
    for ra, rb in zip(out, ref):
        print(''.join('.' if a == b else 'x' for a, b in zip(ra, rb)))
