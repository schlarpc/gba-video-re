"""speaker path capture: at 0:30CA dump its 0x80-word input buffer; at 2:81B0/2:82BE the synthesis output;
on 0x8100 the GBA blocks.  python3 spk_cap2.py FILE.mp3 SECS OUT.pkl"""
import sys, os, shutil, hashlib, ctypes, pickle, numpy as np
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
NMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'c54x')  # the C54x core and board (tools/playyan/c54x)
sys.path.insert(0, NMP)
import host
def capture(path, secs, name='a.mp3'):
    data = open(path, 'rb').read()
    sd = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sd', 'spk_' + hashlib.md5(data).hexdigest()[:12])
    if not os.path.isdir(sd): os.makedirs(sd); shutil.copy(path, os.path.join(sd, name))
    cwd = os.getcwd(); os.chdir(NMP)
    try: h = host.Host(sd=sd)
    finally: os.chdir(cwd)
    h.m.lib.nmp_set_pins(h.m.n, 1, 0)
    assert h.wait_record({0x8001}, 3000)
    h.command([0x0030, 1]); h.command([0x0070, 1]); h.command(h.wstr(0x0050, name)); assert h.wait_record({0x4050})
    L = h.m.lib; cp = h.m.cpu_ptr; cpu = h.m.cpu
    L.c54_set_brk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]; L.c54_step.argtypes = [ctypes.c_void_p]
    B = [0x281b0, 0x282be, 0x030ca]
    def brk(on):
        for i, b in enumerate(B): L.c54_set_brk(cp, i, b if on else 0xFFFFFFFF)
    rd = lambda a, n: [h.m.ram[(a + k) if a + k < 0x8000 else 0x10000 | (a + k)] for k in range(n)]
    syn = []; spk_in = []; blocks = []; pend = None; t0 = h.t
    brk(True)
    while h.t < t0 + secs:
        n = L.nmp_run(h.m.n, 20000); h.t += n / L.nmp_cpu_hz(h.m.n)
        pc = cpu.xpc << 16 | cpu.pc
        if pc == 0x281b0: pend = cpu.acc[0] & 0xFFFF
        elif pc == 0x282be and pend is not None: syn.append(rd(pend, 576)); pend = None
        elif pc == 0x030ca:
            a = cpu.acc[0] & 0xFFFF; cnt = h.rd(cpu.sp + 2, 1)[0]
            spk_in.append((a, cnt, rd(a, cnt)))
        if pc in B: brk(False); L.c54_step(cp); brk(True)
        if L.nmp_hint(h.m.n):
            rec = h.rd(0x100, 8); L.nmp_host_hpic(h.m.n, 0x08)
            if rec[0] == 0x8100 and (rec[2] | rec[3] << 16):
                A = rec[2] | rec[3] << 16; size = min(rec[1], 0x480); nw = ((size >> 3) + 3) & ~3
                blocks.append((size, h.rd(A, 2 * nw), h.rd(A + 2 * nw, 2 * nw)))
            h.wr(0x100, [0])
    return dict(syn=np.array(syn, dtype=np.uint16).view(np.int16), spk_in=spk_in, blocks=blocks)
if __name__ == '__main__':
    r = capture(sys.argv[1], float(sys.argv[2])); pickle.dump(r, open(sys.argv[3], 'wb'))
    print('syn', len(r['syn']), 'spk_in calls', len(r['spk_in']), 'blocks', len(r['blocks']))
