# VXGB: the GBA Video Rev 5 player and codec (Shark Tale, Shrek 2)

Status: **complete; the C decoder is bit-exact on both movies** (section 9).

Two cartridges we have carry an older revision of the player and codec, with the magic `VXGB`
instead of `VX++` at the stream header. quatric reports a third, *Shrek* (USA) (Rev 5), which we
could not check. quatric's mobipeg (`doc/gba_video_vxgb.md`, `libavcodec/vx.c`, August 2026)
documented and implemented this revision first: no frame header, the header QP, CAVLC with the
H.264 tables and the rounded-mean nC, the shared chroma nC, CBP32 and the H.264 zigzag. Its
decoder is exact on every frame of both movies below (`prior-art.md`). This document adds
the player's memory map, the grid bookkeeping, a written comparison with H.264 CAVLC, the blit
and audio details, and whole-movie verification.

| cartridge | ROM sha1 | header | stream |
|---|---|---|---|
| Game Boy Advance Video - Shark Tale (USA) (Rev 5) | `6128a476edb10e6839ac5bd2697e83b9b7a9b234` | "SHARKS TALE", MSAE | 0x20200, 37,697 frames, QP 34 |
| Game Boy Advance Video - Shrek 2 (USA) (Rev 5) | `5cd627e205020297b25d707131883be5850515fe` | | 0x20200, 38,816 frames, QP 32 |

Both carts contain the same player program (ROM 0x8a00..0x18a00, sha1
`33a2f3a71000ac5dcfbe6791252eeb0916dc3a33`); they differ only in the cart header and the
movie. The Rev 6 releases of the same two movies use the VX++ player of `player.md`.

This document lists **every difference from VX++**. Everything not mentioned here is the
same as in `container.md`, `video.md` and `residual.md`, and was checked to be the same:
by an instruction-level diff of the two players (both hand-written ARM decoders align
instruction for instruction outside the parts below), by the ARM oracle, and by the
fuzzer.

In short: VXGB has **no frame header** (the quantiser is fixed per stream in the header) and
codes residuals with **H.264 CAVLC code tables** behind a VXGB-specific syntax, with the nC
context kept in two persistent grids. The macroblock grammar, all 12 prediction modes,
MV prediction, intra, dequantisation, the inverse transform, the bit reader, the container,
the seek table and the audio codec are the VX++ ones.

## 1. Player program

Same boot loader, program table and load-header format (`player.md` section 1). The player
(program 2) has smaller images and its C code moved from IWRAM to EWRAM:

| | VX++ (Shrek) | VXGB (Rev 5) |
|---|---|---|
| EWRAM image | ROM 0x8a00, 0x32c8 bytes | ROM 0x8a00, **0x2b48** bytes |
| IWRAM image | ROM 0xbcc8, 0x7cf4 bytes | ROM **0xb548**, **0x7cd0** bytes |
| player_run | IWRAM 0x03006df4 | EWRAM **0x02000a00** |
| per-frame decode wrapper | 0x03006d30 | inlined into player_run's main loop (same ref rotation) |
| decode_frame | 0x03000520 | **0x03000510** |
| ue(v) / se(v) / refill | 0x0300076c / 0x0300081c / 0x030007a8 | 0x03000680 / 0x03000730 / 0x030006bc (identical code) |
| 16x16 MB dispatch | 0x03001dac | 0x03001ee0 (table 0x03001e80) |
| residual_block | 0x03005650 | **0x03005eb0** |
| 4x4 coefficient decode + IDCT | 0x03005794 | **0x030062e0** |
| audio_decode_packet | 0x03006a14 | **0x03007170** (identical code) |
| Timer1 / Timer3 ISR bodies | 0x03007974 / 0x03007ab0 | EWRAM 0x02001bcc / 0x02001d08 |
| index_find / seek_to_entry | 0x02000400 / 0x02000584 | 0x020002c0 / 0x020004c8 |
| frame slot i | 0x020032c8 + i*0xF000 | **0x02002b48** + i*0xF000 |
| MV array (stride 0x24) | 0x0203f2c8, MB (0,0) at 0x0203f314 | **0x0203f866**, MB (0,0) at 0x0203f8b2 |
| luma nC grid (new) | -- | 0x0203eb48 (0xa69 bytes), MB (0,0) at 0x0203eb8a |
| chroma nC grid (new) | -- | 0x0203f5b1 (0x2b5 bytes), MB (0,0) at 0x0203f5d3 |
| dequant scales | 3 words, IWRAM 0x03005770 | 8 words, ctx+0x100 |
| width / height in ctx | ctx+0x104 / 0x108 | ctx+0x120 / 0x124 |
| audio bounce buffer | 0x06013f90 | 0x06013fa8 |
| "File Format Error" string | | EWRAM 0x02002ab4 (ROM 0xb4b4) |

