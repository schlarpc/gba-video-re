#!/usr/bin/env python3
"""Standalone VC01 machine (C core + peripheral model, libvc01.so) for booting the real firmware
without the GBA.

  vcm.py boot IMG [--sd DIR|IMG] [--cycles N] [--trace]     IMG = py_fwldr, py_fw0, py_fw1, mi_*, fup_sec0/1
(images from $PLAYYAN_WORK/firmware/extract, tools/playyan/extract_firmware.py; SD images go to $PLAYYAN_WORK/sd)
Prints the UART log, the mailbox records and an MMIO access summary."""
import argparse
import ctypes as C
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cvc  # noqa: E402

TMP = cvc.workdir.work('sd')           # SD card images made from host folders
LOADER_REC = bytes([0x7F, 0x2F, 0x00, 0x02, 0x0C, 0x00])
HOSTIRQ_T = C.CFUNCTYPE(None, C.c_void_p, C.c_int)
EVENT_T = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_uint32, C.c_uint32)


def build():
    return cvc.build()


def lib():
    build()
    L = cvc.lib()
    P = C.c_void_p
    sig = {
        'hw_new': (P, [P, C.c_uint32]), 'hw_cpu': (P, [P]), 'hw_reset': (None, [P, C.c_uint32]),
        'hw_sd_open': (C.c_int, [P, C.c_char_p]), 'hw_doorbell': (None, [P]),
        'hw_set_host_irq': (None, [P, P, P]), 'hw_set_event': (None, [P, P, P]),
        'hw_mmio_write': (None, [P, C.c_uint32, C.c_uint32]), 'hw_mmio_read': (C.c_uint32, [P, C.c_uint32]),
        'hw_run': (C.c_int, [P, C.c_uint64]), 'hw_cycles': (C.c_uint64, [P]), 'hw_clk': (C.c_double, [P]),
        'hw_uart': (C.c_char_p, [P]), 'hw_uartn': (C.c_uint32, [P]), 'hw_uart_clear': (None, [P]),
        'hw_nlog': (C.c_int, [P]), 'hw_log': (None, [P, C.c_int, C.POINTER(C.c_uint32)]),
        'hw_log_clear': (None, [P]), 'hw_trace_mmio': (None, [P, C.c_int]), 'hw_host_irqs': (C.c_uint64, [P]),
        'hw_stats': (None, [P, C.POINTER(C.c_uint64)]), 'hw_pcm_buffer': (None, [P, P, C.c_uint64]),
        'hw_pcm_count': (C.c_uint64, [P]), 'hw_pcm_marks': (None, [P, P, C.c_uint64]), 'hw_pcm_marks_n': (C.c_uint64, [P]), 'hw_ac_silence': (C.c_uint64, [P]), 'hw_set_hp_jack': (None, [P, C.c_int]), 'hw_ac_gpio_irqs': (C.c_uint64, [P]), 'vc_nbkpt': (C.c_uint64, [P]), 'vc_bkpts': (None, [P, P]),
        'vc_unaligned': (C.c_uint64, [P]), 'vc_set_bkpt_halt': (None, [P, C.c_int]), 'vc_ring': (None, [P, P]),
        'vc_set_watch': (None, [P, C.c_uint32]), 'vc_set_align': (None, [P, C.c_int]), 'hw_regs': (P, [P]), 'hw_set_ips': (None, [P, C.c_double]), 'hw_tcount': (C.c_double, [P]), 'hw_ac97': (P, [P]),
    }
    for n, (r, a) in sig.items():
        f = getattr(L, n)
        f.restype = r
        f.argtypes = a
    return L


def make_sd(src, img=None, size_mb=64):
    """FAT16 image from a host folder (mkfs.fat + mtools).  Returns the image path."""
    if src.endswith('.img') and os.path.isfile(src):
        return src
    os.makedirs(TMP, exist_ok=True)
    img = img or os.path.join(TMP, os.path.basename(os.path.normpath(src)) + '.img')
    if os.path.exists(img):
        os.remove(img)
    # fixed clock (1980-01-01, the FAT epoch): the volume serial and the directory timestamps
    # otherwise come from the host's clock, and the image would differ from run to run
    env = dict(os.environ, SOURCE_DATE_EPOCH='315532800')
    subprocess.check_call(['mkfs.fat', '-C', '-F', '16', '-n', 'PLAYYAN', img, str(size_mb * 1024)],
                          stdout=subprocess.DEVNULL, env=env)
    ents = [os.path.join(src, e) for e in sorted(os.listdir(src))]
    if ents:
        subprocess.check_call(['mcopy', '-s', '-Q', '-i', img] + ents + ['::/'],
                              env=dict(env, MTOOLS_NO_VFAT='0'))
    return img


