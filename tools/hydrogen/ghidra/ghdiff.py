import re, sys, collections, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from carts import CARTS, BUILD
def load(name):
    src = open(f'gh/{name}.c').read()
    rom = open(f'gh/{name}.rom.bin', 'rb').read(); iw = open(f'gh/{name}.iwram.bin', 'rb').read()
    funcs = {}
    for part in re.split(r'^// ==== ', src, flags=re.M)[1:]:
        hdr, body = part.split('\n', 1)
        addr = int(hdr.split(' @ ')[1], 16)
        names = set()
        for m in re.finditer(r's_[A-Za-z0-9_]+_(0[83][0-9a-f]{6})', body):
            a = int(m.group(1), 16)
            b = rom[a - 0x08000000:a - 0x08000000 + 100] if a >= 0x08000000 else iw[a - 0x03000000:a - 0x03000000 + 100]
            s = b.split(b'\0')[0].decode('latin1')
            if '::' in s or s.endswith(')'): names.add(s)
        norm = re.sub(r'\b(FUN|DAT|LAB|PTR_DAT|PTR_LAB|switchD|caseD|s_[A-Za-z0-9_]+|thunk_FUN|code_r0x|joined_r0x|PTR_s_[A-Za-z0-9_]+)_?[0-9a-f]{6,8}\b', r'\1', body)
        norm = re.sub(r'0x[0-9a-f]{7,8}', 'ADDR', norm)
        norm = re.sub(r'(FUN\((?:DAT|ADDR|DAT \+ -?0x[0-9a-f]+|DAT \+ [0-9]+)\s*,\s*)(0x[0-9a-f]+|[0-9]+)', r'\1LINE', norm)
        norm = re.sub(r'(\w+Var|uStack|local|auStack|iStack|puStack|pbStack|in_r|unaff_r|extraout_r)\w*', 'V', norm)
        funcs[addr] = (sorted(names)[0] if names else None, norm)
    return funcs
ref = load('agu')
refnorm = collections.defaultdict(list)
for a, (n, b) in ref.items(): refnorm[b].append(a)
refname = {n: (a, b) for a, (n, b) in ref.items() if n}
out = {}
for c in CARTS:
    if c == 'MGUE': continue
    f = load(c)
    same = diff = new = 0; diffs = []
    for a, (n, b) in f.items():
        if b in refnorm: same += 1
        elif n and n in refname: diff += 1; diffs.append((hex(a), n))
        else: new += 1
    out[c] = dict(build=BUILD[c], funcs=len(f), identical=same, changed_named=diff, unmatched=new, changed=diffs)
    print(c, BUILD[c], len(f), 'identical', same, 'changed(named)', diff, 'unmatched', new)
    for d in diffs: print('    ', d)
json.dump(out, open('ghdiff.json', 'w'), indent=1)
