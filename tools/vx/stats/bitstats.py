#!/usr/bin/env python3
"""Bitstream statistics for every VX++ / VXGB stream: per-frame video bits from the C decoder
(vxdec --md5 prints each frame's start bit), AFrame sizes from the audio stream (mode field ->
20/14/12/10 bytes), seek points. Writes work/bitstats.json (no content, numbers only).

  bitstats.py            # all streams of the VX carts ($GBAV_ROMS)
"""
import json
import os
import struct
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..", "..")
VXDEC = os.path.join(ROOT, "reference", "vx", "decoder", "vxdec")
sys.path.insert(0, os.path.join(ROOT, "tools", "vx", "container"))
sys.path.insert(0, os.path.join(ROOT, "tools", "common"))
from roms import rom_path  # noqa: E402
WORK = os.path.join(ROOT, "tmp", "vx")
import vxaudio  # noqa: E402

STREAMS = [("shrek-rev6", 0x20200), ("shrek-rev5", 0x20200), ("shrek2-rev6", 0x20200), ("shrek2-rev5", 0x20200),
           ("sharktale-rev6", 0x20200), ("sharktale-rev5", 0x20200), ("shrek-sharktale-rev5", 0x20200),
           ("shrek-sharktale-rev5", 0x1eef800), ("shrek-sharktale-rev5", 0x3dd3600), ("shrek-sharktale-rev5", 0x3e76a00)]
AF_SIZE = [20, 14, 12, 10]


def video_bits(rom, off):
    r = subprocess.run([VXDEC, rom, "--stream", hex(off), "--count", "1000000", "--md5"], capture_output=True, text=True)
    starts, errs = [], 0
    for line in r.stdout.splitlines():
        p = line.split()
        starts.append(int(p[3].split("=")[1]))
        errs += int(p[4].split("=")[1]) != 0
    return starts, errs


def tail_bit(romb, off, h):
    """End of the coded video: last nonzero halfword before the audio stream (video = H+0x38..H+audio)."""
    v0, v1 = off + 0x38, off + h[7]
    j = v1
    while j > v0 and romb[j - 2:j] == b"\0\0":
        j -= 2
    return (j - v0) * 8


def audio_frames(romb, off, h, nframes_expected):
    a0, a1 = off + h[7], off + h[8]
    aud = romb[a0:a1]
    p = vxaudio.CODEBOOK_SIZE
    sizes = []
    n_intra = 0
    while p + 4 <= len(aud):
        w1, w2 = struct.unpack_from("<HH", aud, p)
        if w1 == 0 and w2 == 0 and not any(aud[p:p + 64]):
            break
        sizes.append(AF_SIZE[(w2 >> 12) & 3])
        n_intra += (w1 >> 9) == 0x7F
        p += sizes[-1]
    return np.array(sizes), n_intra


def main():
    out = {}
    for cart, off in STREAMS:
        rp = rom_path(cart)
        romb = open(rp, "rb").read()
        h = struct.unpack_from("<4s13I", romb, off)
        magic, nfr, w, hgt, t3 = h[0].decode(), h[1], h[2], h[3], h[4]
        fps = (1 << 24) / (64 * 3 * t3)
        starts, errs = video_bits(rp, off)
        assert len(starts) == nfr, (cart, len(starts), nfr)
        end = tail_bit(romb, off, h)
        b = np.diff(np.array(starts + [end], dtype=np.int64))
        # per-second (7 frames = 1.0000 s at 7 fps) sliding window p99
        k = int(round(fps))
        win = np.convolve(b, np.ones(k, dtype=np.int64), "valid") * fps / k
        sz, n_intra = audio_frames(romb, off, h, None)
        seek = [struct.unpack_from("<4I", romb, off + h[10] + 16 * i)[0] for i in range(h[11])]
        nseek = sum(1 for s in seek if s < nfr)
        dur = nfr / fps
        abytes = int(sz.sum()) + vxaudio.CODEBOOK_SIZE
        out[f"{cart}:{off:#x}"] = dict(
            cart=cart, stream=off, magic=magic, width=w, height=hgt, frames=nfr, fps=fps, duration_s=dur,
            header_qp=h[5] if magic == "VXGB" else None,
            video_bits=int(end), video_bytes=int((end + 7) // 8), decoder_error_frames=errs,
            video_kbps_mean=float(b.sum() / dur / 1000), video_kbps_p99_1s=float(np.quantile(win, .99) / 1000),
            video_kbps_max_1s=float(win.max() / 1000),
            frame_bits=dict(mean=float(b.mean()), p50=float(np.median(b)), p99=float(np.quantile(b, .99)),
                            max=int(b.max()), min=int(b.min())),
            seek_frame_bits_mean=float(b[[s for s in seek if s < nfr]].mean()),
            seek_points=nseek, aframes=int(len(sz)), aframe_intra=int(n_intra), audio_bytes=abytes,
            audio_kbps=float((abytes - vxaudio.CODEBOOK_SIZE) * 8 / (len(sz) / 128.0) / 1000),
            aframe_size_hist={str(s): int((sz == s).sum()) for s in AF_SIZE})
        print(cart, hex(off), magic, nfr, f"{out[f'{cart}:{off:#x}']['video_kbps_mean']:.1f} kbps", errs, file=sys.stderr)
    os.makedirs(WORK, exist_ok=True)
    json.dump(out, open(os.path.join(WORK, "bitstats.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
