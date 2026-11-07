# Majesco VQ video layer (shared by Hydrogen and ADS)

Code: `reference/majesco/vq.c`, `reference/majesco/vq.h`.
Status: complete. `vq.c` is bit-exact with the Hydrogen players on every frame of all 62 Hydrogen
streams (402,649 frames, modes 1/3/5/7, all 10 builds; emulator framebuffer after every blit), and
with the cartridge's own converter and blit run in unicorn on 900 random codebooks/frames in all 8
modes and all 11 brightness levels (`tools/hydrogen/fuzz.py vq`). See [../hydrogen/README.md](../hydrogen/README.md).
The ADS work independently modelled the same layer (`tools/ads/adsref.py`) and got the same
dither, clamp and slot layout; see "Agreement with the ADS model" below.

This document covers what happens after the compression layer has produced two byte strings
for a segment: the **codebook** and the **index stream**. The compression layer differs by family
(Hydrogen: a DEFLATE variant, [../hydrogen/format.md](../hydrogen/format.md); ADS: LZMA, [../ads/format.md](../ads/format.md)).

## 1. Segment header word and block geometry

Both families start every segment (a run of frames that shares one codebook) with two words.
Word 0 carries the VQ parameters:

| bits | field |
|---|---|
| 0-2 | `mode`: block geometry, table below |
| 3-5 | always 6 on every observed stream (both families); not read by the VQ code |
| 6-15 | `ncb`: codebook entries; 256 on every observed stream |
| 16-31 | frames in this segment |

| mode | block | grid (blocks) | index bytes per frame | VRAM entry stride | chroma pairs per entry |
|---|---|---|---|---|---|
| 0, 1 | 4x4 | 60 x 40 | 2400 | 32 B (16 halfwords) | 4 |
| 2, 3 | 4x3 | 60 x 53 | 3180 | 24 B (12 halfwords) | 3 |
| 4, 5 | 3x3 | 80 x 53 | 4240 | 32 B (16 halfwords) | 2 |
| 6, 7 | 4x2 | 60 x 80 | 4800 | 16 B (8 halfwords) | 2 |

Only odd modes occur in the streams. The even modes are a half-implemented variant: the
chroma unprediction step counts 1 chroma pair per entry for them, but the converter reads them
exactly like the odd mode above, so an even-mode stream would decode with misplaced chroma.
A decoder needs to support the odd modes only; `vq.c` mirrors the cartridge for even modes too.

The 53-row grids cover 159 lines. Line 159 is filled by the blitter from BG palette RAM:
halfwords 0..23 of 0x05000000, repeated 10 times across the row. Both players keep the BG
palette at zero during playback, so line 159 is black.

## 2. Codebook bytes

The inflated codebook is planar:

```
Y[ncb * bw * bh]          luma, unsigned
U[ncb * C]                Cb, signed 8-bit     C = chroma pairs per entry (table above)
V[ncb * C]                Cr, signed 8-bit
```

Luma of one entry is stored as **2x2 quads** for the 4-wide modes, not in raster order:
luma byte `j` of an entry goes to pixel slot `POS[j]` (slot = row*4 + col),

```
4x4, 4x2: POS = {0,1,4,5, 2,3,6,7, 8,9,12,13, 10,11,14,15}   (4x2 uses the first 8)
4x3:      POS = {0,1,4,5, 2,3,6,7, 8,9,10,11}
```

and chroma pair `g` of the entry belongs to luma group `g` (bytes 4g..4g+3). So the 4-wide modes
are 4:2:0 over 2x2 quads, except that in 4x3 the third group is the 1x4 strip of row 2.
The 3x3 mode stores luma in raster order (slot `r*3+c`); chroma pair 0 covers rows 0-1 (6
pixels) and pair 1 covers row 2.

### 2.1 Unprediction (StreamBase::UnPredictLuminance / UnPredictChrominance)

Both planes are delta coded as a running byte sum (mod 256):

