#!/usr/bin/env python3
"""Verify vxdec against ground truth.

  verify.py [--rom ROM] [--stream OFF|IDX] [modes]

The ROM (default ../shrek.gba) is identified by its sha1 in verify/streams.json, which names
the hashes-only manifest of each stream (whole movie, captured with emu/capture.py
--hash-only). A local emu/manifest/ capture is used instead for Shrek when present.

Modes (combine freely; default = --manifest --frames --seek):
  --manifest   decode the whole movie from power-on in one pass; compare md5(slot) with
               emu/manifest yuv_md5 and md5(BGR555) with vram_md5, for every frame.
               Also checks that each seek-table entry's video_bit equals the bit position
               where the continuous decode starts that frame.
  --frames     byte-compare raw dumps (emu/frames, emu/deep20000, emu/deep37800: yuv/ and
               vram/) and report the first mismatching pixel/MB per bad frame.
  --seek       for every seek-table entry, start a FRESH decoder there (player seek
               semantics) and compare the first K (--seek-frames, default 8) frames with the
               manifest: proves every seek point is a self-contained keyframe.
  --oracle N   run the real ARM decoder (uc_oracle.py, unicorn) for frames 0..N-1 and
               compare slots and per-MB start bits with vxdec --trace.
  --audio      decode every AFrame with container/vxaudio.py and compare the 512-byte
               blocks the cartridge plays (count and md5 of the per-block md5 list in
               streams.json). Python, about 10 min per movie.
Exit status 0 only if everything matched.
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
SHREK = os.path.dirname(HERE)
ROM = os.path.join(SHREK, "shrek.gba")
EMU = os.path.join(SHREK, "emu")
VXDEC = os.path.join(HERE, "vxdec")
VX = 0x20200
SHREK_SHA1 = "95d612057682b7b886441d387b167ba2493e49ef"
STREAMS = os.path.join(SHREK, "verify", "streams.json")
sys.path.insert(0, os.path.join(SHREK, "container"))
import vxcart  # noqa: E402


def stream_info():
    """This ROM/stream's entry in verify/streams.json (manifest file, audio digest)."""
    sha = hashlib.sha1(open(ROM, "rb").read()).hexdigest()
    carts = json.load(open(STREAMS))
    if sha not in carts:
        sys.exit(f"ROM sha1 {sha} is not in verify/streams.json")
    st = carts[sha]["streams"].get(f"0x{VX:x}")
    if st is None:
        sys.exit(f"no manifest for stream 0x{VX:x} of {carts[sha]['name']}")
    return sha, carts[sha]["name"], st


def run_vxdec(args):
    r = subprocess.run([VXDEC, ROM, "--stream", hex(VX)] + args, capture_output=True, text=True)
    if r.returncode not in (0, 3):
        sys.exit(f"vxdec failed: {r.stderr}")
    out = {}
    for line in r.stdout.splitlines():
        p = line.split()
        out[int(p[0])] = (p[1], p[2], int(p[3].split("=")[1]), int(p[4].split("=")[1]))
    return out, r.stderr


def load_manifest(info):
    full = os.path.join(EMU, "manifest", "frames", "index.csv")
    if info[0] == SHREK_SHA1 and VX == 0x20200 and os.path.exists(full):
        rows = csv.DictReader(open(full))
    else:  # hashes-only copy shipped in the repo
        rows = csv.DictReader(gzip.open(os.path.join(SHREK, "verify", info[2]["frames_md5"]), "rt"))
    return {int(r["frame"]): (r["yuv_md5"], r["vram_md5"]) for r in rows}


def seek_table():
    rom = open(ROM, "rb").read()
    h = struct.unpack_from("<14I", rom, VX)
    return [struct.unpack_from("<4I", rom, VX + h[10] + 16 * i) for i in range(h[11])], h[1]


