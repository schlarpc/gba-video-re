import struct,collections,os,sys
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import workdir
EXT=workdir.FIRMWARE+'/'
BASE={'py_fwldr':0xc0000,'mi_fwldr':0xc0000}
def load(n): return workdir.load_firmware(n)
def L(h):
    b=h>>8
    if b==0x2f: return 6
    if b<0x20 or 0x28<=b<=0x2e: return 4
    return 2
def funcstarts(d):
    hw=lambda a: struct.unpack_from("<H",d,a)[0]
    c=collections.Counter()
    for a in range(0x200,len(d)-4,2):
        h=hw(a)
        if 0x10<=h>>8<=0x1f and h&0x80:
            off=((h>>8&0xf)<<23)|(hw(a+2)<<7)|(h&0x7f)
            if off&(1<<26): off-=1<<27
            t=a+2*off
            if 0x200<=t<len(d): c[t]+=1
    return c
def code_insns(d,maxlen=0x800):
    """linear sweep inside function bodies (f0 prologue starts) until e0xx/4430 followed by next start"""
    hw=lambda a: struct.unpack_from("<H",d,a)[0]
    st=sorted(t for t in funcstarts(d))
    out=[]
    for i in range(len(st)-1):
        s,e=st[i],st[i+1]
        if e-s>maxlen: e=s+maxlen
        a=s
        while a<e and a+6<=len(d):
            h=hw(a); l=L(h); out.append((a,l)); a+=l
    return out
