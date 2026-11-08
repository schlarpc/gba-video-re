"""ctypes binding for libvc01.so (the C VC01 core, vc01core.c + the peripheral model vc01hw.c) plus a
Python Mem replacement over the same buffer, so the Python reference CPU (vc01.py) and the C core can
share memory.  The library is built on first use into $PLAYYAN_WORK/build (or VC01_LIB names one)."""
import ctypes as C
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ISA = HERE                     # the Python model lives next to the C core
sys.path.insert(0, ISA)
sys.path.append(os.path.join(HERE, '..'))
import workdir  # noqa: E402
LIB = os.environ.get('VC01_LIB') or os.path.join(workdir.BUILD, 'libvc01.so')

M32 = 0xffffffff
RD_T = C.CFUNCTYPE(C.c_uint32, C.c_void_p, C.c_uint32, C.c_int)
WR_T = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_int, C.c_uint32)
IRQ_T = C.CFUNCTYPE(C.c_int, C.c_void_p)


def build():
    src = [os.path.join(HERE, f) for f in ('vc01core.c', 'vc01core.h', 'vc01hw.c')]
    if not os.path.exists(LIB) or any(os.path.getmtime(s) > os.path.getmtime(LIB) for s in src):
        os.makedirs(os.path.dirname(LIB), exist_ok=True)
        subprocess.check_call(['gcc', '-O2', '-g', '-Wall', '-shared', '-fPIC', '-o', LIB, src[0], os.path.join(HERE, 'vc01hw.c')])
    return LIB


def lib():
    L = C.CDLL(build())
    L.vc_new.restype = C.c_void_p
    L.vc_new.argtypes = [C.c_void_p, C.c_uint32]
    for n in ('vc_reset', 'vc_set_ret', 'vc_set_break', 'vc_set_cfg', 'vc_set_flags'):
        getattr(L, n).argtypes = [C.c_void_p, C.c_uint32]
    for n in ('vc_set_vecmode', 'vc_set_wfi', 'vc_set_push_ascending', 'vc_set_align'):
        getattr(L, n).argtypes = [C.c_void_p, C.c_int]
    L.vc_step.argtypes = [C.c_void_p]
    L.vc_run.argtypes = [C.c_void_p, C.c_uint64]
    L.vc_run.restype = C.c_uint64
    L.vc_regp.argtypes = [C.c_void_p]
    L.vc_regp.restype = C.POINTER(C.c_uint32)
    for n, t in (('vc_flags', C.c_uint32), ('vc_icount', C.c_uint64), ('vc_stopcode', C.c_int),
                 ('vc_fault_pc', C.c_uint32), ('vc_fault_msg', C.c_char_p), ('vc_get_wfi', C.c_int),
                 ('vc_unmapped', C.c_uint64), ('vc_unmapped_last', C.c_uint32), ('vc_nirq', C.c_uint64),
                 ('vc_nvf', C.c_int), ('vc_vec_exec', C.c_uint64), ('vc_get_sr', C.c_uint32)):
        getattr(L, n).argtypes = [C.c_void_p]
        getattr(L, n).restype = t
    L.vc_vrf.argtypes = [C.c_void_p]
    L.vc_vrf.restype = C.c_void_p
    L.vc_vflags.argtypes = [C.c_void_p]
    L.vc_vflags.restype = C.c_void_p
    L.vc_get_cbase.argtypes = [C.c_void_p]
    L.vc_get_cbase.restype = C.c_int
    L.vc_set_cbase.argtypes = [C.c_void_p, C.c_int]
    L.vc_vacc.argtypes = [C.c_void_p]
    L.vc_vacc.restype = C.c_void_p
    L.vc_set_sr.argtypes = [C.c_void_p, C.c_uint32]
    L.vc_vf.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_uint32)]
    L.vc_set_hooks.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
    L.vc_poll_irq.argtypes = [C.c_void_p]
    L.vc_take_irq.argtypes = [C.c_void_p, C.c_int]
    L.vc_read.argtypes = [C.c_void_p, C.c_uint32, C.c_int]
    L.vc_read.restype = C.c_uint32
    L.vc_write.argtypes = [C.c_void_p, C.c_uint32, C.c_int, C.c_uint32]
    return L


STOP = {0: 'run', 1: 'halt', 2: 'fault', 3: 'break', 4: 'wfi', 5: 'ret'}


class CMem:
    """Python Mem (vc01.Mem interface) over a flat ctypes buffer shared with the C core."""

    def __init__(self, buf, size):
        self.buf = buf
        self.size = size
        self.mv = memoryview(buf).cast('B')
        self.mmio_read = None
        self.mmio_write = None

    def load(self, base, data):
        self.mv[base:base + len(data)] = bytes(data)

    def r8(self, a):
        return self.mv[a & (self.size - 1)]

    def w8(self, a, v):
        self.mv[a & (self.size - 1)] = v & 0xff

    def read(self, a, n):
        a &= M32
        if self.mmio_read and 0x10000000 <= a < 0x20000000:
            v = self.mmio_read(a, n)
            if v is not None:
                return v
        if a + n <= self.size:
            return int.from_bytes(self.mv[a:a + n], 'little')
        return sum(self.r8(a + i) << (8 * i) for i in range(n))

    def write(self, a, n, v):
        a &= M32
        if self.mmio_write and 0x10000000 <= a < 0x20000000:
            if self.mmio_write(a, n, v):
                return
        if a + n <= self.size:
            self.mv[a:a + n] = (v & ((1 << (8 * n)) - 1)).to_bytes(n, 'little')
        else:
            for i in range(n):
                self.w8(a + i, v >> (8 * i))

    def r16(self, a):
        return self.read(a, 2)


class CVC:
    def __init__(self, size=0x01000000, buf_addr=None):
        self.L = lib()
        self.size = size
        if buf_addr is None:
            self.buf = (C.c_uint8 * size)()
            addr = C.addressof(self.buf)
        else:
            self.buf = (C.c_uint8 * size).from_address(buf_addr)
            addr = buf_addr
        self.p = self.L.vc_new(addr, size)
        self.regs = self.L.vc_regp(self.p)
        self.mem = CMem(self.buf, size)
        self.mem.cv = self

    # registers
    @property
    def r(self):
        return [self.regs[i] for i in range(32)]

    def set_reg(self, i, v):
        self.regs[i] = v & M32

    def pc(self):
        return self.regs[31]

    def flags(self):
        return self.L.vc_flags(self.p)

    def step(self):
        return self.L.vc_step(self.p)

    def run(self, n):
        return self.L.vc_run(self.p, n)

    def stop(self):
        return STOP[self.L.vc_stopcode(self.p)]

    def fault(self):
        return self.L.vc_fault_pc(self.p), self.L.vc_fault_msg(self.p).decode()

    def icount(self):
        return self.L.vc_icount(self.p)

    def vfaults(self):
        out = []
        a = (C.c_uint32 * 9)()
        for i in range(self.L.vc_nvf(self.p)):
            self.L.vc_vf(self.p, i, a)
            out.append(dict(pc=a[0], count=a[1], why=a[2], words=list(a[4:4 + a[3]])))
        return out

    def load(self, base, data):
        self.mem.load(base, data)

    def read(self, a, n):
        return self.mem.read(a, n)

    def write(self, a, n, v):
        self.mem.write(a, n, v)
