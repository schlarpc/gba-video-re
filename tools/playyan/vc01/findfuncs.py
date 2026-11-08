#!/usr/bin/env python3
"""findfuncs.py IMG BASE ADDR: the function containing ADDR (nearest bl target below) and its callers.
IMG is an extracted image name (py_fw1, mi_fwldr, ...) or a file; BASE is its load address (0, or 0xC0000
for the loaders' code from offset 0x200)."""
import struct, sys, bisect, os
def load(fn):
    if not os.path.isfile(fn):
        sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
        import workdir
        fn = workdir.firmware(fn)
    d=open(fn,"rb").read(); hw=lambda a: struct.unpack_from("<H",d,a)[0]
    calls=[]
    for a in range(0x200,len(d)-4,2):
        h=hw(a)
        if h>>7==0x3f:
            disp=(hw(a+2)<<7)|(h&0x7f)
            if disp&0x400000: disp-=0x800000
            t=a+2*disp
            if 0x200<=t<len(d): calls.append((a,t))
    return d,calls
if __name__=="__main__":
    fn=sys.argv[1]; base=int(sys.argv[2],0); x=int(sys.argv[3],0)-base
    d,calls=load(fn); starts=sorted(set(t for a,t in calls))
    i=bisect.bisect_right(starts,x)-1; f=starts[i]
    print("containing function start %#x (next %#x)"%(f+base,starts[i+1]+base if i+1<len(starts) else -1))
    print("callers:",[hex(a+base) for a,t in calls if t==f][:40])
