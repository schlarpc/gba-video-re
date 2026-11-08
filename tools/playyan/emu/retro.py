"""Minimal headless libretro frontend (ctypes) for the mGBA libretro core.

Gives: frame stepping, joypad input, direct pointers into GBA memory
(via RETRO_ENVIRONMENT_SET_MEMORY_MAPS), savestate serialize/unserialize,
and the core's mixed audio output.
"""
import ctypes as C
import os
import subprocess

CORE_DEFAULT = None


def find_core():
    p = os.environ.get("MGBA_LIBRETRO")
    if p and os.path.exists(p):
        return p
    out = subprocess.check_output(
        ["nix", "build", "--no-link", "--print-out-paths", "nixpkgs#libretro.mgba"],
        text=True, stderr=subprocess.DEVNULL).strip().splitlines()[-1]
    return os.path.join(out, "lib/retroarch/cores/mgba_libretro.so")


# joypad ids
JOY = dict(B=0, Y=1, SELECT=2, START=3, UP=4, DOWN=5, LEFT=6, RIGHT=7, A=8, X=9, L=10, R=11)

ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIOB_CB = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
POLL_CB = C.CFUNCTYPE(None)
STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t), ("meta", C.c_char_p)]


class MemDesc(C.Structure):
    _fields_ = [("flags", C.c_uint64), ("ptr", C.c_void_p), ("offset", C.c_size_t),
                ("start", C.c_size_t), ("select", C.c_size_t), ("disconnect", C.c_size_t),
                ("len", C.c_size_t), ("addrspace", C.c_char_p)]


class MemMap(C.Structure):
    _fields_ = [("descriptors", C.POINTER(MemDesc)), ("num", C.c_uint)]


class AVInfo(C.Structure):
    _fields_ = [("base_w", C.c_uint), ("base_h", C.c_uint), ("max_w", C.c_uint), ("max_h", C.c_uint),
                ("aspect", C.c_float), ("fps", C.c_double), ("rate", C.c_double)]


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class Retro:
    def __init__(self, rom_path, core=None, options=None, sysdir=None):
        self.core_path = core or find_core()
        self.lib = C.CDLL(self.core_path)
        self.options = {k.encode(): v.encode() for k, v in (options or {}).items()}
        self._optbufs = {}
        self.sysdir = (sysdir or os.path.join(os.environ.get("PLAYYAN_WORK") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tmp", "playyan"), "sys")).encode()
        os.makedirs(self.sysdir, exist_ok=True)
        self.regions = []  # (start, len, ptr)
        self.buttons = set()
        self.audio = bytearray()
        self.last_video = None
        self.frame_no = 0
        self._cbs = [ENV_CB(self._env), VIDEO_CB(self._video), AUDIO_CB(self._audio1),
                     AUDIOB_CB(self._audiob), POLL_CB(lambda: None), STATE_CB(self._state)]
        L = self.lib
        L.retro_set_environment(self._cbs[0])
        L.retro_init()
        L.retro_set_video_refresh(self._cbs[1])
        L.retro_set_audio_sample(self._cbs[2])
        L.retro_set_audio_sample_batch(self._cbs[3])
        L.retro_set_input_poll(self._cbs[4])
        L.retro_set_input_state(self._cbs[5])
        with open(rom_path, "rb") as f:
            self._rom = f.read()
        self._rombuf = C.create_string_buffer(self._rom, len(self._rom))
        gi = GameInfo(os.path.abspath(rom_path).encode(), C.cast(self._rombuf, C.c_void_p), len(self._rom), None)
        L.retro_load_game.restype = C.c_bool
        if not L.retro_load_game(C.byref(gi)):
            raise RuntimeError("retro_load_game failed")
        av = AVInfo()
        L.retro_get_system_av_info(C.byref(av))
        self.av = av
        L.retro_serialize_size.restype = C.c_size_t
        L.retro_serialize.restype = C.c_bool
        L.retro_unserialize.restype = C.c_bool

    # ---- callbacks
    def _env(self, cmd, data):
        base = cmd & 0xFFFF
        if base == 3:  # GET_CAN_DUPE
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        if base == 10:  # SET_PIXEL_FORMAT
            self.pixfmt = C.cast(data, C.POINTER(C.c_int))[0]
            return True
        if base in (9, 31):  # system / save directory
            C.cast(data, C.POINTER(C.c_char_p))[0] = self.sysdir
            return True
        if base == 15:  # GET_VARIABLE
            v = C.cast(data, C.POINTER(Variable))[0]
            if v.key in self.options:
                buf = self._optbufs.setdefault(v.key, C.create_string_buffer(self.options[v.key]))
                C.cast(data, C.POINTER(Variable))[0].value = C.cast(buf, C.c_char_p)
                return True
            return False
        if base == 17:  # GET_VARIABLE_UPDATE
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if base == 36:  # SET_MEMORY_MAPS
            mm = C.cast(data, C.POINTER(MemMap))[0]
            for i in range(mm.num):
                d = mm.descriptors[i]
                if d.ptr:
                    self.regions.append((d.start, d.len, d.ptr + d.offset))
            return True
        return False

    def _video(self, data, w, h, pitch):
        self.frame_no += 1
        if data:
            self.last_video = (C.string_at(data, h * pitch), w, h, pitch)

    def _audio1(self, l, r):
        self.audio += C.string_at(C.addressof(C.c_int16(l)), 2) + C.string_at(C.addressof(C.c_int16(r)), 2)

    keep_audio = False

    def _audiob(self, data, frames):
        if self.keep_audio:
            self.audio += C.string_at(data, frames * 4)
        return frames

    def _state(self, port, device, index, id_):
        if port == 0 and device == 1:
            if id_ == 256:  # RETRO_DEVICE_ID_JOYPAD_MASK
                m = 0
                for b in self.buttons:
                    m |= 1 << JOY[b]
                return m
            return int(any(JOY[b] == id_ for b in self.buttons))
        return 0

    # ---- API
    def run(self, n=1):
        for _ in range(n):
            self.lib.retro_run()

    def region(self, addr):
        for start, ln, ptr in self.regions:
            if start <= addr < start + ln:
                return start, ln, ptr
        raise KeyError(hex(addr))

    def read(self, addr, n):
        start, ln, ptr = self.region(addr)
        assert addr + n <= start + ln, "read crosses region"
        return C.string_at(ptr + (addr - start), n)

    def write(self, addr, data):
        start, ln, ptr = self.region(addr)
        C.memmove(ptr + (addr - start), data, len(data))

    def u16(self, a):
        return int.from_bytes(self.read(a, 2), "little")

    def u32(self, a):
        return int.from_bytes(self.read(a, 4), "little")

    def save_state(self):
        n = self.lib.retro_serialize_size()
        buf = C.create_string_buffer(n)
        assert self.lib.retro_serialize(buf, n)
        return buf.raw

    def load_state(self, s):
        buf = C.create_string_buffer(s, len(s))
        assert self.lib.retro_unserialize(buf, len(s))
