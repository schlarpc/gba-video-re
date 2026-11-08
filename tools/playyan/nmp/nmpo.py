"""NMP DSP oracle harness: the real C54x firmware in the nmp_lle emulator, driven by a minimal host,
with breakpoint hooks for stage snapshots.

    o = Oracle(mp3_path)     # boots, plays the file (copied onto a private SD image)
    o.run_until(pcs)         # run to one of the program addresses (xpc<<16|pc); returns it
    o.d(addr, n)             # data words; o.d32(addr) = hi:lo pair at addr (even)
"""
import sys, os, ctypes, shutil, hashlib
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
TMP = os.environ.get('TMPDIR', '/tmp')
NMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'c54x')  # the C54x core and board (tools/playyan/c54x)
sys.path.insert(0, NMP)
import host, nmpm
# The shared core (tools/playyan/c54x) now has the STH/ST|| saturation fix (SPRU131G 4.3.3); the local
# patched copy that stood in for it has been removed.

def sx(v, b):
    v &= (1 << b) - 1
    return v - (1 << b) if v >> (b - 1) else v

class Oracle:
    def __init__(self, mp3, name='sample_music.mp3'):
        data = open(mp3, 'rb').read()
        key = hashlib.md5(data).hexdigest()[:12]
        sd = os.path.join(os.path.join(TMP, 'nmp_sd'), key)
        if not os.path.isdir(sd):
            os.makedirs(sd)
            shutil.copy(mp3, os.path.join(sd, name))
        cwd = os.getcwd(); os.chdir(NMP)
        try:
            self.h = h = host.Host(sd=sd)
        finally:
            os.chdir(cwd)
        self.L = h.m.lib; self.cp = h.m.cpu_ptr; self.cpu = h.m.cpu
        self.L.c54_step.argtypes = [ctypes.c_void_p]
        assert h.wait_record({0x8001}, 3000)
        h.command([0x0070, 0]); h.command(h.wstr(0x0050, name))
        assert h.wait_record({0x4050})
        self.name = name

    def pc(self):
        return self.cpu.xpc << 16 | self.cpu.pc

    def _service(self):
        h = self.h
        if self.L.nmp_hint(h.m.n):
            rec = h.rd(0x100, 8)
            self.L.nmp_host_hpic(h.m.n, 0x08)
            if rec[0]: h.records.append((h.t, rec))
            h.wr(0x100, [0])
        h.pcm.extend(h.m.pcm())

    def run_until(self, pcs, max_cycles=10 ** 10):
        pcs = list(pcs)
        for i in range(8): self.L.c54_set_brk(self.cp, i, pcs[i] if i < len(pcs) else 0xFFFFFFFF)
        # step off a breakpoint we are sitting on
        if self.pc() in pcs:
            for i in range(8): self.L.c54_set_brk(self.cp, i, 0xFFFFFFFF)
            self.L.c54_step(self.cp)
            for i in range(8): self.L.c54_set_brk(self.cp, i, pcs[i] if i < len(pcs) else 0xFFFFFFFF)
        done = 0
        while done < max_cycles:
            n = self.L.nmp_run(self.h.m.n, 20000)
            self.h.t += n / self.L.nmp_cpu_hz(self.h.m.n)
            done += n
            self._service()
            if self.pc() in pcs:
                return self.pc()
        return None

    def run(self, secs):
        for i in range(8): self.L.c54_set_brk(self.cp, i, 0xFFFFFFFF)
        t0 = self.h.t
        while self.h.t < t0 + secs:
            n = self.L.nmp_run(self.h.m.n, 20000)
            self.h.t += n / self.L.nmp_cpu_hz(self.h.m.n)
            self._service()

    def d(self, a, n=1):
        return [self.L.c54_dread(self.cp, (a + k) & 0xFFFF) for k in range(n)]

    def d32(self, a, n=1):
        w = self.d(a, 2 * n)
        return [sx(w[2 * k] << 16 | w[2 * k + 1], 32) for k in range(n)]

    def acc(self, i):
        return sx(self.cpu.acc[i], 40)

    def ar(self, i):
        return self.cpu.ar[i]

    def dp(self):
        return (self.cpu.st0 & 0x1FF) << 7

import numpy as _np
def _mem(self):
    """whole data space (64k words) as seen by the DSP with DROM=1 (data 0x8000-0xFFFF = DARAM4-7)"""
    ram = _np.ctypeslib.as_array(self.h.m.ram)
    return _np.concatenate([ram[0:0x8000], ram[0x18000:0x20000]]).copy()
Oracle.mem = _mem

def _regs(self):
    c = self.cpu
    return dict(a=sx(c.acc[0], 40), b=sx(c.acc[1], 40), t=c.t, ar=list(c.ar), sp=c.sp, bk=c.bk,
                st0=c.st0, st1=c.st1, pmst=c.pmst, pc=self.pc())
Oracle.regs = _regs
