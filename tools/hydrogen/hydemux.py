#!/usr/bin/env python3
"""Hydrogen GBA Video demux tool.

  hydemux.py info ROM                     containers, streams, chapters, segment summary
  hydemux.py segments ROM STREAM          one line per segment
  hydemux.py extract ROM STREAM OUTPREFIX OUTPREFIX.video / OUTPREFIX.audio (raw entry payloads)
  hydemux.py wav ROM STREAM OUT.wav       decode the audio with the shared ADPCM (libhydec.so)

STREAM is an index from `info` (0 = logo, then the episodes, last = show intro).
"""
import sys, os, struct, wave, ctypes as C
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import hycart

FPS = 2 ** 24 / (5 * 280896)


class _A(C.Structure):
    _fields_ = [('buf', C.c_void_p), ('nbits_total', C.c_size_t), ('pos', C.c_size_t), ('overrun', C.c_int), ('coding', C.c_int),
                ('step', C.c_int32), ('prev', C.c_int32), ('h0', C.c_int32), ('h1', C.c_int32), ('f0', C.c_int32), ('f1', C.c_int32),
                ('f2', C.c_int32), ('s1', C.c_uint8), ('s2', C.c_uint8), ('ctx', C.c_uint8), ('tab', C.c_uint8 * 64),
                ('left', C.c_uint32), ('block', C.c_uint32)]


def audio_samples(rom, cont, nsamples=None):
    """decode the type-2 entry; default length = the samples the player plays for the video
    (4096-sample prefill excluded: frames * 1371.5625)"""
    lib = C.CDLL(os.path.join(HERE, '..', '..', 'reference', 'hydrogen', 'libhydec.so'))
    ao, al = cont['audio']
    n = struct.unpack_from('<H', rom, ao + 14)[0]
    po = ao + ((16 + 2 * n + 3) & ~3)
    tail = 64          # the cartridge's reader may run a few bits past the entry
    a = _A(); buf = C.create_string_buffer(rom[po:ao + al + tail], al + tail)
    lib.mj_adpcm_init(C.byref(a), buf, al - (po - ao) + tail, 1)
    if nsamples is None: nsamples = n * 16458
    out = (C.c_int8 * nsamples)()
    got = lib.mj_adpcm_decode(C.byref(a), out, nsamples)
    return bytes(out)[:got]


def main():
    if len(sys.argv) < 3: print(__doc__); return 2
    cmd, rom = sys.argv[1], open(sys.argv[2], 'rb').read()
    conts = hycart.find_containers(rom)
    if cmd == 'info':
        for i, c in enumerate(conts):
            s = hycart.Stream(rom, c['video'][0], c['video'][1])
            n = struct.unpack_from('<H', rom, c['audio'][0] + 14)[0]
            print(f"[{i}] container {c['container']:#x}: video {s.off:#x}+{s.length:#x} audio {c['audio'][0]:#x}+{c['audio'][1]:#x}; "
                  f"{s.frames} frames ({s.frames / FPS:.1f} s), {s.nseg} segments, codec {s.vblanks_per_frame}, "
                  f"modes {sorted(set(g['mode'] for g in s.segments))}, audio {n} blocks")
            for k, ch in enumerate(s.chapters):
                print(f"     chapter {k}: segment {ch['segment']} frame {s.segments[ch['segment']]['frame']} {ch['name']!r}")
    elif cmd == 'segments':
        c = conts[int(sys.argv[3])]; s = hycart.Stream(rom, c['video'][0], c['video'][1])
        for g in s.segments:
            print(f"{g['index']:4d} off {g['off']:#09x} frame {g['frame']:6d} n {g['nframes']:4d} mode {g['mode']} ncb {g['ncb']} "
                  f"cb {g['cb_len']:5d} B idx {g['idx_len']:7d} B")
    elif cmd == 'extract':
        c = conts[int(sys.argv[3])]; pre = sys.argv[4]
        open(pre + '.video', 'wb').write(rom[c['video'][0]:c['video'][0] + c['video'][1]])
        open(pre + '.audio', 'wb').write(rom[c['audio'][0]:c['audio'][0] + c['audio'][1]])
    elif cmd == 'wav':
        c = conts[int(sys.argv[3])]; s = hycart.Stream(rom, c['video'][0], c['video'][1])
        pcm = audio_samples(rom, c, int(s.frames * 1371.5625))
        w = wave.open(sys.argv[4], 'wb'); w.setnchannels(1); w.setsampwidth(1); w.setframerate(16384)
        w.writeframes(bytes((b + 128) & 0xff for b in pcm)); w.close()
        print(f'{len(pcm)} samples, {len(pcm) / 16384:.1f} s')
    else:
        print(__doc__); return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
