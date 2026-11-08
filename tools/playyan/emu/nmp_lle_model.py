"""LLE cartridge for the mGBA harness, Nintendo MP3 Player: the real TMS320VC5416 firmware (uploaded by the
real GBA loader) on our C54x core + board model (libnmp.so).

Same observer interface as nmp_model.NMPModel (cmd_log, rec_log, verify, errors, tick, summary_extra), so
run.py and scenarios.py work unchanged with --lle.  Nothing here answers commands: uploads, commands,
records and data reads go through pyan.c's HPI model straight into the DSP's RAM (C to C).  Python only
observes: doorbells (mailbox contents), HINT records, the GBA error log, and the DSP's headphone PCM.
"""
import ctypes as C
import json
import os
import struct
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, '..'))
import workdir  # noqa: E402
sys.path.append(workdir.C54X)
import nmpm  # noqa: E402
import fwimg  # noqa: E402
from pyan_core import EV  # noqa: E402

EV = dict(EV)
EV[16] = "LLE_IRQ"

NMP_CMD = {0x10: "LIST_FIRST", 0x11: "LIST_NEXT", 0x20: "CHDIR", 0x30: "0x30", 0x40: "ID3", 0x50: "PLAY",
           0x51: "STOP", 0x52: "PAUSE", 0x53: "RESUME", 0x60: "SEEK", 0x70: "0x70", 0x80: "VOLUME",
           0x200: "SFX", 0x300: "FOPEN", 0x301: "FREAD", 0x302: "FSEEK", 0x303: "FCLOSE", 0x400: "BG_PLAY",
           0x401: "BG_LEAVE", 0x500: "SLEEP", 0x501: "WAKE", 0x800: "FW_PREPARE"}
STRCMDS = (0x20, 0x40, 0x50, 0x300)


class PYanLLE(C.Structure):
    _fields_ = [("ctx", C.c_void_p), ("run", C.c_void_p), ("reset", C.c_void_p), ("doorbell", C.c_void_p),
                ("mmioWrite", C.c_void_p), ("mmioRead", C.c_void_p)]


