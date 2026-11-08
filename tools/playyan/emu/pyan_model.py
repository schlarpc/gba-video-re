"""High-level model of the Play-Yan / Play-Yan Micro cartridge firmware (VC01 side).

It answers commands that the GBA writes to VC 0xFF020 (protocol.md 6) with 32-byte records
(protocol.md 7) delivered by the C bus model through VC 0xFF000 and the Game Pak IRQ. Payloads
(lists, thumbnails, ID3, frames, audio, key file) are written into VC memory at addresses this
model picks, and the records carry those addresses.

Everything observed is logged to the trace (C side) and to an events list (JSON lines) used by
the report.
"""
import json
import os
import struct

import numpy as np

import media
from pyan_core import CPU_HZ, EV, FRAME_CYCLES

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import workdir  # noqa: E402
FW = workdir.FIRMWARE

# VC payload addresses chosen by this model (the GBA takes them from the records)
LIST_ADDR = 0x00100000
ID3_ADDR = 0x00108000
THUMB_ADDR = 0x0010A000      # 8 slots x 0x2000
KEY_ADDR = 0x00118000
ORDER_ADDR = 0x00119000
FRAME_ADDR = 0x00120000      # 4 slots x 0x13000
AUDIO_ADDR = 0x00170000      # 32 slots x 0x500

REPLY = 20000                # cycles from doorbell to reply record
STARTUP = 200000             # cycles from kick to VC_STARTUP_INDICATION

# expected command lengths (protocol.md 6 and section 10)
CMD_LEN = {0x200: 8, 0x201: 0x108, 0x300: 8, 0x400: 0xC, 0x500: 0x118, 0x600: 0x108, 0x700: 0x110,
           0x701: 4, 0x800: 0x108, 0x801: 4, 0x902: 4, 0x903: 8, 0x904: 4, 0x905: 8, 0x906: 8,
           0xB00: 8, 0xD00: 0x10, 0xD01: 8, 0x2000: 0xC, 0x8000: 4, 0x8001: 8, 0x10000: 4,
           0x800000: 4, 0x3000: 0x108, 0x3001: 8, 0x3002: 0xC, 0x3003: 4, 0x4000: 0x1A8, 0x4001: 4}
CMD_NAME = {0x200: "FILE_LIST", 0x201: "FILE_SELECT", 0x300: "VIDEO_MODE_SET", 0x400: "AUDIO_MODE_SET",
            0x500: "VIDEO_INFO", 0x600: "AUDIO_INFO", 0x700: "VIDEO_DECODE_START", 0x701: "VIDEO_DECODE_STOP",
            0x800: "AUDIO_DECODE_START", 0x801: "AUDIO_DECODE_STOP", 0x902: "PAUSE", 0x903: "STEP",
            0x904: "RESTART", 0x905: "PLAY_POSITION_SET", 0x906: "PLAY_POSITION_IND_SET", 0xB00: "VOLUME_SET",
            0xD00: "AUDIO_3D", 0xD01: "TONE_CONTROL", 0x2000: "OPERATION_SOUND", 0x8000: "FIRMWARE_LIST",
            0x8001: "FIRMWARE_DOWNLOAD", 0x10000: "SLEEP", 0x800000: "RESET_STANDBY", 0x3000: "FILE_OPEN",
            0x3001: "FILE_READ", 0x3002: "FILE_SEEK", 0x3003: "FILE_CLOSE", 0x4000: "VC_AUTO_PLAY_START",
            0x4001: "VC_AUTO_PLAY_STOP"}
NAME_OFF = {0x201: 4, 0x500: 0x14, 0x600: 4, 0x700: 0xC, 0x800: 4, 0x3000: 4}

GARAGE = {"INSECT.ASF": (0, 0x4D5E5AEB), "AVOID.ASF": (1, 0xF8CB9008), "BAT.ASF": (2, 0xD911FE57),
          "CREDIT.ASF": (3, 0x02F86041), "JUMP.ASF": (4, 0x748C5BAF), "FIRE.ASF": (5, 0x0D78A9FE),
          "KEEPER.ASF": (6, 0x3FEBEFE5), "MEMORY.ASF": (7, 0x90119AE6), "NEKOROID.ASF": (8, 0xD839D809),
          "NOSE.ASF": (9, 0x86D3BA78), "FISH.ASF": (10, 0x5741DE47), "TRIPLE.ASF": (11, 0xD5C0A40B),
          "WAVE.ASF": (12, 0xE7DCD82B)}

