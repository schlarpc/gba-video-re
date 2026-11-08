"""Standalone NMP DSP machine (no GBA): the boot table is put into DSP RAM exactly as the GBA's HPI
upload does (fwimg.image), the entry point is written to 007Eh/007Fh, and the DSP runs.

  python3 nmpm.py boot [--cycles N] [--sd DIR|IMG] [--log FILE] [--trace LO:HI]

The C54x core (c54x.c) and the board model (nmphw.c) are built on first use into $PLAYYAN_WORK/build
(libnmp.so; libc54.so = the core alone, for c54test.py and c54dis.py).  SD images go to
$PLAYYAN_WORK/sd, logs to $PLAYYAN_WORK/runs.
"""
import os, sys, ctypes, argparse, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, '..'))
import workdir  # noqa: E402
import fwimg  # noqa: E402

LIB = os.path.join(workdir.BUILD, 'libnmp.so')
LIBC54 = os.path.join(workdir.BUILD, 'libc54.so')

SDDIR = os.path.join(workdir.WORK, 'sd')

def make_sd(src, size_mb=64, part_lba=63):
    """SD card image from a host folder: MBR with one FAT16 partition (type 06) at part_lba,
    filesystem by mkfs.fat, files by mtools.  Rebuilt each time, named after the folder, in $PLAYYAN_WORK/sd."""
    if src.endswith('.img') and os.path.isfile(src):
        return src
    os.makedirs(SDDIR, exist_ok=True)
    img = os.path.join(SDDIR, 'nmp_' + os.path.basename(os.path.normpath(src)) + '.img')
    total = size_mb * 2048
    with open(img, 'wb') as f:
        f.truncate(total * 512)
    nsec = total - part_lba
    subprocess.check_call(['mkfs.fat', '-F', '16', '-n', 'NMP', '--offset', str(part_lba), img, str(nsec // 2)],
                          stdout=subprocess.DEVNULL)
    mbr = bytearray(512)
    e = bytearray(16)
    e[0] = 0x00; e[1:4] = b'\x01\x01\x00'; e[4] = 0x06; e[5:8] = b'\xfe\xff\xff'
    e[8:12] = part_lba.to_bytes(4, 'little'); e[12:16] = nsec.to_bytes(4, 'little')
    mbr[446:462] = e
    mbr[510] = 0x55; mbr[511] = 0xAA
    with open(img, 'r+b') as f:
        f.write(mbr)
    ents = [os.path.join(src, e) for e in sorted(os.listdir(src))]
    if ents:
        subprocess.check_call(['mcopy', '-s', '-Q', '-i', '%s@@%d' % (img, part_lba * 512)] + ents + ['::/'],
                              env=dict(os.environ, MTOOLS_NO_VFAT='0'))
    return img

def build():
    """(libnmp.so, libc54.so), rebuilt when a source is newer"""
    srcs = [os.path.join(HERE, f) for f in ('c54x.c', 'c54x.h', 'nmphw.c')]
    for lib, cs in ((LIB, ['c54x.c', 'nmphw.c']), (LIBC54, ['c54x.c'])):
        if not os.path.exists(lib) or any(os.path.getmtime(s) > os.path.getmtime(lib) for s in srcs):
            os.makedirs(workdir.BUILD, exist_ok=True)
            subprocess.check_call(['gcc', '-O2', '-g', '-Wall', '-fPIC', '-shared', '-o', lib] + [os.path.join(HERE, c) for c in cs])
    return LIB, LIBC54

class C54(ctypes.Structure):
    pass

def cpu_fields():
    # mirror of struct C54 (c54x.h) up to the fields we read from Python
    return [('acc', ctypes.c_uint64 * 2), ('t', ctypes.c_uint16), ('trn', ctypes.c_uint16),
            ('ar', ctypes.c_uint16 * 8), ('sp', ctypes.c_uint16), ('bk', ctypes.c_uint16),
            ('brc', ctypes.c_uint16), ('rsa', ctypes.c_uint16), ('rea', ctypes.c_uint16),
            ('st0', ctypes.c_uint16), ('st1', ctypes.c_uint16), ('pmst', ctypes.c_uint16),
            ('imr', ctypes.c_uint16), ('ifr', ctypes.c_uint16), ('xpc', ctypes.c_uint16), ('pc', ctypes.c_uint16)]
C54._fields_ = cpu_fields()

class Machine:
    def __init__(self):
        build()
        self.lib = L = ctypes.CDLL(build()[0])
        L.nmp_new.restype = ctypes.c_void_p
        L.nmp_new.argtypes = [ctypes.c_void_p]
        for f in ('nmp_cpu', 'nmp_ram'):
            getattr(L, f).restype = ctypes.c_void_p
            getattr(L, f).argtypes = [ctypes.c_void_p]
        L.nmp_run.restype = ctypes.c_int64
        L.nmp_run.argtypes = [ctypes.c_void_p, ctypes.c_int64]
        L.nmp_reset.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.nmp_set_log.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        L.nmp_host_hpic.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
        L.nmp_sd_open.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.nmp_stat.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.nmp_stat.restype = ctypes.c_uint64
        L.nmp_pcm_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        L.nmp_pcm_rate.argtypes = [ctypes.c_void_p]
        L.nmp_pcm_rate.restype = ctypes.c_double
        L.nmp_hint.argtypes = [ctypes.c_void_p]
        L.nmp_cpu_hz.argtypes = [ctypes.c_void_p]
        L.nmp_cpu_hz.restype = ctypes.c_double
        L.nmp_set_pins.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint16]
        L.nmp_report_stderr.argtypes = [ctypes.c_void_p]
        L.nmp_flush_log.argtypes = [ctypes.c_void_p]
        L.c54_pread.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        L.c54_pread.restype = ctypes.c_uint16
        L.c54_dread.argtypes = [ctypes.c_void_p, ctypes.c_uint16]
        L.c54_dread.restype = ctypes.c_uint16
        L.c54_set_trace.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64]
        L.c54_set_brk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
        L.c54_set_ww.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_int]
        L.c54_ring.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.c54_ring.restype = ctypes.c_uint32
        L.c54_irq_count.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.c54_irq_count.restype = ctypes.c_uint32
        self.n = L.nmp_new(None)
        self.cpu = C54.from_address(L.nmp_cpu(self.n))
        self.ram = (ctypes.c_uint16 * 0x40000).from_address(L.nmp_ram(self.n))
        self.cpu_ptr = L.nmp_cpu(self.n)

    def load_builtin(self):
        hdr, secs, mem, loaded = fwimg.image()
        self.lib.nmp_reset(self.n, 1)
        self.lib.nmp_reset(self.n, 0)
        for i, v in enumerate(mem):
            if loaded[i]:
                self.ram[i] = v
        e = hdr['entry']
        self.ram[0x7E] = e >> 16
        self.ram[0x7F] = e & 0xFFFF
        return hdr

    def sd(self, path):
        img = make_sd(path)
        assert self.lib.nmp_sd_open(self.n, img.encode()) == 0
        return img

    def stat(self, k):
        return self.lib.nmp_stat(self.n, k)

    def pcm(self, maxn=1 << 20):
        buf = (ctypes.c_int16 * maxn)()
        k = self.lib.nmp_pcm_read(self.n, buf, maxn)
        return buf[:k]

    def run(self, cycles):
        return self.lib.nmp_run(self.n, cycles)

    def ring(self, n=64):
        return [self.lib.c54_ring(self.cpu_ptr, k) for k in range(n)]

    def irqs(self):
        return {b: self.lib.c54_irq_count(self.cpu_ptr, b) for b in range(16) if self.lib.c54_irq_count(self.cpu_ptr, b)}

    def regs(self):
        c = self.cpu
        return ('PC=%x:%04x A=%010x B=%010x T=%04x ST0=%04x ST1=%04x PMST=%04x SP=%04x IMR=%04x IFR=%04x AR=%s' %
                (c.xpc, c.pc, c.acc[0], c.acc[1], c.t, c.st0, c.st1, c.pmst, c.sp, c.imr, c.ifr,
                 ' '.join('%04x' % a for a in c.ar)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd')
    ap.add_argument('--cycles', type=float, default=5e6)
    ap.add_argument('--log', default=os.path.join(workdir.work('runs'), 'nmp_standalone.log'))
    ap.add_argument('--loglevel', type=int, default=1)
    ap.add_argument('--sd', default=os.path.join(workdir.WORK, 'sd', 'nmp'), help='host folder or image (default: emu/make_sd.py\'s nmp card)')
    ap.add_argument('--trace', default=None, help='LO:HI cycle window')
    ap.add_argument('--pins', default='0,0,0', help='input pin levels per McBSP (hex, PCR bit positions)')
    ap.add_argument('--ww', default=None, help='LO:HI data write watch')
    a = ap.parse_args()
    m = Machine()
    m.lib.nmp_set_log(m.n, a.log.encode(), a.loglevel)
    for i, v in enumerate(a.pins.split(',')):
        m.lib.nmp_set_pins(m.n, i, int(v, 16))
    if a.sd: m.sd(a.sd)
    m.load_builtin()
    if a.trace:
        lo, hi = (int(float(x)) for x in a.trace.split(':'))
        m.lib.c54_set_trace(m.cpu_ptr, lo, hi)
    if a.ww:
        lo, hi = (int(x, 16) for x in a.ww.split(':'))
        m.lib.c54_set_ww(m.cpu_ptr, lo, hi, 1)
    total = int(a.cycles)
    step = 1000000
    done = 0
    while done < total:
        done += m.run(min(step, total - done))
        print('%9d %s' % (done, m.regs()))
    m.lib.nmp_flush_log(m.n)
    m.lib.nmp_report_stderr(m.n)
    print('irqs', m.irqs())
    print('ring', ' '.join('%x' % x for x in m.ring(24)))

if __name__ == '__main__':
    main()
