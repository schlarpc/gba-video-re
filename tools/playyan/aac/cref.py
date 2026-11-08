"""ctypes binding of reference/playyan/aac/libpyaac.so."""
import ctypes as C, os
WORK = os.environ.get('PLAYYAN_WORK', '')  # the local emulator tree (not in this repository)
LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../reference/playyan/aac/libpyaac.so')
EXTRACT = WORK + '/firmware/extract/'
class CAAC:
    def __init__(self, asc, img='mi_fw0'):
        L = self.L = C.CDLL(LIB)
        L.pyaac_open.restype = C.c_void_p; L.pyaac_open.argtypes = [C.c_char_p, C.c_size_t, C.c_int]
        L.pyaac_init.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t]
        L.pyaac_frame.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t, C.c_void_p]
        L.pyaac_rng.argtypes = [C.c_void_p]; L.pyaac_rng.restype = C.c_uint32
        L.pyaac_set_rng.argtypes = [C.c_void_p, C.c_uint32]
        L.pyaac_error.argtypes = [C.c_void_p]
        self.img = open(EXTRACT + img + '.bin', 'rb').read()
        self.d = L.pyaac_open(self.img, len(self.img), 0 if img == 'mi_fw0' else 1)
        L.pyaac_init(self.d, asc, len(asc))
        self.buf = (C.c_int16 * 2048)()
    def frame(self, raw):
        C.memset(self.buf, 0, 4096)   # the oracle's PCM buffer is zeroed before every frame too
        r = self.L.pyaac_frame(self.d, raw, len(raw), self.buf)
        if r == -1: r = ('err', self.L.pyaac_error(self.d))
        return r, list(self.buf)