The player state struct is still at VRAM 0x06013e80 with the same layout up to ctx+0xf0;
ctx+0xf4/0xf8/0xfc are the luma-grid, chroma-grid and MV-array pointers of the current
macroblock. Timers, A/V pacing, input, the OSD and the seek logic are the VX++ C code
recompiled; their behaviour is unchanged.

The 4 frame slots are initialised as in VX++ (Y 0x00, chroma 0x80), and player_run zeroes the
two grids and the MV array **once**. Nothing clears them again: not per frame and not on a
seek. Decode index n starts at 3 and frame n uses slot n&3 with refs (n-1..n-3)&3, as before.

## 2. Header

Same 0x38-byte header (`container.md` section 2), magic `VXGB`. One field changes meaning:

| off | VX++ | VXGB |
|---|---|---|
| 0x14 | unused (0) | **QP of the whole stream** (Shark Tale 34, Shrek 2 32). player_run computes the dequant scales from it once (section 6) |

There is no other per-stream or per-frame quantiser. The video still starts at header+0x38.

## 3. Frame syntax

```
frame():                                   // decode_frame 0x03000510
    for mby in 0..H/16-1: for mbx in 0..W/16-1:
        MV prediction exactly as VX++ (video.md 4)
        nzy = 0x0203eb8a + mby*4*0x41 + mbx*4    // this MB's luma grid entry
        nzc = 0x0203f5d3 + mby*2*0x21 + mbx*2    // this MB's chroma grid entry
        block(16, 16)
```
There is **no frame header**: VX++'s leading `ue(v)` QP field does not exist, and the first
bit of a frame is the first macroblock's mode. (decode_frame only waits for the cart mapper
to go idle, 0x03000710, before the loop.)

## 4. Partition tree and the nC grids

`block(w, h)` reads the same `ue(v)` mode with the same 24/12-entry tables, splits, MC,
mode-5 DC offsets, bilinear and intra modes as VX++ (video.md 5-7). Residual variants
(modes 12..23) still exist only at 16x16, 16x8, 8x16 and 8x8 and call the VXGB residual
(section 5). The only additions are grid bookkeeping:

Grids: one byte per 4x4 block. Luma: 4 bytes per MB row of 4x4s, row stride **0x41**
(65 bytes = up to 64 columns + 1). Chroma: one byte per 4x4 chroma block, i.e. per 8x8 luma
area, **shared by U and V**, row stride **0x21**. The byte holds the number of nonzero
coefficients (TotalCoeff) of the last block coded there, or 0.

- **Splits** move the grid pointers with the half: a split into 8-row halves of a 16-high
  block adds 2 luma rows (0x82) and 1 chroma row (0x21) for the second half; a split into
  8-column halves of a 16-wide block adds 2 / 1. Splits into halves smaller than 8 do not
  move them (the halves do not use the grids).
- After any mode **without** residual at 16x16, 16x8, 8x16 or 8x8 (modes 0, 3..11, and splits
  1/2 whose halves are smaller than 8x8, e.g. 16x8 mode 1 into 16x4s, 8x16 mode 2 into 4x16s,
  8x8 modes 1 and 2), the dispatcher sets the block's whole area in both grids to 0
  (16x16: 4x4 luma + 2x2 chroma entries; 16x8: 4x2 + 2x1; 8x16: 2x4 + 1x2; 8x8: 2x2 + 1).
  Routines 0x03001b20, 0x03002040, 0x03003190, 0x03003720.
- Splits into 8x8-or-larger halves (16x16 modes 1/2, 16x8 mode 2, 8x16 mode 1) leave the
  grids to the halves. Residual modes (12..23) let the residual write them (section 5).
- Blocks smaller than 8x8 never touch the grids; their enclosing 8x8-or-larger block has
  already zeroed the area.

