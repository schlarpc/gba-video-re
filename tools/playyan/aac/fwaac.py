import numpy  # before cvc puts isa/ (dis.py) on sys.path
import os
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
"""Firmware AAC oracle: the Micro / fup video image's own AAC decoder run in the C VC01 core
(tools/playyan/vc01, libvc01.so).  Same call sequence as the firmware's codec wrapper
(0x18a42 create -> 0xd412 init(dec, workbuf), 0xd4bc(dec, 5); 0x18966 -> 0xd4d0(dec, asc);
0x1888a -> 0xcee8(dec, frame, pcm_out, info)); malloc/free stubbed."""
import os, sys, struct
LLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'vc01')  # the C VC01 core (tools/playyan/vc01)
sys.path.insert(0, LLE)
import cvc
# the shared core, fixed 2026-11-08: 48-bit op4-7 '...11' = ldsb, vector memory REP code 7 = 64 rows

EXTRACT = WORK + '/firmware/extract/'
RET = 0x00FFFFF0
# per-image entry points (mi_fw0; fup_sec0 filled in when located)
ENTRY = {
    'mi_fw0': dict(init=0xd412, cfg=0xd4bc, asc=0xd4d0, frame=0xcee8, malloc=0x230d8, fb=0x21bb2),
    'fup_sec0': dict(init=0xd412, cfg=0xd4bc, asc=0xd4d0, frame=0xcee8, malloc=0x23512, fb=0x21f46),
}


class FwAAC:
    def __init__(self, img='mi_fw0', asc=b'\x12\x10'):
        self.E = ENTRY[img]
        self.v = cvc.CVC(0x01000000)
        self.L = self.v.L
        data = open(EXTRACT + img + '.bin', 'rb').read()
        self.v.load(0, data)
        self.gp = struct.unpack_from('<I', data, 0x202)[0]
        self.heap = 0x00A00000
        self.L.vc_set_ret(self.v.p, RET)
        self.L.vc_set_vecmode(self.v.p, 0)
        self.dec = self.malloc(0x4164)
        self.work = self.malloc(0x10000)
        self.call(self.E['init'], self.dec, self.work)
        self.call(self.E['cfg'], self.dec, 5)
        self.ascp = self.malloc(64)
        self.v.load(self.ascp, asc)
        self.init_ret = self.call(self.E['asc'], self.dec, self.ascp)
        self.fbuf = self.malloc(0x4000)   # frame bytes, zero-filled after the frame
        self.pcm = self.malloc(0x2000)
        self.info = self.malloc(64)

    def malloc(self, n):
        a = self.heap
        self.heap += (n + 63) & ~63
        self.v.load(a, bytes(n))
        return a

    def call(self, fn, *args, limit=2_000_000_000):
        v = self.v; L = self.L
        L.vc_reset(v.p, fn)
        for i, a in enumerate(args):
            v.set_reg(i, a)
        v.set_reg(24, self.gp); v.set_reg(25, 0x00F00000); v.set_reg(26, RET)
        n = 0
        while True:
            L.vc_set_break(v.p, self.E['malloc'])
            n += L.vc_run(v.p, limit - n)
            st = v.stop()
            if st == 'ret':
                return v.regs[0]
            if st == 'break':
                pc = v.pc()
                if pc == self.E['malloc']:
                    v.set_reg(0, self.malloc(v.regs[0])); v.set_reg(31, v.regs[26]); continue
            raise RuntimeError('stop %s at %#x %s' % (st, v.pc(), v.fault() if st == 'fault' else ''))

    def frame(self, raw):
        self.v.load(self.fbuf, raw + bytes(0x4000 - len(raw)))
        self.v.load(self.pcm, bytes(0x2000))
        self.v.load(self.info, bytes(64))
        r = self.call(self.E['frame'], self.dec, self.fbuf, self.pcm, self.info)
        info = struct.unpack('<4I', bytes(self.v.mem.mv[self.info:self.info + 16]))
        pcm = struct.unpack('<2048h', bytes(self.v.mem.mv[self.pcm:self.pcm + 4096]))
        return r, info, pcm


if __name__ == '__main__':
    import mp4min, time
    tr, fr = mp4min.audio_frames(sys.argv[1])
    d = FwAAC(asc=tr['dsi'])
    print('init ->', hex(d.init_ret))
    t = time.time()
    for k in range(int(sys.argv[2]) if len(sys.argv) > 2 else 3):
        r, info, pcm = d.frame(fr[k])
        print(k, hex(r), info, pcm[:8], 'icount', d.L.vc_icount(d.v.p), '%.2fs' % (time.time() - t))
