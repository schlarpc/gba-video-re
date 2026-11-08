#!/usr/bin/env python3
"""Recursive-descent code discovery for VC01 images (seeds: entry 0x200, vector table, bl targets
found in reachable code, pc-relative/imm32 references to addresses that start with a push).
reach(img) -> (insns dict addr->Insn, funcs set, stops Counter of unknown encodings that ended a path)"""
import sys,struct,collections
import os
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from vc01 import *
from common import load,BASE

def image(n):
    d=load(n); base=BASE.get(n,0)
    def rd(a):
        if base and a<0x200: o=a
        else: o=a-base
        return struct.unpack_from('<H',d,o)[0] if 0<=o<len(d)-1 else 0
    lo=base+0x200 if base else 0; hi=base+len(d)
    return d,base,rd,lo,hi

def switch_count(ins_list):
    # look back for 'cmp rX,#N ; bhi' or 'addcmpbhi rX,#0,#N' -> N+1 entries
    for i in reversed(ins_list[-4:]):
        if i.op=='alui' and i.a['alu']=='cmp': return i.a['imm']+1
        if i.op=='addcmpb' and i.a['rb'][0]=='i' and i.a['cc'] in (8,3): return i.a['rb'][1]+1
    return None

PUSHOK={0xef}|set(range(0xa0,0xaa))|set(range(0x20,0x2a))
def reach(n, extra=()):
    d,base,rd,lo,hi=image(n)
    inr=lambda a: lo<=a<hi and not a&1
    seeds=[0x200 if not base else base+0x200]
    if base:   # loader vector table at 0
        seeds=[base+0x200]
    for k in range(0,0x100,4):
        v=struct.unpack_from('<I',d,k)[0]
        if inr(v): seeds.append(v)
    seeds+=list(extra)
    # function-pointer heuristic: aligned 32-bit words / bl targets that land on a push
    for k in range(0,len(d)-3,2):
        v=struct.unpack_from('<I',d,k)[0]
        if inr(v) and rd(v)>>8==0xf0 and (rd(v)&0xff) in PUSHOK: seeds.append(v)
    from common import funcstarts
    for t in funcstarts(d):
        if rd(t+base)>>8==0xf0 and (rd(t+base)&0xff) in PUSHOK: seeds.append(t+base)
    funcs=set(seeds); seen={}; stops=collections.Counter(); work=list(seeds)
    def ispush(a): return inr(a) and rd(a)>>8==0xf0
    while work:
        a=work.pop()
        path=[]
        while inr(a) and a not in seen:
            i=decode(rd,a); seen[a]=i; path.append(i)
            op=i.op
            if op in ('unk','unk48') or (op in('alu2','alu3','alui') and i.a['alu'][:2] in ('op','io','x4','x5')):
                stops[i.text()[:14] if op!='unk' else i.a.get('what','?')]+=1
                if op in ('unk','unk48'): break
            nxt=a+i.size
            if op=='b':
                if inr(i.a['target']): work.append(i.a['target'])
                if i.a['cc']==14: break
            elif op=='addcmpb':
                if inr(i.a['target']): work.append(i.a['target'])
            elif op=='bl':
                t=i.a['target']
                if inr(t) and t not in funcs: funcs.add(t); work.append(t)
            elif op in ('rts','rti','br') or (op=='pop' and i.a['lrpc']):
                break
            elif op=='switch':
                cnt=switch_count(path) or 0
                w=i.a['w']
                for k in range(cnt):
                    o=rd(nxt+w*k) if w==2 else (rd((nxt+k)&~1)>>(8*((nxt+k)&1)))&0xff
                    t=nxt+2*sx(o,8*w)
                    if inr(t): work.append(t)
                break
            elif op=='addi3' and i.a['rs']==31:
                t=a+i.a['imm']
                if ispush(t) and t not in funcs: funcs.add(t); work.append(t)
            elif op=='movi':
                t=i.a['imm']
                if ispush(t) and t not in funcs: funcs.add(t); work.append(t)
            a=nxt
    return seen,funcs,stops

if __name__=='__main__':
    for n in sys.argv[1:] or ['py_fwldr','py_fw0','py_fw1']:
        seen,funcs,stops=reach(n)
        nb=sum(i.size for i in seen.values())
        print(n,'reachable insns',len(seen),'bytes',nb,'funcs',len(funcs),'stops',stops.most_common(12))
