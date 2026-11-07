#!/usr/bin/env python3
"""Turn a whole-movie `capture.py --hash-only` run into the hashes-only files in verify/.

  mkmanifest.py --rom ROM --stream OFF --name NAME --cart "Cart (USA) Rev N" CAPDIR

Writes verify/NAME.frame_md5.csv.gz (frame, yuv_md5, vram_md5) and adds the stream to
verify/streams.json, keyed by the ROM's sha1: frame manifest, frame count, and the audio
digest = md5 of the concatenated hex md5s of the 512-byte blocks the cartridge played
(blocks 0..N-1 in order), so no decoded content is stored.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.join(HERE, "..", "verify")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capdir")
    ap.add_argument("--rom", required=True)
    ap.add_argument("--stream", type=lambda x: int(x, 0), required=True)
    ap.add_argument("--name", required=True, help="manifest file stem, e.g. sharktale-rev6")
    ap.add_argument("--cart", required=True, help="cartridge name for streams.json")
    ap.add_argument("--frames-file", help="use this existing verify/ file instead of writing one")
    a = ap.parse_args()
    rom = open(a.rom, "rb").read()
    sha = hashlib.sha1(rom).hexdigest()
    magic, nframes, w, h = struct.unpack_from("<4s3I", rom, a.stream)
    assert magic in (b"VX++", b"VXGB")

    rows = list(csv.DictReader(open(os.path.join(a.capdir, "frames", "index.csv"))))
    frames = sorted(int(r["frame"]) for r in rows)
    if frames != list(range(nframes)):
        sys.exit(f"capture has frames {frames[0]}..{frames[-1]} ({len(frames)}), stream has {nframes}")
    fname = a.frames_file or f"{a.name}.frame_md5.csv.gz"
    if not a.frames_file:
        with gzip.GzipFile(os.path.join(VERIFY, fname), "wb", mtime=0) as gz:
            out = ["frame,yuv_md5,vram_md5"]
            for r in sorted(rows, key=lambda r: int(r["frame"])):
                out.append(f"{r['frame']},{r['yuv_md5']},{r['vram_md5']}")
            gz.write(("\n".join(out) + "\n").encode())

    blocks = list(csv.DictReader(open(os.path.join(a.capdir, "audio", "blocks.csv"))))
    idx = [int(b["block"]) for b in blocks]
    if idx != list(range(len(idx))):
        sys.exit("audio blocks are not 0..N-1 in order")
    digest = hashlib.md5("".join(b["md5"] for b in blocks).encode()).hexdigest()

    path = os.path.join(VERIFY, "streams.json")
    db = json.load(open(path)) if os.path.exists(path) else {}
    cart = db.setdefault(sha, {"name": a.cart, "streams": {}})
    cart["name"] = a.cart
    cart["streams"][f"0x{a.stream:x}"] = {
        "width": w, "height": h, "frames": nframes,
        "displayed": sum(1 for r in rows if r["vram_md5"]),
        "frames_md5": fname, "audio_blocks": len(blocks), "audio_md5": digest}
    cart["streams"] = dict(sorted(cart["streams"].items(), key=lambda kv: int(kv[0], 16)))
    with open(path, "w") as f:
        json.dump(db, f, indent=1)
        f.write("\n")
    print(f"{a.cart} 0x{a.stream:x}: {nframes} frames, {len(blocks)} audio blocks -> {fname}")


if __name__ == "__main__":
    main()
