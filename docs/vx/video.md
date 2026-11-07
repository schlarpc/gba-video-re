# VX++ video bitstream (Shrek GBA Video) -- reimplementation-grade spec

Author: video-bitstream agent. Status: **complete, verified bit-exact on the whole movie.**
Residual coefficients (CBP, VLC, escape, dequant, IDCT) are specified in `docs/residual.md` (residual agent);
this document covers everything else in the video stream.

Reference decoder: `decoder/` (C99). `make` builds `vxdec` and `libvxdec.so`.

```sh
cd decoder && make
./vxdec ../shrek.gba --start-frame 0 --count 300 --out OUTDIR [--rgb] [--y4m out.y4m] [--md5] [--stats]
python3 verify.py                  # whole movie vs emu manifest + raw dumps + every seek point
python3 verify.py --oracle 300     # also vs the real ARM decoder in unicorn, per-MB bit positions
python3 fuzz.py --cases 2000       # differential fuzz vs the ARM decoder (random valid streams, random memory)
```

| file | content |
|---|---|
| `vxdec.c`, `vxdec.h` | the decoder (frame header, MB loop, MV prediction, partition tree, every prediction mode, YUV->BGR555) |
| `vxdec_main.c` | CLI (ROM or bare VX file, seeking, .yuv / .bgr555 / .y4m / md5 output) |
| `bits.h` | register-exact bit reader (shared with residual.c) |
| `residual.h` / `residual.c` | interface (owned here) / implementation (residual agent) |
| `fill_tables.h` | generated op lists for the "bilinear" fills (from `gen_fill.py` + `symfill.py`, symbolic execution of the ARM) |
| `iwram_lut.h` | runtime IWRAM bytes 0x03000000..0x030003FF (clamp table of the RGB blitter) |
| `uc_oracle.py` | standalone unicorn oracle: runs the player's own decoder from ROM images, no savestate needed |
| `verify.py`, `fuzz.py` | verification (below) |

## 0. Verification status

| check | result |
|---|---|
| whole movie, one pass from power-on, md5 of the 0xF000-byte YUV slot vs `emu/manifest` | **37837/37837** |
| md5 of the BGR555 framebuffer image vs manifest `vram_md5` | **37833/37833** (the last 4 frames are never blitted) |
| raw dumps `emu/frames` 0-299, `deep20000` 20000-20029, `deep37800` 37800-37836 (YUV + VRAM) | 367/367 YUV, 363/363 VRAM |
| seek: fresh decoder started at each of the 195 seek-table entries, 8 frames each | 1555/1555 |
| seek-table `video_bit` == bit position of that frame in a continuous decode | 195/195; all 37837 frame start bits also equal `container/out/frame_bits.json` |
| real ARM decoder (unicorn, `uc_oracle.py`), frames 0-299: slots and the bit position at every MB | all equal |
| `fuzz.py`: random valid streams + random reference/padding memory, whole EWRAM image + final bit position vs ARM | 0 failures in 3530 random frames (seeds 1, 3, 7; every mode, every block size, MVs to +-127/+-48, se up to +-127, intra at picture edges, all intra sub-modes, QP changes, residual calls with CBP 0) |

Decode speed: the whole movie (37837 frames) in 7.6 s on one core.

The same checks (`verify.py --rom ROM --stream S --manifest --seek --seek-frames 1000 --audio`)
pass for every VX++ stream of the Shark Tale and Shrek 2 Rev 6 carts and the Shrek + Shark Tale
Rev 5 cart, including the two 240x112 movies. See the table in the README.

## 1. Bit reader (`bits.h`; IWRAM 0x030007a8, 0x0300076c, 0x0300081c)

- The video elementary stream starts at VX header + 0x38 (ROM 0x20238). It is a sequence of
  **little-endian 16-bit halfwords, each read MSB first**. Bit k of the stream = bit (15 - k%16) of halfword k/16.
- There is no byte/halfword alignment anywhere: not between frames, not at segment (seek-point) starts.
  Frame f starts exactly where frame f-1 ended.
- Registers: r1 = halfword pointer, r2 = `cnt`, r3 = `buf` (left-aligned). Valid bits in buf = cnt + 16.
  Every primitive consumes bits by shifting buf left and subtracting from cnt, then refills **once** if cnt < 0:
  `hw = *r1++; cnt += 16; buf |= hw << (16 - cnt)` (ARM register shift: amount >= 32 gives 0).
  Exception: intra 4x4 mode reading lets cnt go down to -12 and refills when cnt < -12 (see 7.3).
