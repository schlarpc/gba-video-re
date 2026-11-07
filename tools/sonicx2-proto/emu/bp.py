import ctypes as C, numpy as np
def bp_set(r, addrs):
    L=r.lib
    arr=(C.c_uint32*8).in_dll(L,'retro_bp_addr')
    for i,a in enumerate(addrs): arr[i]=a
    C.c_int.in_dll(L,'retro_bp_n').value=len(addrs)
    C.c_uint32.in_dll(L,'retro_bp_logn').value=0
def bp_log(r, clear=True):
    L=r.lib
    n=C.c_uint32.in_dll(L,'retro_bp_logn').value
    a=np.ctypeslib.as_array((C.c_uint32*(4096*18)).in_dll(L,'retro_bp_log')).reshape(4096,18)[:n].copy()
    if clear: C.c_uint32.in_dll(L,'retro_bp_logn').value=0
    return [[int(x) for x in row] for row in a]  # id, r0..r15, cpsr
