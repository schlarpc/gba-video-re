#!/usr/bin/env python3
"""VX++ (Actimagine GBA Video) container parser / demuxer.

See docs/container.md for the format. Every command takes [--stream OFF|IDX]: the VX++
header offset (or index, see `info`) of the stream to use; default = the first one.

  vxdemux.py info   [ROM]                 header, chapters, seek table
  vxdemux.py walk   [ROM] [--video]       walk + validate the entire movie (audio framing,
                                          seek table, segment starts); --video also walks the
                                          macroblock grammar of all 37837 frames (~3 min) and
                                          checks every segment lands on the next seek offset
  vxdemux.py extract [ROM] OUTDIR [--per-frame]
                                          video.bin / audio.bin elementary streams,
                                          segments/seg_NNN.vxv (one per seek segment),
                                          index.csv (per-frame video/audio mapping),
                                          --per-frame: frames/NNNNN.vxv (video bits of that frame,
                                          16-byte 'VXF1' prefix: skip, nbits, qp_field) and
                                          frames/NNNNN.aud (that frame's raw AFrames)
  vxdemux.py wav    [ROM] OUT.wav [--hw8] [--frames A:B]
                                          decode audio: 16-bit 16384 Hz mono (codec output,
                                          clipped), or --hw8: the exact unsigned 8-bit 65536 Hz
                                          stream the GBA feeds to Direct Sound FIFO A

Video has no per-frame byte boundaries in the container: the movie is one continuous
MSB-first bitstream (read as little-endian halfwords) and only seek points are indexed.
Per-frame video boundaries come from walking the macroblock grammar (vxvwalk.py), checked
against all 194 interior seek offsets; audio is assigned to frames by the player's clocks.
"""
import os, struct, sys, wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hashlib
import vxaudio
import vxcart
import vxvwalk
import json

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')

DEFAULT_ROM = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shrek.gba')
HDR_SIZE = 0x38
MAGIC = b'VX++'


class BitReader:
    """The player's video reader: 16-bit little-endian halfwords, MSB first."""

    def __init__(self, buf, bitpos=0):
        self.b = buf
        self.p = bitpos

    def bit(self):
        hw = self.p >> 4
        w = self.b[2 * hw] | (self.b[2 * hw + 1] << 8)
        v = (w >> (15 - (self.p & 15))) & 1
        self.p += 1
        return v

    def bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def ue(self):
        z = 0
        while self.bit() == 0:
            z += 1
        return (1 << z) - 1 + self.bits(z)


