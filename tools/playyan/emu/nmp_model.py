"""High-level model of the Nintendo MP3 Player cartridge DSP (protocol.md section 11).

The C side models the HPI-8 register file (CONTROL / address / data in / data out, byte-wide,
MSB at the even address, pre-increment on writes) over a word-addressed DSP memory, the tick
counter at word 0x010F, record delivery through words 0x0100-0x0107 with the Game Pak IRQ, and
the GPIO reset. This model answers commands from the mailbox at word 0x0110.

Sample byte order: the DSP word carries two signed 8-bit samples; this model puts the FIRST
sample in the LOW byte (the byte the GBA reads from the odd address), which is the GBA code's
reading (protocol.md 11.2). Option nmp_swap=True puts it in the high byte instead, for an A/B
check of the GBA output.
"""
import json
import os
import struct

import numpy as np

import media
from pyan_core import CPU_HZ, EV, FRAME_CYCLES

# DSP word addresses for payloads (below 0x8000 so the GBA's bank fold leaves them unchanged)
ENTRY_W = 0x7000
ID3_W = 0x7200
FILE_W = 0x7300
AUDIO_W = 0x7400      # 4 slots x 0x240 words
REPLY = 20000
BOOT_DELAY = 400000

NMP_CMD = {0x10: "LIST_FIRST", 0x11: "LIST_NEXT", 0x20: "CHDIR", 0x30: "AUDIO_RESET?", 0x40: "ID3", 0x50: "PLAY",
           0x51: "STOP", 0x52: "PAUSE", 0x53: "RESUME", 0x60: "SEEK", 0x70: "STREAM_ON/OFF?", 0x80: "VOLUME",
           0x200: "SFX", 0x300: "FOPEN", 0x301: "FREAD", 0x302: "FSEEK", 0x303: "FCLOSE", 0x400: "BG_PLAY",
           0x401: "BG_LEAVE", 0x500: "SLEEP", 0x501: "WAKE", 0x800: "FW_PREPARE"}
NMP_LEN = {0x10: 2, 0x11: 2, 0x20: 0x106, 0x30: 4, 0x40: 0x106, 0x50: 0x106, 0x51: 2, 0x52: 2, 0x53: 2,
           0x60: 2, 0x70: 2, 0x80: 2, 0x200: 4, 0x300: 0x106, 0x301: 2, 0x302: 4, 0x303: 2, 0x400: 0xCE,
           0x401: 2, 0x500: 2, 0x501: 2, 0x800: 2}


def fold(a):
    if 0x8000 <= a <= 0xFFFF:
        return a | 0x10000
    for lo in (0x10000, 0x20000, 0x30000, 0x40000):
        if lo <= a < lo + 0x8000:
            return a & 0xFFFF
    return a


