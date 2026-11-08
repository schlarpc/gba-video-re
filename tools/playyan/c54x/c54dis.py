"""Recursive-descent disassembly of the NMP DSP image, using GNU objdump (tic54x) for the text and
cross-checking every reached instruction's length and mnemonic against our C core's decoder
(c54_insn_len / c54_mnem in libc54.so).

  python3 c54dis.py         -> writes $PLAYYAN_WORK/runs/nmp_code.lst, prints the cross-check summary
  python3 c54dis.py ADDR [N]   -> listing of N instructions from ADDR (23-bit, hex)
"""
import os, re, sys, ctypes, subprocess, array, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fwimg
import nmpm

def load_mem():
    hdr, secs, mem, loaded = fwimg.image()
    return hdr, mem, loaded

def prog_index(a):
    page, off = a >> 16, a & 0xFFFF
    if off < 0x8000:
        return off if off >= 0x80 else (off if off >= 0x60 else None)
    return {1: 0x10000, 2: 0x20000, 3: 0x30000}.get(page, None) + off if page in (1, 2, 3) else None

def objdump_page(mem, page):
    """objdump of program page (overlay low half + page high half) -> {off: (nwords, text)}"""
    words = array.array('H', [0] * 0x10000)
    words[0:0x8000] = array.array('H', mem[0:0x8000])
    base = {0: None, 1: 0x18000, 2: 0x28000, 3: 0x38000}[page]
    if base is not None:
        words[0x8000:0x10000] = array.array('H', mem[base:base + 0x8000])
    path = os.path.join(nmpm.workdir.work('runs'), 'c54dis_page%d.bin' % page)
    words.tofile(open(path, 'wb'))
    out = subprocess.run(['objdump', '-D', '-b', 'binary', '-m', 'tms320c54x', path],
                         capture_output=True, text=True).stdout
    res, cur = {}, None
    for line in out.splitlines():
        m = re.match(r'^\s*([0-9a-f]+):\t([0-9a-f]{4}) \t(.*)$', line)
        if m:
            cur = int(m.group(1), 16)
            res[cur] = [1, m.group(3).strip(), [int(m.group(2), 16)]]
            continue
        m = re.match(r'^\s*([0-9a-f]+):\t([0-9a-f]{4}) \s*$', line)
        if m and cur is not None:
            res[cur][0] += 1
            res[cur][2].append(int(m.group(2), 16))
    os.unlink(path)
    return res

class Dis:
    def __init__(self):
        self.hdr, self.mem, self.loaded = load_mem()
        self.pages = {p: objdump_page(self.mem, p) for p in range(4)}
        self.lib = ctypes.CDLL(nmpm.build()[1])
        self.lib.c54_insn_len.argtypes = [ctypes.c_uint16]
        self.lib.c54_mnem.argtypes = [ctypes.c_uint16, ctypes.c_uint16]
        self.lib.c54_mnem.restype = ctypes.c_char_p

    def word(self, a):
        i = prog_index(a)
        return self.mem[i] if i is not None else 0xFFFF

    def insn(self, a):
        page, off = a >> 16, a & 0xFFFF
        if off < 0x8000: page_tab = self.pages[0]
        else: page_tab = self.pages[page]
        return page_tab.get(off)

    def walk(self, roots):
        seen, work, xrefs = {}, list(roots), collections.defaultdict(set)
        while work:
            a = work.pop()
            while True:
                if a in seen: break
                ins = self.insn(a)
                if ins is None: break
                n, text, ws = ins
                seen[a] = ins
                mn = text.split()[0] if text else '???'
                page = a >> 16
                nxt = (a & ~0xFFFF) | ((a + n) & 0xFFFF)
                tgt = None
                m = re.match(r'(fb|fbd|fcall|fcalld)\s+0x([0-9a-f]+)', text)
                if m:
                    tgt = ((ws[0] & 0x7F) << 16) | ws[1]
                elif mn in ('b', 'bd', 'call', 'calld', 'bc', 'bcd', 'cc', 'ccd', 'banz', 'banzd', 'rptb', 'rptbd'):
                    tgt = (page << 16) | ws[-1]
                if tgt is not None:
                    xrefs[tgt].add(a)
                    if mn not in ('rptb', 'rptbd'): work.append(tgt)
                if mn in ('b', 'fb', 'ret', 'fret', 'frete', 'rete', 'bacc', 'fbacc', 'retf', 'idle?'):
                    break
                if mn in ('bd', 'fbd', 'retd', 'fretd', 'freted', 'reted', 'baccd', 'fbaccd'):
                    # two delay-slot words still execute
                    b = nxt
                    k = 0
                    while k < 2:
                        i2 = self.insn(b)
                        if i2 is None: break
                        seen[b] = i2
                        k += i2[0]
                        b = (b & ~0xFFFF) | ((b + i2[0]) & 0xFFFF)
                    break
                a = nxt
        return seen, xrefs

    def check(self, seen):
        bad = []
        for a, (n, text, ws) in sorted(seen.items()):
            op = ws[0]
            op2 = ws[1] if len(ws) > 1 else 0
            # 0x6F: second opcode word follows the lk word
            if (op >> 8) == 0x6F and len(ws) > 2 and (op & 0x80) and ((op >> 3) & 15) >= 12:
                op2 = ws[2]
            ln = self.lib.c54_insn_len(op)
            mn = self.lib.c54_mnem(op, op2).decode()
            omn = text.split()[0] if text else '???'
            if ln != n or mn != omn:
                bad.append((a, ws, text, ln, mn))
        return bad

