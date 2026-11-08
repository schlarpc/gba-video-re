"""ctypes binding for the patched mGBA libretro core (build.sh: $PLAYYAN_WORK/build/mgba-pyan/mgba_libretro.so,
or MGBA_PYAN).

The C side (src/gba/cart/pyan.c in mgba-pyan.patch) models the cartridge bus: register file, VC or DSP
memory, transfer modes, record delivery with the Game Pak IRQ, and the trace. The Python model
(pyan_model.py / nmp_model.py) gets events through one callback and answers by writing VC
memory and posting records.
"""
import ctypes as C
import os
import sys

import numpy as np

from retro import Retro

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import workdir  # noqa: E402

EV = {1: "RESET_ASSERT", 2: "RESET_RELEASE", 3: "DOORBELL", 4: "W32", 5: "ACK", 6: "CTRL",
      7: "DELIVERED", 8: "CLEARED", 9: "IRQ_RETRY", 10: "NMP_CTRL", 11: "NMP_DOORBELL",
      12: "NMP_GPIO", 14: "BOOTREG", 15: "ANOMALY"}
P = dict(READY_POLLS=0, RSTAT_POLLS=1, REC_GAP=2, IRQ_RETRY=3, NMP_ON_ACK=4, TICK_DIV=5,
         TRACE_RAW=6, FIRST_DELAY=7)
STATS = ["irq", "rec", "ack", "clear", "retry", "win_r", "win_w", "fifo_r", "fifo_w",
         "stat_poll", "rstat_poll", "doorbell", "anomaly", "w32"]
CB_T = C.CFUNCTYPE(C.c_int, C.c_int, C.c_uint32, C.c_uint32, C.c_uint32)
CPU_HZ = 16777216
FRAME_CYCLES = 280896


class PYanCore:
    def __init__(self, rom, trace=None, params=None, core=None):
        core = core or workdir.mgba_core()
        # idle-loop removal would skip the cart polling loops; keep everything cycle-driven
        self.retro = Retro(rom, core=core, options={"mgba_idle_optimization": "Don't Remove"})
        L = self.lib = self.retro.lib
        L.retro_pyan_family.restype = C.c_int
        L.retro_pyan_mem.restype = C.c_void_p
        L.retro_pyan_memsize.restype = C.c_uint32
        L.retro_pyan_cycles.restype = C.c_uint64
        L.retro_pyan_post.argtypes = [C.POINTER(C.c_uint32), C.c_uint32]
        L.retro_pyan_post.restype = C.c_int
        L.retro_pyan_queued.restype = C.c_int
        L.retro_pyan_inflight.restype = C.c_int
        L.retro_pyan_note.argtypes = [C.c_char_p]
        L.retro_pyan_trace_open.argtypes = [C.c_char_p]
        L.retro_pyan_get_ptr.restype = C.c_uint32
        L.retro_pyan_get_ctrl.restype = C.c_uint32
        self.family = L.retro_pyan_family()
        if not self.family:
            raise RuntimeError("core did not detect a Play-Yan family cartridge")
        size = L.retro_pyan_memsize()
        raw = (C.c_uint8 * size).from_address(L.retro_pyan_mem())
        self.mem8 = np.ctypeslib.as_array(raw)
        if self.family == 3:
            self.mem16 = self.mem8.view(np.uint16)
        self.params = (C.c_uint32 * 8).in_dll(L, "retro_pyan_param")
        self.stats_raw = (C.c_uint64 * 32).in_dll(L, "retro_pyan_stats")
        for k, v in (params or {}).items():
            self.params[P[k]] = v
        if trace:
            L.retro_pyan_trace_open(trace.encode())
        self.handler = None
        self._cb = CB_T(self._event)
        L.retro_pyan_set_callback(self._cb)

    def _event(self, ev, a, b, c):
        try:
            if self.handler:
                self.handler(ev, a, b, c)
        except Exception as e:  # never let an exception unwind through C
            import traceback
            traceback.print_exc()
            self.note("PYTHON EXCEPTION %r" % (e,))
        return 0

    # ---- helpers
    def note(self, text):
        self.lib.retro_pyan_note(text.encode("utf-8", "replace")[:900])

    def post(self, words, delay=0):
        w = [int(x) & 0xFFFFFFFF for x in words] + [0] * (8 - len(words))
        arr = (C.c_uint32 * 8)(*w)
        if self.lib.retro_pyan_post(arr, int(delay)) < 0:
            raise RuntimeError("record queue full")

    def queued(self):
        return self.lib.retro_pyan_queued()

    def cycles(self):
        return self.lib.retro_pyan_cycles()

    def stats(self):
        return {k: self.stats_raw[i] for i, k in enumerate(STATS)}

    def flush_trace(self):
        self.lib.retro_pyan_trace_flush()

    # VC memory (Play-Yan, bytes)
    def vc_read(self, addr, n):
        return bytes(self.mem8[addr:addr + n])

    def vc_write(self, addr, data):
        self.mem8[addr:addr + len(data)] = np.frombuffer(bytes(data), np.uint8)

    def vc_u32(self, addr):
        return int.from_bytes(self.vc_read(addr, 4), "little")

    # DSP memory (NMP, 16-bit words)
    def dsp_read(self, waddr, n):
        return self.mem16[waddr:waddr + n].copy()

    def dsp_write(self, waddr, words):
        self.mem16[waddr:waddr + len(words)] = np.asarray(words, np.uint16)

    # GBA side
    def run(self, n=1):
        self.retro.run(n)

    def frame(self):
        return self.retro.frame_no

    def screenshot(self, path):
        from PIL import Image
        data, w, h, pitch = self.retro.last_video
        fmt = getattr(self.retro, "pixfmt", 0)
        if fmt == 2:  # RGB565
            a = np.frombuffer(data, np.uint16).reshape(h, pitch // 2)[:, :w]
            r = ((a >> 11) & 0x1F) << 3
            g = ((a >> 5) & 0x3F) << 2
            b = (a & 0x1F) << 3
        elif fmt == 1:  # XRGB8888
            a = np.frombuffer(data, np.uint32).reshape(h, pitch // 4)[:, :w]
            r, g, b = (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF
        else:  # 0RGB1555
            a = np.frombuffer(data, np.uint16).reshape(h, pitch // 2)[:, :w]
            r, g, b = ((a >> 10) & 0x1F) << 3, ((a >> 5) & 0x1F) << 3, (a & 0x1F) << 3
        img = np.stack([r, g, b], -1).astype(np.uint8)
        Image.fromarray(img).save(path)
        return img

    def gba_read(self, addr, n):
        return self.retro.read(addr, n)

    def gba_u32(self, addr):
        return self.retro.u32(addr)