So every macroblock rewrites all of its grid entries, every frame, before any later block can
read them: nC only looks left and up, at entries written earlier in the same frame. The
persistence of the grids is therefore never observable inside the picture, and a seek point
decodes the same from a cold start as in continuous play (section 9; also measured: no nC in
either movie reads an entry not written in the same frame, of 19.3 M nC reads in Shark Tale
and 12.2 M in Shrek 2). Entries above the picture (row -1) and left of it (column -1 = the
unused column 64 of the previous row, or the zero bytes before the grid) are never written,
so they keep player_run's zeros; a reimplementation must give those border reads 0.

## 5. Residual (0x03005eb0)

```
residual(w, h):                        // w, h in {16, 8}; Y/UV as in VX++ residual.md 2
  for by = 0; by < h; by += 8:  for bx = 0; bx < w; bx += 8:
      cbp = CBP32[ue(v)]               // index >= 32 reads code bytes (undefined)
      ny = nzy + (by/4)*0x41 + bx/4    // luma grid entry of this 8x8's top-left 4x4
      nc = nzc + (by/8)*0x21 + bx/8    // its chroma grid entry
      for k in 0..3:                   // 4x4 luma blocks in raster order: +0, +4, +4*256, +4*256+4
          e = ny + (k>>1)*0x41 + (k&1)
          if cbp & (1<<k): grid[e] = coef4x4(Y block k, nC = (grid[e-1] + grid[e-0x41] + 1) >> 1)
          else:            grid[e] = 0
      if cbp & 0x10:
          nC = (grid[nc-1] + grid[nc-0x21] + 1) >> 1
          tu = coef4x4(U block, nC)    // bytes 0,2,4,6 of the interleaved rows
          tv = coef4x4(V block, nC)    // bytes 1,3,5,7; the SAME nC as U
          grid[nc] = (tu + tv + 1) >> 1
      else:  grid[nc] = 0
```
nC uses the left and top neighbours with no availability test (the picture border reads the
zero entries outside the picture), always rounded up as `(a + b + 1) >> 1`.

CBP32 (IWRAM 0x03005e90; bit 0..3 = luma 4x4 0..3, bit 4 = U **and** V):
```
{ 0x00,0x01,0x02,0x04,0x08,0x10,0x03,0x05,0x0a,0x0c,0x0f,0x1f,0x07,0x0b,0x0d,0x0e,
  0x06,0x09,0x13,0x15,0x1a,0x1c,0x11,0x12,0x14,0x18,0x17,0x1b,0x1d,0x1e,0x16,0x19 }
```
(VX++ has a 64-entry table with separate U and V bits.)

### 5.1 One 4x4 block (0x030062e0)

The code tables are exactly the H.264 CAVLC tables (ITU-T H.264 Table 9-5 coeff_token,
Tables 9-7/9-8 total_zeros for 4x4 blocks, Table 9-10 run_before). All codewords were compared
with FFmpeg's `h264_cavlc.c` tables: 0 differences. The **syntax order and the level code are
not H.264's**:

```
coef4x4(nC):                                   // c[0..15] in scan order, filled from c[15] down
  if nC >= 8:  v = u(6); T1 = v & 3; TC = (v >> 2) + 1; if T1 > TC: TC = 0
  else:        (TC, T1) = coeff_token(nC)      // VLC for 0 <= nC < 2, 2 <= nC < 4, 4 <= nC < 8
  grid entry = TC                              // stored before anything else
  if TC == 0: return                           // block unchanged, no transform
  zerosLeft = (TC < 16) ? total_zeros[TC] : 0
  write 16 - TC - zerosLeft zeros at the top end (c[15], c[14], ...)
  sL = 0
  repeat TC times:                             // highest-frequency coefficient first
      if T1 left:  s = u(1); level = s ? -1 : +1         // trailing ones: sign only; sL unchanged
      else:
          prefix = number of 0 bits before a 1 (the 1 is consumed)
          if prefix == 15:  levelCode = u(11) + (15 << sL)          // escape
          else:             levelCode = (prefix << sL) + u(sL)       // u(0) = 0
          s = u(1); level = s ? -(levelCode + 1) : levelCode + 1
          if levelCode > THR[sL]: sL += 1      // THR = {2, 5, 11, 23, 47, 0x8000}
      store level at the next position down
      if this was the last coefficient: break
      if zerosLeft > 0:
          run = run_before[zerosLeft]          // zerosLeft > 6: H.264's 7+ row
          store run zeros; zerosLeft -= run
  store zerosLeft zeros (the low end)
  dequant + IDCT + add (5.2)
```
Differences from H.264 CAVLC, for a reader who knows it:
- **Interleaved order.** total_zeros comes right after coeff_token; then each coefficient's
  level is followed immediately by its run_before. H.264 sends all levels, then total_zeros,
  then all run_befores.