class VXContainer:
    def __init__(self, rom, base=None):
        self.rom = rom
        if base is None:
            base = self.find()
        self.H = H = base
        f = struct.unpack_from('<4s13I', rom, H)
        if f[0] != MAGIC:
            raise ValueError('no VX++ magic at 0x%x' % H)
        (self.magic, self.nframes, self.width, self.height, self.timer3_reload,
         self.quantizer, self.sample_rate, self.audio_off, self.chapter_off,
         self.last_chapter_frame, self.seek_off, self.nseek, self.last_seek_frame,
         self.fwd_skip_limit) = f
        self.fps = (1 << 24) / (64.0 * 3 * self.timer3_reload)
        self.video_start = H + HDR_SIZE
        self.video_end = H + self.audio_off          # exclusive, 0x200-aligned
        self.audio_start = H + self.audio_off
        self.audio_end = H + self.chapter_off        # exclusive, 0x200-aligned
        c = H + self.chapter_off
        n = struct.unpack_from('<I', rom, c)[0] if self.chapter_off else 0
        self.chapters = list(struct.unpack_from('<%dI' % n, rom, c + 4))
        s = H + self.seek_off
        self.seek = [struct.unpack_from('<4I', rom, s + 16 * i) for i in range(self.nseek)]
        self.video = memoryview(rom)[self.video_start:self.video_end]
        self.audio = memoryview(rom)[self.audio_start:self.audio_end]

    @staticmethod
    def find_all(rom):
        return vxcart.find_streams(rom)   # sector-aligned magic (the cart reads 0x200-byte sectors)

    def find(self):
        return vxcart.pick_stream(self.rom)

    def cache_path(self):
        """Per-stream cache file for the per-frame bit positions."""
        tag = hashlib.sha1(self.rom[self.H:self.H + 0x38] + bytes(self.video[:4096])).hexdigest()[:12]
        return os.path.join(OUT_DIR, 'frame_bits_%x_%s.json' % (self.H, tag))

    # --- seek-segment level
    def segments(self):
        """[(index, first_frame, nframes, start_bit, end_bit, audio_start_byte, audio_end_byte)]"""
        ent = self.seek
        assert ent[-1][0] == self.nframes and ent[-1][1:] == (0, 0, 0), 'missing sentinel'
        real = ent[:-1]
        out = []
        for i, e in enumerate(real):
            if i + 1 < len(real):
                eb, ea = real[i + 1][1], real[i + 1][2]
            else:
                eb, ea = None, None
            out.append((i, e[0], ent[i + 1][0] - e[0], e[1], eb, e[2], ea))
        return out

    def video_tail_bits(self):
        j = len(self.video)
        while j and self.video[j - 1] == 0:
            j -= 1
        return j * 8

    # --- audio
    def aframes(self):
        """Walk all AFrames: yields (index, byte offset in audio stream, size)."""
        a = self.audio
        p, i = vxaudio.CODEBOOK_SIZE, 0
        while p + 4 <= len(a):
            w1, w2 = a[p] | a[p + 1] << 8, a[p + 2] | a[p + 3] << 8
            if w1 == 0 and w2 == 0 and not any(a[p:p + 32]):
                return
            n = vxaudio.AFRAME_BYTES[(w2 >> 12) & 3]
            yield i, p, n
            p += n
            i += 1

    def aframe_for_frame(self, f):
        """First AFrame played with video frame f (128 AFrames/s, 7 fps). The player restarts
        both clocks together at a seek point, so the mapping is anchored on the seek entry at
        or before f: a_seek + floor((128 (f - f_seek) + 1) / 7). In Shrek this equals
        floor((128 f + 1) / 7) everywhere; other movies' encoders placed some seek points'
        audio one AFrame either side of that global formula."""
        if f >= self.nframes:
            e = self._seek_aframes()[-1]
            return e[1] + (128 * (f - e[0]) + 1) // 7
        e = max((x for x in self._seek_aframes() if x[0] <= f), default=(0, 0))
        return e[1] + (128 * (f - e[0]) + 1) // 7

    def _seek_aframes(self):
        """[(seek frame, AFrame index of its audio_byte)]."""
        if not hasattr(self, '_sa'):
            pos = {p: i for i, p, _ in self.aframes()}
            self._sa = [(e[0], pos.get(e[2], (128 * e[0] + 1) // 7)) for e in self.seek[:-1]]
        return self._sa


def video_frame_bits(c, cache=True, verbose=False):
    """Per-frame start bits (len nframes+1) by walking the macroblock grammar
    (vxvwalk.py); every segment must end exactly on the next seek offset."""
    cache = c.cache_path() if cache else None
    if cache and os.path.exists(cache):
        j = json.load(open(cache))
        if len(j['frame_start_bits']) == c.nframes + 1:
            return j['frame_start_bits'], j['frame_qp_field']
    sw = vxvwalk.swap16(bytes(c.video))
    lut_rom = vxcart.player_layout(c.rom)["ewram"][0] + 0x10e4   # EWRAM 0x020010e4 (0x9ae4 in Shrek)
    lut = c.rom[lut_rom:lut_rom + vxvwalk.LUT_SIZE]
    tail = c.video_tail_bits()
    starts, qs = [], []
    for s in c.segments():
        st, q = vxvwalk.walk_segment(sw, lut, s[3], s[2], c.width, c.height)
        exp = s[4] if s[4] is not None else None
        if exp is not None and st[-1] != exp:
            raise ValueError('segment %d ends at bit %d, seek table says %d' % (s[0], st[-1], exp))
        if exp is None and not (st[-1] <= tail and tail - st[-1] < 128):
            raise ValueError('last segment ends at %d, data ends at %d' % (st[-1], tail))
        starts += st[:-1]; qs += q
        if verbose:
            print('\rsegment %d/%d' % (s[0] + 1, len(c.segments())), end='', file=sys.stderr)
    starts.append(st[-1])
    if cache:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump({'frame_start_bits': starts, 'frame_qp_field': qs}, open(cache, 'w'))
    return starts, qs


def cmd_info(c):
    print('VX++ stream at ROM 0x%x' % c.H)
    print('  frames           %d' % c.nframes)
    print('  size             %dx%d (%dx%d MBs)' % (c.width, c.height, c.width // 16, c.height // 16))
    print('  timer3 reload    %d (0x%x) -> %.5f fps (3 ticks/frame, F/64)' % (c.timer3_reload, c.timer3_reload, c.fps))
    print('  quantizer field  %d (unused by VX++ player)' % c.quantizer)
    print('  sample rate      %d Hz (not read by player; hard-coded 128 AFrames/s x 128)' % c.sample_rate)
    print('  video            ROM 0x%x..0x%x (%d bytes)' % (c.video_start, c.video_end, c.video_end - c.video_start))
    print('  audio            ROM 0x%x..0x%x (%d bytes)' % (c.audio_start, c.audio_end, c.audio_end - c.audio_start))
    print('  chapters         ROM 0x%x: %d -> %s' % (c.H + c.chapter_off, len(c.chapters), c.chapters))
    print('  last chapter     %d   last seek frame %d   fwd skip limit %d' % (c.last_chapter_frame, c.last_seek_frame, c.fwd_skip_limit))
    print('  seek table       ROM 0x%x: %d entries (incl. sentinel)' % (c.H + c.seek_off, c.nseek))
    for e in c.seek[:4] + [None] + c.seek[-3:]:
        print('     ...' if e is None else '    frame %6d  video_bit %10d  audio_byte %9d  pad %d' % e)


def walk(c, verbose=True, video_frames=False):
    ok = True
    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            print('FAIL:', msg)
    segs = c.segments()
    # seek table monotonic, chapters on seek points
    fr = [e[0] for e in c.seek]
    check(fr == sorted(fr) and len(set(fr)) == len(fr), 'seek frames not increasing')
    check(all(c.seek[i][1] < c.seek[i + 1][1] for i in range(len(segs) - 1)), 'video bits not increasing')
    check(all(c.seek[i][2] < c.seek[i + 1][2] for i in range(len(segs) - 1)), 'audio bytes not increasing')
    check(all(e[3] == 0 for e in c.seek), 'seek pad field nonzero')
    seekset = set(fr)
    # Shark Tale has 2 chapters one frame after a seek point; the player seeks to the entry
    # containing the chapter frame, so it lands on that seek point (harmless).
    off_seek = [ch for ch in c.chapters if ch not in seekset]
    check(all(max(x for x in fr if x <= ch) >= ch - 1 for ch in off_seek), 'chapter far from a seek point')
    # hdr+0x24 = last chapter start; 0 when the table holds only frame 0 (short streams)
    check((c.chapters[-1] if len(c.chapters) > 1 else 0) == c.last_chapter_frame, 'hdr+0x24 != last chapter')
    check(c.seek[-2][0] == c.last_seek_frame, 'hdr+0x30 != last seek frame')
    # each segment's first video frame starts with ue(v) QP != 0 (a leading 0 bit)
    qps = []
    for s in segs:
        br = BitReader(c.video, s[3])
        q = br.ue()
        qps.append(q)
        check(q != 0, 'segment %d does not start with a QP' % s[0])
    tail = c.video_tail_bits()
    check(segs[-1][3] < tail <= len(c.video) * 8, 'last segment beyond video region')
    # audio
    seek_a = {e[2]: e[0] for e in c.seek[:-1]}
    hits = {}
    intra = []
    n = 0
    last_end = 0
    for i, p, sz in c.aframes():
        if p in seek_a:
            hits[p] = i
        if (c.audio[p + 1] >> 1) == 0x7f:
            intra.append(p)
        n = i + 1
        last_end = p + sz
    check(len(hits) == len(seek_a), 'only %d/%d seek audio offsets are AFrame starts' % (len(hits), len(seek_a)))
    check(sorted(intra) == sorted(seek_a), 'intra AFrames != seek points')
    # the encoder puts each seek point's audio within one AFrame of floor((128 f + 1) / 7)
    dev = [i - (128 * seek_a[p] + 1) // 7 for p, i in hits.items()]
    check(all(abs(d) <= 1 for d in dev), 'seek-point audio more than 1 AFrame off the 7 fps / 128 Hz clock')
    check(not any(c.audio[last_end:]), 'nonzero audio tail')
    check((128 * c.nframes + 1) // 7 <= n + 1, 'audio shorter than video')
    if video_frames:
        try:
            fb, qs = video_frame_bits(c, cache=False, verbose=verbose)
            sizes = [fb[i + 1] - fb[i] for i in range(c.nframes)]
            if verbose:
                print('\nvideo per-frame walk: %d frames, all %d segment ends exact; frame sizes %d..%d bits, '
                      'QP changes inside segments: %d' % (c.nframes, len(segs), min(sizes), max(sizes),
                                                         sum(1 for f, q in enumerate(qs) if q and f not in seekset)))
        except ValueError as e:
            check(False, 'video walk: %s' % e)
    if verbose:
        print('stream @0x%x: %d frames, %d seek segments, %d chapters' % (c.H, c.nframes, len(segs), len(c.chapters)))
        print('video: %d bits used of %d (tail zero pad %d bytes); segment sizes %d..%d bits' % (
            tail, len(c.video) * 8, len(c.video) - tail // 8,
            min((s[4] or tail) - s[3] for s in segs), max((s[4] or tail) - s[3] for s in segs)))
        print('segment QPs: min %d max %d, first %s' % (min(qps), max(qps), qps[:8]))
        print('audio: %d AFrames (%.3f s), %d bytes + %d zero pad; all %d seek offsets hit, %d intra AFrames' % (
            n, n * 128 / 16384, last_end, len(c.audio) - last_end, len(hits), len(intra)))
        print('seek-point AFrame - floor((128f+1)/7): %s; chapters one frame past a seek point: %s' % (
            {d: dev.count(d) for d in sorted(set(dev))}, off_seek or 'none'))
        print('audio per frame: AFrames [floor((128f+1)/7), floor((128(f+1)+1)/7)) from the seek point; '
              'floor((128n+1)/7) = %d of %d AFrames' % ((128 * c.nframes + 1) // 7, n))
        print('WALK', 'OK' if ok else 'FAILED')
    return ok


def frame_index(c):
    """Per-frame rows: (frame, segment, is_segment_start, video_start_bit, video_end_bit, qp_field,
    aframe_first, aframe_count, audio_byte_start, audio_byte_end)."""
    fb, qs = video_frame_bits(c)
    af = [(p, sz) for _, p, sz in c.aframes()]
    segs = c.segments()
    tail = c.video_tail_bits()
    rows = []
    si = 0
    for f in range(c.nframes):
        while si + 1 < len(segs) and segs[si + 1][1] <= f:
            si += 1
        s = segs[si]
        a0 = c.aframe_for_frame(f)
        a1 = c.aframe_for_frame(f + 1) if f + 1 < c.nframes else len(af)
        a1 = min(a1, len(af))
        b0 = af[a0][0] if a0 < len(af) else len(c.audio)
        b1 = af[a1][0] if a1 < len(af) else af[-1][0] + af[-1][1]
        rows.append((f, si, int(f == s[1]), fb[f], fb[f + 1], qs[f], a0, a1 - a0, b0, b1))
    return rows


def bits_to_bytes(buf, start, end):
    """Copy bit range [start,end) as halfword-aligned raw bytes plus the leading skip.
    Returns (skip_bits, nbits_incl_skip, payload) where payload keeps the cartridge's
    little-endian halfword order (read with BitReader at bit `skip`)."""
    h0 = start >> 4
    h1 = (end + 15) >> 4
    return start & 15, end - (h0 << 4), bytes(buf[2 * h0:2 * h1])


def cmd_extract(c, out, per_frame=False):
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'video.bin'), 'wb').write(bytes(c.video))
    open(os.path.join(out, 'audio.bin'), 'wb').write(bytes(c.audio))
    open(os.path.join(out, 'audio_codebook.bin'), 'wb').write(bytes(c.audio[:vxaudio.CODEBOOK_SIZE]))
    sd = os.path.join(out, 'segments')
    os.makedirs(sd, exist_ok=True)
    tail = c.video_tail_bits()
    with open(os.path.join(out, 'segments.csv'), 'w') as f:
        f.write('segment,first_frame,nframes,start_bit,end_bit,skip_bits,audio_start,audio_end\n')
        for s in c.segments():
            eb = s[4] if s[4] is not None else tail
            skip, nb, payload = bits_to_bytes(c.video, s[3], eb)
            # 16-byte prefix like mobipeg's GVX1 but keeping LE halfword order:
            hdr = struct.pack('<4sIII', b'VXS1', skip, nb, s[2])
            open(os.path.join(sd, 'seg_%03d.vxv' % s[0]), 'wb').write(hdr + payload)
            f.write('%d,%d,%d,%d,%d,%d,%d,%s\n' % (s[0], s[1], s[2], s[3], eb, skip, s[5], s[6] if s[6] is not None else ''))
    rows = frame_index(c)
    with open(os.path.join(out, 'index.csv'), 'w') as f:
        f.write('frame,segment,is_segment_start,video_start_bit,video_end_bit,qp_field,'
                'aframe_first,aframe_count,audio_byte_start,audio_byte_end\n')
        for r in rows:
            f.write(','.join(map(str, r)) + '\n')
    if per_frame:
        fd = os.path.join(out, 'frames')
        os.makedirs(fd, exist_ok=True)
        for r in rows:
            open(os.path.join(fd, '%05d.aud' % r[0]), 'wb').write(bytes(c.audio[r[8]:r[9]]))
            skip, nb, payload = bits_to_bytes(c.video, r[3], r[4])
            open(os.path.join(fd, '%05d.vxv' % r[0]), 'wb').write(
                struct.pack('<4sIII', b'VXF1', skip, nb, r[5]) + payload)
    print('wrote', out)


def cmd_wav(c, out, hw8=False, frames=None):
    dec = vxaudio.VXAudioDecoder(vxaudio.Codebook(bytes(c.audio[:vxaudio.CODEBOOK_SIZE])))
    af = list(c.aframes())
    a0, a1 = 0, len(af)
    if frames:
        f0, f1 = frames
        # start decoding at the seek point at or before f0 (decoder state resets at intra)
        segs = [s for s in c.segments() if s[1] <= f0]
        start_af = c.aframe_for_frame(segs[-1][1])
        a0 = c.aframe_for_frame(f0)
        a1 = min(len(af), c.aframe_for_frame(f1))
    else:
        start_af = 0
    buf = bytes(c.audio)
    w = wave.open(out, 'wb')
    w.setnchannels(1)
    if hw8:
        w.setsampwidth(1); w.setframerate(65536)
    else:
        w.setsampwidth(2); w.setframerate(16384)
    chunk = []
    for i in range(start_af, a1):
        _, p, _ = af[i]
        _, s, o = dec.decode(buf, p)
        if i < a0:
            continue
        if hw8:
            chunk.append(bytes((b ^ 0x80) for b in o))   # WAV 8-bit is unsigned
        else:
            chunk.append(struct.pack('<128h', *[-32768 if v < -32768 else 32767 if v > 32767 else v for v in s]))
        if len(chunk) >= 1024:
            w.writeframes(b''.join(chunk)); chunk = []
            if not frames:
                print('\r%d/%d' % (i, a1), end='', file=sys.stderr)
    w.writeframes(b''.join(chunk))
    w.close()
    print('\nwrote', out, file=sys.stderr)


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 1
    cmd = argv[1]
    rest, stream = [], None
    it = iter(argv[2:])
    for a in it:
        if a == '--stream':
            stream = int(next(it), 0)
        elif a == '--frames':
            next(it)
        else:
            rest.append(a)
    args = [a for a in rest if not a.startswith('--')]
    flags = [a for a in rest if a.startswith('--')]
    rom_path = args.pop(0) if args and args[0].endswith('.gba') else DEFAULT_ROM
    rom = open(rom_path, 'rb').read()
    c = VXContainer(rom, vxcart.pick_stream(rom, stream))
    if cmd == 'info':
        cmd_info(c)
        print('VX++ streams (sector-aligned magic) in ROM:')
        for i, h in enumerate(VXContainer.find_all(rom)):
            n, w, hh = struct.unpack_from('<3I', rom, h + 4)
            print('  %d  0x%x  %dx%d  %d frames' % (i, h, w, hh, n))
    elif cmd == 'walk':
        return 0 if walk(c, video_frames='--video' in flags) else 2
    elif cmd == 'extract':
        cmd_extract(c, args[0], '--per-frame' in flags)
    elif cmd == 'wav':
        fr = None
        for i, a in enumerate(argv):
            if a == '--frames':
                x, y = argv[i + 1].split(':'); fr = (int(x), int(y))
        cmd_wav(c, args[0], '--hw8' in flags, fr)
    else:
        print(__doc__); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
