#!/usr/bin/env python3
"""Verify the VX family decoders (reference/vx/decoder/vxdec for video, tools/vx/container/vxaudio.py
for audio) against the hashes-only emulator manifests in verify/vx/, for every stream of every
VX++ / VXGB cart.

  verify_vx.py [--roms DIR] [--only CART[:OFF]] [--no-audio] [--json OUT]

Manifests (from a whole-stream `capture.py --hash-only` run in mGBA, see mkmanifests.py):
  <cart>_<off>.frame_md5.csv.gz   frame, yuv_md5 (decoder slot, 0xF000 B), vram_md5 (screen picture)
  <cart>_<off>.audio_md5.csv.gz   block, md5 (512-byte s8 PCM block, in play order)

Checks per stream:
  video: md5 of every decoded slot == yuv_md5; md5 of the BGR555 picture == vram_md5, where a
         screen equal to the previous frame's is accepted as a blit the player skipped (late frame);
  audio: vxaudio.py block k == emulator block k for every block the cart played. Blocks played
         after an audio-window underrun of the emulated cart are reported separately (see perf-vx.md).
Exit status 0 if every frame matches and every audio block before any underrun matches.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..", "..")
VXDEC = os.path.join(ROOT, "reference", "vx", "decoder", "vxdec")
MAN = os.path.join(ROOT, "verify", "vx")
sys.path.insert(0, os.path.join(ROOT, "tools", "vx", "container"))
sys.path.insert(0, os.path.join(ROOT, "tools", "common"))
from roms import rom_dir, rom_path  # noqa: E402

STREAMS = [("shrek-rev6", 0x20200), ("shrek-rev5", 0x20200), ("shrek2-rev6", 0x20200), ("shrek2-rev5", 0x20200),
           ("sharktale-rev6", 0x20200), ("sharktale-rev5", 0x20200), ("shrek-sharktale-rev5", 0x20200),
           ("shrek-sharktale-rev5", 0x1eef800), ("shrek-sharktale-rev5", 0x3dd3600),
           ("shrek-sharktale-rev5", 0x3e76a00)]

# first audio block the emulated cart plays from a misaligned window (perf-vx.md, Shrek 2 Rev 5)
KNOWN_UNDERRUN = {"shrek2-rev5:0x20200": 296039}


def stem(cart, off):
    return f"{cart}_{off:x}"


def check_video(rom, cart, off):
    man = {int(r["frame"]): (r["yuv_md5"], r["vram_md5"])
           for r in csv.DictReader(gzip.open(os.path.join(MAN, stem(cart, off) + ".frame_md5.csv.gz"), "rt"))}
    r = subprocess.run([VXDEC, rom, "--stream", hex(off), "--count", "1000000", "--md5"], capture_output=True, text=True)
    res = {}
    errs = 0
    for line in r.stdout.splitlines():
        p = line.split()
        res[int(p[0])] = (p[1], p[2])
        errs += int(p[4].split("=")[1]) != 0
    bad_y = [f for f in man if res.get(f, ("",))[0] != man[f][0]]
    shown = [f for f in man if man[f][1]]
    bad_v = [f for f in shown if res.get(f, ("", ""))[1] != man[f][1]]
    skipped = [f for f in bad_v if f - 1 in man and man[f - 1][1] == man[f][1]]
    bad_v = [f for f in bad_v if f not in skipped]
    return dict(frames=len(man), yuv_ok=len(man) - len(bad_y), shown=len(shown), vram_ok=len(shown) - len(bad_v) - len(skipped),
                vram_skipped_blit=len(skipped), skipped_list=skipped[:50], bad_yuv=bad_y[:20], bad_vram=bad_v[:20],
                decoder_error_frames=errs, extra_decoded=len(res) - len(man))


def check_audio(romb, off):
    import vxaudio
    p = os.path.join(MAN, stem(*off) + ".audio_md5.csv.gz")
    if not os.path.exists(p):  # per-block audio manifest not in verify/ (optional; gbav verify checks the audio segment manifests)
        return dict(blocks=0, blocks_ok=0, first_mismatch=None, ok_after_first_mismatch=None, skipped=True)
    man = [r["md5"] for r in csv.DictReader(gzip.open(p, "rt"))]
    h = struct.unpack_from("<4s13I", romb, off[1])
    aud = romb[off[1] + h[7]:off[1] + h[8]]
    dec = vxaudio.VXAudioDecoder(vxaudio.Codebook(aud))
    p = vxaudio.CODEBOOK_SIZE
    first_bad = None
    ok = 0
    for k, m in enumerate(man):
        n, _, out = dec.decode(aud, p)
        p += n
        if hashlib.md5(out).hexdigest() == m:
            ok += 1
        elif first_bad is None:
            first_bad = k
    tail_ok = None
    if first_bad is not None:
        tail_ok = ok - first_bad     # matches after the first mismatch (0 = all garbage after it)
    return dict(blocks=len(man), blocks_ok=ok, first_mismatch=first_bad, ok_after_first_mismatch=tail_ok)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roms", default=rom_dir(), help="ROM directory (default $GBAV_ROMS, else roms/)")
    ap.add_argument("--only")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    out = {}
    allok = True
    for cart, off in STREAMS:
        if a.only and a.only not in (cart, f"{cart}:{off:#x}"):
            continue
        rp = rom_path(cart, a.roms)
        romb = open(rp, "rb").read()
        v = check_video(rp, cart, off)
        rec = dict(cart=cart, stream=hex(off), sha1=hashlib.sha1(romb).hexdigest(), video=v)
        vok = v["yuv_ok"] == v["frames"] and v["vram_ok"] + v["vram_skipped_blit"] == v["shown"] and not v["decoder_error_frames"]
        line = (f"{cart:22s} {off:#9x} video yuv {v['yuv_ok']}/{v['frames']} screen {v['vram_ok']}/{v['shown']}"
                f" (+{v['vram_skipped_blit']} skipped blits) err {v['decoder_error_frames']}")
        if not a.no_audio:
            au = check_audio(romb, (cart, off))
            rec["audio"] = au
            line += " | audio skipped (no per-block manifest)" if au.get("skipped") else f" | audio {au['blocks_ok']}/{au['blocks']}"
            if au["first_mismatch"] is not None:
                line += f" first mismatch at block {au['first_mismatch']} ({au['ok_after_first_mismatch']} match after it)"
                # only acceptable where the emulated cart underran its audio window (perf-vx.md)
                exp = KNOWN_UNDERRUN.get(f"{cart}:{off:#x}")
                vok = vok and au["first_mismatch"] == exp
                line += " = known emulator underrun" if au["first_mismatch"] == exp else " UNEXPECTED"
        print(line, flush=True)
        out[f"{cart}:{off:#x}"] = rec
        allok &= vok
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
