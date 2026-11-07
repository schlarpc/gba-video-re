#!/usr/bin/env python3
"""Ground-truth capture for the Shrek GBA Video cartridge (VX++ player).

Runs the ROM headless in the mGBA libretro core (ctypes frontend in retro.py),
presses A at the title menu ("Play"), then per emulated frame watches the
player context struct (in VRAM at 0x06013E80) and dumps:

  frames/yuv/NNNNN.yuv      decoder output slot for video frame N, exact copy
                            (0xF000 bytes: Y 256x160 stride 256, then
                            interleaved U,V 256x80 (UVUV.., 120 pairs used/row))
  frames/vram/NNNNN.bgr555  mode-3 framebuffer after frame N was displayed
                            (240x160 little-endian BGR555, 76800 bytes)
  frames/png/NNNNN.png      PNG of the above
  frames/index.csv          per-frame metadata
  audio/pcm.s8              signed 8-bit mono PCM, 65536 Hz: decoded audio
                            blocks (512 samples each) in decode order, read
                            from the DMA1->FIFO A ring 0x06015F40 (16 slots)
  audio/pcm.wav             same, as 8-bit WAV
  audio/blocks.csv          per-block metadata
  audio/core_mix.wav        libretro core's mixed stereo output (NOT bit exact)
  states/vx_NNNNN.state     (optional) mGBA savestates taken right after VX frame N
                            was decoded (seeking; seed for mkstate.py / unicorn)

All frame numbers are VX stream frame indices (0 = first coded frame in the
file; the player's internal decode index is this + 3).

Usage:
  capture.py --frames 300 --out DIR           # first 300 video frames
  capture.py --skip 20000 --frames 50 --out DIR   # frames 20000..20049
  capture.py --checkpoints 1000 --frames 37837 --no-dump   # build states/
"""
import argparse
import csv
import hashlib
import os
import struct
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retro  # noqa: E402
from util import bgr555_to_img  # noqa: E402

CTX = 0x06013E80          # player context struct (r5 in the main loop)
SLOT_SIZE = 0xF000        # Y 0xA000 + UV 0x5000
AUDIO_RING = 0x06015F40   # ctx+0x8C; 16 blocks x 512 bytes, s8 PCM
AUDIO_BLOCK = 512
AUDIO_RATE = 65536        # TM0 reload 0xFF00, prescaler 1 -> 2^24/256
MENU_FRAME = 880          # title menu is up and accepting input by here
PRESS_FRAMES = 5
PREROLL = 3               # player_run starts the decode/display counters at 3 (slots 0..2 black)
ROM_DEFAULT = os.path.join(HERE, "..", "shrek.gba")


def ctx_words(r):
    return struct.unpack("<64I", r.read(CTX, 256))


def boot_to_play(r):
    """Run to the title menu and start pressing A ("Play" is preselected).
    The caller keeps stepping; the button is released after PRESS_FRAMES."""
    r.run(MENU_FRAME)
    r.buttons = {"A"}
    r.press_until = r.frame_no + PRESS_FRAMES


def find_checkpoint(n, sdir=os.path.join(HERE, "states")):
    """Latest states/vx_K.state with K <= n (state saved right after VX frame K was decoded)."""
    import glob
    import re
    best = None
    for p in glob.glob(os.path.join(sdir, "vx_*.state")):
        k = int(re.findall(r"vx_(\d+)", p)[0])
        if k <= n and (best is None or k > best[0]):
            best = (k, p)
    return best[1] if best else None


