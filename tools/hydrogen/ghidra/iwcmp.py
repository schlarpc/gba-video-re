"""IWRAM codec routines: word-by-word compare against All Grown Up, modulo relocation
(branch offsets, literal-pool pointers into IWRAM/ROM)."""
import sys, struct, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')); sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'perf'))
from carts import CARTS, BUILD, rom_path
from hyperf import AGU_RANGES, build_ranges, iwram_image
ref = open(rom_path('MGUE'), 'rb').read(); ro, rn = iwram_image(ref); refimg = ref[ro:ro + rn]
def eq(a, b):
    if a == b: return True
    if (a >> 25) & 7 == 5 and (b >> 25) & 7 == 5 and a >> 24 == b >> 24: return True      # B/BL, same cond+L
    ptr = lambda w: 0x03000000 <= w < 0x03008000 or 0x08000000 <= w < 0x0a000000
    return ptr(a) and ptr(b)
res = {}
for c in CARTS:
    rom = open(rom_path(c), 'rb').read(); o, n = iwram_image(rom); img = rom[o:o + n]
    ranges, ev, shift = build_ranges(rom)
    out = []
    for (lo, hi, name), (lo2, hi2, _) in zip(AGU_RANGES, ranges):
        if name in ('osd_font', 'veneer', 'memutil'): continue    # memutil: SetTablePool + the debug symbol dump group B removed
        A = refimg[lo - 0x03000000:hi - 0x03000000]; B = img[lo2 - 0x03000000:hi2 - 0x03000000]
        if len(A) != len(B): out.append((name, hex(lo), 'size', len(A), len(B))); continue
        bad = [i * 4 for i in range(len(A) // 4) if not eq(struct.unpack_from('<I', A, i * 4)[0], struct.unpack_from('<I', B, i * 4)[0])]
        if bad: out.append((name, hex(lo), 'words differ at', [hex(lo + x) for x in bad[:4]], len(bad)))
    print(c, BUILD[c], 'shifts', shift, 'codec routines identical' if not out else out)