def listing(d, a, n):
    """objdump from exactly address a (23-bit), n instructions"""
    words = array.array('H', [d.word((a & ~0xFFFF) | ((a + k) & 0xFFFF)) for k in range(n * 3 + 3)])
    path = os.path.join(nmpm.workdir.work('runs'), 'c54dis_snip.bin')
    words.tofile(open(path, 'wb'))
    out = subprocess.run(['objdump', '-D', '-b', 'binary', '-m', 'tms320c54x', '--adjust-vma=0x%x' % (a & 0xFFFF), path],
                         capture_output=True, text=True).stdout
    os.unlink(path)
    res, cnt = [], 0
    for line in out.splitlines():
        m = re.match(r'^\s*([0-9a-f]+):\t([0-9a-f]{4}) \t(.*)$', line)
        if m:
            cnt += 1
            if cnt > n: break
            res.append('%02x:%s  %s  %s' % (a >> 16, m.group(1).rjust(4, '0'), m.group(2), m.group(3)))
        else:
            m = re.match(r'^\s*([0-9a-f]+):\t([0-9a-f]{4}) \s*$', line)
            if m and res: res[-1] = res[-1].replace('  ', ' %s  ' % m.group(2), 1) if False else res[-1] + '   [+%s]' % m.group(2)
    return res

def roots(d):
    r = [d.hdr['entry']]
    # vector table at 0x80 (IPTR = 1 after the firmware sets PMST = 0x00AC)
    for v in range(0x80, 0x100, 4):
        r.append(v)
    return r

if __name__ == '__main__':
    d = Dis()
    if len(sys.argv) > 1:
        a = int(sys.argv[1], 16); n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
        for line in listing(d, a, n): print(line)
        sys.exit()
    seen, xrefs = d.walk(roots(d))
    with open(os.path.join(nmpm.workdir.work('runs'), 'nmp_code.lst'), 'w') as f:
        prev = None
        for a in sorted(seen):
            n, text, ws = seen[a]
            if prev is not None and a != prev: f.write('\n')
            if a in xrefs: f.write('L_%06x:   ; from %s\n' % (a, ' '.join('%06x' % x for x in sorted(xrefs[a])[:8])))
            f.write('%06x: %-15s %s\n' % (a, ' '.join('%04x' % w for w in ws), text))
            prev = (a & ~0xFFFF) | ((a + n) & 0xFFFF)
    bad = d.check(seen)
    cnt = collections.Counter(t[1].split()[0] for t in seen.values())
    print('reached %d instructions (%d words); decode mismatches vs objdump: %d' %
          (len(seen), sum(t[0] for t in seen.values()), len(bad)))
    for b in bad[:40]:
        print('  %06x %s  objdump=%r  ours=len %d %s' % (b[0], ' '.join('%04x' % w for w in b[1]), b[2], b[3], b[4]))
    print('mnemonics:', ' '.join('%s:%d' % kv for kv in cnt.most_common()))