class NMPLLEModel:
    def __init__(self, core, rom_path, sd_root, events_path=None, opts=None):
        self.c = core
        self.opts = dict(period=8778, sd_image=None, keep_pcm=True, speed=1.0)
        self.opts.update(opts or {})
        self.ev = open(events_path, "w") if events_path else None
        self.cmd_log, self.rec_log, self.errors, self.verify = [], [], [], []
        self.rec_full = []
        self.audio_blocks = []         # (frame, size, A) of 0x8100 sample records
        self.pos_recs = []             # (frame, trackbar, seconds)
        self.booted = False
        self.last_unknown = None
        self.pcm = []                  # headphone PCM (int16 interleaved words as sent on McBSP0)
        self.pcm_rate = None
        nmpm.build()
        L = self.L = C.CDLL(nmpm.LIB)
        L.nmp_new.restype = C.c_void_p
        L.nmp_new.argtypes = [C.c_void_p]
        for f in ("nmp_set_host_irq",):
            getattr(L, f).argtypes = [C.c_void_p, C.c_void_p, C.c_void_p]
        L.nmp_sd_open.argtypes = [C.c_void_p, C.c_char_p]
        L.nmp_set_log.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
        L.nmp_flush_log.argtypes = [C.c_void_p]
        L.nmp_stat.argtypes = [C.c_void_p, C.c_int]
        L.nmp_stat.restype = C.c_uint64
        L.nmp_pcm_read.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
        L.nmp_pcm_rate.argtypes = [C.c_void_p]
        L.nmp_pcm_rate.restype = C.c_double
        L.nmp_cpu_hz.argtypes = [C.c_void_p]
        L.nmp_cpu_hz.restype = C.c_double
        L.nmp_set_speed.argtypes = [C.c_void_p, C.c_double]
        L.nmp_report_stderr.argtypes = [C.c_void_p]
        L.nmp_set_pins.argtypes = [C.c_void_p, C.c_int, C.c_uint16]
        ram = core.lib.retro_pyan_mem()
        assert core.lib.retro_pyan_memsize() == 0x40000 * 2
        self.n = L.nmp_new(C.c_void_p(ram))
        if self.opts.get("log"):
            L.nmp_set_log(self.n, self.opts["log"].encode(), int(self.opts.get("loglevel", 4)))
        if self.opts.get("speed", 1.0) != 1.0:
            L.nmp_set_speed(self.n, float(self.opts["speed"]))
        if self.opts.get("dr1") is not None:
            L.nmp_set_pins(self.n, 1, 0x10 if int(self.opts["dr1"]) else 0)
        img = self.opts["sd_image"] or nmpm.make_sd(sd_root)
        if L.nmp_sd_open(self.n, img.encode()):
            raise RuntimeError("cannot open SD image %s" % img)
        self.sd_image = img
        irqfn = C.cast(core.lib.retro_pyan_lle_host_irq, C.c_void_p).value
        L.nmp_set_host_irq(self.n, C.c_void_p(irqfn), None)
        addr = lambda nm: C.cast(getattr(L, nm), C.c_void_p).value
        self.lle = PYanLLE(self.n, addr("nmp_lle_run"), addr("nmp_lle_reset"), None, None, None)
        C.c_uint32.in_dll(core.lib, "retro_pyan_lle_period").value = int(self.opts["period"])
        core.lib.retro_pyan_set_nmp_hpic.argtypes = [C.c_void_p]
        core.lib.retro_pyan_set_nmp_hpic(C.c_void_p(addr("nmp_lle_hpic")))
        core.lib.retro_pyan_set_lle.argtypes = [C.POINTER(PYanLLE)]
        core.lib.retro_pyan_set_lle(C.byref(self.lle))
        core.handler = self.on_event
        self._pcmbuf = (C.c_int16 * (1 << 18))()
        self.reset_seen = False
        self.ticks = []
        self.log("lle_start", sd_image=img, period=self.opts["period"])

    # ------------------------------------------------------------------ logging
    def log(self, kind, **kw):
        kw.update(kind=kind, frame=self.c.frame(), cyc=self.c.cycles())
        if self.ev:
            self.ev.write(json.dumps(kw, default=str) + "\n")
        return kw

    def note(self, s):
        self.c.note(s)

    def stat(self, k):
        return self.L.nmp_stat(self.n, k)

    # ------------------------------------------------------------------ bus events
    def on_event(self, ev, a, b, c):
        name = EV.get(ev)
        if name == "NMP_DOORBELL":
            self.on_command()
        elif name == "LLE_IRQ":
            self.on_record()
        elif name == "RESET_ASSERT":
            self.reset_seen = True
            self.log("reset")
        elif name == "NMP_CTRL":
            if a == 0x08 and self.reset_seen:
                self.reset_seen = False
                self.verify_upload()

    def verify_upload(self):
        """CONTROL 0x08 after a reset: the entry point is written; compare the DSP RAM with the boot table"""
        hdr, secs = fwimg.parse()
        mem = self.c.mem16
        bad, total = [], 0
        for dest, ws in secs:
            for k, w in enumerate(ws):
                a = fwimg.hpi_fold(dest + k)
                total += 1
                if int(mem[a]) != w:
                    bad.append("%05X" % a)
                    break
        ent = (int(mem[0x7E]) << 16) | int(mem[0x7F])
        r = dict(sections=len(secs), words=total, mismatched_sections=len(bad), first_bad=bad[:5],
                 entry_written="%X" % ent, entry_expected="%X" % hdr["entry"], exact=(not bad and ent == hdr["entry"]),
                 fup_marker_7F00=["%04X" % int(x) for x in mem[0x7F00:0x7F04]])
        self.verify.append(r)
        self.log("fw_verify", **r)
        self.note("NMP LLE upload: %d sections, %d words, %d mismatched; entry %X" % (len(secs), total, len(bad), ent))

    def on_command(self):
        mem = self.c.mem16
        words = [int(x) for x in mem[0x110:0x110 + 0x110]]
        cid = words[0]
        args = {}
        if cid in STRCMDS:
            s = ""
            for x in words[1:0x106]:
                if x == 0:
                    break
                s += chr(x)
            args["name"] = s
        self.cmd_log.append((self.c.frame(), cid, None))
        self.log("cmd", id="%X" % cid, cname=NMP_CMD.get(cid, "?"), words=["%X" % x for x in words[1:8]], **args)

    def on_record(self):
        mem = self.c.mem16
        rec = [int(x) for x in mem[0x100:0x108]]
        rid = rec[0]
        self.rec_log.append((self.c.frame(), rid))
        A = rec[2] | rec[3] << 16
        if rid == 0x8001:
            self.booted = True
        if rid == 0x8100:
            if A:
                size = min(rec[1], 0x480)
                nw = (((size >> 3) + 3) & ~3) * 2   # words per channel: the GBA reads n 32-bit units (docs/playyan/protocol.md 11.5)
                a1 = fwimg.hpi_fold(A); a2 = fwimg.hpi_fold(A + nw)   # channel 2 at A + n*2 (n in 32-bit units)
                self.audio_blocks.append((self.c.frame(), rec[1], A, mem[a1:a1 + nw].copy(), mem[a2:a2 + nw].copy()))
            else:
                self.pos_recs.append((self.c.frame(), rec[4] | rec[5] << 16, rec[6] | rec[7] << 16))
        if rid != 0x8100 or len(self.rec_full) < 300 or not A:
            self.rec_full.append((self.c.frame(), rec))
            self.log("rec", rec=["%04X" % x for x in rec])

    # ------------------------------------------------------------------ per frame
    def tick(self):
        if self.c.frame() % 60 == 0 and self.booted:
            self.ticks.append((self.c.frame(), int(self.c.mem16[0x10F])))
        k = self.L.nmp_pcm_read(self.n, self._pcmbuf, len(self._pcmbuf))
        if k and self.opts["keep_pcm"]:
            self.pcm.extend(self._pcmbuf[:k])
        self.pcm_rate = self.L.nmp_pcm_rate(self.n)
        try:
            u = self.c.retro.u16(0x03001080)
        except KeyError:
            u = None
        if u and u != 0x5678 and u != self.last_unknown:
            self.last_unknown = u
            self.errors.append((self.c.frame(), ["unknown id %X" % u]))
            self.log("gba_unknown_id", id="%X" % u)

    def save_pcm(self, path):
        n = len(self.pcm) // 2 * 2
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(int(round(self.pcm_rate or 44100)))
            w.writeframes(struct.pack("<%dh" % n, *self.pcm[:n]))
        return n // 2

    def tick_rate(self):
        """DSP tick counter (word 0x10F) increments per GBA frame: min and mean over 60-frame windows"""
        r = []
        for (f0, t0), (f1, t1) in zip(self.ticks, self.ticks[1:]):
            if f1 > f0:
                r.append(((t1 - t0) & 0xFFFF) / (f1 - f0))
        return dict(min=round(min(r), 4), mean=round(sum(r) / len(r), 4), windows=len(r)) if r else None

    def save_gba_blocks(self, path):
        """0x8100 sample blocks as the DSP left them: npz with size, A, ch1 words, ch2 words"""
        if not self.audio_blocks:
            return 0
        np.savez(path, frame=np.array([b[0] for b in self.audio_blocks]), size=np.array([b[1] for b in self.audio_blocks]),
                 addr=np.array([b[2] for b in self.audio_blocks]),
                 ch1=np.concatenate([b[3] for b in self.audio_blocks]), ch2=np.concatenate([b[4] for b in self.audio_blocks]),
                 nwords=np.array([len(b[3]) for b in self.audio_blocks]))
        return len(self.audio_blocks)

    def summary_extra(self):
        ids = {}
        for f, r in self.rec_log:
            ids["%04X" % r] = ids.get("%04X" % r, 0) + 1
        cids = {}
        for f, cid, _ in self.cmd_log:
            cids["%04X" % cid] = cids.get("%04X" % cid, 0) + 1
        return dict(nmp_verify=self.verify, lle=dict(
            records=ids, commands=cids, hint=self.stat(0), dspint=self.stat(1), pcm_words=self.stat(2),
            audio_dma_blocks=self.stat(3), pcm_overrun=self.stat(4), dsp_cycles=self.stat(5),
            sd_cmds=self.stat(10), sd_reads=self.stat(11), sd_writes=self.stat(12),
            cpu_mhz=round(self.L.nmp_cpu_hz(self.n) / 1e6, 4), pcm_rate=self.pcm_rate,
            gba_audio_blocks=len(self.audio_blocks), position_records=len(self.pos_recs),
            ticks_per_frame=self.tick_rate(),
            first_records=[(f, ["%04X" % x for x in r]) for f, r in self.rec_full[:40]]))
