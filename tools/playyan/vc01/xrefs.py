#!/usr/bin/env python3
"""xrefs.py IMG BASE ADDR... (IMG: image name or file, as findfuncs.py): find lea (gp/pc) and mov-imm32 references to each address (also +-0x40 window
for table interiors), report referencing site and containing function (nearest bl target below)."""
import struct, sys, bisect
sys.path.insert(0, __file__.rsplit("/",1)[0]); from findfuncs import load
fn=sys.argv[1]; base=int(sys.argv[2],0); tg=[int(x,0) for x in sys.argv[3:]]
d,calls=load(fn); starts=sorted(set(t for a,t in calls))
hw=lambda a: struct.unpack_from("<H",d,a)[0]; gp=struct.unpack_from("<I",d,0x202)[0]
refs=[]
for a in range(0x200,len(d)-6,2):
    h=hw(a)
    if h>>8!=0x2f: continue
    if h&0xe0==0x20:
        rb=hw(a+4)>>11; imm=((hw(a+4)&0x7ff)<<16)|hw(a+2)
        if imm&0x4000000: imm-=0x8000000
        b={31:a+base,24:gp}.get(rb)
        if b is not None: refs.append((a,b+imm,"lea"))
    elif h&0xe0==0x60: refs.append((a,hw(a+2)|hw(a+4)<<16,"mov"))
for t in tg:
    hits=[(a,v,k) for a,v,k in refs if t<=v<t+0x40]
    out=[]
    for a,v,k in hits:
        i=bisect.bisect_right(starts,a)-1
        out.append("%s@%#x(fn %#x%s)"%(k,a+base,starts[i]+base if i>=0 else -1,"" if v==t else ",+%#x"%(v-t)))
    print("%#08x: %s"%(t," ".join(out) if out else "-"))