- **Level code.** Sign is an explicit bit after the suffix; magnitude = levelCode + 1. The
  suffix length starts at 0 always (no "TC > 10 and T1 < 3" rule), there is no "+1 for the
  first level after fewer than 3 trailing ones", no 4-bit suffix for prefix 14, and the
  escape is prefix 15 + an 11-bit suffix. suffixLength grows by one when levelCode exceeds
  THR[sL] (H.264 uses |level| > 3 << (sL-1)) and stops at 5.
- No chroma DC block, no separate chroma tables: chroma 4x4s use the luma tables with the
  chroma grid's nC.

Bit-reader detail (matters only for codes longer than the buffer): coeff_token is a 16-bit
peek walked as a tree of 4-bit nibbles (IWRAM tree 0x030079c0, start nodes 0x03006120,
lengths 0x03006130) and consumed with one refill; total_zeros and run_before (zerosLeft <= 6)
peek 9/6/6/5/5/6/6/6/6/5/4/4/3/2/1 resp. 1/2/2/3/3/3 bits into a table; the level prefix
shifts out its zeros and the 1, then refills once; suffix+sign is one shift of sL+1 (escape:
12) bits; a trailing-one sign is one bit. Each group refills once if the count went negative.
`reference/vx/decoder/vxgb_residual.c` mirrors these groups. Invalid codes: an all-zero 9-bit total_zeros
peek for TC=1 decodes as 0 with length 1; the coeff_token tree yields no symbol for
`00000000 000000xx`-like codes; zero fills that run past the buffer loop ~2^32 times in the
ARM. vxdec flags these as errors.

### 5.2 Dequantisation and transform

The coefficient at raster position (x, y) (scan -> raster by the same H.264 zigzag as VX++)
is multiplied by `S[(y & 1)*4 + x]` (32-bit wrap) with
```
S[i] = D[QP % 6][i] << (QP / 6),     QP = header 0x14
D = EWRAM 0x02002a14 = per row {a,b,a,b, b,c,b,c} for (a,b,c) =
    {10,13,16},{11,14,18},{13,16,20},{14,18,23},{16,20,25},{18,23,29}
```
i.e. the same LevelScale classes as VX++ (even/even a, mixed b, odd/odd c). The inverse
transform, rounding (+32, >>6), add and clamp are identical to VX++ (`residual.md` 6); VXGB
has no DC-only fast path, which in VX++ gives the same result anyway. A block with TC = 0 is
not transformed.

## 6. Quantiser

QP comes only from header 0x14; player_run fills the 8 scales once. Any QP that indexes the
6-row table works (QP/6 is a plain shift). The bitstream has no way to change it.

## 7. Display

