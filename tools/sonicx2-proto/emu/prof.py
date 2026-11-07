import sys, os, pickle
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np, ctypes as C
from nav import N
PROF={'iwram':(0x2000,0x03000000,4),'ewram':(0x10000,0x02000000,4),'rom':(0x200000,0x08000000,16),'bios':(0x1000,0,4),'vram':(0x6000,0x06000000,4)}
def arr(r,nm,n): return np.ctypeslib.as_array((C.c_uint64*n).in_dll(r.lib,'retro_prof_'+nm))
def reset(r):
    for nm,(n,_,_) in PROF.items(): arr(r,nm,n)[:]=0
    for nm in ('thumb','arm','other'): arr(r,nm,16)[:]=0
def get(r):
    d={nm:arr(r,nm,n).copy() for nm,(n,_,_) in PROF.items()}
    for nm in ('thumb','arm','other'): d[nm]=arr(r,nm,16).copy()
    return d
if __name__=='__main__':
    n=N(sys.argv[1], sys.argv[2]); reset(n.r); n.r.run(int(sys.argv[3])); p=get(n.r)
    pickle.dump(p,open(f'{sys.argv[1]}/prof.pkl','wb'))
    tot=sum(p[k].sum() for k in PROF)
    print('total',tot, {k:round(p[k].sum()/tot,3) for k in PROF})
    for nm,(cnt,base,g) in PROF.items():
        a=p[nm]; idx=np.argsort(a)[::-1][:25]
        print(nm, [(hex(base+i*g), round(a[i]/tot,4)) for i in idx if a[i]>0])
