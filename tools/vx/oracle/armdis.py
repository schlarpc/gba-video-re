import os,sys,struct,capstone
d=open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'shrek.gba'),'rb').read()
# EWRAM image ROM 0x8a00..0xbcc8 -> 0x02000000 ; IWRAM image ROM 0xbcc8..0x13cc8 -> 0x03000000
def rom(ea):
    if 0x02000000<=ea<0x020032c8: return ea-0x02000000+0x8a00
    if 0x03000000<=ea<0x03008000: return ea-0x03000000+0xbcc8
    if 0x08000000<=ea<0x0c000000: return ea-0x08000000
    raise ValueError(hex(ea))
def u32(ea): return struct.unpack_from('<I',d,rom(ea))[0]
def dis(ea,n,thumb=False):
    md=capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if thumb else capstone.CS_MODE_ARM)
    off=rom(ea); code=d[off:off+n]
    for ins in md.disasm(code,ea):
        extra=''
        if '[pc' in ins.op_str and '#' in ins.op_str:
            try:
                imm=int(ins.op_str.split('#')[-1].rstrip(']'),0)
                pc=(ins.address+(4 if thumb else 8))&~3
                extra=' ; =0x%08x'%u32(pc+imm)
            except Exception: pass
        print('%08x: %-8s %s%s'%(ins.address,ins.mnemonic,ins.op_str,extra))
if __name__=='__main__':
    dis(int(sys.argv[1],16),int(sys.argv[2],16),len(sys.argv)>3)
