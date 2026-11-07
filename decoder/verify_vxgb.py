#!/usr/bin/env python3
"""Verify vxdec's VXGB mode against ground truth for a GBA Video Rev 5 cartridge
(Shark Tale or Shrek 2). See docs/vxgb.md.

  verify_vxgb.py ROM [--manifest] [--seek [--seek-frames K]] [--oracle N] [--audio]
                     (default: --manifest --seek)

  --manifest   decode the whole movie in one pass from power-on; compare md5(slot) and
               md5(BGR555 screen) of every frame with the emulator manifest
               verify/<movie>_20200.frame_md5.csv.gz, and every frame's start bit with
               verify/<movie>_20200.frame_bits.json.gz; also the seek table's bit offsets.
               A screen hash may instead equal the previous frame's: the player skips the
               blit of a frame whose decode ran late (av_tick), leaving the old picture.
  --seek       start a FRESH decoder at every seek-table entry (what the cart does when a
               chapter is picked from the menu) and compare the first K frames.
  --oracle N   run the Rev 5 player's own decoder (uc_oracle_vxgb.py) for frames 0..N-1 and
               compare the slot and the bit position of every macroblock.
  --audio      decode the audio with container/vxaudio.py and compare the digest of the
               512-byte output blocks with the emulator's (the blocks the cartridge played).
Exit status 0 only if everything matched.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VXDEC = os.path.join(HERE, "vxdec")
VX = 0x20200
# sha1 of the whole ROM -> (manifest stem, emulator audio blocks, digest = md5 of the
# concatenated hex md5s of those 512-byte blocks, as emu/mkmanifest.py computes it)
MOVIES = {
    "6128a476edb10e6839ac5bd2697e83b9b7a9b234": ("sharktale-rev5_20200", 689240, "f6fa7b811f49a3a32412ee39bd55ebb3"),
    # Shrek 2: the emulator's playback underruns the audio window at block 296039 (the main
    # loop refills it only between video frames, and frame 16191 took too long), so the
    # cart then plays misaligned AFrames; only the blocks before that are the codec's output.
    # Digest of all 709698 played blocks: 2e831e52abb536df79fd9ca58bf1fc4a.
    "5cd627e205020297b25d707131883be5850515fe": ("shrek2-rev5_20200", 296039, "2a0d1aa3b4308d6f55c7792125deb312"),
}


def run_vxdec(rom, args):
    r = subprocess.run([VXDEC, rom] + args, capture_output=True, text=True)
    if r.returncode not in (0, 3):
        sys.exit(f"vxdec failed: {r.stderr}")
    out = {}
    for line in r.stdout.splitlines():
        p = line.split()
        out[int(p[0])] = (p[1], p[2], int(p[3].split("=")[1]), int(p[4].split("=")[1]))
    return out


def seek_table(romb):
    h = struct.unpack_from("<14I", romb, VX)
    return [struct.unpack_from("<4I", romb, VX + h[10] + 16 * i) for i in range(h[11])], h[1]


def check_manifest(rom, romb, name):
    print("== whole movie, one pass from power-on ==")
    man = {int(r["frame"]): (r["yuv_md5"], r["vram_md5"])
           for r in csv.DictReader(gzip.open(os.path.join(ROOT, "verify", f"{name}.frame_md5.csv.gz"), "rt"))}
    bits = json.load(gzip.open(os.path.join(ROOT, "verify", f"{name}.frame_bits.json.gz"), "rt"))["frame_start_bits"]
    res = run_vxdec(rom, ["--count", "100000", "--md5"])
    bad_y = [f for f in man if f not in res or res[f][0] != man[f][0]]
    havev = [f for f in man if man[f][1]]
    bad_v = [f for f in havev if f not in res or res[f][1] != man[f][1]]
    # a skipped blit leaves the previous screen: same hash as the frame before (chains allowed)
    dropped = [f for f in bad_v if f - 1 in man and man[f - 1][1] == man[f][1]]
    bad_v = [f for f in bad_v if f not in dropped]
    bad_b = [f for f in res if f < len(bits) and res[f][2] != bits[f]]
    errs = sum(1 for f in res if res[f][3])
    st, nfr = seek_table(romb)
    sb_bad = [e[0] for e in st if e[0] < nfr and res[e[0]][2] != e[1]]
    print(f"frames decoded {len(res)}, manifest {len(man)}")
    print(f"YUV slot md5:   {len(man) - len(bad_y)}/{len(man)} match" + (f", first bad {bad_y[:10]}" if bad_y else ""))
    print(f"BGR555 md5:     {len(havev) - len(bad_v) - len(dropped)}/{len(havev)} match; {len(dropped)} show the previous"
          f" frame (blit skipped by the player)" + (f"; first bad {bad_v[:10]}" if bad_v else ""))
    print(f"frame start bits: {len(res) - len(bad_b)}/{len(res)} equal frame_bits" + (f", first bad {bad_b[:5]}" if bad_b else ""))
    print(f"seek-table bit offsets: {sum(1 for e in st if e[0] < nfr) - len(sb_bad)}/{sum(1 for e in st if e[0] < nfr)}"
          " equal the continuous decode's frame start bits")
    print(f"decoder error flags raised in {errs} frames")
    return not (bad_y or bad_v or bad_b or sb_bad or errs)


def check_seek(rom, romb, k):
    print(f"== fresh decoder at every seek point, {k} frames each ==")
    cont = run_vxdec(rom, ["--count", "100000", "--md5"])
    st, nfr = seek_table(romb)
    good = bad = 0
    for fr, bit, _, _ in st:
        if fr == 0 or fr >= nfr:
            continue
        res = run_vxdec(rom, ["--start-frame", str(fr), "--count", str(k), "--md5"])
        ok = all(res[f][:2] == cont[f][:2] for f in res)
        good += ok
        bad += not ok
        if not ok:
            print(f"  seek point {fr}: MISMATCH")
    print(f"seek points: {good}/{good + bad} decode identically from a cold start")
    return bad == 0


def check_oracle(rom, n):
    print(f"== vs the Rev 5 ARM decoder in unicorn, frames 0..{n - 1} ==")
    sys.path.insert(0, HERE)
    from uc_oracle_vxgb import OracleGB
    o = OracleGB(rom)
    o.reset()
    tr = os.path.join(HERE, ".verify_vxgb_trace.txt")
    subprocess.run([VXDEC, rom, "--count", str(n), "--trace", tr], capture_output=True)
    cmb = {}
    cur = -1
    for line in open(tr):
        if line.startswith("FRAME"):
            cur = int(line.split()[1])
        m = re.match(r" MB \d+,\d+ bit (\d+)", line)
        if m:
            cmb.setdefault(cur, []).append(int(m.group(1)))
    os.remove(tr)
    res = run_vxdec(rom, ["--count", str(n), "--md5"])
    bad = 0
    for f in range(n):
        o.mb_trace = []
        y = o.decode()
        if hashlib.md5(y).hexdigest() != res[f][0] or o.mb_trace != cmb.get(f):
            bad += 1
            if bad < 5:
                print(f"  frame {f}: MISMATCH")
    print(f"frames: {n - bad}/{n} equal (slot md5 and the start bit of all {len(cmb.get(0, []))} MBs)")
    return bad == 0


def check_audio(romb, nblocks, digest):
    print("== audio: vxaudio.py vs the emulator's played blocks ==")
    if not nblocks:
        print("no emulator audio digest for this cart")
        return False
    sys.path.insert(0, os.path.join(ROOT, "container"))
    import vxaudio
    h = struct.unpack_from("<4s13I", romb, VX)
    aud = romb[VX + h[7]:VX + h[8]]
    dec = vxaudio.VXAudioDecoder(vxaudio.Codebook(aud))
    md, p = [], vxaudio.CODEBOOK_SIZE
    for _ in range(nblocks):
        n, _, out = dec.decode(aud, p)
        md.append(hashlib.md5(out).hexdigest())
        p += n
    got = hashlib.md5("".join(md).encode()).hexdigest()
    print(f"{nblocks} blocks: digest {got} {'==' if got == digest else '!='} emulator {digest}")
    return got == digest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--seek", action="store_true")
    ap.add_argument("--seek-frames", type=int, default=8)
    ap.add_argument("--oracle", type=int, default=0)
    ap.add_argument("--audio", action="store_true")
    a = ap.parse_args()
    romb = open(a.rom, "rb").read()
    mv = MOVIES.get(hashlib.sha1(romb).hexdigest())
    if romb[VX:VX + 4] != b"VXGB" or not mv:
        sys.exit("not a known VXGB cartridge (Shark Tale / Shrek 2 USA Rev 5)")
    name = mv[0]
    if not (a.manifest or a.seek or a.oracle or a.audio):
        a.manifest = a.seek = True
    ok = True
    if a.manifest:
        ok &= check_manifest(a.rom, romb, name)
    if a.seek:
        ok &= check_seek(a.rom, romb, a.seek_frames)
    if a.oracle:
        ok &= check_oracle(a.rom, a.oracle)
    if a.audio:
        ok &= check_audio(romb, mv[1], mv[2])
    print("ALL OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
