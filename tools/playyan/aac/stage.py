"""Compare the spectrum entering the filter bank (0x21bb2 r4) per channel: firmware vs C."""
import numpy as np, sys, ctypes as C, struct
import fwaac, mp4min, cref
tr, fr = mp4min.audio_frames(sys.argv[1]); K = [int(x) for x in sys.argv[2].split(',')]
f = fwaac.FwAAC(asc=tr["dsi"]); c = cref.CAAC(tr["dsi"])
c.L.pyaac_dbg_spec.restype = C.POINTER(C.c_int32); c.L.pyaac_dbg_spec.argtypes = [C.c_void_p, C.c_int]
c.L.pyaac_dbg_ics.argtypes = [C.c_void_p, C.c_int, C.c_int]
FB = 0x21bb2
def fw_frame_specs(raw):
    caps = []
    v = f.v; L = f.L
    v.load(f.fbuf, raw + bytes(16)); v.load(f.pcm, bytes(0x2000)); v.load(f.info, bytes(64))
    L.vc_reset(v.p, 0xcee8)
    for i, a in enumerate((f.dec, f.fbuf, f.pcm, f.info)): v.set_reg(i, a)
    v.set_reg(24, f.gp); v.set_reg(25, 0xF00000); v.set_reg(26, fwaac.RET)
    while True:
        L.vc_set_break(v.p, FB)
        L.vc_run(v.p, 10**9)
        if v.stop() == 'ret': return caps
        if v.pc() == FB:
            r = v.regs
            caps.append((r[1], r[2], r[3], np.frombuffer(bytes(v.mem.mv[r[4]:r[4] + 4096]), '<i4').copy()))
            L.vc_set_break(v.p, 0); L.vc_step(v.p)
for k in range(max(K) + 1):
    caps = fw_frame_specs(fr[k]); c.frame(fr[k])
    if k not in K: continue
    for ch, (seq, sh, shp, sp) in enumerate(caps):
        cs = np.ctypeslib.as_array(c.L.pyaac_dbg_spec(c.d, ch), (1024,)).copy()
        info = [c.L.pyaac_dbg_ics(c.d, ch, w) for w in range(7)]
        d = sp - cs
        nz = np.nonzero(d)[0]
        print('frame', k, 'ch', ch, 'fw seq/shape/prev', seq, sh, shp, 'C ics', info, 'ndiff', len(nz), nz[:10], d[nz[:5]] if len(nz) else '')
