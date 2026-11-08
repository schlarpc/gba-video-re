#!/usr/bin/env python3
"""VC01 (Alphamosaic VideoCore 1, Play-Yan) disassembler.

  vc01dis2.py IMG                 recursive-descent listing of all discovered code, function headers,
                                  string / gp annotations
  vc01dis2.py IMG START END       linear sweep of [START,END)
IMG is an image name from $PLAYYAN_WORK/firmware/extract (py_fwldr, py_fw0, py_fw1, mi_*, fup_sec0/1) or a path to a raw
binary (then give --base=0x...).  Encodings and evidence: docs/playyan/vc01-isa.md.  Decoder: vc01.py."""
import sys, struct, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vc01 import decode, disasm
from common import load, BASE

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    opts = dict(a[2:].split('=', 1) for a in sys.argv[1:] if a.startswith('--') and '=' in a)
    if not args:
        print(__doc__); return
    n = args[0]
    if os.path.exists(n):
        d = open(n, 'rb').read(); base = int(opts.get('base', '0'), 0)
    else:
        d = load(n); base = BASE.get(n, 0)
    gp = int(opts['gp'], 0) if 'gp' in opts else struct.unpack_from('<I', d, 0x202)[0]
    def cstr(t):
        o = t - base
        if 0 <= o < len(d):
            z = d[o:o + 64].split(b'\0')[0]
            if len(z) >= 3 and all(32 <= c < 127 or c in (9, 10, 13) for c in z):
                return repr(z.decode())
        return ''
    def ann(i):
        a = i.a
        if i.op == 'addi3' and a['rs'] in (24, 31, 32):
            t = {24: gp, 31: i.addr, 32: 0}[a['rs']] + a['imm']
            return ('=%#x ' % (t & 0xffffffff) if a['rs'] == 24 else '') + cstr(t)
        if i.op == 'movi':
            return cstr(a['imm'])
        return ''
    def show(i):
        x = ann(i)
        print('%08x: %-24s %s%s' % (i.addr, ' '.join('%04x' % w for w in i.words), i.text(),
                                    ('   ; ' + x) if x.strip() else ''))
    if len(args) >= 3:
        for i in disasm(d, base, int(args[1], 0), int(args[2], 0)):
            show(i)
        return
    from reach import reach
    seen, funcs, _ = reach(n)
    prev = None
    for a in sorted(seen):
        if a in funcs:
            print('\n; ===== function %#x' % a)
        elif prev is not None and a != prev:
            print('; ...')
        i = seen[a]; show(i); prev = a + i.size

if __name__ == '__main__':
    main()
