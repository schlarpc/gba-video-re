"""Firmware MP3 oracle: the music image's own decoder (py_fw1 0x222f2) run on the C VC01 core.
Usage as a module:  d = FwMp3('py_fw1'); d.load(file_bytes); r, pcm = d.frame(offset)
CLI: python3 fwmp3.py FILE.mp3 OUT.pcm [nframes]   (16-bit stereo LE, 1152 x 2 per frame)
Image addresses are for py_fw1; other images via ADDR."""
import ctypes as C, os, struct, sys
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
HERE = os.path.dirname(os.path.abspath(__file__))
EXTRACT = WORK + '/firmware/extract'
ADDR = {'py_fw1': dict(init=0x2405c, create=0x238a0, malloc=0x21d12, decode=0x222f2),
        'mi_fw1': dict(init=0x2405c - 0x538, create=0x238a0 - 0x540, malloc=0x21d12 - 0x546, decode=0x222f2 - 0x546),
        'fup_sec1': dict(init=0x2405c - 0x5f8, create=0x238a0 - 0x600, malloc=0x21d12 - 0x600, decode=0x222f2 - 0x600)}
RET = 0xFFFFFFF0
STACK = 0x00F00000
MEM = 0x01000000


class FwMp3:
    def __init__(self, img='py_fw1', data=None):
        import build_core
        L = self.L = C.CDLL(build_core.build())
        L.vc_new.restype = C.c_void_p; L.vc_new.argtypes = [C.c_void_p, C.c_uint32]
        L.vc_run.argtypes = [C.c_void_p, C.c_uint64]; L.vc_run.restype = C.c_uint64
        L.vc_regp.argtypes = [C.c_void_p]; L.vc_regp.restype = C.POINTER(C.c_uint32)
        for n in ('vc_set_ret', 'vc_set_break'):
            getattr(L, n).argtypes = [C.c_void_p, C.c_uint32]
        L.vc_stopcode.argtypes = [C.c_void_p]; L.vc_stopcode.restype = C.c_int
        L.vc_fault_msg.argtypes = [C.c_void_p]; L.vc_fault_msg.restype = C.c_char_p
        L.vc_fault_pc.argtypes = [C.c_void_p]; L.vc_fault_pc.restype = C.c_uint32
        L.vc_set_vecmode.argtypes = [C.c_void_p, C.c_int]
        L.vc_icount.argtypes = [C.c_void_p]; L.vc_icount.restype = C.c_uint64
        self.buf = (C.c_uint8 * MEM)()
        self.mv = memoryview(self.buf).cast('B')
        self.c = L.vc_new(C.addressof(self.buf), MEM)
        L.vc_set_vecmode(self.c, 0)
        self.r = L.vc_regp(self.c)
        if data is None:
            data = open(os.path.join(EXTRACT, img + '.bin'), 'rb').read()
        self.mv[0:len(data)] = data
        self.gp = struct.unpack_from('<I', data, 0x202)[0]
        self.A = ADDR[img]
        self.heap = 0x00800000
        L.vc_set_ret(self.c, RET)
        L.vc_brk_set.argtypes = [C.c_void_p, C.c_int]
        L.vc_brk_skip.argtypes = [C.c_int]
        self.hooks = {}
        self.set_hooks({self.A['malloc']: self._malloc_hook})
        self.call(self.A['init'])
        self.dec = self.call(self.A['create'])
        self.FB = self.malloc(1 << 22)
        self.PCM = self.malloc(1152 * 4 * 2)
        self.ST = self.malloc(64)

    def set_hooks(self, h):
        h = dict(h); h[self.A['malloc']] = self._malloc_hook
        self.hooks = h
        a = (C.c_uint32 * len(h))(*h.keys())
        self.L.vc_brk_set(a, len(h))

    def _malloc_hook(self, _):
        self.r[0] = self.malloc(self.r[0]); self.r[31] = self.r[26]

    def rd32(self, a, n=1):
        return list(struct.unpack_from('<%di' % n, self.mv, a))

    def rd16(self, a, n=1):
        return list(struct.unpack_from('<%dh' % n, self.mv, a))

    def malloc(self, n):
        a = self.heap; self.heap += (n + 31) & ~31
        self.mv[a:a + n] = bytes(n)
        return a

    def call(self, fn, *args, maxinsn=2_000_000_000):
        r = self.r
        for k in range(32):
            r[k] = 0
        for k, v in enumerate(args):
            r[k] = v & 0xffffffff
        r[24] = self.gp; r[25] = STACK; r[26] = RET; r[31] = fn
        self.L.vc_brk_skip(0)
        while True:
            self.L.vc_run(self.c, maxinsn)
            self.L.vc_brk_skip(0)
            s = self.L.vc_stopcode(self.c)
            if s == 5:
                return r[0]
            if s == 3 and r[31] in self.hooks:
                pc = r[31]
                self.hooks[pc](self)
                self.L.vc_brk_skip(1 if r[31] == pc else 0)
                continue
            raise RuntimeError('stop %d at %#x: %s' % (s, self.L.vc_fault_pc(self.c), self.L.vc_fault_msg(self.c)))

    def load(self, data):
        self.mv[self.FB:self.FB + len(data)] = data
        self.flen = len(data)

    def frame(self, off):
        self.mv[self.PCM:self.PCM + 4608] = bytes(4608)
        ret = self.call(self.A['decode'], self.dec, self.FB + off, self.PCM, self.ST)
        pcm = struct.unpack_from('<2304h', self.mv, self.PCM)
        st = struct.unpack_from('<4I', self.mv, self.ST)
        return ret, pcm, st


def frames(data):
    p = 0
    if data[:3] == b'ID3':
        p = 10 + ((data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9])
    BR = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    SR = [44100, 48000, 32000]
    out = []
    while p + 4 <= len(data):
        h = int.from_bytes(data[p:p + 4], 'big')
        if (h >> 21) & 0x7ff != 0x7ff or (h >> 19) & 3 != 3 or (h >> 17) & 3 != 1 or (h >> 10) & 3 == 3:
            p += 1; continue
        br = BR[(h >> 12) & 15]; sr = SR[(h >> 10) & 3]; pad = (h >> 9) & 1
        if not br: p += 1; continue
        n = 144000 * br // sr + pad
        out.append((p, n, sr, (h >> 6) & 3)); p += n
    return out


if __name__ == '__main__':
    import time
    data = open(sys.argv[1], 'rb').read()
    fr = frames(data)
    n = int(sys.argv[3]) if len(sys.argv) > 3 else len(fr)
    d = FwMp3(); d.load(data)
    t = time.time(); out = []
    for k in range(min(n, len(fr))):
        ret, pcm, st = d.frame(fr[k][0])
        out.append(struct.pack('<2304h', *pcm))
        if k < 3: print(k, ret, st, pcm[:6])
    open(sys.argv[2], 'wb').write(b''.join(out))
    print('frames', len(out), '%.1fs' % (time.time() - t), 'insn', d.L.vc_icount(d.c))