- Start state. Power-on (player_run 0x03006df4, start frame 0): buf = hw[0] << 16, cnt = 0, r1 -> hw[1].
  Seek (0x02000584) to stream bit b: buf = (hw[b/16] << 16 | hw[b/16+1]) << (b%16), cnt = 16 - b%16, r1 -> hw[b/16+2].
  Both describe the same bit position (bitpos = 16*index(r1) - cnt - 16); the different refill phase only matters for
  over-long codes (below).
- `ue(v)` (0x0300076c): count leading zeros z (shifting each out), shift out the 1, value = (1<<z) - 1 + next z bits.
  Consumes 2z+1 bits. `se(v)` (0x0300081c): k = ue+1; v = (k odd) ? -(k>>1) : k>>1, i.e. 0, 1, -1, 2, -2, ...
- **Code length limit.** Because a primitive refills only once, a code longer than cnt+16 bits (>= 16 always
  available) is corrupted: missing bits read as 0, and the late refill ORs the halfword in at the wrong place. Codes of
  <= 16 bits (ue <= 254, |se| <= 127) are always safe. The ARM loop also hangs on an all-zero buffer. `bits.h` models
  the registers exactly, so the C decoder reproduces even the corrupted behaviour (the fuzzer found the ARM and C
  identical up to the point where the ARM jumps through an invalid mode). The Shrek stream never needs more than 16 bits.

## 2. Stream structure, frames and seek points

- One frame = `frame_header` + `macroblock` x (W/16 x H/16) in raster order. W x H = 240x160 = 15x10 MBs.
- Frame boundaries are implicit. The seek table (container.md section 4) gives `video_bit` = stream bit offset
  (from header+0x38) of 195 keyframes. Each equals the continuous decode position (verified, all 195).
- Seek-point frames are self-contained: a decoder started there with any initial frame memory produces the same
  visible pixels (`vxdec --init-fill 0x37,0x91` vs default: 1167 post-seek frames, visible pixels identical). The reference slots are not reset on seek.

### 2.1 Frame header (0x03000520)

```
frame_header:
    q = ue(v)          // 0: keep the current QP.  1..47: QP = q
    if q != 0: scale[i] = LevelScale[q % 6][i] << (q / 6),  i = 0..2   -> IWRAM 0x03005770..0x0300577b
```
`LevelScale` = IWRAM 0x03000680 = `{10,13,16},{11,14,18},{13,16,20},{14,18,23},{16,20,25},{18,23,29}`.
The ARM indexes byte tables 0x030006c8 (= (q%6)*12) and 0x030006f8 (= q/6), 48 entries each, so q >= 48 reads
past them (undefined; vxdec flags an error). There is no frame type: intra/inter is per block.
In Shrek: q = 30 at all 195 seek points, q = 0 (keep) in the other 37642 frames.
At power-on the scales are 0 (IWRAM 0x03005770 is zero in the image) until the first q != 0.

## 3. Frame buffers and reference rotation (0x03006d30)

- 4 slots in EWRAM: slot i at 0x020032C8 + i*0xF000. Y: 256x160 bytes, stride 256 (columns 240..255 are padding).
  Chroma at +0xA000: 80 rows x 256 bytes, **U and V interleaved** (byte 2x = U, 2x+1 = V of chroma column x), stride 256.
  The MV array follows at 0x0203F2C8 (0x318 bytes).
- Initial content (player_run): Y = 0x00, chroma = 0x80 in all 4 slots (padding included), MV array = 0.
- Decode counter n (ctx+0x28) starts at 3. Frame n is written to slot n&3; **ref0 = slot (n-1)&3 (previous frame),
  ref1 = (n-2)&3, ref2 = (n-3)&3**. VX frame 0 is n = 3: slot 3, refs = slots 2,1,0 (initial grey/black).
  ctx+0x24 = n-1. Both increment by 1 after each frame. Nothing else moves; seek does not change n.
