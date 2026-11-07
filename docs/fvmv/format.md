# FVMV: container, video bitstream and audio bitstream

This document specifies the FVMV format of the four *Pokemon* GBA Video cartridges (USA,
Volumes 1 to 4) precisely enough to write a bit-exact decoder. Every statement was taken from
the player's ARM code and then checked against it. The C decoder in `reference/fvmv/` follows this text
and reproduces the cartridge output exactly for every frame and every audio block of all eight
episodes (see [Verification](#7-verification)).

Notation:

- All integers are little-endian. `u32` is an unsigned 32-bit value.
- Arithmetic on pixels and audio samples is 32-bit two's complement with wraparound, as on
  the ARM7TDMI, unless the text says otherwise. `>>` on a signed value is an arithmetic shift.
- Addresses such as `0x03005fe8` are in the Vol 1 player (see [player.md](player.md)). The decoder
  code is the same in all four volumes.

Evidence tags, as in the [VX documentation](../vx/video.md): **(code)** read from the ARM code, **(emu)**
confirmed by running the cartridge code (mGBA capture or the unicorn oracle), **(data)**
confirmed by walking all eight streams.

## 1. Stream inventory

Each cartridge holds two streams, one per episode, stored back to back in ROM. Nothing else
in the ROM uses the format: the menus are static images. **(data)**

| Cart (code) | Stream | Header offset | Frames | Duration at 7.9917 fps | Title (menu) |
|---|---|---|---|---|---|
| Vol 1 (MPAE) | 0 | 0x0001fbfc | 9,936 | 20:43.3 | A Hot Water Battle |
| Vol 1 (MPAE) | 1 | 0x00f4add4 | 9,963 | 20:46.7 | For Ho-Oh the Bells Toll! |
| Vol 2 (MPBE) | 0 | 0x0001faa0 | 9,977 | 20:48.4 | Playing with Fire! |
| Vol 2 (MPBE) | 1 | 0x00fa8dd8 | 9,937 | 20:43.4 | Johto Photo Finish |
| Vol 3 (MPCE) | 0 | 0x0001f728 | 10,213 | 21:17.9 | Pokemon - I Choose You! |
| Vol 3 (MPCE) | 1 | 0x00f84580 | 10,215 | 21:18.2 | Here Comes the Squirtle Squad |
| Vol 4 (MPDE) | 0 | 0x0001fe78 | 10,598 | 22:06.1 | Beach Blank-out Blastoise |
| Vol 4 (MPDE) | 1 | 0x011e735c | 10,133 | 21:07.9 | Go West Young Meowth |

The player finds a stream through a pointer table in its menu code, not by scanning. A
decoder can find streams by scanning for `FVMV` at 4-byte aligned offsets and checking that
the header holds 240 and 160.

## 2. Container **(code)(data)**

### 2.1 Stream header (32 bytes)

| Offset | Type | Value | Meaning |
|---|---|---|---|
| 0x00 | char[4] | `FVMV` | magic, checked by the player (0x03006068) |
| 0x04 | u32 | 9,936 ... | number of frame records |
| 0x08 | u32 | 240 | picture width in pixels |
| 0x0c | u32 | 160 | picture height in pixels |
| 0x10 | u32 | 1704 | frame period in **scanlines** (one scanline = 1232 CPU cycles) |
| 0x14 | u32 | | size of the record area that follows the header, in bytes |
| 0x18 | u32 | | size of the largest record (its `size` field). The player does not read it |
| 0x1c | u32 | 4608 | audio buffer parameter. The player uses `2 * value` bytes for its decoded-PCM buffer (section 2.4) |

The player reads all fields except 0x18. Width and height go to the decoder, but the decoder
also assumes a row stride of 480 bytes, so only 240-pixel-wide streams work.

The frame period gives the frame rate: 16,777,216 / (1704 x 1232) = **7.99171 fps**
(0.1251297 s per frame). It is not 8 fps.

### 2.2 Frame records

Records start at header + 0x20 and follow each other with no gaps. The last record ends
exactly at header + 0x20 + size(0x14). Each record is:

```
u32 size        bytes after the first 16 bytes of the record (= vlen + alen + 8)
s32 back        offset from this record to the seek point before the current one (<= 0)
u32 fwd         offset from this record to the next seek point
u32 index       frame number, 0, 1, 2, ...
u32 vlen        video payload size (a multiple of 4)
u8  video[vlen] video payload (section 3)
u32 alen        audio payload size (40 bytes per block)
u8  audio[alen] audio blocks (section 5)
```

The next record starts at `record + 16 + size`.

**Seek points.** A seek point is a frame whose video can be decoded with no reference to
earlier frames (a keyframe). The seek points divide the stream into segments. For a record
in the segment that starts at seek point S(k), `fwd` points to S(k+1) and `back` points to
S(k-1), the start of the previous segment (S(0) = frame 0 points to itself). In the last
segment both `back` and `fwd` point to its own start. A rewind from inside a segment therefore
lands on the start of the previous segment. The set of seek points is the union of all `back`
and `fwd` targets: 93 to 162 per stream, 3 to 225 frames apart (about 6 to 30 s). The player
treats a `fwd` that does not point forward as "no next seek point". The player uses the links for fast forward, rewind and chapter skip
(see [player.md](player.md)). All 992 seek-point frames decode to the same picture whatever the
memory contents, so seeking is exact. **(data)(emu)**

**Audio interleave.** A normal record carries 8 audio blocks (320 bytes = 2048 samples at
16,384 Hz = exactly 1/8 s). Frame 0 and every frame `125n - 1` (124, 249, 374, ...) carry
**9 blocks**. Over 125 frames this gives 1001 blocks, so audio runs at
1001 x 256 / 16,384 / 125 = 0.125125 s per frame. This matches the video frame period of
0.1251297 s to within 38 ppm. The extra blocks are a rate correction for the 7.9917 fps frame
clock, not priming. **(data)**

### 2.3 Video payload layout

```
u32 nbits           byte length of the bit stream (a multiple of 4)
u32 ncol            byte length of the colour stream (a multiple of 2)
u8  bits[nbits]     mode bits, read as 32-bit words
u16 col[ncol/2]     RGB555 colours and colour offsets
u8  mv[...]         motion vector indices, up to the end of the payload
u8  pad[0..3]       zero to three padding bytes, so that vlen is a multiple of 4
```

In all eight streams the decoder consumes `bits` and `col` exactly, and the MV stream up to
its 0 to 3 padding bytes. The decoder itself never checks any of these lengths: each reader
just runs on through whatever follows. **(code)(data)**

### 2.4 Decoder memory

The player allocates one 256,000-byte block on the heap, which starts at **0x02000008** in EWRAM
(the heap header takes 0x02000000-0x02000007). **(code)(emu)**

| Address | Size | Use |
|---|---|---|
| 0x02000008 | 0x12c00 | frame buffer 0 (240 x 160 x u16) |
| 0x02012c08 | 0x12c00 | frame buffer 1 |
| 0x02025808 | 0x12c00 | display buffer, used only by the menus |
| 0x02038408 | 0x2400 | decoded audio, 256 x s32 per block (2 x 4608 bytes = 9 blocks) |
| 0x0203a808 | 0x4000 | 8-bit PCM ring buffer read by DMA1 |

Frame `n` of a stream is decoded into buffer `n & 1`, with buffer `(n & 1) ^ 1` (frame `n-1`)
as the reference. The player keeps this parity across fast forward and rewind because it
toggles the buffer on every decode.

## 3. Video bitstream

### 3.1 Picture format

- The picture is GBA RGB555: bits 0-4 red, 5-9 green, 10-14 blue.
- Bit 15 has no meaning for the display (mode 3 ignores it). But the decoder writes 16-bit
  values and some operations carry into bit 15 and out of it, so **bit 15 is part of the
  decoder state**. A bit-exact decoder must keep all 16 bits of every pixel.
- There is no YUV, no transform and no residual. Every block operation works on RGB555
  pixels in the frame buffer.
- In this document a pixel address is a byte address `a`. Pixel `(x, y)` of a buffer at base
  `B` is at `B + 480*y + 2*x`. The decoder computes addresses only in this linear form and
  never clips. The edge rules in section 3.6 follow from that.

### 3.2 Readers **(code 0x03005fe8, 0x030024bc)**

**Bit reader.** It holds a 32-bit register `R`, initialised to 0x80000000. Reading one bit:

```
bit = R >> 31; R = R << 1
if R == 0:                       # the marker bit has been shifted out: refill
    W = next u32 of bits[]       # little-endian word
    bit = W >> 31; R = (W << 1) | 1
return bit
```

Bits are therefore taken MSB-first from little-endian 32-bit words. The frame driver reads one
bit before the first block and throws it away, so **bit 31 of the first word is never used**.

**Colour reader.** Each read returns the next u16 of `col[]`.

**MV reader.** Each read returns the next byte `i` of `mv[]`. The cartridge looks it up in a
256-entry table at IWRAM 0x03005be8. The table is just this formula **(data)**:

```
dx = (i & 15) - 8        # -8..7 pixels
dy = (i >> 4) - 8        # -8..7 rows
offset = 480*dy + 2*dx   # bytes
```

Vectors are integer-pel. The reference source of a block at byte position `p` is
`ref + p + offset`.

### 3.3 Frame structure

The frame is 30 x 20 blocks of 8 x 8 pixels, in raster order:

```
discard one bit
for by in 0..19:
    for bx in 0..29:
        block(8, 8, p = 480*8*by + 16*bx)
```

There is no frame header, no frame type and no slice structure.

### 3.4 Block grammar **(code 0x030024d0 and the 14 functions it calls)**

`block(w, h, p)` decodes a `w x h` block at byte position `p`. Sizes are powers of two from
8 x 8 down to 2 x 1 and 1 x 2. The cartridge has one hand-unrolled function per size. The
grammar is the same for every size, with these differences: some sizes cannot be split in one
direction, and some sizes have no 2-colour pattern mode.

```
block(w, h, p):
    if bit() == 0:
        if bit() == 0:  COPY(w, h, p, 0)                     # "skip": same position
        else:           COPY(w, h, p, mv())                  # motion-compensated copy
        return
    # first bit was 1
    if w*h > 2:                                              # 2x1 and 1x2 cannot split
        if bit() == 0:
            if w > 1 and h > 1:  vertical = bit()
            else:                vertical = (h == 1)         # the only possible direction
            if not vertical:     # top / bottom halves
                block(w, h/2, p); block(w, h/2, p + 480*(h/2))
            else:                # left / right halves
                block(w/2, h, p); block(w/2, h, p + w)       # w/2 pixels = w bytes
            return
    if PATTERN_SIZE(w, h):                                   # 8x8, 8x4, 4x8, 4x4
        if bit() == 1:  COPY_OFFSET(w, h, p, mv(), col()); return
        if bit() == 0:  FILL(w, h, p, col()); return
        PATTERN(w, h, p)                                     # see 3.5.4
        return
    if bit() == 0:  COPY_OFFSET(w, h, p, mv(), col()); return
    if w*h == 2 and bit() == 1:                              # 2x1, 1x2 only
        a = col(); store(p, a)
        b = col(); store(p + (w == 2 ? 2 : 480), b)          # two raw pixels
        return
    FILL(w, h, p, col())
```

In the COPY_OFFSET mode the MV byte is read before the colour.

The prefix codes per size, written out:

| Code | 8x8, 8x4, 4x8, 4x4 | 8x2, 4x2, 2x8, 2x4, 2x2 | 8x1, 4x1, 1x8, 1x4 | 2x1, 1x2 |
|---|---|---|---|---|
| `00` | skip | skip | skip | skip |
| `01` | MV copy | MV copy | MV copy | MV copy |
| `100` | split top/bottom | split top/bottom | split (the only way) = `10` | -- |
| `101` | split left/right | split left/right | | -- |
| `111` | copy + offset | | | |
| `1100` | fill | | | |
| `1101` | pattern (8x8: + 1 bit) | | | |
| `110` | | copy + offset | copy + offset | -- |
| `111` | | fill | fill | -- |
| `10` | | | | copy + offset |
| `110` | | | | fill |
| `111` | | | | two raw pixels |

For the one-dimensional sizes the split code is `10`, then `110` copy + offset and `111` fill.

### 3.5 Block operations

All operations write to the current buffer `out`. COPY and COPY_OFFSET read from `ref`. FILL
and PATTERN(8x8) also read neighbour pixels of `out`.

#### 3.5.1 COPY(w, h, p, offset)

```
for y in 0..h-1:
    read the w pixels at ref + p + offset + 480*y
    write them to out + p + 480*y
```

The source may have any 2-byte alignment. The cartridge has separate word-aligned and
halfword-aligned code paths, and both give the same result. Each row is read completely
before it is written.

#### 3.5.2 COPY_OFFSET(w, h, p, offset, c)

The colour offset `c` is added with one 32-bit addition to each **pair** of pixels:

```
K = c | (c << 16)
for each row, for each pixel pair (x, x+1), x even:
    s = (src[x] | src[x+1] << 16) + K       # mod 2^32
    out[x] = s & 0xffff; out[x+1] = s >> 16
```

So a carry out of the left pixel (bit 15 to bit 16) adds 1 to the red channel of the right
pixel, and carries between channels inside a pixel are not masked. For 1-pixel-wide blocks
the addition is 16-bit: `out = (src + c) & 0xffff`. The decoder does only this plain integer
addition; any per-channel meaning of `c` is up to the encoder.

#### 3.5.3 FILL(w, h, p, c)

If `w == 1` or `h == 1`, every pixel of the block is set to `c`.

Otherwise the block is filled with `c` and its top and left edges are blended with the
neighbours above and to the left. The blending also changes those neighbour pixels. Define:

```
M = 0x7bdf7bdf, L = 0x04200420              # M clears the LSB of G and B and bit 15
K  = c | (c << 16)
r0 = K & M ; r1 = K & L                     # both are 32-bit values
blend3(n, e, i, odd):                       # n = neighbour, e = edge pixel, i = inner pixel
    r5 = load16(n); r2 = r5 & L; r5 = r5 & M
    r6 = ((r0 + r5) >> 1) | (odd ? r1 : r2);  store16(e, r6)
    r6 = r6 & M
    r7 = ((r6 + r5) >> 1) | (odd ? r2 : r1);  store16(n, r7)
    r7 = ((r6 + r0) >> 1) | (odd ? r2 : r1);  store16(i, r7)
```

`store16` stores the low 16 bits. The shifts are logical shifts of 32-bit values. The upper
half of `r0` (a copy of the colour) shifts into bit 15 of the stored results, so bit 15 of the
blended pixels depends on the colour's low bits.

The steps, in this order:

```
1. for y in 2..h-1, x in 2..w-1:  pixel(x, y) = c          # interior
2. for x in 0..w-1:  blend3(pixel(x, -1), pixel(x, 0), pixel(x, 1), x & 1)     # top edge
3. for y in 0..h-1:  blend3(pixel(-1, y), pixel(0, y), pixel(1, y), y & 1)     # left edge
```

Pixel coordinates here are relative to the block. Step 3 overwrites pixels (0..1, 0..1) that
step 2 wrote. The result is a flat block whose top two rows and left two columns ramp towards
the neighbours. The neighbour row above and the neighbour column to the left are pulled
towards the fill colour. The right and bottom edges are not blended; they are blended later
if the next block is also a FILL.

#### 3.5.4 PATTERN (2-colour blocks)

The palette comes from four colour reads:

```
c0 = col(); c1 = col(); lo = col(); hi = col(); I = lo | hi << 16
pal[0] = c0; pal[1] = c1                      # all 16 bits, as read
for each 5-bit channel a (of c0), b (of c1):  # bits 15 of c0 and c1 are ignored here
    pal[2].ch = (2a + b + 1) / 3              # integer division
    pal[3].ch = (a + 2b + 1) / 3
```

The cartridge uses a 96-byte table at IWRAM 0x03001e70 for the `/3`. Its entries are
`(k + 1) / 3` for k = 0..93.

**4x4 pattern** at `p`: pixel `k` (k = 0..15, row-major) = `pal[(I >> 2k) & 3]`.

The pattern mode of each size:

| Size | After code `1101` |
|---|---|
| 4x4 | one 4x4 pattern |
| 8x4 | two 4x4 patterns: left, then right |
| 4x8 | two 4x4 patterns: top, then bottom |
| 8x8 | one more bit: `0` = four 4x4 patterns (top-left, top-right, bottom-left, bottom-right); `1` = one 8x8 interpolated pattern |

Each 4x4 pattern reads its own 4 colours.

**8x8 interpolated pattern.** The 16 indices give the pixels at odd rows and odd columns.
The other pixels are averaged:

```
for r in 0..3:                               # rows 1, 3, 5, 7
    y = 2r + 1
    left = pixel(-1, y) & M                  # 16-bit load
    for k in 0..3:
        v = pal[(I >> 2(4r + k)) & 3]
        pixel(2k + 1, y) = v
        pixel(2k, y)     = ((left + (v & M)) >> 1) | (v & L)
        left = v & M
for k in 0..3:                               # pixel pairs (2k, 2k+1), as 32-bit words
    W(y) = the 32-bit word at pixel(2k, y)
    a = W(7);  W(6) = (((a & M) + (W(5) & M)) >> 1) | (a & L)
    a = W(5);  W(4) = (((a & M) + (W(3) & M)) >> 1) | (a & L)
    a = W(3);  W(2) = (((a & M) + (W(1) & M)) >> 1) | (a & L)
    a = W(1);  W(0) = (((a & M) + (W(-1) & M)) >> 1) | (a & L)
```

All values in the second loop are read before they are overwritten, in the order shown. The
32-bit shift moves bit 0 of the right pixel's sum into bit 15 of the left pixel. Row 0 is
averaged with the pixel row above the block (row -1), and row 1..7 column 0 with the column
to the left.

### 3.6 Edges and references **(code)(emu)**

The decoder never clips. Every access is a linear address computed as in section 3.1, so:

- A vector that points left of column 0 reads the end of the previous row. One that points
  right of column 239 reads the start of the next row.
- A vector that points above row 0 or below row 159 reads outside the reference picture. With
  the player's layout this is the other frame buffer, the heap header, the display buffer, or
  (above buffer 0) the addresses below 0x02000000, which are open bus on the GBA.
- FILL and the 8x8 pattern read and write the row above and the column to the left. At
  column 0 that column is the last pixel of the row above. At row 0 the row is outside the
  picture: for buffer 1 it is the last row of buffer 0 (the reference), for buffer 0 it is
  below EWRAM (writes are lost, reads are open bus).

What the eight streams actually use **(data)**:

- **Row wrap is used, and a decoder must reproduce it.** 28,691 to 44,421 motion vectors per
  stream (about 3 to 4 per frame) have a source that crosses the left or right picture edge,
  so part of the copied block comes from the neighbouring row. A decoder that clamps or pads
  the reference picture gets these blocks wrong. The encoder evidently searched in linear
  memory: every source address range lies inside the reference buffer.
- No source range leaves the reference buffer (0 vertical cases in 80,972 frames).
- No FILL or 8x8 pattern sits at column 0 or row 0, so their neighbour accesses never wrap or
  leave the picture.

So a decoder for these streams needs linear addressing inside one 240 x 160 buffer, but no
open-bus or cross-buffer behaviour. The C decoder still models the whole EWRAM, so that it
also matches the cartridge on arbitrary input (see the fuzzing results).

### 3.7 Initial state

The frame buffers are not cleared before an episode. This does not matter: frame 0 of every
stream is a seek point, and decoding from zeroed memory or from the real menu state gives the
same frames. **(emu)**

## 4. Mode statistics **(data)**

The mode shares, from the C decoder over all eight streams, are in `stats.json` and
[features.md](features.md). In short, copies (skip and MV) cover most of the picture. The pattern modes are
rare, and most of them are in seek-point frames.

## 5. Audio bitstream

FVMV audio is a stripped-down **GSM 06.10 full-rate** decoder (ETSI EN 300 961). It keeps the
LAR and RPE quantisers and the short-term lattice synthesis. It has no long-term predictor,
no LAR interpolation and no saturation. Each 40-byte block decodes to 256 samples at 16,384 Hz.
An output stage then interpolates them 4x and quantises them to 8 bits for the 65,536 Hz
DirectSound channel. The output stage is the same as in Actimagine's VX audio (Shrek).

### 5.1 Block layout **(code 0x0300146c)**

A block is ten little-endian u32 words `w[0..9]`. Fields are numbered from bit 31 down.

| Field | Bits |
|---|---|
| LARc[0] (6 bit) | w0[31:26] |
| LARc[1] (6 bit) | w0[25:20] |
| LARc[2] (5 bit) | w0[19:15] |
| LARc[3] (5 bit) | w0[14:10] |
| LARc[4] (4 bit) | w0[9:6] |
| LARc[6] (3 bit) | w0[5:3] |
| LARc[7] (3 bit) | w0[2:0] |
| LARc[5] (4 bit) | w3[0], w5[0], w7[0], w9[0] (MSB first) |
| xmaxc[k] (6 bit), k = 0..3 | w1[13+6k : 8+6k] |
| grid[k] (2 bit), k = 0..3 | w1[1+2k : 2k] |
| pulses of sub-block k | words A = w[2+2k], B = w[3+2k] |

Sub-block `k` has 21 pulse codes `x[0..20]` of 3 bits each:

```
x[i]      = (A >> (29 - 3i)) & 7      i = 0..9
x[10 + i] = (B >> (29 - 3i)) & 7      i = 0..9
x[20]     = ((A & 3) << 1) | ((B >> 1) & 1)
```

The total is 32 + 32 + 4 x 63 + 4 = 320 bits.

### 5.2 Dequantisation (GSM 06.10 tables) **(code)(data)**

The cartridge reads the reflection coefficients and pulse amplitudes from tables in IWRAM
(0x0300101c-0x030012db and 0x0300081c-0x0300101b). Each table equals, entry for entry, the
standard GSM 06.10 decoding below, so a decoder can compute them:

```
# LARc[i] -> reflection coefficient rp[i] (GSM 5.2.8 + 4.2.9/4.2.10, 16-bit saturating)
MIC  = [-32,-32,-16,-16,-8,-8,-4,-4]
B    = [0, 0, 2048, -2560, 94, -1792, -341, -1144]
INVA = [13107,13107,13107,13107,19223,17476,31454,29708]
t = sat16((LARc + MIC[i]) * 1024); t = sat16(t - 2*B[i])
t = (INVA[i]*t + 16384) >> 15;     larp = sat16(2*t)
a = |larp|; a = a < 11059 ? 2a : a < 20070 ? a + 11059 : (a >> 2) + 26112
rp = sign(larp) * a

# xmaxc, pulse code x -> amplitude (GSM 5.2.15 APCM inverse quantisation)
FAC = [18431,20479,22527,24575,26623,28671,30719,32767]
exp = xmaxc > 15 ? (xmaxc >> 3) - 1 : 0;  mant = xmaxc - (exp << 3)
if mant == 0: exp = -4; mant = 7
else: while mant <= 7: mant = 2*mant + 1; exp -= 1
      mant -= 8
t2 = 6 - exp;  t3 = t2 > 0 ? 1 << (t2 - 1) : 0
amp = (((FAC[mant] * ((2x - 7) << 12) + 16384) >> 15) + t3) >> t2
```

### 5.3 Excitation **(code 0x03001988)**

Each sub-block `k` covers 64 samples `e[64k .. 64k+63]`. All 64 are zero except the 21 pulse
positions:

```
e[64k + grid[k] + 3i] = amp(xmaxc[k], x[i])     i = 0..20
```

So the pulses use grid[k] leading zeros, 21 pulses at stride 3 (61 samples), and
3 - grid[k] trailing zeros. GSM uses 13 pulses per 40 samples; FVMV uses 21 per 64.

### 5.4 Synthesis **(code 0x03001b88)**

The state is the lattice `v[0..8]` and the de-emphasis memory `msr`. All are int32, start at
zero at power-on, and are **never reset**, not even between episodes.

```
for n in 0..255:
    s = e[n]
    for i in 7 down to 0:
        s      = s - ((rp[i] * v[i] + 0x4000) >> 15)
        v[i+1] = v[i] + ((rp[i] * s + 0x4000) >> 15)
    v[0] = s
    msr  = s + ((msr * 28180 + 0x4000) >> 15)        # de-emphasis, as GSM 5.3.5
    y[n] = 2 * msr
```

Each product is computed as a 32-bit multiply-accumulate (`mla`) that wraps, followed by an
arithmetic shift. There is no clamping anywhere. The same `rp[]` is used for all 256 samples.

### 5.5 Output stage **(code 0x03001d18)**

The state is `prev` (last input sample), `err` (last quantisation error) and `fb` (feedback).
All are int32, zero at power-on, and never reset. For each input sample `x = y[n]`:

```
for v in ((3*prev + x) >> 2, (prev + x) >> 1, (3*x + prev) >> 2, x):
    q = (v + 0x80 - fb) >> 8
    q = clamp(q, -128, 127)
    output q as a signed byte
    e_old = err
    err = (q << 8) - v
    f = e_old + 2*err
    fb = f - (f >> 4)
prev = x
```

So the output is 1024 signed 8-bit samples per block at 65,536 Hz: 4x linear interpolation
followed by noise shaping with `fb = (e[n-1] + 2 e[n]) * 15/16`. The player writes them into
the 16 KiB ring for DMA1 and FIFO A. **(code)(emu)**

## 6. Comparison with VX audio

VX audio is specified in [../vx/container.md](../vx/container.md) sections 6 and 9.

| | FVMV audio | VX audio (Shrek) |
|---|---|---|
| Samples per block | 256 | 128 |
| Block size | fixed 40 bytes | 10-20 bytes, 4 modes |
| LPC coding | GSM 06.10 LARs, scalar 6/6/5/5/4/4/3/3 bits | 3-stage VQ codebook |
| Pulses | 4 x 21 pulses, 3 bit, stride 3, 2-bit grid | mode 0: 42 x 3 bit, stride 3, 2-bit start |
| Long-term prediction | none | yes |
| Synthesis | 8-tap lattice, Q15, no saturation | 8-tap direct form after a lattice conversion |
| Output stage | identical: 4x interpolation, 8-bit quantiser, (e1 + 2e) * 15/16 | |

## 7. Verification

- **mGBA whole-stream capture.** The cartridge was played in mGBA from power-on through each
  of the eight episodes. The capture recorded the md5 of the output buffer after every decode
  and the md5 of every decoded audio block, both before and after the output stage. The
  C decoder matches **80,972 / 80,972 frames** (all 16 bits) and **648,427 / 648,427 audio
  blocks** (int32 synthesis and 8-bit PCM). The manifests are in `verify/fvmv/` (`pokemon1_0` ... `pokemon4_1`), and
  `tools/fvmv/verify.py` repeats the check. One capture limit: in Vol 3 the player clears the PCM
  ring when an episode ends, within the same video refresh as the last frame's audio, so the
  capture could not read the last frame's 8-bit PCM. Those 16 PCM hashes (the last frame of
  each Vol 3 stream) come from the unicorn oracle. The oracle ran the cartridge's audio
  routines over both whole Vol 3 streams and matched the C decoder on all 163,588 blocks.
- **Unicorn oracle fuzzing.** The cartridge's own decoder routines were run in unicorn and
  compared with the C code, with the whole EWRAM compared after each case. There were
  1,000 random payloads, 1,000 bit-flipped real frames and 1,000 real frames, all on random
  memory with random buffer placement, and every (size, mode) pair was hit. There were also
  50,000 random and 32,033 real audio blocks, with the state compared after each block.
  There were no mismatches (`tools/fvmv/fuzz.py`).
