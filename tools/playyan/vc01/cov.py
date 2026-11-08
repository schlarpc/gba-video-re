#!/usr/bin/env python3
"""Coverage over recursively-discovered code (reach.py): bytes whose encoding decodes to a named
instruction with implemented semantics.  Usage: cov.py [img...]"""
import sys,re,collections
import os
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from vc01 import *
from reach import reach
def unknown(i):
    if i.op in ('unk','unk48'): return True
    if i.op in ('alu2','alu3','alui') and re.match(r'(op|iop|x)\d',i.a['alu']): return True
    return False
def key(i):
    if i.op=='unk': return i.a.get('what','?')
    if i.op=='unk48': return '48op%d'%i.a['sub']
    return ('3reg:' if i.op=='alu3' else '')+i.a['alu']
if __name__=='__main__':
    tot=collections.Counter()
    for n in sys.argv[1:] or ['py_fwldr','py_fw0','py_fw1','mi_fwldr','mi_fw0','mi_fw1','fup_sec0','fup_sec1']:
        seen,funcs,stops=reach(n)
        good=sum(i.size for i in seen.values() if not unknown(i)); allb=sum(i.size for i in seen.values())
        for i in seen.values():
            if unknown(i): tot[key(i)]+=1
        print("%-9s %4d funcs  %6d code bytes  %6.2f%% decoded"%(n,len(funcs),allb,100*good/allb))
    print("unknown in reachable code:",tot.most_common(40))