- The decoder writes the output slot block by block, so reads "outside the block" see (a) already-decoded blocks of the
  current frame above/left, (b) not-yet-decoded data of frame n-4 below/right (e.g. intra 4x4 diagonal-down-left reads
  4 pixels above-right). Reads outside the picture see the padding columns (Y 0x00, chroma 0x80, never written) or,
  for row -1 of the picture, the last row of the memory before the plane: the previous slot's chroma row 79 (luma), the
  same slot's Y row 159 (chroma), or EWRAM bytes 0x020031C8.. (luma of slot 0). The C decoder keeps a full
  256 KiB EWRAM image with the player's layout, so all such reads are reproduced (the Shrek stream does not depend on
  them, see 2).

## 4. Macroblock loop and MV prediction (0x03000548..0x03000664)

MV array: u16 entries, stride 0x24 bytes (18 entries) per MB row. MB (x,y) uses entry
`E(x,y) = 0x0203F314 + y*0x24 + 2x`. Entries outside the 15x10 area (row -1, column -1 = entry 17 of the row above,
columns 15..17) are never written and stay 0.

```
for mby in 0..9: for mbx in 0..14:
    E(mbx,mby) = 0
    A = unpack(E(mbx-1,mby)); B = unpack(E(mbx,mby-1)); C = unpack(E(mbx+1,mby-1))
    pmx = median(A.x, B.x, C.x); pmy = median(A.y, B.y, C.y)      // a+b+c-min-max
    block(16, 16, pmx, pmy)
unpack(v):  x = (int8)(v & 0xff);  y = ((int16)v >> 8) + (x < 0 ? 1 : 0)      // v = x + 256*y (16-bit)
```
- An unavailable neighbour (picture edge) is simply (0,0). Top-right of the last column is entry 15 -> (0,0).
  The top-right neighbour is always taken from the row above (no fallback to top-left as in H.264).
- **What is stored**: every MC copy (modes 0/3/8/9/10/11, any block size) writes its final MV as
  `(mx + 256*my) & 0xffff` into the MB's entry; later sub-blocks overwrite earlier ones, so the stored MV is that of the
  **last MC sub-block in decode order**, or (0,0) if the MB has none. Mode 5 (absolute MV) and all intra modes do not
  store. The reference index is not stored (a ref1/ref2 MV predicts like a ref0 MV).
- **The predictor (pmx,pmy) is computed once per MB and used unchanged by every sub-block of the partition tree**
  (sub-block MVs do not predict each other).

## 5. Partition tree / block syntax (dispatchers)

```
block(w, h, pmx, pmy):
    m = ue(v)                                     // m < 24 if w >= 8 and h >= 8, else m < 12
    r = (m >= 12); m = m % 12
    switch m:
      0: mc(ref0, pmx, pmy)                       8: mc(ref1, pmx, pmy)      10: mc(ref2, pmx, pmy)
      3: dx = se; dy = se; mc(ref0, pmx+dx, pmy+dy)   9: same with ref1      11: same with ref2
      1: block(w, h/2) ; block(w, h/2) at +h/2 rows        // top, bottom  (invalid if h == 2)
      2: block(w/2, h) ; block(w/2, h) at +w/2 columns     // left, right  (invalid if w == 2)
      4: bilinear(w, h)                           // section 6.3
      5: mc_dc(w, h)                              // section 6.2
      6: if w >= 4 and h >= 4: intra_whole(w, h)  else nothing (no bits)     // 7.1
      7: if w >= 4 and h >= 4: intra_4x4(w, h)    else nothing (no bits)     // 7.3
    if r: residual(w, h)                          // 0x03005650, docs/residual.md; only w,h in {8,16}
```
Moving to a sub-block adds `rows*256` to the luma offset and `(rows/2)*256` to the chroma offset, or `cols` to both
(chroma is interleaved, so w/2 luma columns = w/2 chroma bytes). The residual is added after the whole block
(including all its sub-blocks) has been predicted.

Sizes reachable: w, h in {16, 8, 4, 2}. Dispatch tables (ARM, index = m; `-` = null pointer, the ARM jumps to 0):

