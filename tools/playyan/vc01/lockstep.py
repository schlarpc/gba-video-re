#!/usr/bin/env python3
"""Validate the C core (libvc01.so) against the Python reference (vc01.py and the vector model
vc01v.py) instruction by instruction.

Every Python CPU gets its memory from a C core (shared buffer).  Before each Python step the C
core receives the Python registers and flags; both execute the same instruction; then all
registers (except sr), flags, and for vector instructions the vector register file, are compared.
Stores are idempotent, so executing each instruction twice on one memory is safe as long as the
MMIO hooks are stateless (they are in the test suites).

  lockstep.py tests        run tests.py with every instruction cross-checked
  lockstep.py g726         run g726_test.py likewise (vector instructions included)
  lockstep.py suite vec/mp3_test.py [args]   a kernel suite script
  lockstep.py boot IMG N   boot IMG (py_fwldr, py_fw0, py_fw1, ...) for N steps, dummy MMIO
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cvc  # noqa: E402

sys.path.insert(0, cvc.ISA)
os.chdir(cvc.ISA)
import vc01  # noqa: E402

STATS = dict(steps=0, vec=0, mism=0, kinds=set())
CONLY = '--conly' in sys.argv
if CONLY:
    sys.argv.remove('--conly')
MAXREPORT = 20


def vstate(cv):
    import ctypes
    P = ctypes.cast(cv.L.vc_vrf(cv.p), ctypes.POINTER(ctypes.c_uint8))
    fl = ctypes.cast(cv.L.vc_vflags(cv.p), ctypes.POINTER(ctypes.c_uint8))
    acc = ctypes.cast(cv.L.vc_vacc(cv.p), ctypes.POINTER(ctypes.c_int16))
    return P, fl, acc


def vsync(cv, pv):
    """copy the Python VPU state (byte VRF, lane flags N/Z/C, accumulators) into the C core, so every
    vector instruction is checked on its own (tests also poke the VRF directly)."""
    import ctypes
    P, fl, acc = vstate(cv)
    ctypes.memmove(P, bytes(b for row in pv.P for b in row), 4096)
    for k in range(16):
        fl[k] = pv.N[k]; fl[16 + k] = pv.Z[k]; fl[32 + k] = pv.C[k]
        acc[k] = ((pv.acc[k] + 0x8000) & 0xffff) - 0x8000
    cv.L.vc_set_cbase(cv.p, getattr(pv, 'cbase', 0))


def install():
    orig_init = vc01.CPU.__init__

    def __init__(self, mem=None):
        orig_init(self, mem)
        if mem is None or not isinstance(mem, cvc.CMem):
            self._cv = cvc.CVC()
            self._cv.L.vc_set_vecmode(self._cv.p, 0)
            self._cv.L.vc_set_push_ascending(self._cv.p, 0)   # vc01.py now stores descending (VC4 order)
            self._cv.L.vc_set_align(self._cv.p, 1)            # vc01.py now aligns word/halfword accesses
            self.m = self._cv.mem
        else:
            self._cv = mem.cv
        self._inlock = False

    vc01.CPU.__init__ = __init__
    wrapped = set()

    def wrap(cls):
        if cls in wrapped:
            return
        wrapped.add(cls)
        orig_step = cls.__dict__['step']

        def step(self):
            if self._inlock or not hasattr(self, '_cv'):
                return orig_step(self)
            cv = self._cv
            pc = self.r[31]
            for k in range(32):
                cv.regs[k] = self.r[k] & 0xffffffff
            f = self.N << 3 | self.Z << 2 | self.C << 1 | self.V
            cv.L.vc_set_flags(cv.p, f)
            if CONLY:
                # run the instruction on the C core only (its own semantics), copy state back
                cv.L.vc_step(cv.p)
                for k in range(32):
                    self.r[k] = cv.regs[k]
                f = cv.flags()
                self.N, self.Z, self.C, self.V = f >> 3 & 1, f >> 2 & 1, f >> 1 & 1, f & 1
                STATS['steps'] += 1
                if cv.L.vc_stopcode(cv.p) not in (0, 4):
                    raise vc01.EmuError('C core stop %s %s' % (cv.stop(), cv.fault()))
                return None
            i0 = self.fetch(pc)
            if i0.op == 'vec':
                if getattr(self, 'vpu', None) is None:
                    self.vpu = vc01.VPU(self)
                vsync(cv, self.vpu)
            self._inlock = True
            try:
                i = self.fetch(pc)
                res = orig_step(self)
            finally:
                self._inlock = False
            cv.L.vc_step(cv.p)
            STATS['steps'] += 1
            STATS['kinds'].add(i.op)
            bad = []
            if cv.L.vc_stopcode(cv.p) not in (0, 4):
                bad.append('C stop %s %s' % (cv.stop(), cv.fault()))
            for k in list(range(30)) + [31]:
                if (self.r[k] & 0xffffffff) != cv.regs[k]:
                    bad.append('r%d py=%#x c=%#x' % (k, self.r[k] & 0xffffffff, cv.regs[k]))
            f2 = self.N << 3 | self.Z << 2 | self.C << 1 | self.V
            if f2 != cv.flags():
                bad.append('flags py=%x c=%x' % (f2, cv.flags()))
            if i.op == 'vec' and getattr(self, 'vpu', None) is not None:
                STATS['vec'] += 1
                cP, cfl, cacc = vstate(cv)
                pv = self.vpu
                for y in range(64):
                    if bytes(pv.P[y]) != bytes(cP[y * 64:(y + 1) * 64]):
                        x = next(x for x in range(64) if pv.P[y][x] != cP[y * 64 + x])
                        bad.append('P[%d][%d] py=%#x c=%#x' % (y, x, pv.P[y][x], cP[y * 64 + x]))
                        break
                if [cfl[k] for k in range(16)] != list(pv.N) or [cfl[16 + k] for k in range(16)] != list(pv.Z) \
                        or [cfl[32 + k] for k in range(16)] != list(pv.C):
                    bad.append('lane flags differ')
                if cv.L.vc_get_cbase(cv.p) != getattr(pv, 'cbase', 0):
                    bad.append('cbase py=%d c=%d' % (getattr(pv, 'cbase', 0), cv.L.vc_get_cbase(cv.p)))
                if [cacc[k] for k in range(16)] != [((a + 0x8000) & 0xffff) - 0x8000 for a in pv.acc]:
                    bad.append('acc differs py=%s c=%s' % (pv.acc, [cacc[k] for k in range(16)]))
            if bad:
                STATS['mism'] += 1
                if STATS['mism'] <= MAXREPORT:
                    print('MISMATCH at %#x %s: %s' % (pc, i.text(), '; '.join(bad[:4])))
                # resync C to Python so later instructions are still checked individually
            return res

        cls.step = step

    vc01.CPU._lock_wrap = staticmethod(wrap)
    orig_new = vc01.CPU.__new__

    def __new__(cls, *a, **k):
        o = orig_new(cls) if orig_new is object.__new__ else orig_new(cls, *a, **k)
        for c in cls.__mro__:
            if 'step' in c.__dict__ and c is not object:
                wrap(c)
        return o

    vc01.CPU.__new__ = __new__


def vm_lib_lib():
    vc01.CPU.__init__.__defaults__  # noqa


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'tests'
    install()
    if what == 'tests':
        sys.argv = ['tests.py'] + sys.argv[2:]
        import tests
        tests.main()
    elif what == 'g726':
        sys.argv = ['g726_test.py'] + sys.argv[2:]
        import runpy
        runpy.run_path(os.path.join(cvc.ISA, 'g726_test.py'), run_name='__main__')
    elif what == 'suite':
        # kernel suite scripts (vec/*_test.py)
        import runpy
        script = os.path.abspath(os.path.join(cvc.ISA, sys.argv[2]))
        sys.path.insert(0, os.path.join(cvc.ISA, 'vec'))
        sys.argv = [script] + sys.argv[3:]
        try:
            runpy.run_path(script, run_name='__main__')
        except SystemExit:
            pass
    elif what == 'boot':
        sys.argv = ['boot.py'] + sys.argv[2:]
        import runpy
        runpy.run_path(os.path.join(cvc.ISA, 'boot.py'), run_name='__main__')
    print('LOCKSTEP: %d instructions cross-checked (%d vector), %d mismatches, %d instruction kinds'
          % (STATS['steps'], STATS['vec'], STATS['mism'], len(STATS['kinds'])))
    print('kinds:', ' '.join(sorted(STATS['kinds'])))


if __name__ == '__main__':
    main()
