"""ctypes binding of mp3k.c: the C reference's MP3 kernels (reference/playyan/mp3) as bit-exact models
of the music firmware's IMDCT (0xa96c, 0x12446), matrixing (0x49c) and synthesis window (0x316b4).
The library is built on first use into $PLAYYAN_WORK/build/libmp3k.so; constants come from the image."""
import ctypes as C
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(HERE, '..', '..'))
import workdir  # noqa: E402

REF = os.path.join(workdir.REPO, 'reference', 'playyan', 'mp3')
LIB = os.path.join(workdir.BUILD, 'libmp3k.so')
I32 = C.c_int32


def build():
    srcs = [os.path.join(HERE, 'mp3k.c')] + [os.path.join(REF, f) for f in ('pymp3.c', 'kernels.h', 'pymp3.h', 'md5.h')]
    if not os.path.exists(LIB) or any(os.path.getmtime(s) > os.path.getmtime(LIB) for s in srcs):
        os.makedirs(workdir.BUILD, exist_ok=True)
        subprocess.check_call(['gcc', '-O2', '-std=c99', '-fPIC', '-shared', '-I', REF, '-o', LIB, srcs[0]])
    return LIB


class MP3K:
    def __init__(self, img='py_fw1'):
        L = self.L = C.CDLL(build())
        L.mp3k_new.restype = C.c_void_p
        L.mp3k_new.argtypes = [C.c_char_p, C.c_size_t]
        L.mp3k_free.argtypes = [C.c_void_p]
        L.mp3k_imdct.argtypes = [C.c_void_p, C.c_int] + [C.POINTER(I32)] * 4
        L.mp3k_dct32.argtypes = [C.c_void_p, C.POINTER(I32), C.POINTER(I32)]
        L.mp3k_synth_slot.argtypes = [C.c_void_p, C.c_int, C.POINTER(I32), C.POINTER(C.c_int16)]
        self.img = workdir.load_firmware(img)
        self.d = L.mp3k_new(self.img, len(self.img))
        assert self.d, 'not a music image'

    def __del__(self):
        if getattr(self, 'd', None):
            self.L.mp3k_free(self.d)

    def imdct(self, bt, X, ov):
        """-> (out[18], new overlap[18])"""
        o, v = (I32 * 18)(), (I32 * 18)()
        self.L.mp3k_imdct(self.d, bt, (I32 * 18)(*X), (I32 * 18)(*ov), o, v)
        return list(o), list(v)

    def dct32(self, x):
        row = (I32 * 34)()
        self.L.mp3k_dct32(self.d, (I32 * 32)(*x), row)
        return list(row)

    def synth_slot(self, row, ch=0):
        """next slot of channel ch's synthesis (state carries over between calls) -> 32 samples"""
        out = (C.c_int16 * 64)()
        self.L.mp3k_synth_slot(self.d, ch, (I32 * 34)(*row), out)
        return list(out)[0::2]
