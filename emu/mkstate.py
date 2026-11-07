#!/usr/bin/env python3
"""Make an mGBA savestate where the player's main loop is idle in its
"decode queue full" wait loop (PC in 0x030072F0..0x03007314, main stack),
right after VX frame N has been decoded. Such a state is the seed for the
unicorn oracle (uc_decode.py): jumping to 0x03007318 decodes VX frame N+1.
The wait loop is only reached once the 4-slot queue is full, so N >= 4
(frames 0..4 come from the mGBA dumps; the oracle covers 5 onwards).

  mkstate.py N [--out states/wait_NNNNN.state] [--from states/vx_XXXXX.state]
"""
import argparse, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retro
from capture import boot_to_play, ctx_words, ctx_valid, find_checkpoint, ROM_DEFAULT, PREROLL

WAIT_LO, WAIT_HI, MAIN_SP = 0x030072F0, 0x03007318, 0x03007E98


def make(n, out=None, src=None, rom=ROM_DEFAULT):
    r = retro.Retro(rom)
    src = src or find_checkpoint(n)
    if src:
        r.run(1)
        r.load_state(open(src, "rb").read())
    else:
        boot_to_play(r)
    while True:
        r.run(1)
        if r.buttons and r.frame_no >= getattr(r, "press_until", 0):
            r.buttons = set()
        w = ctx_words(r)
        if not ctx_valid(w):
            continue
        dec = w[10] - PREROLL  # VX frames decoded so far
        if dec > n + 1:
            sys.exit(f"overshot: VX frames decoded={dec} (start state too late?)")
        if dec == n + 1:
            s = r.save_state()
            g = struct.unpack_from("<16I", s, 0x20)
            if WAIT_LO <= g[15] < WAIT_HI and g[13] == MAIN_SP:
                out = out or os.path.join(HERE, "states", f"wait_{n:05d}.state")
                os.makedirs(os.path.dirname(out), exist_ok=True)
                open(out, "wb").write(s)
                print(f"wrote {out} (emu frame {r.frame_no}, pc={g[15]:08x})")
                return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int)
    ap.add_argument("--out")
    ap.add_argument("--from", dest="src")
    a = ap.parse_args()
    make(a.n, a.out, a.src)