class Machine:
    def __init__(self, size=0x01000000, buf_addr=None):
        self.L = lib()
        if buf_addr is None:
            self.buf = (C.c_uint8 * size)()
            buf_addr = C.addressof(self.buf)
        else:
            self.buf = (C.c_uint8 * size).from_address(buf_addr)
        self.size = size
        self.mv = memoryview(self.buf).cast('B')
        self.h = self.L.hw_new(buf_addr, size)
        self.c = self.L.hw_cpu(self.h)
        self.regs = self.L.vc_regp(self.c)
        self.records = []
        self.sd_events = []
        self._hirq = HOSTIRQ_T(self._host_irq)
        self._ev = EVENT_T(self._event)
        self.L.hw_set_host_irq(self.h, self._hirq, None)
        self.L.hw_set_event(self.h, self._ev, None)
        self.on_record = None
        self.L.vc_set_vecmode(self.c, 1)

    # ---- callbacks
    def _host_irq(self, ctx, level):
        rec = struct.unpack_from('<8I', self.mv, 0xFF000)
        self.records.append((self.L.hw_cycles(self.h), rec))
        if self.on_record:
            self.on_record(rec)

    def _event(self, ctx, ev, a, b):
        if ev == 2:
            self.sd_events.append(('cmd', a, b))
        elif ev == 1:
            self.sd_events.append(('rd', a, b))
        elif ev == 3:
            self.sd_events.append(('wr', a, b))

    # ---- memory
    def load(self, a, data):
        self.mv[a:a + len(data)] = bytes(data)

    def u32(self, a):
        return struct.unpack_from('<I', self.mv, a)[0]

    def upload(self, name):
        """What the GBA does: image to VC 0 (loader: split + jump record), then the kick."""
        d = cvc.workdir.load_firmware(name)
        if name.endswith('fwldr'):
            self.load(0, d[:0x200])
            self.load(0x200, LOADER_REC)
            self.load(0xC0200, d[0x200:])
        else:
            self.load(0, d)
        self.L.hw_reset(self.h, 0x200)

    def sd(self, path):
        if path is None:
            return self.L.hw_sd_open(self.h, None)
        img = make_sd(path)
        r = self.L.hw_sd_open(self.h, img.encode())
        if r:
            raise RuntimeError('cannot open %s' % img)
        return img

    def run(self, cycles):
        return self.L.hw_run(self.h, int(cycles))

    def doorbell(self, words):
        """Write a command to 0xFF020 and ring the doorbell (as the GBA does)."""
        self.load(0xFF020, struct.pack('<%dI' % len(words), *[w & 0xffffffff for w in words]))
        self.L.hw_doorbell(self.h)

    def uart(self):
        return self.L.hw_uart(self.h).decode('latin-1')

    def mmio_log(self):
        out = []
        a = (C.c_uint32 * 5)()
        for i in range(self.L.hw_nlog(self.h)):
            self.L.hw_log(self.h, i, a)
            out.append(tuple(a))
        return out

    def bkpts(self):
        a = (C.c_uint32 * 16)()
        self.L.vc_bkpts(self.c, a)
        return [x for x in a if x]

    def stats(self):
        a = (C.c_uint64 * 16)()
        self.L.hw_stats(self.h, a)
        return dict(bkpt=self.L.vc_nbkpt(self.c), unaligned=self.L.vc_unaligned(self.c), sd_cmds=a[0], sd_reads=a[1], sd_writes=a[2], host_irqs=a[3], pcm=a[4], irqs=a[5],
                    insns=a[6], cycles=a[7], dma_cbs=a[8], dma_bytes=a[9], ac_rate=a[10], ac_reg2a=a[11], ac_reg2c=a[12])

    def pc(self):
        return self.regs[31]

    def stop_info(self):
        return cvc.STOP[self.L.vc_stopcode(self.c)], self.L.vc_fault_pc(self.c), self.L.vc_fault_msg(self.c).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('what')
    ap.add_argument('img')
    ap.add_argument('--sd')
    ap.add_argument('--cycles', type=float, default=50e6)
    ap.add_argument('--trace', action='store_true')
    a = ap.parse_args()
    m = Machine()
    m.upload(a.img)
    if a.sd:
        print('SD image:', m.sd(a.sd))
    if a.trace:
        m.L.hw_trace_mmio(m.h, 1)
    import time
    t = time.time()
    st = m.run(a.cycles)
    dt = time.time() - t
    print('stop:', cvc.STOP.get(st, st), m.stop_info(), 'pc=%#x' % m.pc(), '%.2fs' % dt)
    print('bkpts:', [hex(x) for x in m.bkpts()])
    print('stats:', m.stats(), 'clk %.3f MHz' % (m.L.hw_clk(m.h) / 1e6))
    print('records:', [(c, ' '.join('%08x' % x for x in r[:4])) for c, r in m.records[:20]])
    u = m.uart()
    print('UART (%d bytes):' % len(u))
    print(u[-6000:])
    log = m.mmio_log()
    log.sort(key=lambda x: x[0])
    print('MMIO:')
    for addr, w, n, pc, last in log:
        print('  %s %08x n=%-8d first_pc=%08x last=%08x' % ('W' if w else 'R', addr, n, pc, last))
    print('sd events:', m.sd_events[:40])


if __name__ == '__main__':
    main()