| size | dispatcher | table | notes |
|---|---|---|---|
| 16x16 | 0x03001dac | 0x03001d4c | 24 modes |
| 16x8 | 0x03002184 | 0x03002124 | 24 |
| 8x16 | 0x030030f4 | 0x03003094 | 24 |
| 8x8 | 0x03003584 | 0x03003524 | 24 |
| 16x4, 8x4, 4x16, 4x8, 4x4 | 0x030024ac, 0x03003818, 0x030041cc, 0x03004468, 0x03004750 | dispatcher-0x30 | 12 |
| 16x2, 8x2, 4x2 | 0x030028ec, 0x03003adc, 0x03004950 | dispatcher-0x30 | 12; m1 = `-`; m6, m7 = no-op |
| 2x16, 2x8, 2x4 | 0x03004fd0, 0x03005294, 0x03005494 | dispatcher-0x30 | 12; m2 = `-`; m6, m7 = no-op |
| 2x2 | 0x030055fc | 0x030055cc | 12; m1, m2 = `-`; m6, m7 = no-op |

The 24-entry tables are `[the 12 modes] + [the same 12 + residual(w,h)]`, in the same order. Mode usage in Shrek
(all block sizes together): every mode 0..23 occurs; invalid modes never occur.

## 6. Inter modes

### 6.1 MC copy (modes 0/3/8/9/10/11; 0x03001578 / 0x030015a4 (16 wide), 0x03002910 / 0x0300293c (8),
0x03003b00 / 0x03003b2c (4), 0x03004964 / 0x0300498c (2))

Integer-pel only, no interpolation, no clamping of the MV or of the source position:
```
luma:   out[y][x]  = ref.Y[by + my + y][bx + mx + x]                       (w x h)
chroma: out.UV[cy + j][cxb + i] = ref.UV[cy + j + (my >> 1)][cxb + i + 2*(mx >> 1)]   (w bytes x h/2 rows)
```
i.e. in byte addresses: luma src = ref + off + mx + 256*my; chroma src = refUV + uvoff + (mx & ~1) + 128*(my & ~1).
**Chroma MV = floor(mv/2)** per component (arithmetic shift; -3 -> -2), applied to U and V alike.
The four alignment variants of each routine (jump table, e.g. 0x03001568) are all exact byte copies. Out-of-picture
sources read whatever memory is there (section 3). Shrek: no MV ever leaves the picture.

### 6.2 Mode 5: absolute MV + DC offsets (0x03001854 (16), 0x03002b9c (8), 0x03003d58 (4), 0x03004aac (2))
```
mx = se; my = se; dY = se; dU = se; dV = se          // in this order
src from ref0 only (same addressing as 6.1, MV NOT relative to the predictor)
Y = clip(refY + 2*dY), U = clip(refU + 2*dU), V = clip(refV + 2*dV)   clip to 0..255
```
The MV is not written to the MV array.

## 6.3 Mode 4: "bilinear" intra (recursive bisection; Mobiclip-style)

Per plane (luma W x H, then U, then V, each (W/2) x (H/2) on the interleaved chroma grid), with T[x] = pixel (x,-1),
L[y] = pixel (-1,y) read from the frame (no availability check):
```
corner (W-1,H-1) = ((T[W-1] + L[H-1] + 1) >> 1) + 2*se          // one se per plane: luma, U, V
fill(0, 0, W, H)
fill(x0, y0, w, h):           // P(x,y) relative to the block; the corner P(x0+w-1, y0+h-1) is known
    if w == 1 and h == 1: return
    BR = P(x0+w-1, y0+h-1)
    if w >= 2 and h >= 2:
        bm = (P(x0-1, y0+h-1) + BR) >> 1   -> P(x0+w/2-1, y0+h-1)          // bottom middle
        rm = (P(x0+w-1, y0-1) + BR) >> 1   -> P(x0+w-1, y0+h/2-1)          // right middle
        if w*h is a power of 4 (2x2, 4x4, 8x8, 16x16, 8x2, 2x8, 16x4, 4x16):
             c = (bm + P(x0+w/2-1, y0-1)) >> 1                              // "vertical"
        else (2x4, 4x2, 4x8, 8x4, 8x16, 16x8, 16x2, 2x16):
             c = (rm + P(x0-1, y0+h/2-1)) >> 1                              // "horizontal"
        P(x0+w/2-1, y0+h/2-1) = c
        fill the 4 quadrants (w/2 x h/2): top-left, top-right, bottom-left, bottom-right
    elif w == 1:   P(x0, y0+h/2-1) = (P(x0, y0-1) + BR) >> 1;  fill top half, bottom half
    else (h == 1): P(x0+w/2-1, y0) = (P(x0-1, y0) + BR) >> 1;  fill left half, right half
```
`>>` is arithmetic. All values are in 0..255 except when the corner overflows (2*se pushes it outside 0..255). Then
the stored bytes are the low 8 bits, and some later averages use the full-precision register value while others re-read
the stored byte. That choice is code-layout dependent, so the exact per-size operation lists are generated from the ARM
by symbolic execution: `fill_tables.h` (`gen_fill.py`), entries `fill_m4[w][h]`: each op is
`val = (A + B) >> 1` or `val = ((A + B + 1) >> 1) + 2*se`, operands either "byte in memory at offset" or "full value
of op k". The geometry above is verified equal to those tables for all 16 sizes on in-range data; the tables are
verified against the ARM by the fuzzer, overflow included. Shrek never overflows a corner.
Mode-4 entry points: 16x16 0x03001ac0, 16x8 0x03001e98, 8x16 0x03002e08, 8x8 0x03003298, 16x4 0x0300233c,
8x4 0x030036a8, 4x16 0x0300405c, 4x8 0x030042f8, 4x4 0x030045e0, 16x2 0x03002564, 8x2 0x030039b0,
4x2 0x03004824, 2x16 0x03004c48, 2x8 0x03005168, 2x4 0x03005368, 2x2 0x030054d0.

