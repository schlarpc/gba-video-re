"""Actimagine VX audio decoder, modelled on the GBA VX++ player (Shrek GBA Video).

Reference implementation of IWRAM FUN_03006a14 (ROM 0x126dc) and its callees,
bit-for-bit including 32-bit wraparound, the two 0x400-byte pulse/sample
buffers at VRAM 0x06013680/0x06013a80 and the 4x interpolating, noise-shaped
8-bit output stage that feeds Direct Sound FIFO A at 65536 Hz.

Codebook ("codec info", first 3124 bytes of the audio stream):
  +0     int16 lpc_cb[3][64][8]
  +3072  int16 scale_mod[8]
  +3088  int32 lpc_base[8]
  +3120  int32 scale_initial
AFrame: LE halfwords, w1, w2, then 8/5/4/3 pulse words (mode = (w2>>12)&3);
128 output samples at 16384 Hz.
"""
import struct

PULSE_WORDS = (8, 5, 4, 3)
PULSE_STRIDE = (3, 3, 4, 5)
AFRAME_BYTES = (20, 14, 12, 10)
CODEBOOK_SIZE = 3124
VOL = [min(8, i + 1, 128 - i) for i in range(128)]


def s32(x):
    x &= 0xffffffff
    return x - 0x100000000 if x & 0x80000000 else x


def mul(a, b):
    return s32(a * b)


def aframe_size(buf, off):
    w2 = buf[off + 2] | (buf[off + 3] << 8)
    return AFRAME_BYTES[(w2 >> 12) & 3]


class Codebook:
    def __init__(self, blob):
        assert len(blob) >= CODEBOOK_SIZE
        v = struct.unpack_from('<1536h', blob, 0)
        self.cb = [[list(v[(i * 64 + j) * 8:(i * 64 + j) * 8 + 8]) for j in range(64)] for i in range(3)]
        self.scale_mod = list(struct.unpack_from('<8h', blob, 3072))
        self.lpc_base = list(struct.unpack_from('<8i', blob, 3088))
        self.scale_initial = struct.unpack_from('<i', blob, 3120)[0]