The YUV->BGR555 blitter (0x03000370) is the same code with the same self-modifying init, but
its literal pool moved by 12 bytes, so the byte at IWRAM 0x380 is 0x7c instead of 0x88. That
byte is T[400] of the clamp table (index range -260..509). The blitter adds the six table
values of a horizontal pixel pair (`R | G<<5 | B<<10`, odd pixel `<< 16`) into one register
and stores a word, so table bytes above 31 (indices >= 384 read the blitter's own code)
carry into the next field and into the odd pixel. Shark Tale hits this on single pixels (for
example frame 62, pixel (187, 15): emulator R = 10 where an OR-combined model gives 7);
vxdec and `tools/vx/emu/yuv2bgr.py` model the addition (this also applies to VX++, which never
triggers it).

## 8. Container and audio

- Header, chapter table, seek table, seek procedure, video bit reader, audio stream layout
  and AFrame codec: **unchanged**. The audio routine is byte-identical code at a new address;
  `tools/vx/container/vxaudio.py` matches it on every AFrame (section 9).
- The encoder's audio/video alignment differs per movie. Seek-point AFrame index vs. frame:
  Shrek (VX++) `floor((128f+1)/7)`; Shark Tale `floor((128f+4)/7)` at all 191 seek points;
  Shrek 2 `floor(128f/7) + 1` (f > 0) at all 197. The player has no formula: it plays both
  streams from the seek entry, so `vxdemux.py` anchors the mapping on the seek entries.
- Shark Tale has two chapters that start one frame after a seek point (32893, 34241).

## 9. Verification

Tools: `reference/vx/decoder/vxdec` (auto-detects `VXGB`), `tools/vx/oracle/uc_oracle_vxgb.py` (the Rev 5
player's own decode_frame in unicorn), `tools/vx/verify/verify_vxgb.py`, `tools/vx/oracle/fuzz_vxgb.py`,
`tools/vx/oracle/emu_audio.py`, `tools/vx/container/vxdemux.py walk --video`, and `tools/vx/emu/capture.py` (A at the
title menu at emu frame 1000 starts Play on both carts).

| check | Shark Tale | Shrek 2 |
|---|---|---|
| vxdec vs emulator (mGBA), md5 of the 0xF000-byte slot, whole movie | 37,697/37,697 | 38,816/38,816 |
| vxdec vs emulator, md5 of the 240x160 BGR555 screen | 37,667/37,693 exact; the other 26 show the previous frame (blit skipped, below) | 38,804/38,812 exact; the other 8 show the previous frame |
| vxdec vs the ARM decoder in unicorn: whole 256 KiB EWRAM (4 slots, both grids, MV array) and bit position after every frame | 37,696/37,696 frames | 38,815/38,815 frames |
| seek-table video_bit = continuous decode's frame start bit | 191/191 | 197/197 |
| fresh decoder at every seek point after frame 0 (menu chapter start), 8 frames each | 190/190 | 196/196 |
| 200 random mid-play seek jumps (slots and grids kept, as the player does), 8 frames each | 200/200 | 200/200 |
| `vxdemux.py walk --video`: grammar walk lands on every seek offset | 37,697 frames, 191/191 segments | 38,816 frames, 197/197 segments |
| vxaudio.py vs the Rev 5 ARM audio routine, all AFrames | 689,317/689,317 AFrames (512 output bytes + length) | 709,779/709,779 AFrames |
| vxaudio.py vs the emulator's decoded audio blocks | 689,240 blocks (all the cart plays): digest equal | first 296,039 blocks: digest equal; then the emulated cart underruns (below) |

The screen hashes that differ are frames whose blit the player skipped: av_tick does not blit
when the display is more than 0x20 ticks behind (`player.md` 5), so the screen keeps the previous
picture. The CAVLC decoder is slower than VX++'s, so a few busy scenes overrun (Shark Tale
22610-22623, 31445-31522, 33107; the Shrek VX++ player never skips). Each such screen equals
the previous frame's screen; the decoded slot itself matches for every frame.

**Shrek 2 audio underrun.** The audio window (0x08001800, two 1 KiB halves) is refilled only by
the main loop, between video frames (`player.md` 5). In the emulator, decoding Shrek 2 frame 16191
takes so long that the Timer3 handler's audio decoding runs through the half at 0x08001c00 while it
still holds the ROM bytes of 2 KiB earlier (emu frames 139127-139150: refill pending, read
pointer passes 0x08001c00 at AFrame 296028). The AFrame sizes read from the stale bytes put the
read pointer off the AFrame boundaries, and it never realigns, so from block 296,039 to the end
the cart plays garbage. Nothing in the codec causes this; the ARM audio routine itself matches
vxaudio.py on every correctly-aligned AFrame of the stream. Whether real hardware underruns at
the same place depends on its exact cartridge timing and was not checked.

`fuzz_vxgb.py`: 650 random valid frames (random reference memory, random grids,
QP 0..51, all CBPs, all nC classes and the >= 8 FLC, escapes, suffixLength up to 5, every
total_zeros/run_before value) give the same whole EWRAM image and bit position from the ARM
and the C decoder. Mutations of the nC rounding, the chroma nC sharing, the suffixLength
threshold and the split zeroing rule are each caught within 10 cases.

Hashes-only manifests: `verify/vx/sharktale-rev5_20200.frame_md5.csv.gz`,
`verify/vx/shrek2-rev5_20200.frame_md5.csv.gz` (per frame: yuv_md5, vram_md5 from the emulator),
`verify/vx/*-rev5_20200.frame_bits.json.gz` (start bit of every frame), and the audio block
digests in `tools/vx/verify/verify_vxgb.py`.

## 10. Building

`make -C decoder VXGB_ROM=path/to/rev5.gba` (or put `sharktale.gba` / `shrek2.gba` at the
repository root). `gen_vxgb_tables.py` copies the CAVLC tables, CBP32, the dequant table and
the Rev 5 blitter bytes out of the ROM into `vxgb_tables.h` (generated, not committed). Without
a VXGB ROM a stub is generated and vxdec decodes VX++ only.