```
acc = 0; for i in 0 .. ncb*bw*bh-1:            acc = (acc + Y[i]) & 255; Y[i] = acc
acc = 0; for i in 0 .. 2*ncb*Cu-1 over U then V: acc = (acc + UV[i]) & 255; UV[i] = acc
```

`Cu` is the unpredict step's own chroma count: {1,4,1,3,1,2,1,2} for modes 0..7 (equal to
C for the odd modes). The chroma sum runs straight from the end of U into V.

### 2.2 Conversion to RGB555 with ordered dither

For every pixel slot `k` of an entry with luma `y` (0..255) and its chroma pair `u, v`
(signed):

```
R = F[k][ L(y + 2*v) ]
G = F[k][ L(y - (u >> 1) - v) ]       (>> is arithmetic)
B = F[k][ L(y + 2*u) ]
pixel = R | G << 5 | B << 10          (GBA BGR555)
```

`L(x)` is the brightness clamp: 0 for x < 0, `lut[255]` for x > 255, else `lut[x]`, with
`lut[i] = min(255, (i * g) >> 8)`. `g` is the brightness gain, from an 11-entry ROM table
{128, 153, 179, 204, 230, 256, 281, 307, 332, 358, 384} indexed by the player's brightness
setting (default 5, i.e. g = 256 and `lut` is the identity). The player builds the clamp as
a 0x200 zero bytes / 256-byte lut / 0x200 bytes of `lut[255]` array, indexed at -0x200..0x2ff.

`F[k][v]` is the 8-to-5-bit quantiser with a 4x4 ordered dither:

```
F[k][v] = 31                              if v >= 248
        = (v >> 3) + (D[k] < (v & 7))     otherwise
```

`D` is a 16-byte table read from the ROM (generated at build time; it is the same in every
Hydrogen and ADS cart seen so far). The dither index `k` is always the pixel's position
`row*4 + col` inside the block's 4x4 cell: for the 4-wide modes that equals the slot; for 3x3 it is
`(s/3)*4 + s%3` for slot `s`. (The ADS model writes the same quantiser as
`min(31, (v + D'[k]) >> 3)` with `D' = 7 - D`; the two forms are identical.)

The converted entry is written to the VRAM codebook at 0x06012C00 + entry*stride. For 3x3 the
cartridge writes slots 0..8 and a zero in slot 9 (the 3x3 blitter packs two blocks per word and
relies on that zero); slots 10..15 are not written.

## 3. Index stream and blit

The decompressed index stream of a segment is the concatenation of its frames: frame `f` of the
segment is bytes `f*N .. f*N+N-1` with `N` = grid_w*grid_h. Each byte selects a codebook entry;
blocks are in raster order (left to right, top to bottom). Block (bx, by) pixel (r, c) =
entry slot `r*bw + c`, written to framebuffer pixel (by*bh + r, bx*bw + c). The blitter writes
the mode-3 framebuffer at 0x06000000 directly (no double buffering), every 5th VBlank.

There is no inter-frame prediction in the VQ layer itself. The only temporal redundancy removal
is the LZ stage: its window spans frames within a segment, so a block row repeated from the
previous frame becomes one long match.

## 4. Agreement with the ADS model (tools/ads/adsref.py, 2026-11-06)

| element | ADS model | this spec |
|---|---|---|
| segment word 0 layout | `nfr<<16 \| ncb<<6 \| 0x30 \| mode` | same (0x30 = bits 3-5 = 6) |
| unprediction | luma, then chroma restarting at 0 | same |
| dither | `min(31,(v+D'[k])>>3)`, D' = {7,3,6,2,1,5,0,4,6,2,7,3,0,4,1,5} | same function, D = 7-D' |
| slot layout | POS44 / POS43 / 3x3 linear | same |
| line 159 | BG palette halfwords 0..23 repeated | same |

## 5. Open items

- Brightness levels other than the default are covered by the unicorn fuzzing only (the captures
  play at the default level 5, gain 256).
- ADS-specific blit differences: none in the output; code-level differences are in
  [ads-vs-hydrogen.md](ads-vs-hydrogen.md).