class NMPModel:
    def __init__(self, core, rom_path, sd_root, events_path=None, opts=None):
        self.c = core
        self.rom = open(rom_path, "rb").read()[:0x100000]
        self.opts = dict(nmp_swap=False, headphones=None, music_lead=0.10)
        self.opts.update(opts or {})
        self.sd = media.SDCard(sd_root)
        self.ev = open(events_path, "w") if events_path else None
        self.cmd_log, self.rec_log, self.errors, self.verify, self.mailbox_lens = [], [], [], [], []
        self.running = False
        self.booted = False
        self.listing = None
        self.music = None
        self.volume = 0x2E
        self.stream_on = True
        self.audio_slot = 0
        self.last_unknown = None
        self.entries_sent = []
        self.wd_reads = []
        core.handler = self.on_event

    def log(self, kind, **kw):
        kw.update(kind=kind, frame=self.c.frame(), cyc=self.c.cycles())
        if self.ev:
            self.ev.write(json.dumps(kw) + "\n")

    def note(self, s):
        self.c.note(s)

    def post(self, w, delay=REPLY, why=""):
        """w: dict(id, st, A, B, C) -> 8 words (A/B/C low word first)."""
        A, B, Cc = w.get("A", 0), w.get("B", 0), w.get("C", 0)
        rec = [w["id"], w.get("st", 0), A & 0xFFFF, A >> 16, B & 0xFFFF, B >> 16, Cc & 0xFFFF, Cc >> 16]
        self.rec_log.append((self.c.frame(), w["id"]))
        if w["id"] != 0x8100:
            self.log("post", rec=["%04X" % x for x in rec], why=why)
            self.note("POST %s %s" % (" ".join("%04X" % x for x in rec), why))
        self.c.post(rec, delay)

    # ------------------------------------------------------------------ events
    def on_event(self, ev, a, b, c):
        name = EV.get(ev)
        if name == "RESET_ASSERT":
            self.running = False
            self.music = None
            self.log("reset")
        elif name == "NMP_CTRL":
            if a == 0x08 and not self.running:
                self.start()
        elif name == "NMP_DOORBELL":
            self.on_command()

    def boot_table(self):
        w = np.frombuffer(self.rom[0xA8F34:0xA8F34 + 0xF730 * 2], "<u2")
        assert w[0] == 0x08AA
        entry = (int(w[3]) << 16) | int(w[4])
        i, recs = 5, []
        while True:
            n = int(w[i])
            if n == 0:
                break
            addr = (int(w[i + 1]) << 16) | int(w[i + 2])
            recs.append((addr, w[i + 3:i + 3 + n]))
            i += 3 + n
        return entry, recs

    def start(self):
        """CONTROL 0x08 after reset: the firmware entry point has been written. Verify the
        uploaded image word-exact against the boot table (with the GBA's bank fold)."""
        entry, recs = self.boot_table()
        mem = self.c.mem16
        bad = []
        total = 0
        for addr, data in recs:
            a = fold(addr)
            got = mem[a:a + len(data)]
            total += len(data)
            if not np.array_equal(got, data):
                k = int(np.argmax(got != data))
                bad.append(("%05X" % addr, "%05X" % (a + k), int(got[k]), int(data[k])))
        ent = (int(mem[0x7E]) << 16) | int(mem[0x7F])
        r = dict(records=len(recs), words=total, mismatched_records=len(bad), first_bad=bad[:5],
                 entry_expected="%X" % entry, entry_written="%X" % ent, exact=(not bad and ent == entry))
        self.verify.append(r)
        self.log("fw_verify", **r)
        self.note("NMP FIRMWARE: %d records, %d words, %d mismatched; entry %X (expect %X)" % (
            len(recs), total, len(bad), ent, entry))
        self.running = True
        self.post(dict(id=0x8001, st=0), BOOT_DELAY, "boot complete")
        if self.opts["headphones"] is not None:
            self.post(dict(id=0x8600, st=int(self.opts["headphones"])), BOOT_DELAY, "headphones")

    # ------------------------------------------------------------------ commands
    def wstr(self, words):
        out = []
        for x in words:
            if x == 0:
                break
            out.append(chr(int(x)))
        return "".join(out)

    def on_command(self):
        mem = self.c.mem16
        words = mem[0x110:0x110 + 0x110].copy()
        cid = int(words[0])
        name = NMP_CMD.get(cid, "?")
        args = {}
        if NMP_LEN.get(cid) == 0x106:
            args["name"] = self.wstr(words[1:0x106])
        self.cmd_log.append((self.c.frame(), cid, None))
        self.log("cmd", id="%X" % cid, cname=name, words=["%X" % x for x in words[1:8]], **args)
        self.note("NMP CMD %X %s %s %s" % (cid, name, " ".join("%X" % x for x in words[1:6]), args.get("name", "")))
        mem[0x110] = 0          # consumed: the GBA polls word 0x110 until it reads 0
        fn = getattr(self, "cmd_%x" % cid, None)
        if fn:
            fn(words)
        else:
            self.note("NMP CMD %X unhandled" % cid)

    # listing: one entry per command
    def cmd_10(self, w):
        ents = []
        for nm, typ in self.sd.music_list():
            if nm == "..":
                continue
            ents.append((nm, 1 if typ == 0 else 2))
        self.listing = ents
        self.list_pos = 0
        self.send_entry(0x4010)

    def cmd_11(self, w):
        self.send_entry(0x4011)

    def send_entry(self, rid):
        if not self.listing or self.list_pos >= len(self.listing):
            self.post(dict(id=rid, st=1), why="end of list")
            return
        nm, typ = self.listing[self.list_pos]
        self.list_pos += 1
        buf = np.zeros(0x108, np.uint16)
        cs = [ord(ch) for ch in nm][:0x105]
        buf[:len(cs)] = cs
        buf[0x20C // 2] = typ
        self.c.dsp_write(ENTRY_W, buf)
        self.entries_sent.append(nm)
        self.post(dict(id=rid, st=0, A=ENTRY_W), why="entry %r type %d" % (nm, typ))

    def cmd_20(self, w):
        nm = self.wstr(w[1:0x106])
        if nm == "\\":
            self.sd.cwd = []
            st = 0
        elif nm == ".":
            st = 0
        else:
            st = self.sd.chdir(nm)
        self.post(dict(id=0x4020, st=st), why="chdir %r -> /%s" % (nm, "/".join(self.sd.cwd)))

    def cmd_30(self, w):
        pass

    def cmd_40(self, w):
        nm = self.wstr(w[1:0x106])
        p = self.sd.resolve(nm)
        if not p:
            self.post(dict(id=0x4040, st=1, A=0), why="no file")
            return
        t = {}
        for k, v in media.tags(p).items():
            try:
                v = v.encode("latin-1").decode("shift_jis")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            t[k] = v
        buf = np.zeros(0x88, np.uint16)
        title = [ord(ch) for ch in (t.get("title") or os.path.splitext(os.path.basename(p))[0])][:0x41]
        artist = [ord(ch) for ch in (t.get("artist") or "")][:0x41]
        buf[2:2 + len(title)] = title
        buf[0x44:0x44 + len(artist)] = artist
        secs = int(round(media.duration(p)))
        buf[0x86] = secs & 0xFFFF
        buf[0x87] = secs >> 16
        self.c.dsp_write(ID3_W, buf)
        self.post(dict(id=0x4040, st=0, A=ID3_W), why="id3 %s %ds" % (nm, secs))

    def cmd_50(self, w):
        nm = self.wstr(w[1:0x106])
        p = self.sd.resolve(nm)
        if not p:
            self.post(dict(id=0x4050, st=1), why="no file")
            return
        aud = media.audio_s8(p, 16384)
        self.music = dict(name=nm, audio=aud, pos=0.0, apos=0, last=None, paused=False, dur=len(aud) / 16384,
                          next_ind=0)
        self.log("music_start", name=nm, dur=self.music["dur"])
        self.post(dict(id=0x4050, st=0), why="play %s" % nm)

    def cmd_51(self, w):
        self.music = None
        self.post(dict(id=0x4051, st=0), why="stop")

    def cmd_52(self, w):
        if self.music:
            self.music["paused"] = True

    def cmd_53(self, w):
        if self.music:
            self.music["paused"] = False
            self.music["last"] = None

    def cmd_60(self, w):
        val = int(w[1])
        if self.music:
            m = self.music
            before = m["pos"]
            m["pos"] = max(0.0, min(val / 0x6400 * m["dur"], m["dur"]))
            m["apos"] = int(m["pos"] * 16384)
            self.log("seek_music", value=val, before=before, after=m["pos"])

    def cmd_70(self, w):
        self.stream_on = bool(w[1])

    def cmd_80(self, w):
        self.volume = int(w[1])

    def cmd_200(self, w):
        sid, vol = int(w[1]), int(w[2])
        n = int(16384 * 0.05) // 16 * 16
        t = np.arange(n) / 16384
        tone = (np.sin(2 * np.pi * (600 + 100 * (sid % 8)) * t) * (vol / 63) * 100).astype(np.int8)
        self.post_audio(tone, tone)

    def cmd_300(self, w):
        nm = self.wstr(w[1:0x106])
        p = os.path.join(self.sd.root, nm)
        if os.path.isfile(p):
            self.fdata = open(p, "rb").read()
            self.fpos = 0
            self.post(dict(id=0x4300, st=0), why="open %s" % nm)
        else:
            self.fdata = None
            self.post(dict(id=0x4300, st=1), why="open %s: absent" % nm)

    def cmd_301(self, w):
        n = int(w[1])
        if self.fdata is None:
            self.post(dict(id=0x4301, st=1))
            return
        data = self.fdata[self.fpos:self.fpos + n]
        self.fpos += len(data)
        b = data + b"\0"
        words = [b[i] | (b[i + 1] << 8) if i + 1 < len(b) else b[i] for i in range(0, len(b), 2)]
        self.c.dsp_write(FILE_W, words)
        self.post(dict(id=0x4301, st=0, A=FILE_W, B=len(data)))

    def cmd_302(self, w):
        self.fpos = int(w[2]) | (int(w[3]) << 16)
        self.post(dict(id=0x4302, st=0))

    def cmd_303(self, w):
        self.post(dict(id=0x4303, st=0))

    def cmd_400(self, w):
        self.log("bg_play", mode=int(w[1]), n1=int(w[2]), n2=int(w[3]), idx=int(w[4]))
        self.post(dict(id=0x4400, st=0))

    def cmd_401(self, w):
        self.c.dsp_write(FILE_W, [0, 0, 0, 0])
        self.post(dict(id=0x4401, st=0, A=1, B=0, C=FILE_W))

    def cmd_500(self, w):
        self.log("sleep")

    def cmd_501(self, w):
        self.post(dict(id=0x8501, st=0), why="wake")

    def cmd_800(self, w):
        self.post(dict(id=0x4800, st=0))

    # ------------------------------------------------------------------ audio
    def post_audio(self, left, right):
        n = len(left)
        size = 2 * n
        per_words = (((size >> 3) + 3) & ~3) * 2      # halfwords per channel the GBA reads
        blk = np.zeros(per_words * 2 * 2, np.int8)    # bytes
        blk[:n] = right                               # channel 1 -> FIFO A -> right speaker
        blk[per_words * 2:per_words * 2 + n] = left
        b = blk.view(np.uint8).astype(np.uint16)
        if self.opts["nmp_swap"]:
            words = (b[0::2] << 8) | b[1::2]          # first sample in the high (even-address) byte
        else:
            words = b[0::2] | (b[1::2] << 8)          # first sample in the low (odd-address) byte
        a = AUDIO_W + (self.audio_slot % 4) * 0x240
        self.audio_slot += 1
        self.c.dsp_write(a, words)
        self.c.post([0x8100, size, a & 0xFFFF, a >> 16, 0, 0, 0, 0], 2000)

    def tick(self):
        now = self.c.cycles()
        m = self.music
        if m and not m["paused"]:
            if m["last"] is not None:
                m["pos"] += (now - m["last"]) / CPU_HZ
            m["last"] = now
            if m["pos"] >= m["dur"]:
                self.music = None
                self.post(dict(id=0x4051, st=0), why="end of song")
            else:
                target = int((m["pos"] + self.opts["music_lead"]) * 16384)
                aud = m["audio"]
                while self.stream_on and m["apos"] < target and m["apos"] < len(aud):
                    k = min(576, len(aud) - m["apos"])
                    seg = aud[m["apos"]:m["apos"] + k]
                    self.post_audio(seg[:, 0], seg[:, 1])
                    m["apos"] += k
                if now >= m["next_ind"]:
                    m["next_ind"] = now + 15 * FRAME_CYCLES
                    tb = int(min(m["pos"] / m["dur"], 1.0) * 0x6400)
                    self.c.post([0x8100, 0, 0, 0, tb & 0xFFFF, tb >> 16, int(m["pos"]) & 0xFFFF, int(m["pos"]) >> 16], 2000)
        elif m:
            m["last"] = now
        # the GBA keeps an unknown record ID at 0x03001080 (error 1)
        try:
            u = self.c.retro.u16(0x03001080)
        except KeyError:
            u = None
        if u and u != 0x5678 and u != self.last_unknown:  # 0x5678: low half of the 0x12345678 init value
            self.last_unknown = u
            self.errors.append((self.c.frame(), ["unknown id %X" % u]))
            self.log("gba_unknown_id", id="%X" % u)
        if not self.booted and any(r[1] == 0x8001 for r in self.rec_log) and self.c.queued() == 0:
            self.booted = True

    def summary_extra(self):
        return dict(nmp_verify=self.verify, entries=self.entries_sent)
