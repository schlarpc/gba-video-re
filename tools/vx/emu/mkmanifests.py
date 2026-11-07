#!/usr/bin/env python3
"""Hashes-only manifests from whole-stream `capture.py --hash-only` runs (cap/<cart>_<off>/):
  verify/vx/<cart>_<off>.frame_md5.csv.gz   frame,yuv_md5,vram_md5
  verify/vx/<cart>_<off>.audio_md5.csv.gz   block,md5
Gzip with mtime 0 (reproducible). No decoded content is stored.

  mkmanifests.py CAPDIR...      (CAPDIR basename = <cart>_<hex off>)
"""
import csv
import gzip
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "verify", "vx")


def main():
    os.makedirs(OUT, exist_ok=True)
    for cap in sys.argv[1:]:
        name = os.path.basename(cap.rstrip("/"))
        rows = sorted(csv.DictReader(open(os.path.join(cap, "frames", "index.csv"))), key=lambda r: int(r["frame"]))
        assert [int(r["frame"]) for r in rows] == list(range(len(rows))), name
        with gzip.GzipFile(os.path.join(OUT, name + ".frame_md5.csv.gz"), "wb", mtime=0) as g:
            g.write(("frame,yuv_md5,vram_md5\n" + "".join(f"{r['frame']},{r['yuv_md5']},{r['vram_md5']}\n" for r in rows)).encode())
        bl = list(csv.DictReader(open(os.path.join(cap, "audio", "blocks.csv"))))
        assert [int(b["block"]) for b in bl] == list(range(len(bl))), name
        with gzip.GzipFile(os.path.join(OUT, name + ".audio_md5.csv.gz"), "wb", mtime=0) as g:
            g.write(("block,md5\n" + "".join(f"{b['block']},{b['md5']}\n" for b in bl)).encode())
        print(f"{name}: {len(rows)} frames, {len(bl)} audio blocks")


if __name__ == "__main__":
    main()
