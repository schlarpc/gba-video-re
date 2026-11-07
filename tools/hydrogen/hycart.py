#!/usr/bin/env python3
"""Hydrogen (Majesco) GBA Video cartridge: find movie containers, parse stream headers, walk segments."""
import struct, sys, os, json, hashlib

ALPHA = {}
for i in range(10): ALPHA[1 + i] = chr(ord('0') + i)
for i in range(26): ALPHA[11 + i] = chr(ord('a') + i); ALPHA[37 + i] = chr(ord('A') + i)
ALPHA[0xFF] = ' '; ALPHA[0xFE] = '\n'


def decode_name(b):
    return ''.join(ALPHA.get(c, '{%02x}' % c) for c in b)


def u32(b, o): return struct.unpack_from('<I', b, o)[0]
def u16(b, o): return struct.unpack_from('<H', b, o)[0]


def find_containers(rom):
    """Movie containers: u32 (size<<4 | nentries) then entries {u32 type<<28 | len, data[len-4]};
    entry 1 is a video stream whose header has width 240 in bits 10..19 of word 1."""
    import numpy as np
    a = np.frombuffer(rom[:len(rom) // 4 * 4], dtype='<u4')
    idx = np.nonzero(((a[1:-2] >> 28) == 1) & ((a[2:-1] >> 28) == 2) & (((a[3:] >> 10) & 0x3ff) == 240))[0]
    out = []
    for i in idx:
        o = int(i) * 4
        w0 = u32(rom, o); n = w0 & 0xf; size = w0 >> 4
        p = o; ents = {}; ok = 0 < n < 8
        for k in range(n if ok else 0):
            w = u32(rom, p + 4)
            ents[w >> 28] = (p + 8, (w & 0xfffffff) - 4)
            p += 4 + (w & 0xfffffff)
            if p > len(rom): ok = False; break
        if ok and p - o + 4 == size:
            out.append(dict(container=o, size=size, video=ents.get(1), audio=ents.get(2)))
    return out


class Stream:
    def __init__(self, rom, off, length=None):
        self.rom = rom; self.off = off; self.length = length
        w0, w1 = struct.unpack_from('<II', rom, off)
        self.w0, self.w1 = w0, w1
        self.kind = w0 >> 28
        self.nchap = w0 & 0xff
        self.total_frames = (w0 >> 8) & 0xfffff
        self.vblanks_per_frame = w1 & 0xf
        self.fps_nominal = (w1 >> 4) & 0x3f
        self.width = (w1 >> 10) & 0x3ff
        self.height = (w1 >> 20) & 0x3ff
        self.flags = w1 >> 30
        self.nseg = u16(rom, off + 8)
        self.hdr_u16_10 = u16(rom, off + 10)
        p = 12; self.chapters = []
        for i in range(self.nchap):
            seg = u16(rom, off + p); q = p + 2
            e = rom.index(b'\0', off + q) - off
            self.chapters.append(dict(segment=seg, name=decode_name(rom[off + q:off + e])))
            p = e + 1
            if p & 1: p += 1
        if p & 3: p += 4 - (p & 3)
        self.seg0 = off + p
        self.segments = []
        q = self.seg0; frame = 0
        for i in range(self.nseg):
            a, b = struct.unpack_from('<II', rom, q)
            s = dict(index=i, off=q, frame=frame, nframes=a >> 16, mode=a & 7, mbits=(a >> 3) & 7,
                     ncb=(a >> 6) & 0x3ff, cb_len=(b & 0x1fff) * 4, idx_len=(b >> 13) * 4)
            s['cb_off'] = q + 8; s['idx_off'] = q + 8 + s['cb_len']
            self.segments.append(s)
            frame += s['nframes']
            q += 8 + s['cb_len'] + s['idx_len']
        self.end = q
        self.frames = frame

    def info(self):
        return dict(off=self.off, w0=self.w0, w1=self.w1, frames=self.frames, total_frames=self.total_frames,
                    nseg=self.nseg, width=self.width, height=self.height, vbl=self.vblanks_per_frame,
                    fps_nominal=self.fps_nominal, flags=self.flags, u16_10=self.hdr_u16_10,
                    chapters=self.chapters, end=self.end, length=self.length)


def streams(rom):
    res = []
    for c in find_containers(rom):
        v = c['video']
        s = Stream(rom, v[0], v[1])
        res.append((c, s))
    return res


if __name__ == '__main__':
    rom = open(sys.argv[1], 'rb').read()
    for c, s in streams(rom):
        i = s.info()
        modes = sorted(set(g['mode'] for g in s.segments)); mb = sorted(set(g['mbits'] for g in s.segments))
        ncb = sorted(set(g['ncb'] for g in s.segments))
        print(f"cont {c['container']:#x} video {s.off:#x}+{s.length:#x} end {s.end - s.off:#x} frames {s.frames}/{s.total_frames} segs {s.nseg} "
              f"{s.width}x{s.height} vbl {s.vblanks_per_frame} fps {s.fps_nominal} flags {s.flags} u16 {s.hdr_u16_10} modes {modes} mbits {mb} ncb {ncb[:3]}..{ncb[-3:]} audio {c['audio'][0]:#x}+{c['audio'][1]:#x}")
        for ch in s.chapters: print('   ', ch)
