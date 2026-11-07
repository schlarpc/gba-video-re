#!/usr/bin/env python3
"""Function-by-function diff of the codec routines of every ADS player build against SRMTHFG's.

Reference functions (SRMTHFG, build bde72f6aba53) are given as [start, end) ranges. In every other
build the routine is located by searching function starts (ARM push / Thumb push) for the best
normalised match, then compared instruction by instruction. Normalisation: branch targets are made
relative to the function start, PC-relative literal loads keep only the loaded *value class*
(IWRAM/EWRAM/ROM/VRAM pointer or constant), so pure relocation does not count as a difference.
usage: builddiff.py [--detail NAME]
"""
import os, sys, struct, difflib, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import capstone
import carts, images

REF = 'srmt'
# name: (arm|thumb, start, end) in the SRMTHFG image
FUNCS = {
    'lzma_init':        ('arm', 0x03000404, 0x03000504),
    'lzma_decode':      ('arm', 0x03000554, 0x030005f8),
    'lzma_core':        ('arm', 0x03001c58, 0x03002a90),
    'cb_convert':       ('arm', 0x03000610, 0x03000c20),
    'unpredict_luma':   ('arm', 0x03000d8c, 0x03000df4),
    'unpredict_chroma': ('arm', 0x03000df4, 0x03000eb8),
    'vq_blit':          ('arm', 0x03001748, 0x03001c58),
    'adpcm_samples':    ('arm', 0x03002b14, 0x03002d98),
    'adpcm_block_hdr':  ('thumb', 0x020086b4, 0x02008856),
    'adpcm_refill':     ('thumb', 0x020085f8, 0x02008654),
    'adpcm_driver':     ('thumb', 0x02008654, 0x020086b4),
    'chunk_frames':     ('thumb', 0x02002130, 0x02002300),
    'chunk_codebook':   ('thumb', 0x02002304, 0x02002478),
}
BUILDS = {}
for cid, b in images.BUILD_OF.items():
    BUILDS.setdefault(b, cid)

def image(cid):
    rom = carts.load(cid)
    if cid in ('jimmy', 'noah-proto'):
        im = images.hydrogen_images(rom)
        return {0x03000000: im['iwram'], 0x08000000: rom[:0x40000]}
    im = images.sfcd_images(rom)
    return {0x03000000: im['iwram'], 0x02000000: im['ewram']}

def region(img, a):
    for base, buf in img.items():
        if base <= a < base + len(buf): return base, buf
    return None, None

def vclass(v):
    t = v >> 24
    return {2: 'EWRAM', 3: 'IWRAM', 6: 'VRAM', 8: 'ROM', 9: 'ROM'}.get(t, '#%x' % v)

def norm(img, kind, start, end):
    base, buf = region(img, start)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if kind == 'thumb' else capstone.CS_MODE_ARM)
    md.detail = True
    out = []
    for ins in md.disasm(buf[start - base:end - base], start):
        op = ins.op_str
        if ins.mnemonic.startswith('b') and op.startswith('#'):
            t = int(op[1:], 16)
            op = ('+%x' % (t - start)) if start <= t < end else 'ext'
        elif 'pc' in op and ins.mnemonic.startswith('ldr'):
            # literal load: compare the value class only
            try:
                disp = ins.operands[1].mem.disp
                la = ((ins.address + (4 if kind == 'thumb' else 8)) & ~3) + disp
                b2, buf2 = region(img, la)
                v = struct.unpack_from('<I', buf2, la - b2)[0] if buf2 else 0
                op = op.split(',')[0] + ', =' + vclass(v)
            except Exception:
                pass
        out.append(f'{ins.mnemonic} {op}')
    return out

def starts(img, kind):
    res = []
    for base, buf in img.items():
        if kind == 'arm':
            for o in range(0, len(buf) - 4, 4):
                if struct.unpack_from('<I', buf, o)[0] >> 16 == 0xe92d: res.append(base + o)
        else:
            for o in range(0, len(buf) - 2, 2):
                if buf[o + 1] == 0xb5 or (buf[o + 1] == 0xb4): res.append(base + o)
    return res

def first_matches(img, kind, first):
    step = 2 if kind == 'thumb' else 4
    res = []
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if kind == 'thumb' else capstone.CS_MODE_ARM)
    for base, buf in img.items():
        for o in range(0, len(buf) - 4, step):
            ins = next(md.disasm(buf[o:o + 4], base + o), None)
            if ins and ins.mnemonic == first.split()[0]: res.append(base + o)
    return res

def find(img, kind, ref, length):
    best = (0, None, None)
    rs = ref[:24]
    cands = starts(img, kind) if ref[0].startswith('push') else first_matches(img, kind, ref[0])
    for s in cands:
        base, buf = region(img, s)
        e = min(base + len(buf), s + length + 0x200)
        n = norm(img, kind, s, min(e, s + 24 * (2 if kind == 'thumb' else 4) + 64))[:24]
        r = difflib.SequenceMatcher(None, rs, n, autojunk=False).ratio()
        if r > best[0]: best = (r, s, None)
    if best[1] is None: return None
    # take the same number of bytes as the reference plus slack, then trim to the best alignment
    s = best[1]
    base, buf = region(img, s)
    cand = norm(img, kind, s, min(base + len(buf), s + length + 0x100))
    sm = difflib.SequenceMatcher(None, ref, cand, autojunk=False)
    last = max((b.b + b.size for b in sm.get_matching_blocks() if b.size), default=len(ref))
    return s, cand[:last], best[0]

def main():
    detail = sys.argv[sys.argv.index('--detail') + 1] if '--detail' in sys.argv else None
    rimg = image(REF)
    refs = {k: norm(rimg, kind, a, b) for k, (kind, a, b) in FUNCS.items()}
    table = {}
    for build, cid in sorted(BUILDS.items(), key=lambda x: x[1]):
        img = image(cid)
        row = {}
        for k, (kind, a, b) in FUNCS.items():
            if not any(region(img, x)[0] for x in [0x02000000 if kind == 'thumb' else 0x03000000]) and kind == 'thumb' and cid in ('jimmy', 'noah-proto'):
                pass
            f = find(img, kind, refs[k], b - a)
            if not f: row[k] = None; continue
            s, cand, score = f
            sm = difflib.SequenceMatcher(None, refs[k], cand, autojunk=False)
            ndiff = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal')
            row[k] = dict(addr=hex(s), n=len(cand), ndiff=ndiff, ratio=round(sm.ratio(), 3))
            if detail == k and ndiff:
                print(f'--- {k} {cid} ({build}) @ {hex(s)}')
                for tag, i1, i2, j1, j2 in sm.get_opcodes():
                    if tag != 'equal':
                        print('  ref', refs[k][i1:i2]); print('  got', cand[j1:j2])
        table[build] = dict(cart=cid, funcs=row)
        print(build, cid, ' '.join(f"{k}={v['ndiff'] if v else '-'}" for k, v in row.items()), flush=True)
    json.dump(table, open(os.path.join(HERE, '..', '..', 'tmp', 'ads', 'builddiff.json'), 'w'), indent=1)

if __name__ == '__main__':
    main()
