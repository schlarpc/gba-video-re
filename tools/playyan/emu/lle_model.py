"""LLE cartridge for the mGBA harness: the real VC01 firmware on the C VC01 emulator (libvc01.so).

Same interface as pyan_model.PlayYanModel (cmd_log, rec_log, verify, errors, tick, summary_extra),
so run.py and scenarios.py work unchanged with --lle.  Nothing here answers commands: the GBA's
uploads, kicks, commands and record reads go to the emulated chip through pyan.c's LLE hooks
(C to C, no Python in the loop); Python only observes (doorbells, host IRQs, SD and UART).
"""
import ctypes as C
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, '..'))
import workdir  # noqa: E402
sys.path.append(workdir.VC01)
import vcm  # noqa: E402
from pyan_core import EV  # noqa: E402

EV = dict(EV)
EV[16] = "LLE_IRQ"

FW = workdir.FIRMWARE
RUNS = os.path.join(workdir.WORK, 'runs')    # thumbs/, vexec/, cap/ (diagnostics)
GBA_HZ = 16777216


class PYanLLE(C.Structure):
    _fields_ = [("ctx", C.c_void_p), ("run", C.c_void_p), ("reset", C.c_void_p), ("doorbell", C.c_void_p),
                ("mmioWrite", C.c_void_p), ("mmioRead", C.c_void_p)]


CMD_NAME = None


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