## 7. Intra modes 6 and 7 (block sizes with w, h >= 4)

"Available" below means only "not on the top row / left column of the picture": it is decided from the block's offset
in the frame (`off = x + 256*y`), never from slice or MB boundaries. Predictors that do not check (V, H, plane, all
4x4 directional modes, mode 4) just read the neighbouring memory.

### 7.1 Mode 6: whole-block intra (0x03000884)
```
lm = ue; cm = ue;  luma_pred[lm](w, h);  chroma_pred[cm](w, h)       // both codes are read before predicting
```
Luma table 0x03000864 (H.264 16x16 order), generic w x h:
| lm | ARM | prediction |
|---|---|---|
| 0 | 0x030009b8 | V: every row = row -1 |
| 1 | 0x03000a04 | H: every column = column -1 |
| 2 | 0x03000a54 | DC: off == 0 -> 128; x == 0 -> t; y == 0 -> l; else (t + l + 1) >> 1, with t = (sum T[0..w-1] + w/2) >> log2 w, l = (sum L[0..h-1] + h/2) >> log2 h |
| 3 | 0x03000ba4 | "plane": corner (w-1,h-1) = (T[w-1] + L[h-1] + 1) >> 1, then `fill` of 6.3 (tables `fill_pl`, entry table 0x03000b78 indexed (w>>3) + 4*(h>>3)) |

Chroma table 0x03000874 (H.264 chroma order), on (w/2) x (h/2) U,V pairs, U and V independently:
| cm | ARM | prediction |
|---|---|---|
| 0 | 0x03000c08 | DC: availability as luma (from the chroma offset); t = (sum of w/2 top values + w/4) >> log2(w/2), l likewise with h/2 rows; both -> (t+l+1)>>1; none -> 128 |
| 1 | 0x03000d90 | H |
| 2 | 0x03000dd4 | V |
| 3 | 0x03000e44 | plane: U corner = (U_T[w/2-1] + U_L[h/2-1] + 1) >> 1, V likewise, then the chroma `fill` (tables `fill_pc`, entry table 0x03000e18) on U, then V |
lm, cm >= 4 jump through garbage in the ARM (undefined; never used).

