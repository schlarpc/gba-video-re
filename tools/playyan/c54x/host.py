"""A Python stand-in for the GBA side of the NMP protocol (protocol.md 11 / nmp.md), driving the real
DSP firmware in the standalone machine.  Used to exercise commands quickly before the mGBA coupling.

  python3 host.py list            boot, list the root folder
  python3 host.py play FILE [sec] boot, ID3 + play FILE, capture headphone PCM and 0x8100 audio
"""
import os, sys, struct, wave, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nmpm

def hpi_fold(a):
    if 0x8000 <= a <= 0xFFFF: return a | 0x10000
    if (a >> 16) in (1, 2, 3, 4) and (a & 0xFFFF) < 0x8000: return a & 0xFFFF
    return a

class Host:
    def __init__(self, sd=None, log=None, loglevel=0):
        sd = sd or os.path.join(nmpm.workdir.WORK, 'sd', 'nmp')
        self.m = nmpm.Machine()
        self.m.sd(sd)
        if log:
            self.m.lib.nmp_set_log(self.m.n, log.encode(), loglevel)
        if os.environ.get('NMPSPEED'):
            self.m.lib.nmp_set_speed.argtypes = [nmpm.ctypes.c_void_p, nmpm.ctypes.c_double]
            self.m.lib.nmp_set_speed(self.m.n, float(os.environ['NMPSPEED']))
        self.m.load_builtin()
        self.cyc = 0
        self.t = 0.0
        self.records = []
        self.pcm = []
        self.gba_audio = []

    def rd(self, a, n=1):
        return [self.m.ram[hpi_fold(a + k)] for k in range(n)]

    def wr(self, a, ws):
        for k, w in enumerate(ws):
            self.m.ram[hpi_fold(a + k)] = w & 0xFFFF

    def step(self, cycles=None, dt=0.001):
        """advance dt seconds of real time (cycles at the DSP's current clock)"""
        hz = self.m.lib.nmp_cpu_hz(self.m.n)
        if cycles is None: cycles = int(dt * hz)
        self.m.run(cycles)
        self.t += cycles / hz
        self.cyc = self.t
        self.pcm.extend(self.m.pcm())
        if self.m.lib.nmp_hint(self.m.n):
            rec = self.rd(0x100, 8)
            self.m.lib.nmp_host_hpic(self.m.n, 0x08)
            if rec[0] != 0:
                self.records.append((self.cyc, rec))
                self.on_record(rec)
            self.wr(0x100, [0])
            return rec
        return None

    def on_record(self, rec):
        rid = rec[0]
        A = rec[2] | rec[3] << 16
        if rid == 0x8100 and A:
            size = min(rec[1], 0x480)
            nw = ((size >> 3) + 3) & ~3
            ch1 = self.rd(A, nw); ch2 = self.rd(A + nw * 2, nw)
            self.gba_audio.append((size, ch1, ch2))

    def wait_record(self, ids, ms=5000):
        for _ in range(ms):
            r = self.step()
            if r and r[0] in ids: return r
        return None

    def command(self, words):
        for _ in range(10000):
            if self.rd(0x110)[0] == 0: break
            self.step(10000)
        self.wr(0x110, words)
        self.m.lib.nmp_host_hpic(self.m.n, 0x04)

    def wstr(self, cid, s):
        w = [cid] + [ord(ch) for ch in s] + [0]
        return w + [0] * (0x106 - len(w))

def fmt(rec):
    return '%04x %04x | A=%08x B=%08x C=%08x' % (rec[0], rec[1], rec[2] | rec[3] << 16, rec[4] | rec[5] << 16, rec[6] | rec[7] << 16)

def wide(ws):
    s = ''
    for w in ws:
        if w == 0: break
        s += chr(w)
    return s

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd')
    ap.add_argument('arg', nargs='?')
    ap.add_argument('secs', nargs='?', type=float, default=5)
    a = ap.parse_args()
    h = Host(log=os.path.join(nmpm.workdir.work('runs'), 'nmp_host.log'), loglevel=int(os.environ.get('NMPLOG', '4')))
    r = h.wait_record({0x8001}, 3000)
    print('boot:', fmt(r) if r else None, 'at %.1f ms' % (h.t * 1000))
    if a.cmd in ('list', 'play'):
        h.command([0x0010, 0])
        while True:
            r = h.wait_record({0x4010, 0x4011})
            if not r: print('no dir record'); break
            if r[1] != 0: print('end', fmt(r)); break
            A = r[2] | r[3] << 16
            ent = h.rd(A, 0x108)
            print('entry', fmt(r), repr(wide(ent)), 'type', ent[0x106], 'extra', ent[0x107])
            h.command([0x0011, 0])
    if a.cmd == 'play':
        name = a.arg or 'sample_music.mp3'
        h.command(h.wstr(0x0040, name))
        r = h.wait_record({0x4040})
        print('id3', fmt(r) if r else None)
        if r and (r[2] | r[3] << 16):
            A = r[2] | r[3] << 16
            blk = h.rd(A, 0x88)
            print('  title', repr(wide(blk[2:0x44])), 'artist', repr(wide(blk[0x44:0x86])), 'len', blk[0x86] | blk[0x87] << 16)
        h.command([0x0070, 1])
        h.command(h.wstr(0x0050, name))
        r = h.wait_record({0x4050})
        print('play', fmt(r) if r else None)
        n0 = len(h.records)
        for _ in range(int(a.secs * 1000)):
            h.step()
        ids = {}
        for c, rec in h.records[n0:]:
            ids[rec[0]] = ids.get(rec[0], 0) + 1
        print('records during play:', {hex(k): v for k, v in ids.items()})
        for c, rec in h.records[n0:n0 + 12]: print('  %.1f ms' % (c * 1000), fmt(rec))
        print('pcm words', len(h.pcm), 'rate', h.m.lib.nmp_pcm_rate(h.m.n), 'gba blocks', len(h.gba_audio))
        with wave.open(os.path.join(nmpm.workdir.work('runs'), 'nmp_host_headphone.wav'), 'wb') as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(int(round(h.m.lib.nmp_pcm_rate(h.m.n))))
            w.writeframes(struct.pack('<%dh' % (len(h.pcm) // 2 * 2), *h.pcm[:len(h.pcm) // 2 * 2]))
    print('records:', len(h.records))
    h.m.lib.nmp_report_stderr(h.m.n)

if __name__ == '__main__':
    main()
