#!/usr/bin/env python3
"""Run a Play-Yan family ROM on the patched mGBA core (build.sh) with an emulated cartridge.

  python3 run.py ROM SCENARIO [--lle] [--tag NAME] [--sd DIR|IMG] [--param READY_POLLS=37 ...] [--opt key=value ...]

ROM: orig | micro | nmp (from $PY_ROMS) or a path. SCENARIO: a function of scenarios.py (boot, boot_nmp,
music, video, garage, ...).  --lle runs the cartridge's real firmware: the VC01 core (../vc01) for the
Play-Yan and Micro, the C54x core (../c54x) for the MP3 Player.  Without it a high-level Python model
answers the commands (pyan_model.py / nmp_model.py, media decoded by ffmpeg).
SD card: --sd, else $PLAYYAN_WORK/sd/<orig|micro|nmp> (make_sd.py builds them from $PY_MEDIA).
Outputs in $PLAYYAN_WORK/runs: traces/<tag>.txt (bus trace), out/<tag>.events.jsonl,
out/<tag>.summary.json, shots/<tag>_*.png, out/<tag>.wav (GBA audio, with --wav).
"""
import argparse
import json
import os
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, '..'))
import workdir  # noqa: E402
sys.path.append(workdir.VC01)
sys.path.append(workdir.C54X)

from pyan_core import PYanCore  # noqa: E402
import scenarios  # noqa: E402

ROMS = ("orig", "micro", "nmp")
OUT = os.path.join(workdir.WORK, "runs")     # traces/, out/, shots/ (capture.py redirects this)