class VXAudioDecoder:
    """State mirrors the player's: VRAM 0x06013600..0x06013680, the two
    buffers B[0]/B[1] (each: lo = 128 words, hi = 128 words) and the IWRAM
    8-sample history ring at 0x0300660c."""

    def __init__(self, codebook, vram_init=None):
        self.c = codebook
        self.filt = [0] * 8          # 0x06013600
        self.infl = {0x20: [0] * 8, 0x40: [0] * 8}   # 0x06013620 / 0x06013640
        self.scale = 0               # 0x06013660
        self.toggle = 0              # 0x06013664
        self.sh_e = 0                # 0x06013668
        self.sh_fb = 0               # 0x0601366c
        self.sh_prev = 0             # 0x06013670
        self.hist = [0] * 8          # 0x0300660c, hist[-1] = newest
        self.B = [[0] * 256, [0] * 256]
        if vram_init is not None:
            vram_init(self)

    # --- 0x0300645c: filter update + reflection->direct conversion
    def _filter(self, base, i0, i1, i2):
        cb = self.c.cb
        f = [s32(base[k] + cb[0][i0][k] + cb[1][i1][k] + cb[2][i2][k]) for k in range(8)]
        self.filt = f
        psi = []
        for i in range(8):
            c = f[i]
            old = psi[:]
            for j in range(i):
                psi[j] = s32(old[j] + (mul(old[i - 1 - j], c) >> 15))
            psi.append(c)
        return [s32(-(p >> 1)) for p in psi]

    # --- 0x03006ba0: parse header fields, scale update, add pulses into dst
    def _pulses(self, w1, w2, words, dst):
        smi = (w1 >> 6) & 7
        start = (w2 >> 14) & 3
        mode = (w2 >> 12) & 3
        self.scale = mul(self.scale, self.c.scale_mod[smi]) >> 13
        s = self.scale
        pos = 128 + start
        if mode == 0:
            a, b = s32(2 * s), s32(-7 * s)
            low = 0
            for w in words[:8]:
                for sh in (13, 10, 7, 4, 1):
                    dst[pos] = s32(dst[pos] + s32(a * ((w >> sh) & 7) + b)); pos += 3
                low = (low << 1) | (w & 1)
            for v in ((low >> 5) & 7, (low >> 2) & 7):
                dst[pos] = s32(dst[pos] + s32(a * v + b)); pos += 3
        else:
            a, b = s32(2 * s), s32(-3 * s)
            st = PULSE_STRIDE[mode]
            for w in words[:PULSE_WORDS[mode]]:
                for sh in (14, 12, 10, 8, 6, 4, 2, 0):
                    dst[pos] = s32(dst[pos] + s32(a * ((w >> sh) & 3) + b)); pos += st
        return (w1 & 0x3f, (w2 >> 6) & 0x3f, w2 & 0x3f)

    # --- 0x0300662c: 8-tap all-pole synthesis, n samples
    def _synth(self, src, si, dst, di, infl, n):
        h = self.hist
        for k in range(n):
            acc = s32(src[si + k] << 14)
            for j in range(8):
                acc = s32(acc + infl[j] * h[7 - j])
            v = acc >> 14
            h.pop(0); h.append(v)
            dst[di + k] = v

    def decode(self, buf, off=0):
        """Decode one AFrame at buf[off]. Returns (nbytes, samples[128], pcm8 bytes[512])."""
        w1, w2 = struct.unpack_from('<HH', buf, off)
        mode = (w2 >> 12) & 3
        n = AFRAME_BYTES[mode]
        words = struct.unpack_from('<%dH' % PULSE_WORDS[mode], buf, off + 4)
        pfo = w1 >> 9
        B0, B1 = self.B
        if pfo == 0x7f:                       # 0x03006c60: intra
            self.scale = self.c.scale_initial  # 0x030069c0
            self.toggle = 1
            self.hist = [0] * 8
            self.sh_e = self.sh_fb = self.sh_prev = 0
            for i in range(128, 256):
                B1[i] = 0
            idx = self._pulses(w1, w2, words, B1)
            infl = self._filter(self.c.lpc_base, *idx)
            self.infl[0x40] = infl
            out = B0
            self._synth(B1, 128, B0, 0, infl, 128)
        else:                                 # 0x03006c9c: predicted
            cur, oth = self.B[self.toggle], self.B[self.toggle ^ 1]
            cur[0:128] = oth[128:256]
            if pfo == 0x7e:
                for i in range(128, 256):
                    cur[i] = 0
            else:                             # 0x03006288
                b = 0x7f - pfo
                for i in range(128):
                    cur[128 + i] = mul(oth[b + i], VOL[i]) >> 4
            idx = self._pulses(w1, w2, words, cur)
            infl = self._filter(self.filt, *idx)
            if self.toggle == 1:
                cslot, pslot = 0x40, 0x20
            else:
                cslot, pslot = 0x20, 0x40
            prev = self.infl[pslot]
            self.infl[cslot] = infl
            q1 = [(a + b) >> 1 for a, b in zip(infl, prev)]
            q0 = [(a + b) >> 1 for a, b in zip(q1, prev)]
            q2 = [(a + b) >> 1 for a, b in zip(q1, infl)]
            for q, (blk) in zip((q0, q1, q2, infl), range(4)):
                self._synth(cur, 128 + 32 * blk, oth, 32 * blk, q, 32)
            out = oth
        self.toggle ^= 1
        samples = out[0:128]
        return n, samples, self._output(samples)

    # --- 0x03006a54..0x03006b54: 4x interpolation + error-feedback 8-bit quantiser
    def _output(self, samples):
        e, fb, prev = self.sh_e, self.sh_fb, self.sh_prev
        o = bytearray(512)
        k = 0
        for cur in samples:
            for x in ((3 * prev + cur) >> 2, (cur + prev) >> 1, (3 * cur + prev) >> 2, cur):
                x = s32(x)
                q = (s32(x + 0x80 - fb)) >> 8
                q = -128 if q < -128 else 127 if q > 127 else q
                ne = s32((q << 8) - x)
                fb = s32(e + 2 * ne)
                fb = s32(fb - (fb >> 4))
                e = ne
                o[k] = q & 0xff; k += 1
            prev = cur
        self.sh_e, self.sh_fb, self.sh_prev = e, fb, prev
        return bytes(o)