def check_manifest(man):
    print("== whole movie, one pass from power-on ==")
    res, err = run_vxdec(["--count", "40000", "--md5"])
    bad_y = [f for f in man if f not in res or res[f][0] != man[f][0]]
    havev = [f for f in man if man[f][1]]
    bad_v = [f for f in havev if f not in res or res[f][1] != man[f][1]]
    errs = sum(1 for f in res if res[f][3])
    print(f"frames decoded {len(res)}, manifest {len(man)}")
    print(f"YUV slot md5: {len(man) - len(bad_y)}/{len(man)} match" + (f", first bad {bad_y[:10]}" if bad_y else ""))
    print(f"BGR555 md5:   {len(havev) - len(bad_v)}/{len(havev)} match" + (f", first bad {bad_v[:10]}" if bad_v else ""))
    st, nfr = seek_table()
    sb_bad = [(e[0], e[1], res[e[0]][2]) for e in st if e[0] < nfr and e[0] in res and res[e[0]][2] != e[1]]
    print(f"seek-table bit offsets: {sum(1 for e in st if e[0] < nfr) - len(sb_bad)}/{sum(1 for e in st if e[0] < nfr)}"
          f" equal the continuous decode's frame start bits" + (f", bad {sb_bad[:5]}" if sb_bad else ""))
    print(f"decoder error flags raised in {errs} frames; {err.strip().splitlines()[0] if err.strip() else ''}")
    return not bad_y and not bad_v and not sb_bad


def first_diff(a, b):
    for i in range(len(a)):
        if a[i] != b[i]:
            if i < 0xA000:
                y, x = divmod(i, 256)
                return f"Y ({x},{y}) MB ({x // 16},{y // 16}) got {a[i]} want {b[i]}"
            j = i - 0xA000
            y, x = divmod(j, 256)
            return f"{'UV'[x & 1]} ({x // 2},{y}) MB ({x // 16},{y // 8}) got {a[i]} want {b[i]}"
    return None


def check_frames():
    ok = True
    print("== raw dumps ==")
    for sub in ("frames", "deep20000/frames", "deep37800/frames"):
        d = os.path.join(EMU, sub, "yuv")
        if not os.path.isdir(d):
            continue
        fr = sorted(int(n[:5]) for n in os.listdir(d) if n.endswith(".yuv"))
        if not fr:
            continue
        lo, hi = fr[0], fr[-1]
        st, _ = seek_table()
        start = max([e[0] for e in st if e[0] <= lo] or [0])
        tmp = os.path.join(HERE, "..", "decoder_out", f"verify_{sub.split('/')[0]}")
        os.makedirs(tmp, exist_ok=True)
        subprocess.run([VXDEC, ROM, "--stream", hex(VX), "--start-frame", str(start), "--count", str(hi - start + 1),
                        "--out", tmp, "--rgb"], check=False, capture_output=True)
        good = vgood = vn = 0
        for f in fr:
            mine = open(os.path.join(tmp, f"{f:05d}.yuv"), "rb").read()
            want = open(os.path.join(d, f"{f:05d}.yuv"), "rb").read()
            if mine == want:
                good += 1
            else:
                ok = False
                print(f"  {sub} frame {f}: {sum(x != y for x, y in zip(mine, want))} bytes differ; first {first_diff(mine, want)}")
            vp = os.path.join(EMU, sub, "vram", f"{f:05d}.bgr555")
            if os.path.exists(vp):
                vn += 1
                if open(vp, "rb").read() == open(os.path.join(tmp, f"{f:05d}.bgr555"), "rb").read():
                    vgood += 1
                else:
                    ok = False
                    print(f"  {sub} frame {f}: BGR555 differs")
        print(f"{sub}: frames {lo}..{hi} (decoded from seek point {start}): YUV {good}/{len(fr)}, VRAM {vgood}/{vn}")
    return ok


def check_seek(man, k):
    print(f"== every seek point, fresh decoder, first {k} frames ==")
    st, nfr = seek_table()
    bad = []
    n = 0
    for e in st:
        if e[0] >= nfr:
            continue
        res, _ = run_vxdec(["--start-frame", str(e[0]), "--count", str(k), "--md5"])
        for f, v in res.items():
            n += 1
            if v[0] != man[f][0]:
                bad.append(f)
    print(f"{n - len(bad)}/{n} frames match" + (f", bad {bad[:20]}" if bad else ""))
    return not bad


