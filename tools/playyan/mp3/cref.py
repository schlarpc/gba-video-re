"""ctypes binding of the C reference (reference/playyan/mp3/libpymp3.so)."""
import ctypes as C, os, struct
WORK = os.environ.get('PLAYYAN_WORK', '')  # the local emulator tree (not in this repository)
HERE = os.path.dirname(os.path.abspath(__file__))
SO = os.path.join(HERE, '../../../reference/playyan/mp3/libpymp3.so')
EXTRACT = WORK + '/firmware/extract'


class CMp3:
    def __init__(self, img='py_fw1', strict=False):
        L = self.L = C.CDLL(SO)
        L.pymp3_new.restype = C.c_void_p; L.pymp3_new.argtypes = [C.c_char_p, C.c_size_t, C.c_int]
        L.pymp3_image_kind.argtypes = [C.c_char_p, C.c_size_t]
        L.pymp3_decode_frame.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t, C.c_void_p, C.POINTER(C.c_int)]
        L.pymp3_set_strict.argtypes = [C.c_void_p, C.c_int]
        self.img = open(os.path.join(EXTRACT, img + '.bin'), 'rb').read()
        kind = L.pymp3_image_kind(self.img, len(self.img))
        self.d = L.pymp3_new(self.img, len(self.img), kind)
        assert self.d, 'pymp3_new failed'
        L.pymp3_set_strict(self.d, 1 if strict else 0)
        self.pcm = (C.c_int16 * 2304)()

    def frame(self, data):
        n = C.c_int()
        r = self.L.pymp3_decode_frame(self.d, data, len(data), self.pcm, C.byref(n))
        return r, list(self.pcm), n.value
