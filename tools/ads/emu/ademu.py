"""ADS emulator driver: mGBA libretro core (patched with synchronous PC callbacks) via retro.py."""
import os, sys, ctypes as C, hashlib, struct
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retro

CORE = os.environ.get("ADS_CORE") or os.path.join(HERE, '..', '..', '..', 'tmp', 'mgba', 'ads-hooks', 'mgba_libretro.so')  # tools/ads/emu/mgba-hooks.patch
CB_T = C.CFUNCTYPE(None, C.c_int, C.POINTER(C.c_int32))

class Emu:
    def __init__(self, rom_path, core=CORE):
        self.r = retro.Retro(rom_path, core=core, sysdir=os.path.join(HERE, "sys"),
                             options={"mgba_frameskip": "fixed_interval", "mgba_frameskip_interval": "10"})
        L = self.r.lib
        self.L = L
        self._bpaddr = (C.c_uint32 * 32).in_dll(L, "retro_bp_addr")
        self._bpn = C.c_int.in_dll(L, "retro_bp_n")
        self._mask = C.c_uint32.in_dll(L, "retro_bp_cbmask")
        self._cbptr = C.c_void_p.in_dll(L, "retro_bp_cb")
        self.handlers = []
        self._bpn.value = 0                                  # the hook table is global to the core
        self._cb = CB_T(self._dispatch)
        self._cbptr.value = C.cast(self._cb, C.c_void_p).value
        C.c_int.in_dll(L, "retro_prof_on").value = 0      # the PC histogram is only needed for profiling
        self.mem = {}
        for start, ln, ptr in self.r.regions:
            self.mem[start] = (ln, ptr)

    def _dispatch(self, i, gprs):
        self.handlers[i](gprs)

    def set_hooks(self, hooks):
        """hooks: list of (pc, fn(gprs)). gprs is a pointer to the live register file (writable)."""
        self.handlers = [h for _, h in hooks]
        for i, (a, _) in enumerate(hooks):
            self._bpaddr[i] = a
        self._mask.value = (1 << len(hooks)) - 1
        self._bpn.value = len(hooks)

    def clear_hooks(self):
        self._bpn.value = 0; self.handlers = []

    # memory
    def read(self, a, n): return self.r.read(a, n)
    def write(self, a, d): return self.r.write(a, d)
    def u32(self, a): return self.r.u32(a)
    def u16(self, a): return self.r.u16(a)
    def u8(self, a): return self.r.read(a, 1)[0]
    def run(self, n=1): self.r.run(n)
    def press(self, btn, hold=6, after=2):
        self.r.buttons = set(btn.split("+")); self.r.run(hold); self.r.buttons = set(); self.r.run(after)
    def save(self): return self.r.save_state()
    def load(self, s): self.r.load_state(s)
    def shot(self, path):
        from util import video_to_img
        video_to_img(self.r.last_video).save(path)
    def vram_fb(self): return self.r.read(0x06000000, 240 * 160 * 2)