def check_oracle(nf):
    print(f"== unicorn oracle (real ARM decoder), frames 0..{nf - 1}, per-MB bit positions ==")
    sys.path.insert(0, HERE)
    from uc_oracle import Oracle
    o = Oracle(ROM, VX)
    o.reset(None)
    tr = os.path.join(HERE, "..", "decoder_out", "verify_trace.txt")
    os.makedirs(os.path.dirname(tr), exist_ok=True)
    tmp = os.path.join(HERE, "..", "decoder_out", "verify_oracle")
    os.makedirs(tmp, exist_ok=True)
    subprocess.run([VXDEC, ROM, "--stream", hex(VX), "--count", str(nf), "--out", tmp, "--trace", tr], check=False)
    mine = {}
    fr = -1
    for line in open(tr):
        if line.startswith("FRAME"):
            fr = int(line.split()[1]); mine[fr] = []
        elif line.startswith(" MB"):
            mine[fr].append(int(line.split()[-1]))
    ok = True
    for f in range(nf):
        o.mb_trace = []
        y = o.decode()
        m = open(os.path.join(tmp, f"{f:05d}.yuv"), "rb").read()
        if y != m or o.mb_trace != mine[f]:
            ok = False
            k = next((i for i, (a, b) in enumerate(zip(o.mb_trace, mine[f])) if a != b), None)
            print(f"  frame {f}: slot {'ok' if y == m else 'DIFF'}, first MB bit mismatch at MB {k}")
    print("oracle: all match" if ok else "oracle: MISMATCHES")
    return ok


def check_audio(info):
    import vxaudio
    st = info[2]
    print(f"== audio: vxaudio.py vs the {st['audio_blocks']} blocks the cartridge played ==")
    rom = open(ROM, "rb").read()
    h = struct.unpack_from("<14I", rom, VX)
    aud = rom[VX + h[7]:VX + h[8]]
    dec = vxaudio.VXAudioDecoder(vxaudio.Codebook(aud))
    p, digest = vxaudio.CODEBOOK_SIZE, hashlib.md5()
    for i in range(st["audio_blocks"]):
        n, _, out = dec.decode(aud + b"\0" * 32, p) if p + 20 > len(aud) else dec.decode(aud, p)
        digest.update(hashlib.md5(out).hexdigest().encode())
        p += n
    ok = digest.hexdigest() == st["audio_md5"]
    print(f"audio blocks 0..{st['audio_blocks'] - 1}: {'match' if ok else 'MISMATCH'}")
    return ok


def main():
    global ROM, VX
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", default=ROM)
    ap.add_argument("--stream", type=lambda x: int(x, 0), help="VX++ header offset or index (default: first)")
    ap.add_argument("--audio", action="store_true")
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--frames", action="store_true")
    ap.add_argument("--seek", action="store_true")
    ap.add_argument("--seek-frames", type=int, default=8)
    ap.add_argument("--oracle", type=int, default=0)
    a = ap.parse_args()
    ROM = os.path.abspath(a.rom)
    VX = vxcart.pick_stream(open(ROM, "rb").read(), a.stream)
    if not (a.manifest or a.frames or a.seek or a.oracle or a.audio):
        a.manifest = a.frames = a.seek = True
    subprocess.run(["make", "-C", HERE, "-s", "vxdec"], check=True)
    info = stream_info()
    print(f"{info[1]}, stream 0x{VX:x}")
    man = load_manifest(info)
    ok = True
    if a.manifest:
        ok &= check_manifest(man)
    if a.frames:
        if info[0] != SHREK_SHA1 or VX != 0x20200:
            print("== raw dumps: only kept for Shrek; skipped ==")
        elif os.path.isdir(os.path.join(EMU, "frames")):
            ok &= check_frames()
        else:
            print("== raw dumps: skipped (no emu/frames; run emu/capture.py to create them) ==")
    if a.seek:
        ok &= check_seek(man, a.seek_frames)
    if a.oracle:
        ok &= check_oracle(a.oracle)
    if a.audio:
        ok &= check_audio(info)
    print("ALL OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