ROMCFG = {
    1: dict(name="orig", imgs={"video": "py_fw0.bin", "music": "py_fw1.bin", "loader": "py_fwldr.bin"},
            entry=268, typeoff=0x108, listcopy=0x67AC, ramtest=(0xEC400, 0xFF000), pattern_rom=0x0F25DC,
            ramflag=0x03002B18, errcnt=0x03001691, errbuf=0x03001698, unkid=0x030016A8),
    2: dict(name="micro", imgs={"video": "mi_fw0.bin", "music": "mi_fw1.bin", "loader": "mi_fwldr.bin"},
            entry=272, typeoff=0x10C, listcopy=0x6938, ramtest=(0x10000, 0x22C00), pattern_rom=0x0E7648,
            ramflag=0x03002738, errcnt=0x03001259, errbuf=0x03001260, unkid=None),
}
LOADER_REC = bytes([0x7F, 0x2F, 0x00, 0x02, 0x0C, 0x00])


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def s32(b, o):
    return struct.unpack_from("<i", b, o)[0]


def cstr(b, o, n=0x100):
    raw = b[o:o + n].split(b"\0", 1)[0]
    try:
        return raw.decode("shift_jis")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class PlayYanModel:
    def __init__(self, core, rom_path, sd_root, events_path=None, opts=None):
        self.c = core
        self.fam = core.family
        self.cfg = ROMCFG[self.fam]
        self.rom = open(rom_path, "rb").read()[:0x100000]
        self.opts = dict(update_parts=0, video_fps=None, combine=False, garage_force=True,
                         startup_status=0, audio_lead=0.12, music_lead=0.10)
        self.opts.update(opts or {})
        video_ext = (".asf",) if self.fam == 1 else (".asf", ".mp4")
        self.sd = media.SDCard(sd_root, video_ext)
        self.ev = open(events_path, "w") if events_path else None
        self.imgs = {k: open(os.path.join(FW, v), "rb").read() for k, v in self.cfg["imgs"].items()}
        self.running = None          # which firmware image is running
        self.reset_since_kick = True
        self.sleeping = False
        self.volume = 56
        self.bright = 0x101
        self.pos_ind = True
        self.video = None
        self.music = None
        self.thumb_slot = 0
        self.frame_slot = 0
        self.audio_slot = 0
        self.keyfile = None
        self.cmd_log = []
        self.rec_log = []
        self.ff080 = []
        self.kicks = []
        self.verify = []
        self.errors = []
        self.garage = []
        self.anomalies = 0
        self.last_doorbell = None
        core.handler = self.on_event

    # ------------------------------------------------------------------ logging
    def log(self, kind, **kw):
        kw.update(kind=kind, frame=self.c.frame(), cyc=self.c.cycles())
        if self.ev:
            self.ev.write(json.dumps(kw) + "\n")
        return kw

    def note(self, s):
        self.c.note(s)

    def post(self, rec, delay=REPLY, why=""):
        rec = list(rec) + [0] * (8 - len(rec))
        self.rec_log.append((self.c.frame(), rec[0]))
        if rec[0] != 0x80001000:
            self.log("post", rec=["%08X" % x for x in rec], why=why)
            self.note("POST %s %s" % (" ".join("%08X" % x for x in rec), why))
        self.c.post(rec, delay)

    # ------------------------------------------------------------------ events
    def on_event(self, ev, a, b, c):
        name = EV.get(ev)
        if name == "RESET_ASSERT":
            self.running = None
            self.reset_since_kick = True
            self.video = self.music = None
            self.log("reset")
        elif name == "W32":
            self.on_w32(a, b)
        elif name == "DOORBELL":
            self.on_command(a, b)
        elif name == "CTRL":
            self.log("ctrl_bit4", old=a, new=b)
        elif name == "BOOTREG":
            self.log("bootreg", value=a)
        elif name == "ANOMALY":
            self.anomalies += 1

    def on_w32(self, addr, value):
        if addr == 0xFF080:
            self.ff080.append(value)
            self.log("sync_word", value=value, ready_polls=self.c.params[0])
        elif addr == 0x10000400:
            self.on_kick(value)

    def on_kick(self, value):
        if value != 0xA5A50032:
            self.log("kick_bad", value=value)
        if self.sleeping and not self.reset_since_kick:
            self.sleeping = False
            self.log("wake")
            self.post([0x80010001, 0], STARTUP, "wake kick")
            return
        img = self.identify()
        self.kicks.append(img)
        self.running = img["image"]
        self.reset_since_kick = False
        self.sleeping = False
        st = self.opts["startup_status"]
        self.post([0x80000100, st], STARTUP, "startup after %s" % img["image"])

    def identify(self):
        """Which ROM image is in VC memory, byte-exact?"""
        res = {}
        vc = self.c.mem8
        for k, img in self.imgs.items():
            if k == "loader":
                exp = [(0, img[:0x200]), (0x200, LOADER_REC), (0xC0200, img[0x200:])]
            else:
                exp = [(0, img)]
            ok = True
            first_bad = None
            for base, data in exp:
                got = bytes(vc[base:base + len(data)])
                if got != data:
                    ok = False
                    d = np.frombuffer(got, np.uint8) != np.frombuffer(data, np.uint8)
                    first_bad = base + int(np.argmax(d))
                    break
            res[k] = (ok, first_bad)
        hit = [k for k, (ok, _) in res.items() if ok]
        # the loader's 6-byte record makes fw-at-0 checks fail for loader and vice versa; pick exact
        image = hit[0] if hit else "unknown"
        out = dict(image=image, exact=bool(hit), detail={k: v[1] for k, v in res.items()})
        if image == "loader":
            out["vc200_original"] = self.imgs["loader"][0x200:0x206].hex()
        self.verify.append(out)
        self.log("kick", **out)
        self.note("KICK: VC image = %s (byte-exact=%s) %s" % (image, bool(hit), out["detail"]))
        return out

    def check_ramtest(self):
        lo, hi = self.cfg["ramtest"]
        pat = self.rom[self.cfg["pattern_rom"]:self.cfg["pattern_rom"] + 0x1FF]
        exp = (pat * ((hi - lo) // len(pat) + 2))[:hi - lo]
        got = bytes(self.c.mem8[lo:hi])
        flag = self.c.retro.read(self.cfg["ramflag"], 1)[0]
        r = dict(range="%X-%X" % (lo, hi), pattern_match=(got == exp), gba_flag=flag)
        if got != exp:
            d = np.frombuffer(got, np.uint8) != np.frombuffer(exp, np.uint8)
            r["first_mismatch"] = lo + int(np.argmax(d))
        self.log("ramtest", **r)
        return r

    # ------------------------------------------------------------------ commands
    def on_command(self, nbytes, nwrites):
        raw = bytes(self.c.mem8[0xFF020:0xFF020 + 0x220])
        cid = u32(raw, 0)
        name = CMD_NAME.get(cid, "?")
        exp = CMD_LEN.get(cid)
        args = {}
        if cid in NAME_OFF:
            args["name"] = cstr(raw, NAME_OFF[cid])
        words = [u32(raw, i) for i in range(4, min(max(nbytes, 4), 0x40), 4)]
        self.cmd_log.append((self.c.frame(), cid, nbytes))
        rec = self.log("cmd", id="%X" % cid, cname=name, nbytes=nbytes, writes=nwrites, expected_len=exp,
                       len_ok=(exp == nbytes), words=["%X" % w for w in words[:8]], **args)
        self.note("CMD %X %s len=%d (expect %s) %s %s" % (cid, name, nbytes, exp, " ".join("%X" % w for w in words[:6]),
                                                          args.get("name", "")))
        fn = getattr(self, "cmd_%x" % cid, None)
        if fn is None:
            self.note("CMD %X unhandled" % cid)
            return
        fn(raw)

    # firmware management
    def cmd_8000(self, raw):
        if not getattr(self, "ramtest_checked", False):
            self.ramtest_checked = True       # first 0x8000 follows the boot RAM test
            self.check_ramtest()
        self.post([0x40008000, 0, self.opts["update_parts"]])

    def cmd_8001(self, raw):
        self.post([0x40008001, 0])

    def cmd_800000(self, raw):
        self.video = self.music = None
        self.post([0x40800000, 0])

    def cmd_10000(self, raw):
        self.sleeping = True
        self.reset_since_kick = False
        self.log("sleep")

    # settings
    def cmd_300(self, raw):
        self.bright = u32(raw, 4)

    def cmd_400(self, raw):
        pass

    def cmd_906(self, raw):
        self.pos_ind = bool(u32(raw, 4))

    def cmd_b00(self, raw):
        self.volume = u32(raw, 4)

    def cmd_d00(self, raw):
        pass

    def cmd_d01(self, raw):
        pass

    # lists
    def write_list(self, entries):
        es = self.cfg["entry"]
        n = min(len(entries), 99)
        buf = bytearray(8 + 99 * es)
        struct.pack_into("<II", buf, 0, 0, n)
        for i, (nm, typ) in enumerate(entries[:n]):
            o = 8 + i * es
            b = nm.encode("shift_jis", "replace")[:0xFF]
            buf[o:o + len(b)] = b
            buf[o + self.cfg["typeoff"]] = typ
        self.c.vc_write(LIST_ADDR, buf)
        return n

    def cmd_200(self, raw):
        typ = raw[4]
        if typ == 1:
            entries = self.sd.video_list()
        else:
            entries = self.sd.music_list()
        n = self.write_list(entries)
        self.log("list", type=typ, n=n, names=[e[0] for e in entries[:99]], cwd="/".join(self.sd.cwd))
        self.post([0x40000200, LIST_ADDR], why="list type %d, %d entries" % (typ, n))

    def cmd_201(self, raw):
        nm = cstr(raw, 4)
        st = self.sd.chdir(nm)
        self.post([0x40000201, st], why="chdir %r -> /%s" % (nm, "/".join(self.sd.cwd)))

    # thumbnails, ID3
    def cmd_500(self, raw):
        w, h = u32(raw, 4), u32(raw, 8)
        nm = cstr(raw, 0x14)
        p = self.sd.resolve(nm)
        if not p or not w or not h or w * h > 240 * 160:
            self.post([0x40000500, 1, 0, 0], why="no file %r" % nm)
            return
        px = media.first_frame(p, w, h)
        base = nm.split("\\")[-1]
        if base in GARAGE:
            idx, target = GARAGE[base]
            hv = media.garage_hash(px)
            g = dict(file=nm, index=idx, table_hash="%08X" % target, decoded_hash="%08X" % hv, match=hv == target)
            if hv != target and self.opts["garage_force"]:
                fx = media.force_garage_hash(px, target)
                if fx is not None:
                    px = fx
                    g["forced"] = True
            self.garage.append(g)
            self.log("garage_thumb", **g)
        slot = THUMB_ADDR + (self.thumb_slot % 8) * 0x2000
        self.thumb_slot += 1
        self.c.vc_write(slot, px.astype("<u2").tobytes())
        dur = int(media.duration(p) * 1000)
        self.post([0x40000500, 0, slot, dur], why="thumb %dx%d %s dur=%dms" % (w, h, nm, dur))

    def cmd_600(self, raw):
        nm = cstr(raw, 4)
        p = self.sd.resolve(nm)
        if not p:
            self.post([0x40000600, 0], why="no file")
            return
        t = {}
        for k, v in media.tags(p).items():
            try:  # ffprobe hands Shift-JIS tag bytes back as latin-1
                v = v.encode("latin-1").decode("shift_jis")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            t[k] = v
        blk = bytearray(0x8C)
        title = (t.get("title") or os.path.splitext(os.path.basename(p))[0]).encode("shift_jis", "replace")[:0x40]
        artist = (t.get("artist") or "").encode("shift_jis", "replace")[:0x42]
        blk[4:4 + len(title)] = title
        blk[0x45:0x45 + len(artist)] = artist
        struct.pack_into("<I", blk, 0x88, int(round(media.duration(p))))
        self.c.vc_write(ID3_ADDR, blk)
        self.post([0x40000600, ID3_ADDR], why="id3 %s len=%ds" % (nm, int(round(media.duration(p)))))

    # Micro key file
    def cmd_3000(self, raw):
        nm = cstr(raw, 4)
        p = os.path.join(self.sd.root, nm)
        if os.path.isfile(p):
            self.keyfile = open(p, "rb").read()
            self.keypos = 0
            self.post([0x40003000, 0], why="open %s (%d bytes)" % (nm, len(self.keyfile)))
        else:
            self.keyfile = None
            self.post([0x40003000, 1], why="open %s: absent" % nm)

    def cmd_3001(self, raw):
        mx = u32(raw, 4)
        if self.keyfile is None:
            self.post([0x40003001, 1])
            return
        data = self.keyfile[self.keypos:self.keypos + mx]
        self.keypos += len(data)
        self.c.vc_write(KEY_ADDR, data + b"\0" * 4)
        self.post([0x40003001, 0, len(data), KEY_ADDR], why="read %d bytes" % len(data))

    def cmd_3002(self, raw):
        self.keypos = u32(raw, 8)
        self.post([0x40003002, 0])

    def cmd_3003(self, raw):
        self.post([0x40003003, 0])

    def cmd_4000(self, raw):
        self.log("autoplay", mode=u32(raw, 4), n1=u32(raw, 8), n2=u32(raw, 0xC), p4=u32(raw, 0x10))
        self.post([0x40004000, 0])

    def cmd_4001(self, raw):
        self.c.vc_write(ORDER_ADDR, bytes(0xC6))
        self.post([0x40004001, 0, 1, 0, 0, ORDER_ADDR])

    # ------------------------------------------------------------------ sound effects
    def cmd_2000(self, raw):
        sid, vol = u32(raw, 4), u32(raw, 8)
        rate = 8192
        n = int(rate * 0.05) // 16 * 16
        t = np.arange(n) / rate
        tone = (np.sin(2 * np.pi * (600 + 100 * (sid % 8)) * t) * (vol / 63) * 100).astype(np.int8)
        self.post_audio(tone, tone, why="sfx %d vol %d" % (sid, vol))

    def post_audio(self, left, right, ms=0, extra=None, why=""):
        n = len(left)
        per = ((n * 2 >> 3) + 3 & ~3) * 4  # what the GBA reads per channel for 2n bytes
        blk = np.zeros(per * 2, np.int8)
        blk[:n] = right            # first half -> FIFO A -> right speaker
        blk[per:per + n] = left
        a = AUDIO_ADDR + (self.audio_slot % 32) * 0x500
        self.audio_slot += 1
        self.c.vc_write(a, blk.tobytes())
        rec = [0x80001000, 0, 0, a, 2 * n, 0, ms, 0]
        if extra:
            for k, v in extra.items():
                rec[k] = v
        self.c.post(rec, 2000)

    # ------------------------------------------------------------------ video
    def cmd_700(self, raw):
        w, h = u32(raw, 4), u32(raw, 8)
        nm = cstr(raw, 0xC)
        p = self.sd.resolve(nm)
        if not p:
            self.post([0x40000700, 1], why="no file")
            return
        frames = media.video_frames(p, w, h)
        fps = self.opts["video_fps"] or media.video_fps(p)
        aud = media.audio_s8(p, 8192)
        self.video = dict(name=nm, w=w, h=h, frames=frames, fps=fps, audio=aud, pos=0.0, apos=0,
                          last=None, paused=False, next_frame=0, dur=len(frames) / fps, nframes=0, step=0)
        self.log("video_start", name=nm, w=w, h=h, fps=fps, frames=len(frames))
        self.post([0x40000700, 0], why="video start %dx%d %s" % (w, h, nm))

    def cmd_701(self, raw):
        self.video = None
        self.post([0x40000701, 0], why="video stop")

    def cmd_902(self, raw):
        if self.video:
            self.video["paused"] = True
        if self.music:
            self.music["paused"] = True

    def cmd_904(self, raw):
        for s in (self.video, self.music):
            if s:
                s["paused"] = False
                s["last"] = None

    def cmd_903(self, raw):
        n = u32(raw, 4)
        v = self.video
        if v:
            v["pos"] = min(v["pos"] + n / v["fps"], v["dur"])
            self.post_frame(force=True)

    def cmd_905(self, raw):
        val = s32(raw, 4)
        if self.video:
            v = self.video
            before = v["pos"]
            v["pos"] = max(0.0, min(v["pos"] + val, v["dur"] - 1.0 / v["fps"]))
            v["apos"] = int(v["pos"] * 8192)
            v["next_frame"] = int(v["pos"] * v["fps"])
            self.log("seek_video", value=val, before=before, after=v["pos"], paused=v["paused"])
            self.post_frame(force=True)
        elif self.music:
            m = self.music
            before = m["pos"]
            m["pos"] = max(0.0, min(val / 0x6400 * m["dur"], m["dur"]))
            m["apos"] = int(m["pos"] * 16384)
            self.log("seek_music", value=val, before=before, after=m["pos"])
        else:
            self.log("seek_idle", value=val)

    def frame_pixels(self, v, i):
        f = v["frames"][min(i, len(v["frames"]) - 1)]
        if self.bright != 0x101:
            g = self.bright / 0x100
            r = np.minimum((f & 31) * g, 31).astype(np.uint16)
            gg = np.minimum(((f >> 5) & 31) * g, 31).astype(np.uint16)
            b = np.minimum(((f >> 10) & 31) * g, 31).astype(np.uint16)
            f = r | gg << 5 | b << 10
        return f

    def post_frame(self, force=False):
        v = self.video
        i = int(v["pos"] * v["fps"] + 1e-6)
        if i >= len(v["frames"]):
            return
        a = FRAME_ADDR + (self.frame_slot % 4) * 0x13000
        self.frame_slot += 1
        self.c.vc_write(a, self.frame_pixels(v, i).astype("<u2").tobytes())
        ms = int(i * 1000 / v["fps"])
        self.c.post([0x80001000, a, v["w"] * v["h"] * 2, 0, 0, 0, ms, 0], 2000)
        v["nframes"] += 1
        v["next_frame"] = i + 1

    def tick_video(self, now):
        v = self.video
        if v["paused"] or self.sleeping:
            v["last"] = now
            return
        if v["last"] is not None:
            v["pos"] += (now - v["last"]) / CPU_HZ
        v["last"] = now
        if v["pos"] >= v["dur"]:
            self.video = None
            self.post([0x40000701, 0], why="end of video")
            return
        # audio: keep the posted position ahead of the play position
        target = int((v["pos"] + self.opts["audio_lead"]) * 8192)
        aud = v["audio"]
        while v["apos"] < target and v["apos"] < len(aud):
            n = min(272, len(aud) - v["apos"])
            seg = aud[v["apos"]:v["apos"] + n]
            scale = self.volume / 56
            l = (seg[:, 0] * scale).astype(np.int8)
            r = (seg[:, 1] * scale).astype(np.int8)
            self.post_audio(l, r)
            v["apos"] += n
        # frames at the source rate, at most one pending
        i = int(v["pos"] * v["fps"])
        if i >= v["next_frame"] and self.c.queued() < 6:
            self.post_frame()

    # ------------------------------------------------------------------ music
    def cmd_800(self, raw):
        nm = cstr(raw, 4)
        p = self.sd.resolve(nm)
        if not p:
            self.post([0x40000800, 1], why="no file")
            return
        aud = media.audio_s8(p, 16384)
        self.music = dict(name=nm, audio=aud, pos=0.0, apos=0, last=None, paused=False,
                          dur=len(aud) / 16384, next_ind=0)
        self.log("music_start", name=nm, dur=self.music["dur"])
        self.post([0x40000800, 0], why="music start %s" % nm)

    def cmd_801(self, raw):
        self.music = None
        self.post([0x40000801, 0], why="music stop")

    def tick_music(self, now):
        m = self.music
        if m["paused"]:
            m["last"] = now
            return
        if m["last"] is not None:
            m["pos"] += (now - m["last"]) / CPU_HZ
        m["last"] = now
        if m["pos"] >= m["dur"]:
            self.music = None
            self.post([0x40000801, 0], why="end of song")
            return
        target = int((m["pos"] + self.opts["music_lead"]) * 16384)
        aud = m["audio"]
        while m["apos"] < target and m["apos"] < len(aud):
            n = min(576, len(aud) - m["apos"])
            seg = aud[m["apos"]:m["apos"] + n]
            self.post_audio(seg[:, 0], seg[:, 1])
            m["apos"] += n
        if self.pos_ind and now >= m["next_ind"]:
            m["next_ind"] = now + 15 * FRAME_CYCLES
            tb = int(min(m["pos"] / m["dur"], 1.0) * 0x6400) if m["dur"] else 0
            self.c.post([0x80001000, 0, 0, 0, 0, tb, int(m["pos"]), 0], 2000)

    # ------------------------------------------------------------------ per frame
    def tick(self):
        now = self.c.cycles()
        if self.video:
            self.tick_video(now)
        if self.music:
            self.tick_music(now)
        self.poll_errors()

    def poll_errors(self):
        r = self.c.retro
        cnt = r.read(self.cfg["errcnt"], 1)[0]
        if cnt:
            codes = list(r.read(self.cfg["errbuf"], min(cnt, 16)))
            unk = r.u32(self.cfg["unkid"]) if self.cfg["unkid"] else None
            self.errors.append((self.c.frame(), codes))
            self.log("gba_error", codes=codes, unknown_id="%08X" % unk if unk is not None else None)
            self.note("GBA ERROR LOG %s" % codes)
            r.write(self.cfg["errcnt"], b"\0")  # drain (the ROM never pops it)