### 7.2 Intra 4x4 predictors (table 0x030008d8)
p(x,y) with T[0..7] = row -1 (T[4..7] = above-right, read unconditionally), L[0..3] = column -1, M = (-1,-1).
All are the H.264 4x4 formulas, verified line by line against the ARM:
| mode | ARM | |
|---|---|---|
| 0 V | 0x03000ecc | p = T[x] |
| 1 H | 0x03000eec | p = L[y] |
| 2 DC | 0x03000f90 | off == 0 -> 128; else s = 0, n = 1; if x != 0: s += L0+L1+L2+L3+2, n++; if y != 0: s += T0+T1+T2+T3+2, n++; p = s >> n |
| 3 diag down-left | 0x03000fe0 | k = x+y: k < 6: (T[k] + 2T[k+1] + T[k+2] + 2) >> 2; k = 6: (T6 + 3T7 + 2) >> 2 |
| 4 diag down-right | 0x030010c4 | H.264 |
| 5 vertical-right | 0x030011ac | H.264 |
| 6 horizontal-down | 0x030012b0 | H.264 |
| 7 vertical-left | 0x030013b4 | H.264 (uses T[0..6]) |
| 8 horizontal-up | 0x030014b0 | H.264 |
(x, y here = the 4x4 block's position in the picture.)

### 7.3 Mode 7: intra 4x4 (0x030008fc), generic w x h
```
ctx: 5-column byte grid at IWRAM 0x030008bc, top row and left column fixed at 9
for each 4x4 in raster order inside the block (by, bx):
    pred = min(ctx[top], ctx[left]); if pred == 9: pred = 2
    if u(1) == 1: mode = pred
    else: rem = u(3); mode = rem + (rem >= pred ? 1 : 0)
    ctx[this] = mode; predict 4x4 (7.2) from the current frame memory
cm = ue; chroma_pred[cm](w, h)                                 // as 7.1
```
- Mode prediction uses **only 4x4 blocks inside the current block**: the border of the context grid is always 9, so
  the first row/column of 4x4s of each block predicts from inside only, and a block whose top and left are both outside
  predicts DC (2). Unlike H.264 there is no "unavailable -> DC" rule: min(9, m) = m.
- Each 4x4 is predicted from the pixels already written, including the 4x4s just predicted inside this block (the
  residual is added only after the whole block).
- Bit-level detail (needed only for over-long codes): the flag and the 3 bits are shifted out of buf directly
  (cnt -= 1 or 4), the reader refills only when cnt < -12, and once more after the loop if cnt < 0.

## 8. YUV -> BGR555 (0x03000370; `vx_yuv_to_bgr555`)

For pixel (x,y), Y = luma, U,V = chroma pair (x>>1, y>>1):
```
Yd = Y - 4*((x+y) & 1)                      // 2x2 ordered dither on the luma index
R = T[Yd + 2(V-128)];  G = T[Yd - ((V-128) + ((U-128) >> 1))];  B = T[Yd + 2(U-128)]
pixel = R | G<<5 | B<<10        (screen at ((160-H)/2*240 + (240-W)/2), here 0)
```
T = IWRAM bytes at 0x030001F0 + k. For k in 0..~255 this is min(k>>3, 31); k in -136..-1 is 0 and k >= 256 is 31
until the blitter code at 0x03000370. The index range is -260..509, so T also covers IRQ-handler code bytes
(k < -136) and the blitter's own code (k >= 384, including the init-time patched immediates at 0x0300038C = 160 and
0x03000394 = 240). `iwram_lut.h` holds the exact runtime bytes; brightness 0 only (the A/B brightness patch shifts
the T base by +-0x18/0x30 and changes the byte at 0x03000388).

## 9. Not modelled / undefined

- ue mode values beyond the table, `lm`/`cm` >= 4, QP >= 48: the ARM jumps through or reads past its tables.
  vxdec counts an error and continues. None of these occur in Shrek.
- Reference reads below 0x02000000 (only possible with |my| > ~50 from slot 0): GBA open bus; vxdec wraps
  addresses modulo 256 KiB like EWRAM's upward mirror. Never happens in Shrek.

## 10. Prior art

quatric's mobipeg (`doc/gba_video_vxpp.md`, `libavcodec/vx.c`, August 2026) had already worked out
this layer: the partition tree and modes, integer-pel MC with chroma MV = floor(mv/2), the median MV
predictor and its packing, the reference ring, all intra predictors including the bisection fill
and its parity rule, intra 4x4 mode coding with the border-9 context, and availability from the
block offset. Its native decoder already behaves as described here for the MV predictor (one per MB)
and the stored MV (the last MC sub-block's), though its notes do not state these rules. Everything
in this document was re-derived from the ARM code and verified as in section 0. Additions here are
the register-exact bit reader (section 1), the exact fill operations when a corner overflows (6.3),
the memory outside the picture (3), and the clamp table, dither and carries of the RGB blit (8).
See `docs/prior-art.md` for the full comparison.