class Runner:
    def __init__(self, rom, tag, params=None, opts=None, sd=None, lle=False):
        self.rom_key = rom if rom in ROMS else "custom"
        path = workdir.rom(rom)
        self.tag = tag
        for d in ("traces", "out", "shots"):
            os.makedirs(os.path.join(OUT, d), exist_ok=True)
        self.core = PYanCore(path, trace=os.path.join(OUT, "traces", tag + ".txt"), params=params)
        sd = sd or os.path.join(workdir.WORK, "sd", self.rom_key if self.rom_key in ROMS else "orig")
        if not os.path.exists(sd):
            sys.exit("SD card %s not found: build it with make_sd.py (PY_MEDIA) or give --sd" % sd)
        ev = os.path.join(OUT, "out", tag + ".events.jsonl")
        if lle and self.core.family == 3:
            # the real C54x firmware on our C54x core + board model (../c54x)
            from nmp_lle_model import NMPLLEModel
            self.model = NMPLLEModel(self.core, path, sd, ev, opts)
        elif lle and self.core.family != 3:
            # the real VC01 firmware on the VC01 emulator (../vc01), instead of the Python model
            from lle_model import LLEModel
            self.model = LLEModel(self.core, path, sd, ev, opts)
        elif self.core.family == 3:
            from nmp_model import NMPModel
            self.model = NMPModel(self.core, path, sd, ev, opts)
        else:
            from pyan_model import PlayYanModel
            self.model = PlayYanModel(self.core, path, sd, ev, opts)
        self.buttons = self.core.retro.buttons
        self.core.run(1)  # the core publishes its memory map on the first frame
        self.shots = []
        self.marks = {}

    # ---- primitives for scenarios
    def step(self, n=1):
        for _ in range(n):
            self.model.tick()
            self.core.run(1)

    def press(self, btn, hold=3, after=10):
        for b in btn.split("+"):
            self.buttons.add(b)
        self.step(hold)
        for b in btn.split("+"):
            self.buttons.discard(b)
        self.step(after)

    def hold(self, btn, n):
        for b in btn.split("+"):
            self.buttons.add(b)
        self.step(n)
        for b in btn.split("+"):
            self.buttons.discard(b)

    def until(self, pred, timeout=600, what=""):
        for i in range(timeout):
            if pred():
                return i
            self.step(1)
        self.core.note("TIMEOUT waiting for %s" % what)
        print("  timeout waiting for", what, "at frame", self.core.frame())
        return None

    def until_cmd(self, cid, timeout=600, after=None):
        start = len(self.model.cmd_log) if after is None else after
        return self.until(lambda: any(c[1] == cid for c in self.model.cmd_log[start:]), timeout, "cmd %X" % cid)

    def until_rec(self, rid, timeout=600, after=None):
        start = len(self.model.rec_log) if after is None else after
        return self.until(lambda: any(r[1] == rid for r in self.model.rec_log[start:]), timeout, "rec %X" % rid)

    def shot(self, name):
        p = os.path.join(OUT, "shots", "%s_%s.png" % (self.tag, name))
        self.core.screenshot(p)
        self.shots.append(p)
        self.core.note("SCREENSHOT %s" % p)
        return p

    def nrec(self):
        return len(self.model.rec_log)

    def ncmd(self):
        return len(self.model.cmd_log)

    def mark(self, name):
        self.marks[name] = self.core.frame()
        self.core.note("MARK %s" % name)

    def summary(self):
        m = self.model
        s = dict(tag=self.tag, rom=self.rom_key, frames=self.core.frame(), stats=self.core.stats(),
                 kicks=getattr(m, "verify", None), ff080=getattr(m, "ff080", None),
                 commands=[("%X" % c, n) for _, c, n in m.cmd_log][:400],
                 gba_errors=m.errors, garage=getattr(m, "garage", None), shots=self.shots, marks=self.marks)
        if hasattr(m, "summary_extra"):
            s.update(m.summary_extra())
        return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("scenario")
    ap.add_argument("--tag")
    ap.add_argument("--param", action="append", default=[])
    ap.add_argument("--opt", action="append", default=[])
    ap.add_argument("--sd")
    ap.add_argument("--wav", action="store_true")
    ap.add_argument("--lle", action="store_true", help="run the real cartridge firmware (VC01: ../vc01, NMP C54x: ../c54x) instead of the HLE model")
    a = ap.parse_args()
    params = {k: int(v, 0) for k, v in (p.split("=") for p in a.param)}
    opts = {}
    for o in a.opt:
        k, v = o.split("=", 1)
        try:
            v = json.loads(v)
        except ValueError:
            pass
        opts[k] = v
    tag = a.tag or "%s_%s%s" % (a.rom, a.scenario, "_lle" if a.lle else "")
    r = Runner(a.rom, tag, params, opts, a.sd, lle=a.lle)
    if a.wav:
        r.core.retro.keep_audio = True
    t = time.time()
    getattr(scenarios, a.scenario)(r)
    dt = time.time() - t
    r.core.flush_trace()
    s = r.summary()
    s["wall_s"] = round(dt, 1)
    json.dump(s, open(os.path.join(OUT, "out", tag + ".summary.json"), "w"), indent=1, default=str)
    if a.wav and r.core.retro.audio:
        w = wave.open(os.path.join(OUT, "out", tag + ".wav"), "wb")
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(int(r.core.retro.av.rate))
        w.writeframes(bytes(r.core.retro.audio))
        w.close()
    if hasattr(r.model, "save_pcm") and getattr(r.model, "pcm", None):
        n = r.model.save_pcm(os.path.join(OUT, "out", tag + ".headphone.wav"))
        print("  headphone PCM: %d frames -> out/%s.headphone.wav" % (n, tag))
    if hasattr(r.model, "save_gba_blocks"):
        k = r.model.save_gba_blocks(os.path.join(OUT, "out", tag + ".gbablocks.npz"))
        if k:
            print("  %d GBA audio blocks -> out/%s.gbablocks.npz" % (k, tag))
    print("%s: %d frames in %.1fs; stats %s" % (tag, r.core.frame(), dt, r.core.stats()))
    print("  kicks:", [k.get("image") if isinstance(k, dict) else k for k in (s["kicks"] or [])])
    print("  errors:", s["gba_errors"][:10])
    print("  shots:", len(r.shots))


if __name__ == "__main__":
    main()
