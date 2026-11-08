"""Host-folder SD card and media decoding (ffmpeg) for the Play-Yan model.

Decoding choices that the GBA code does not fix (docs/playyan/README.md, emulator check):
- 240x176 source video is fitted to the requested w x h by cropping 8 rows top and bottom
  (240x160) and then scaling (60x40 is a 1/4 scale of the crop).
- Audio is resampled by ffmpeg to the GBA's rate for the play state (8192 Hz video, 16384 Hz
  music), signed 8-bit, and split per channel: the first half of each block is the RIGHT channel
  (FIFO A drives the right speaker, protocol.md 7.4).
"""
import hashlib
import json
import os
import subprocess

import numpy as np

import sys  # noqa: E402
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import workdir  # noqa: E402
CACHE = os.path.join(workdir.WORK, "cache")   # ffmpeg decodes for the HLE model
VIDEO_EXT = (".asf", ".mp4")
MUSIC_EXT = (".mp3",)


def _key(*parts):
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


def _ffmpeg(args, out_path):
    os.makedirs(CACHE, exist_ok=True)
    if not os.path.exists(out_path):
        tmp = out_path + ".part"
        subprocess.run(["ffmpeg", "-v", "error", "-y"] + args + [tmp], check=True)
        os.replace(tmp, out_path)
    return out_path


def probe(path):
    k = os.path.join(CACHE, "probe_" + _key(os.path.abspath(path), os.path.getsize(path)) + ".json")
    if os.path.exists(k):
        return json.load(open(k))
    os.makedirs(CACHE, exist_ok=True)
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                         capture_output=True, text=True).stdout
    info = json.loads(out or "{}")
    json.dump(info, open(k, "w"))
    return info


def duration(path):
    try:
        return float(probe(path)["format"]["duration"])
    except Exception:
        return 0.0


def video_fps(path):
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video":
            n, d = s.get("avg_frame_rate", "0/1").split("/")
            if float(d) and float(n):
                return float(n) / float(d)
            n, d = s.get("r_frame_rate", "30/1").split("/")
            return float(n) / float(d) if float(d) else 30.0
    return 30.0


def tags(path):
    t = {}
    f = probe(path).get("format", {})
    for k, v in (f.get("tags") or {}).items():
        t[k.lower()] = v
    return t


def _vf(w, h):
    # crop the 240x176 source to 240x160 (8 rows off top and bottom), then scale
    return "crop=iw:ih*160/176:0:(ih-ih*160/176)/2,scale=%d:%d:flags=bilinear" % (w, h)


def video_frames(path, w, h):
    """All frames as uint16 BGR555 array (n, h, w)."""
    out = os.path.join(CACHE, "frames_%s_%dx%d.raw" % (_key(os.path.abspath(path)), w, h))
    _ffmpeg(["-i", path, "-an", "-vf", _vf(w, h), "-pix_fmt", "bgr555le", "-f", "rawvideo"], out)
    a = np.fromfile(out, np.uint16) & 0x7FFF
    return a.reshape(-1, h, w)


def first_frame(path, w, h):
    out = os.path.join(CACHE, "thumb_%s_%dx%d.raw" % (_key(os.path.abspath(path)), w, h))
    _ffmpeg(["-i", path, "-an", "-frames:v", "1", "-vf", _vf(w, h), "-pix_fmt", "bgr555le", "-f", "rawvideo"], out)
    return (np.fromfile(out, np.uint16) & 0x7FFF).reshape(h, w)


def audio_s8(path, rate):
    """(n, 2) int8 samples, columns (left, right)."""
    out = os.path.join(CACHE, "audio_%s_%d.raw" % (_key(os.path.abspath(path)), rate))
    try:
        _ffmpeg(["-i", path, "-vn", "-ac", "2", "-ar", str(rate), "-f", "s8"], out)
    except subprocess.CalledProcessError:
        return np.zeros((0, 2), np.int8)
    return np.fromfile(out, np.int8).reshape(-1, 2)


def garage_hash(pixels):
    h = 0
    for p in np.asarray(pixels, np.uint16).reshape(-1)[:0x960]:
        h = (h + int(p)) & 0xFFFFFFFF
        h = ((h << 1) | (h >> 31)) & 0xFFFFFFFF
    return h


def force_garage_hash(pixels, target):
    """Nudge up to 32 pixels by +1 so the thumbnail hashes to target. The cartridge's decoder is
    not bit-exact with ffmpeg, so this is the 'homemade unlock file' route of protocol.md 9.
    rotl(x, 1) is multiplication by 2 modulo 2^32 - 1, so (ignoring rare carries) the hash is
    sum(p_i * 2^((n - i + 1) mod 32)) mod (2^32 - 1): pixel i weighs a power of two."""
    base = np.asarray(pixels, np.uint16).reshape(-1)
    n = 0x960
    M = 0xFFFFFFFF
    for sign in (1, -1):
        for start in range(n - 32, -1, -1):
            px = base.copy().astype(np.int32)
            d = (target - garage_hash(px)) % M
            if sign < 0:
                d = (M - d) % M
            win = range(start, start + 32)
            if any(not (0 <= px[i] + sign <= 0x7FFF) for i in win):
                continue
            for i in win:
                e = (n - i) % 32          # 0-based index i -> exponent (n - (i + 1) + 1) mod 32
                if (d >> e) & 1:
                    px[i] += sign
            px = px.astype(np.uint16)
            if garage_hash(px) == target:
                return px.reshape(np.asarray(pixels).shape)
    return None


class SDCard:
    """Host folder as the SD card. Names are returned as the cartridge would: plain names for the
    music browser, backslash paths relative to the root for the recursive video list."""

    def __init__(self, root, video_ext=VIDEO_EXT):
        self.root = os.path.abspath(root)
        self.cwd = []  # music browser folder stack
        self.video_ext = video_ext

    def resolve(self, name):
        name = name.replace("\\", "/").lstrip("/")
        p = os.path.join(self.root, *self.cwd, name)
        if os.path.exists(p):
            return p
        p = os.path.join(self.root, name)
        return p if os.path.exists(p) else None

    def video_list(self):
        out = []
        for d, dirs, files in os.walk(self.root, followlinks=True):
            dirs.sort()
            for f in sorted(files):
                if f.lower().endswith(self.video_ext):
                    rel = os.path.relpath(os.path.join(d, f), self.root)
                    out.append((rel.replace("/", "\\"), 1))
        return out

    def music_list(self):
        here = os.path.join(self.root, *self.cwd)
        out = []
        if self.cwd:
            out.append(("..", 0))
        for f in sorted(os.listdir(here)):
            p = os.path.join(here, f)
            if os.path.isdir(p):
                out.append((f, 0))
            elif f.lower().endswith(MUSIC_EXT):
                out.append((f, 2))
        return out

    def chdir(self, name):
        if name == "..":
            if self.cwd:
                self.cwd.pop()
                return 0
            return 1
        p = os.path.join(self.root, *self.cwd, name)
        if os.path.isdir(p):
            self.cwd.append(name)
            return 0
        return 1