def ctx_valid(w):
    return w[0] == 0x020032C8 and w[8] != 0  # buffers set up and stream pointer (+0x20) loaded


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", default=ROM_DEFAULT)
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--frames", type=int, default=300, help="number of video frames to dump")
    ap.add_argument("--skip", type=int, default=0, help="first video frame index to dump")
    ap.add_argument("--state", help="start from an mGBA savestate made by this script (states/vx_N.state)")
    ap.add_argument("--checkpoints", type=int, default=0, help="save a savestate every N decoded frames")
    ap.add_argument("--state-at", type=int, nargs="*", default=[],
                    help="also save a state right after VX frame N has been decoded")
    ap.add_argument("--no-dump", action="store_true", help="do not write frames/audio (just states)")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--hash-only", action="store_true",
                    help="write only index.csv / blocks.csv with md5s (no raw dumps); for whole-movie manifests")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    fdir, adir, sdir = os.path.join(out, "frames"), os.path.join(out, "audio"), os.path.join(out, "states")
    if not args.no_dump:
        for d in ("yuv", "vram", "png"):
            if not args.hash_only:
                os.makedirs(os.path.join(fdir, d), exist_ok=True)
        os.makedirs(fdir, exist_ok=True)
        os.makedirs(adir, exist_ok=True)
    if args.checkpoints or args.state_at:
        os.makedirs(sdir, exist_ok=True)

    r = retro.Retro(args.rom)
    if args.state is None and args.skip > 0:
        args.state = find_checkpoint(args.skip - 1)
        if args.state:
            print(f"seeking via checkpoint {args.state}")
    if args.state:
        r.run(1)  # let the core publish its memory map
        with open(args.state, "rb") as f:
            r.load_state(f.read())
    else:
        boot_to_play(r)
    r.audio = bytearray()

    first, last = args.skip, args.skip + args.frames  # dump [first, last)
    w = ctx_words(r)
    started = ctx_valid(w)
    if started:
        dec_seen = w[10]        # ctx+0x28: number of frames decoded
        disp_seen = w[11]       # ctx+0x2C: number of frames displayed
        aud_seen = w[0x29]      # ctx+0xA4: number of 512-sample audio blocks decoded
    else:
        dec_seen = disp_seen = aud_seen = 0
    print(f"start: emu frame {r.frame_no}, decoded {dec_seen}, displayed {disp_seen}, audio blocks {aud_seen}")
    if args.state is None and dec_seen - PREROLL > first:
        print(f"WARNING: {dec_seen} frames already decoded at start; earlier ones lost", file=sys.stderr)

    idx_rows = {}
    pcm = bytearray()
    ablocks = []
    audio_start = None
    multi_warn = 0
    t0 = time.time()
    state_at = set(args.state_at)
    last_dec_value, last_dec_emu = -1, 0
    cur_warned = False

    while True:
        r.run(1)
        if r.buttons and r.frame_no >= getattr(r, "press_until", 0):
            r.buttons = set()
        w = ctx_words(r)
        if not started:
            if not ctx_valid(w):
                if r.frame_no > MENU_FRAME + 600:
                    sys.exit("movie never started")
                continue
            started = True
            print(f"movie started at emu frame {r.frame_no}: decoded {w[10]}, displayed {w[11]}")
        if not ctx_valid(w):
            print("context struct no longer valid (movie ended / menu?)", file=sys.stderr)
            break
        dec, disp, aud = w[10], w[11], w[0x29]
        bufs = w[0:4]

        # ---- decoder output (YUV slot). ctx+0x28 = decode count; the frame just decoded has
        # decoder index n = count-1 and sits in slot n&3. player_run pre-sets the count to 3
        # (slots 0..2 = black, never decoded), so VX stream frame = n - PREROLL.
        if dec != dec_seen:
            if dec - dec_seen > 1:
                multi_warn += 1
            if w[12] != dec - PREROLL and not cur_warned:  # ctx+0x30 = VX frames decoded so far
                cur_warned = True
                print(f"WARNING: ctx+0x30={w[12]} != decoded-3={dec - PREROLL}; numbering may be off "
                      "(seek/chapter jump?)", file=sys.stderr)
            for n in range(dec_seen, dec):
                vx = n - PREROLL
                if vx < 0:
                    continue
                slot = bufs[n & 3]
                if vx in state_at or (args.checkpoints and vx % args.checkpoints == 0 and n == dec - 1):
                    with open(os.path.join(sdir, f"vx_{vx:05d}.state"), "wb") as f:
                        f.write(r.save_state())
                if not args.no_dump and first <= vx < last:
                    yuv = r.read(slot, SLOT_SIZE)
                    if not args.hash_only:
                        with open(os.path.join(fdir, "yuv", f"{vx:05d}.yuv"), "wb") as f:
                            f.write(yuv)
                    idx_rows.setdefault(vx, {}).update(
                        frame=vx, dec_index=n, slot=n & 3, slot_addr=f"{slot:08x}", emu_decoded=r.frame_no,
                        multi_decode=int(dec - dec_seen > 1),
                        vid_next_map=f"{w[0xBC // 4]:08x}", vid_rdptr=f"{w[0xC0 // 4]:08x}",
                        yuv_md5=hashlib.md5(yuv).hexdigest())
            dec_seen = dec

        # ---- display: ctx+0x2C advances right after the IRQ-side YUV->BGR555 blit finishes
        if disp != disp_seen:
            vx = disp - 1 - PREROLL
            if disp - disp_seen > 1 and disp_seen != 0:
                print(f"WARNING: display jumped {disp_seen}->{disp} at emu frame {r.frame_no} "
                      "(player dropped a frame)", file=sys.stderr)
            if not args.no_dump and first <= vx < last and disp_seen != 0:
                fb = r.read(0x06000000, 240 * 160 * 2)
                if not args.hash_only:
                    with open(os.path.join(fdir, "vram", f"{vx:05d}.bgr555"), "wb") as f:
                        f.write(fb)
                if not args.no_png and not args.hash_only:
                    bgr555_to_img(fb).save(os.path.join(fdir, "png", f"{vx:05d}.png"))
                idx_rows.setdefault(vx, {}).update(frame=vx, emu_displayed=r.frame_no,
                                                  vram_md5=hashlib.md5(fb).hexdigest())
            disp_seen = disp

        # ---- audio blocks (only while inside the dumped video span)
        if aud != aud_seen:
            # block j is written to ring slot j&15 by the timer-3 IRQ (0x03007AB0 -> decoder
            # 0x03006A14) and stays there until block j+16 is decoded
            if aud - aud_seen > 15:
                print(f"WARNING: audio ring overrun at emu frame {r.frame_no}", file=sys.stderr)
            in_span = dec_seen - PREROLL > first or first == 0
            for k in range(aud_seen, aud):
                if not args.no_dump and in_span:
                    if audio_start is None:
                        audio_start = k
                    blk = r.read(AUDIO_RING + (k & 15) * AUDIO_BLOCK, AUDIO_BLOCK)
                    if not args.hash_only:
                        pcm += blk
                    ablocks.append(dict(block=k, emu_frame=r.frame_no, ring_slot=k & 15, played_blocks=w[0x28],
                                        vx_decoded=dec_seen - PREROLL, vx_displayed=disp_seen - PREROLL,
                                        aud_vptr=f"{w[0x90 // 4]:08x}", md5=hashlib.md5(blk).hexdigest()))
            aud_seen = aud

        if disp_seen - PREROLL >= last and dec_seen - PREROLL >= last:
            break
        if dec != last_dec_value:
            last_dec_value, last_dec_emu = dec, r.frame_no
        elif r.frame_no - last_dec_emu > 600:
            print(f"no decode progress for 600 emu frames (movie ended?) decoded={dec} displayed={disp}",
                  file=sys.stderr)
            break

    dt = time.time() - t0
    print(f"done at emu frame {r.frame_no} in {dt:.1f}s; multi-decode emu frames: {multi_warn}")

    if args.no_dump:
        return
    cols = ["frame", "dec_index", "slot", "slot_addr", "emu_decoded", "multi_decode", "emu_displayed",
            "vid_next_map", "vid_rdptr", "yuv_md5", "vram_md5"]
    with open(os.path.join(fdir, "index.csv"), "w", newline="") as f:
        cw = csv.DictWriter(f, cols)
        cw.writeheader()
        for n in sorted(idx_rows):
            cw.writerow(idx_rows[n])
    with open(os.path.join(adir, "blocks.csv"), "w", newline="") as f:
        if ablocks:
            cw = csv.DictWriter(f, list(ablocks[0]))
            cw.writeheader()
            cw.writerows(ablocks)
    if args.hash_only:
        print(f"wrote hash manifests for {len(idx_rows)} frames, {len(ablocks)} audio blocks")
        return
    with open(os.path.join(adir, "pcm.s8"), "wb") as f:
        f.write(pcm)
    u8 = (np.frombuffer(bytes(pcm), np.int8).astype(np.int16) + 128).astype(np.uint8).tobytes()
    with wave.open(os.path.join(adir, "pcm.wav"), "wb") as wv:
        wv.setnchannels(1); wv.setsampwidth(1); wv.setframerate(AUDIO_RATE); wv.writeframes(u8)
    with wave.open(os.path.join(adir, "core_mix.wav"), "wb") as wv:
        wv.setnchannels(2); wv.setsampwidth(2); wv.setframerate(int(r.av.rate)); wv.writeframes(bytes(r.audio))
    print(f"wrote {len(idx_rows)} frames to {fdir}, {len(pcm)} PCM samples ({len(ablocks)} blocks from #{audio_start}) to {adir}")


if __name__ == "__main__":
    main()