class LLEModel:
    def __init__(self, core, rom_path, sd_root, events_path=None, opts=None):
        global CMD_NAME
        from pyan_model import CMD_NAME as CN, CMD_LEN, NAME_OFF, ROMCFG, GARAGE, cstr
        CMD_NAME = CN
        self.CMD_LEN, self.NAME_OFF, self.GARAGE, self.cstr = CMD_LEN, NAME_OFF, GARAGE, cstr
        self.c = core
        self.fam = core.family
        self.cfg = ROMCFG[self.fam]
        self.opts = dict(ips=50e6, period=1024, vecmode=1, sd_image=None)   # 1024 GBA cycles (61 us): the Micro sends commands ~1500 cycles apart; with 35112 the next one overwrote the mailbox before the doorbell ISR ran
        self.opts.update(opts or {})
        self.ev = open(events_path, "w") if events_path else None
        self.cmd_log, self.rec_log, self.verify, self.errors, self.garage, self.ff080 = [], [], [], [], [], []
        self.kicks = []
        self.uart_log = []
        self.sd_stats = dict(cmds=0, reads=0, writes=0)
        self.stops = []
        self.thumbs = []
        self.vexec = {}
        self.vf = {}                 # (image, pc) -> fault record, accumulated across kicks
        self.cur_image = None
        self.booted = False
        self.music = None
        # keyed by file stem (py_fw0, mi_fwldr, ...)
        self.imgs = {v[:-4]: open(os.path.join(FW, v), "rb").read() for k, v in self.cfg["imgs"].items()}
        # the emulated chip, on the core's VC memory
        L = self.L = vcm.lib()
        buf = core.lib.retro_pyan_mem()
        size = core.lib.retro_pyan_memsize()
        self.m = vcm.Machine(size=size, buf_addr=buf)
        h = self.m.h
        L.hw_set_ips(h, float(self.opts["ips"]))
        L.vc_set_vecmode(self.m.c, int(self.opts["vecmode"]))
        L.vc_set_memrep7.argtypes = [C.c_void_p, C.c_int]
        L.vc_set_memrep7(self.m.c, int(self.opts.get("memrep7", 0)))
        L.hw_set_ac_scale.argtypes = [C.c_void_p, C.c_double]
        L.hw_set_ac_scale(h, float(self.opts.get("ac_scale", 1.0)))
        L.hw_set_hp_jack(h, int(self.opts.get("hp_jack", 0)))   # WM9712 reg 0x54 bit 1: headphones plugged
        img = self.opts["sd_image"] or vcm.make_sd(sd_root, os.path.join(vcm.TMP, "lle_%s.img" % os.path.basename(os.path.normpath(sd_root))))
        if L.hw_sd_open(h, img.encode()):
            raise RuntimeError("cannot open SD image %s" % img)
        self.sd_image = img
        self.m._ev = vcm.EVENT_T(self._hw_event)
        L.hw_set_event(h, self.m._ev, None)
        # host IRQ line -> pyan.c (C function pointer, no Python)
        irqfn = C.cast(core.lib.retro_pyan_lle_host_irq, C.c_void_p).value
        L.hw_set_host_irq(h, irqfn, None)
        addr = lambda n: C.cast(getattr(L, n), C.c_void_p).value
        self.lle = PYanLLE(h, addr("hw_lle_run"), addr("hw_lle_reset"), addr("hw_lle_doorbell"),
                           addr("hw_lle_mmio_write"), addr("hw_lle_mmio_read"))
        C.c_uint32.in_dll(core.lib, "retro_pyan_lle_period").value = int(self.opts["period"])
        # cartridge bridge: WIN_R1 (pixel window) converts RGB565 -> BGR555 (pyan.c; docs/playyan/protocol.md 8.2)
        C.c_uint32.in_dll(core.lib, "retro_pyan_win1_bgr555").value = int(self.opts.get("win1_bgr555", 1))
        core.lib.retro_pyan_set_lle.argtypes = [C.POINTER(PYanLLE)]
        core.lib.retro_pyan_set_lle(C.byref(self.lle))
        core.handler = self.on_event
        self.log("lle_start", sd_image=img, ips=self.opts["ips"], period=self.opts["period"])

    # ------------------------------------------------------------------ logging
    def log(self, kind, **kw):
        kw.update(kind=kind, frame=self.c.frame(), cyc=self.c.cycles())
        if self.ev:
            self.ev.write(json.dumps(kw, default=str) + "\n")
        return kw

    def note(self, s):
        self.c.note(s)

    # ------------------------------------------------------------------ chip events (libvc01)
    def _hw_event(self, ctx, ev, a, b):
        if ev == 2:
            self.sd_stats["cmds"] += 1
        elif ev == 1:
            self.sd_stats["reads"] += 1
        elif ev == 3:
            self.sd_stats["writes"] += 1
            self.log("sd_write", addr=a)
        elif ev == 10:
            st, pc = a, b
            info = self.m.stop_info()
            self.stops.append((self.c.frame(), st, pc, info))
            self.log("vc_stop", code=st, pc="%X" % pc, info=info)
            self.note("VC01 STOPPED code=%d pc=%08X %s" % (st, pc, info))
            print("  VC01 stopped: code %d pc %08X %s (frame %d)" % (st, pc, info, self.c.frame()))
        elif ev == 13:
            self.harvest_vf()
            h = self.vexec.setdefault(self.cur_image, {})
            for pc, n in self.harvest_vh().items():
                h[pc] = h.get(pc, 0) + n
            img = self.identify()
            self.cur_image = img["image"]
            self.kicks.append(img)
            cap = os.environ.get("LLE_CAP")       # e.g. py_fw0:0x26002:3000 -> $PLAYYAN_WORK/runs/cap/<tag>_<pc>.bin
            if cap:
                ci, cpc, cmax = cap.split(":")
                cpc = int(cpc, 0); cmax = int(cmax)
                if not hasattr(self, "_capbuf"):
                    self._capbuf = (C.c_uint8 * (cmax * (4096 + 128 + 1024)))()
                self.L.vc_set_cap.argtypes = [C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32]
                self.L.vc_cap_n.argtypes = [C.c_void_p]; self.L.vc_cap_n.restype = C.c_uint32
                if self.cur_image == ci:
                    self.L.vc_set_cap(self.m.c, cpc, self._capbuf, cmax)
                    self.L.vc_set_cap_reg.argtypes = [C.c_void_p, C.c_int]
                    self.L.vc_set_cap_reg(self.m.c, int(os.environ.get("LLE_CAP_REG", "0")))
                    self._cap = (ci, cpc)
                else:
                    self.L.vc_set_cap(self.m.c, 0, None, 0)
            self.note("KICK: VC01 started at 0x200, image %s" % img["image"])
        elif ev == 14:
            self.log("host_mmio", addr="%X" % a, value="%X" % b)
            self.note("HOST MMIO W %08X = %08X (wake)" % (a, b))

    def identify(self):
        """Which image is running: the code at 0x210..0x8000 decides (data/BSS change at run time);
        the loader is recognised by its jump record at 0x200 and its code at 0xC0200."""
        vc = self.m.mv
        out = dict(image="unknown")
        cands = dict(self.imgs)
        for n in ("fup_sec0", "fup_sec1"):
            p = os.path.join(FW, n + ".bin")
            if os.path.exists(p) and self.fam == 1:
                cands[n] = open(p, "rb").read()
        if bytes(vc[0x200:0x206]) == bytes([0x7F, 0x2F, 0x00, 0x02, 0x0C, 0x00]):
            for k, img in cands.items():
                if k.endswith("fwldr") and bytes(vc[0xC0200:0xC8000]) == img[0x200:0x8000]:
                    out = dict(image=k, exact=True)
        else:
            for k, img in cands.items():
                if not k.endswith("fwldr") and bytes(vc[0x210:0x8000]) == img[0x210:0x8000]:
                    out = dict(image=k, exact=True)
                    break
        out["frame"] = self.c.frame()
        self.verify.append(out)
        self.log("kick", **out)
        return out

    # ------------------------------------------------------------------ bus events (pyan.c)
    def on_event(self, ev, a, b, c):
        name = EV.get(ev)
        if name == "DOORBELL":
            self.on_command(a, b)
        elif name == "LLE_IRQ":
            self.on_record()
        elif name == "W32":
            if a == 0xFF080:
                self.ff080.append(b)
            if a >= 0x10000000:
                self.log("w32_mmio", addr="%X" % a, value="%X" % b)
        elif name == "RESET_ASSERT":
            self.log("reset")

    def on_command(self, nbytes, nwrites):
        raw = bytes(self.m.mv[0xFF020:0xFF020 + 0x220])
        cid = u32(raw, 0)
        self.cmd_log.append((self.c.frame(), cid, nbytes))
        args = {}
        if cid in self.NAME_OFF:
            args["name"] = self.cstr(raw, self.NAME_OFF[cid])
        words = [u32(raw, i) for i in range(4, min(max(nbytes, 4), 0x40), 4)]
        self.log("cmd", id="%X" % cid, cname=CMD_NAME.get(cid, "?"), nbytes=nbytes,
                 words=["%X" % w for w in words[:8]], **args)

    def on_record(self):
        rec = struct.unpack_from("<8I", self.m.mv, 0xFF000)
        self.rec_log.append((self.c.frame(), rec[0]))
        if rec[0] == 0x80000100:
            self.booted = True
            # an image downloaded by the loader (FIRMWARE_DOWNLOAD) starts without a kick
            img = self.identify()
            if img["image"] != self.cur_image:
                self.harvest_vf()
                h = self.vexec.setdefault(self.cur_image, {})
                for pc, n in self.harvest_vh().items():
                    h[pc] = h.get(pc, 0) + n
                self.note("STARTUP from image %s (was %s)" % (img["image"], self.cur_image))
                self.cur_image = img["image"]
        if rec[0] == 0x40000500:
            self.on_thumb(rec)
        if rec[0] == 0x80001000 and rec[1] and os.environ.get("LLE_FRAMES"):
            # raw 240x160 frames as sent to the GBA: LLE_FRAMES=dir[:max] -> dir/fNNNNN.bin (+ index.txt)
            d, _, mx = os.environ["LLE_FRAMES"].partition(":")
            n = getattr(self, "_nframes", 0)
            if n < int(mx or 1000000) and rec[1] + 0x12C00 <= len(self.m.mv):
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, "f%05d.bin" % n), "wb").write(bytes(self.m.mv[rec[1]:rec[1] + 0x12C00]))
                with open(os.path.join(d, "index.txt"), "a") as f:
                    f.write("%d %d %08X %d\n" % (n, self.c.frame(), rec[1], rec[6]))
                self._nframes = n + 1
        if rec[0] != 0x80001000 or len(self.rec_log) < 400:
            self.log("rec", rec=["%08X" % x for x in rec])

    def on_thumb(self, rec):
        """VIDEO_INFO_CONFIRM: the real firmware's 60x40 thumbnail; check Garage hashes."""
        addr = rec[2]
        name = None
        for f, cid, n in reversed(self.cmd_log):
            if cid == 0x500:
                break
        raw = bytes(self.m.mv[0xFF020 + 0x14:0xFF020 + 0x14 + 0x100])
        name = self.cstr(raw, 0)
        px = []
        if addr and addr + 0x12C0 <= len(self.m.mv):
            px = struct.unpack_from("<2400H", self.m.mv, addr)
        h = 0
        for p in px:
            p = ((p >> 11) & 31) | ((p >> 6) & 31) << 5 | (p & 31) << 10   # RGB565 -> BGR555: what the GBA reads through WIN_R1
            h = ((h + p) & 0xFFFFFFFF)
            h = ((h << 1) | (h >> 31)) & 0xFFFFFFFF
        base = (name or "").split("\\")[-1]
        t = dict(name=name, addr="%X" % addr, status=rec[1], duration_ms=rec[3], hash="%08X" % h)
        if base in self.GARAGE:
            idx, target = self.GARAGE[base]
            t.update(garage_index=idx, table_hash="%08X" % target, match=h == target)
            self.garage.append(t)
        self.thumbs.append(dict(t, pixels=bytes(self.m.mv[addr:addr + 0x12C0]) if px else b""))
        if px:      # keep the raw 60x40 u16 thumbnail for inspection
            d = os.path.join(RUNS, "thumbs")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "%s.bin" % (base or "noname")), "wb").write(bytes(self.m.mv[addr:addr + 0x12C0]))
        self.log("thumb", **t)
        self.note("THUMB %s hash %08X %s" % (name, h, t.get("match", "")))

    # ------------------------------------------------------------------ per frame
    def poll_errors(self):
        r = self.c.retro
        cnt = r.read(self.cfg["errcnt"], 1)[0]
        if cnt:
            codes = list(r.read(self.cfg["errbuf"], min(cnt, 16)))
            unk = r.u32(self.cfg["unkid"]) if self.cfg["unkid"] else None
            self.errors.append((self.c.frame(), codes, "%08X" % unk if unk is not None else None))
            self.log("gba_error", codes=codes, unknown_id="%08X" % unk if unk is not None else None)
            self.note("GBA ERROR LOG %s" % codes)
            r.write(self.cfg["errcnt"], b"\0")

    def tick(self):
        self.poll_errors()
        n = self.L.hw_uartn(self.m.h)
        if n:
            s = self.m.uart()
            self.L.hw_uart_clear(self.m.h)
            self.uart_log.append((self.c.frame(), s))
            for line in s.splitlines():
                if line.strip():
                    self.note("UART %s" % line[:200])

    def harvest_vh(self):
        """executed vector instructions per pc since the last harvest (C-core histogram)"""
        out = {}
        a = (C.c_uint32 * 2)()
        self.L.vc_vhist.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_uint32)]
        for i in range(8192):
            self.L.vc_vhist(self.m.c, i, a)
            if a[1]:
                out[a[0]] = a[1]
        self.L.vc_vhist_clear.argtypes = [C.c_void_p]
        self.L.vc_vhist_clear(self.m.c)
        return out

    def harvest_vf(self):
        for f in self.m_vfaults():
            k = (self.cur_image, f["pc"])
            if k in self.vf:
                self.vf[k]["count"] += f["count"]
            else:
                self.vf[k] = dict(f, image=self.cur_image)
        self.L.vc_vf_clear.argtypes = [C.c_void_p]
        self.L.vc_vf_clear(self.m.c)

    def summary_extra(self):
        st = self.m.stats()
        self.harvest_vf()
        vf = list(self.vf.values())
        if getattr(self, "_cap", None):
            n = self.L.vc_cap_n(self.m.c)
            d = os.path.join(RUNS, "cap")
            os.makedirs(d, exist_ok=True)
            tag = getattr(self, "tag", None) or (os.path.basename(self.ev.name).split(".")[0] if self.ev else "run")
            open(os.path.join(d, "%s_%x.bin" % (tag, self._cap[1])), "wb").write(bytes(self._capbuf)[:n * (4096 + 128 + 1024)])
        self.vexec.setdefault(self.cur_image, {})
        for pc, n in self.harvest_vh().items():
            self.vexec[self.cur_image][pc] = self.vexec[self.cur_image].get(pc, 0) + n
        try:
            d = os.path.join(RUNS, "vexec")
            os.makedirs(d, exist_ok=True)
            tag = getattr(self, "tag", None) or (os.path.basename(self.ev.name).split(".")[0] if self.ev else "run")
            json.dump({img: {"%x" % k: v for k, v in h.items()} for img, h in self.vexec.items()},
                      open(os.path.join(d, tag + ".json"), "w"))
        except Exception as e:  # diagnostics only
            self.note("vexec dump failed: %s" % e)
        import vfreport
        vfreport.save(getattr(self, "tag", None) or os.path.basename(self.ev.name).split(".")[0] if self.ev else "run", vf)
        return dict(lle=True, kicks_lle=self.kicks, vc_stats=st, sd=self.sd_stats, stops=self.stops,
                    uart="".join(s for _, s in self.uart_log)[-4000:], vec_faults=len(vf),
                    thumbs=[{k: v for k, v in t.items() if k != "pixels"} for t in self.thumbs],
                    records=[("%08X" % r) for _, r in self.rec_log[:300]])

    def m_vfaults(self):
        out = []
        a = (C.c_uint32 * 9)()
        for i in range(self.L.vc_nvf(self.m.c)):
            self.L.vc_vf(self.m.c, i, a)
            out.append(dict(pc=a[0], count=a[1], why=a[2], words=list(a[4:4 + a[3]])))
        return out
