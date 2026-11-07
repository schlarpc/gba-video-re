# Sonic X Vol 2 prototype: container, video and audio format

Cart: *Game Boy Advance Video - Sonic X - Volume 2 (USA) (Proto)*, dev file name
`Test6_Sonic_2_17March2004.gba`, 32022872 bytes, sha1 `00257021d9c4444e96d96331fb6eb07e756f53cc`.
Family: **Majesco Hydrogen** (DEFLATE-variant compression, YUV vector-quantised video, VLC-ADPCM
audio). Player build id (classifier) `96a25377cb75`.

This document is reimplementation grade: everything here was checked against the cart's own code,
both by whole-stream capture in mGBA and by running the cart's routines in a unicorn oracle (see
"Verification"). The C reimplementation is `reference/sonicx2-proto/` (`sx2dec`, `audio/sx2adec`).

Notation: ROM *file* offsets unless an address has the 0x08/0x03/0x02/0x06 prefix. All integers
little endian. `>>` on signed values is arithmetic. Bit strings are read most-significant bit first.

## 1. ROM layout

| ROM offset | Size | Content |
|---|---|---|
| 0x000000 | ~0x16000 | ARM crt0 (entry 0xc0) + Thumb "Hydrogen Library" C++ player |
| ~0x16000 | ~0x4b00 | read-only data: tables, strings, C library messages |
| 0x01ab14 | 0xf3e540 | file "Episode 103" (video 11697988 B + audio 4285936 B) |
| 0xf59054 | 8 | file slot -> Episode 103 |
| 0xf5905c | 0xeee098 | file "Episode 104" (video 11301100 B + audio 4353952 B) |
| 0x1e470f4 | 8 | file slot -> Episode 104 |
| 0x1e47104 | 0xc78c | file "intro movie" (Majesco DIGITAL VIDEO logo; 34448 B video + 16624 B audio) |
| 0x1e4f890.. 0x1e871e0 | | menu resources (bitmaps, fonts, thumbnails); file slot for the intro at 0x1e53890 |
| 0x1e871e0 | 0x2f78 | IWRAM image, copied verbatim to 0x03000000 at boot (ARM decoders + .data) |
| 0x1e8a158 | | end of file. The IWRAM image is the last item and ends exactly at the file size (32022872 = 0x1e8a158): the dump is complete, the ROM was simply never padded to a power of two |

### 1.1 File slots

Large resources are not reached through literal pools. The build tool wrote a two-word **file slot**
`{u32 rom_pointer, u32 file_size}` into the ROM and the code loads the pointer from the slot:

```
slot @0xf59054  = {0x0801ab14, 0x00f3e540}   read by 0x0800d3b4 -> episode table[0]
slot @0x1e470f4 = {0x08f5905c, 0x00eee098}   read by 0x0800d3b4 -> episode table[1]
slot @0x1e53890 = {0x09e47104, 0x0000c78c}   read by IntroMovie() 0x0800616e
```

Each slot sits immediately after the end of the previous file, which suggests the ROM was built
by appending files and back-patching slots. `file_size` always equals the size field of the file
header below. A slot scan (pointer into ROM whose target header has `size == slot.size` and a
1..8 resource count) finds exactly these three; `sx2dec --list` uses that scan.

The episode table at IWRAM 0x03002f28 (in the copied .data) holds 12-byte records
`{u32 file, u32 name/thumbnail resource, u32 0}` for the two episodes.

### 1.2 File (resource container)

```
u32 head = file_size << 4 | count          (count 1..15)
repeat count:
    u32 rhead = type << 28 | size_bytes
    u8  data[size_bytes]                    (size is a multiple of 4 in practice)
```

`FindResource(file, type)` (0x08005d36) walks the list and returns `&data`. Types used: **1 = video
stream**, **2 = audio stream** (also 4 = image, 5 = text in menu resources). Every movie file holds
exactly one video and one audio resource, video first.

| Stream | File | Video data | Video B | Audio data | Audio B |
|---|---|---|---|---|---|
| ep103 | 0x01ab14 | 0x01ab1c | 11697988 | 0xb42a64 | 4285936 |
| ep104 | 0xf5905c | 0xf59064 | 11301100 | 0x1a20154 | 4353952 |
| intro | 0x1e47104 | 0x1e4710c | 34448 | 0x1e4f7a0 | 16624 |

## 2. Video stream

### 2.1 Stream header (12 bytes) and chapter table

```
u32 w0:  [31:28] type = 2   (asserted by StreamBase::DecompressCodebook 0x08005a90)
         [27:24] 0
         [23:8]  total frames  (13828 / 14286 / 59)
         [7:0]   chapter count (6 / 6 / 0)
u32 w1:  [29:20] height = 160
         [19:10] width  = 240
         [9:4]   frame rate = 12   (IntroMovie asserts 12 or 15)
         [3:0]   5 = VBlanks per frame (60/12)
u16 nscenes   (321 / 344 / 4)
u16 0
chapter[count]: u16 first_scene; char name[] NUL-terminated, padded to an even length
pad to a multiple of 4
scene[nscenes]
```

Chapter names use the player's font alphabet, not ASCII: 1..10 = `0`..`9`, 11..36 = `a`..`z`,
37..62 = `A`..`Z`, 0x4c = space (the same alphabet `FontRenderer::ConvertTextToFont`, IWRAM
0x0300118c, produces). Both episodes have six chapters named "Chapter 1".."Chapter 6", starting at
scenes 0/26/69/114/191/245 (ep103) and 0/16/64/134/191/266 (ep104).

At load (0x08005778) the player converts the chapter table into 12-byte runtime records
`{scene pointer, u16 first frame, u16 first scene, name pointer}` by walking the scenes.

### 2.2 Scene

A scene is the unit of random access: it carries its own codebook and its own compressed index
stream (the LZ window restarts at every scene). Every scene is therefore a keyframe/seek point.

```
u32 s0:  [31:16] nframes
         [15:6]  ncodebook (number of codebook entries, 256 in every scene on this cart)
         [5:3]   6 in every scene; not read by the proto player
         [2:0]   mode (block geometry, table below)
u32 s1:  [31:13] frame_words   (frame-index blob size / 4)
         [12:0]  cb_words      (codebook blob size / 4)
u8  codebook_blob[cb_words*4]      Hydrogen-inflate blob (section 3)
u8  frame_blob[frame_words*4]      Hydrogen-inflate blob, nframes * blocks_per_frame bytes
next scene follows immediately
```

| mode | block | grid (cols x rows) | blocks/frame | lines written | luma/entry | chroma/entry/plane | RGB entry stride |
|---|---|---|---|---|---|---|---|
| 0, 1 | 4x4 | 60 x 40 | 2400 | 160 | 16 | 4 (mode 1) | 32 B |
| 2, 3 | 4x3 | 60 x 53 | 3180 | 159 | 12 | 3 (mode 3) | 24 B |
| 4, 5 | 3x3 | 80 x 53 | 4240 | 159 | 9 | 2 | 32 B |
| 6, 7 | 4x2 | 60 x 80 | 4800 | 160 | 8 | 2 | 16 B |

Block dimensions come from the ROM table at 0x08016d7c `{w,h}` x 8. Grid = 240/w x floor(160/h).
Used on this cart: **mode 5** (all 321 scenes of ep103) and **mode 7** (all 344 scenes of ep104 and
the 4 intro scenes). The even modes share the geometry and the RGB conversion of the odd ones but
the chroma un-prediction (2.4) only covers 1 chroma byte per entry per plane for them, which does
not match what the conversion reads; they are not used by any stream here and look unfinished.

If `ncodebook > 256` the player inflates 2 bytes per block (flag at context +0x10d8), but the blit
only reads byte indices; 16-bit indices are a dead path in this build.

A second consumer of the codebook exists: menu thumbnails. IWRAM 0x03001a8c averages each YUV
entry to one pixel (luma mean over w*h, chroma mean over the entry's samples, with the cart's
divide helpers) and 0x03001470 converts with a 1x1 geometry, so one VQ block becomes one thumbnail
pixel. It only drives the live previews in the main/chapters menus, not playback.

### 2.3 Codebook

`codebook_blob` inflates (section 3) into a buffer of at most 0x1800 bytes (asserted;
IWRAM 0x0300320c). With `n = ncodebook`, `P = w*h` pixels per entry, `C` = chroma samples per
entry per plane (table above) the layout is planar:

```
Y[n*P]          luma, entry by entry, pixels in the entry's luma order (below)
U[n*C]          first chroma plane ("blue difference")
V[n*C]          second chroma plane ("red difference")
```

Both are delta coded:

```
UnPredictLuminance (0x03001be0):   acc = 0; for i in 0..n*P-1:  acc = (acc + Y[i]) & 255; Y[i] = acc
UnPredictChrominance (0x03001ce4): acc = 0; for i in 0..2*n*C-1: acc = (acc + UV[i]) & 255; UV[i] = acc
```

(one running sum across all entries; the chroma sum runs through U and then V without reset). The
cart asserts that `n*P` and `2*n*C` are multiples of 4.

**Luma order and chroma sharing.** For 3x3 (modes 4/5) luma is raster order and entry pixels
0..5 (rows 0-1) use chroma sample 0, pixels 6..8 (row 2) use sample 1. For all other modes luma
is stored in groups of four, each group sharing one U and one V sample, and the group order is
2x2 sub-blocks: with `map = [0,1,4,5, 2,3,6,7, 8,9,12,13, 10,11,14,15]` (4x4 and 4x2, ROM
0x08016dd8) or `[0,1,4,5, 2,3,6,7, 8,9,10,11]` (4x3, ROM 0x08016de8), luma byte k of an entry is
the pixel at position `map[k]` = `row*4 + col` in the 4-wide block. (So 4x2 and 4x4 carry 2x2
chroma; the third row of 4x3 has 4x1 chroma.)

**Colour conversion** (0x03001470) to RGB555 with an ordered dither:

```
y = luma (0..255), u = (int8)U, v = (int8)V
R = Bc[y + 2*v]
G = Bc[y - (u >> 1) - v]
B = Bc[y + 2*u]
pixel = D[pos][R] | D[pos][G] << 5 | D[pos][B] << 10        (bit 15 = 0)
```

`Bc[x]` (context +0x10dd, built by 0x08005904) is the brightness/clamp table:
`Bc[x] = 0` for x < 0, `min(255, floor(coef*x/256))` for 0..255, `Bc[255]` for x > 255
(x ranges over -0x200..0x2ff). `coef` is the brightness level chosen with A/B during playback: 8 levels
`{230, 256, 281, 307, 332, 358, 384, 409}` = floor(256*(0.9 + 0.1*k)) (ROM 0x08016d4c), default
k = 1 (256 = identity). (Shipped Hydrogen builds have 11 levels 128..384 with default index 5 =
256; the default picture is the same.)

`D[pos][c] = min(31, (c + O[pos]) >> 3)` (context +0x2c, 16 tables of 256) with the 4x4 ordered
dither matrix

```
O = [7,3,6,2,
     1,5,0,4,
     6,2,7,3,
     0,4,1,5]
```

i.e. `O = 7 - B` where `B` = the 4x4 Bayer matrix halved (`[0,4,1,5, 6,2,7,3, 1,5,0,4, 7,3,6,2]`,
stored in ROM at 0x08016d6c, 16 bytes before the block-size table), indexed by `pos = row*4 + col`
of the pixel inside its block (for 3x3 blocks the pixel at (row,
col) also uses `O[row*4+col]`, i.e. the top-left 3x3 of the matrix). The matrix is constant: its
values only depend on the pixel's position within the block, not on the screen position.

The RGB entries are written to VRAM 0x06012c00 (`n` entries at the stride in the mode table, pixel
`row*w + col` at halfword `row*w + col`; 3x3 entries use 9 of their 16 halfwords). Converting
YUV->RGB for 256 entries happens once per scene (player state 2), and again whenever the brightness
changes (from a backup of the un-predicted YUV codebook kept in EWRAM 0x020126a8).

### 2.4 Frames

`frame_blob` inflates to `nframes * blocks_per_frame` bytes of codebook indices, row-major over
the block grid, frame after frame. The player decodes it incrementally, one frame's worth per call
(`UncompressBlock(blocks)`), with the LZ window carried across frames inside the scene: LZ matches
into the previous frame's indices are the only inter-frame coding ("skip" = a long match at
distance blocks_per_frame). Output buffer: EWRAM 0x02000018 with the window-rebasing scheme of 3.4.

**Blit** (IWRAM 0x0300202c, dispatch on `mode`): for each block (row-major), copy the `w x h`
RGB entry `index` into the mode-3 frame buffer at VRAM 0x06000000 (240x160x2 B, pitch 480). The
blit writes `rows*h` lines: 159 for 3x3/4x3, 160 for 4x4/4x2. The proto never writes line 159 in
the 53-row modes, so it keeps whatever VRAM held (black during every capture); shipped builds fill
it with 48 bytes of BG palette RAM repeated. There is no double buffering (mode 3 has one page);
the blit starts in VBlank and stays ahead of the beam, so it does not tear (perf doc).

### 2.5 Timing

`MoviePlayer::Process` (0x08004b28) runs one state per VBlank; a video frame takes 5 VBlanks
(w1[3:0]): state 1 new-scene codebook inflate (+ luma un-predict, YUV backup DMA), state 2 chroma
un-predict + RGB conversion (+ frame-stream inflate init), state 3 inflate one frame of indices,
state 4 blit (+ OSD text such as "Play" drawn into the bitmap by IWRAM 0x03001388 for 30 frames after
an OSD event),
state 5/6 input. Frame rate = 16777216 / 280896 / 5 = **11.9455 fps** (nominal 12 in w1). In the
whole-stream captures every frame of every stream was blitted exactly 5 VBlanks after the previous
one: no late or dropped frame.

## 3. Compression: Hydrogen Inflate

All video blobs (and nothing else on the cart) use `hyCompressionManager::Inflate`
(source path `C:\dev\Libraries\Hydrogen Library\Sources\Managers\Compression\UncompressInflate.cxx`,
IWRAM 0x030005c8 and helpers). It is DEFLATE (RFC 1951) re-packed for the GBA:

### 3.1 Blob and bit reader

```
u32 uncompressed_size
u16 halfwords[]       bitstream
```

Bits are read **MSB-first out of little-endian 16-bit halfwords**: a 32-bit buffer holds the next
bits left-aligned; a refill ORs the next halfword in at `buf |= hw << (16 - cnt); cnt += 16`. Every
read site refills **one** halfword if fewer than `t` bits are buffered, with `t` fixed per site:

| read | t | width |
|---|---|---|
| block type | 2 | 2 |
| dynamic header (HLIT/HDIST/HCLEN) | 14 | 14 |
| code-length code lengths | 3 | 3 each |
| Huffman symbol (code-length, literal/length, distance) | 15 | code length |
| repeat counts for 16/17/18 | 2/3/7 | 2/3/7 |
| length / distance extra bits | n | n |
| fixed-block distance code | 5 | 5 |
| stored length | 16 | 16 |
| stored odd last byte | 8 | 8 |

The exact refill points matter only for stored blocks (3.3), which depend on how many bits are
buffered. Huffman codes are canonical and read MSB-first like everything else (in RFC 1951 they
are bit-reversed relative to the other fields; here nothing is).

### 3.2 Blocks

```
loop:
    type = bits(2)
    0: stored block (3.3)
    1: fixed-Huffman block (RFC 1951 fixed code: 0-143 8 bits, 144-255 9, 256-279 7, 280-287 8)
    2: dynamic-Huffman block: HLIT-257 (5) HDIST-1 (5) HCLEN-4 (4), then HCLEN x 3-bit lengths in
       the RFC order 16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15, then HLIT+HDIST code lengths
       with 16 = repeat previous 3+bits(2), 17 = 3+bits(3) zeros, 18 = 11+bits(7) zeros
       (one sequence across the literal and distance alphabets)
    3: end of stream
```

There is no BFINAL bit; type 3 terminates. Inside Huffman blocks symbols are as in RFC 1951:
0-255 literal, 256 end of block, 257-285 length with the standard base/extra table, distance codes
0-29 with the standard base/extra table (window 32 KiB, matches may overlap). Length/distance
tables live in IWRAM .data at 0x03002d1c as `{u16 dist_base, u16 dist_extra, u16 len_base,
u16 len_extra}` x 30 and are the standard RFC values.

The player never reads the type-3 code during playback (it stops as soon as it has produced the
requested bytes) but every blob on the cart ends with end-of-block + `11`; the encoder writes it.
On this cart: 1521 dynamic blocks, 1 fixed block (in an ep103 frame blob), 0 stored blocks across 1338 blobs (669 codebook + 669 frame-index blobs).

The decoder builds its Huffman lookup tables (8-bit first level for dynamic codes, 9-bit for the
fixed code, a second level for longer codes) in a 0x740-byte node pool at VRAM 0x06017140; the
"Table node pool overflow!" assert guards its size. Lookup tables are an implementation detail:
canonical decoding gives identical results for every code on the cart and under fuzzing.

### 3.3 Stored block (0x03000490)

```
n = bits(16)
if cnt >= 8:                 # >= 8 bits still buffered after reading n
    out(top 8 bits of buf); consume 8; n -= 1
drop all buffered bits (cnt = 0)        # realign to the next unread halfword
copy n & ~1 bytes verbatim from the halfword stream (memory order)
if n is odd: out(bits(8))    # refills one halfword: its HIGH byte is the data byte and its low
                             # byte stays buffered as the next 8 bits of the bitstream
```

This differs from RFC 1951 (no LEN/NLEN pair, a byte may come from the bit buffer, the odd byte
is the second byte of its halfword). No stored block occurs on this cart; the behaviour was
established from the code and confirmed against the cart's routine with synthetic streams.

### 3.4 Streaming and window

`Initialize(obj, blob, dest, mode)` (0x03000094) sets `remaining_total = usize`, `left = 0`,
`state = 0`, `stored_left = 0`, `out = dest`, `in = blob + 4`. `UncompressBlock(obj, n)`
(0x030005c8) is called once per video frame with `n = blocks per frame` and returns the address
where that frame's `n` bytes start. In window mode (`mode = 1`, frame indices) the output buffer is
kept short: when `out` is >= 64 KiB past the buffer start, the newest 32 KiB (+3 for alignment) are
moved to the start with two BIOS CpuSet copies split across consecutive calls. This is invisible in
the output. Codebooks use mode 0 (plain buffer, one call).

The call's accounting, exactly (field names: `left` = obj+0x38, `total` = obj+0x48,
`stored_left` = obj+0x4c, `state` = obj+0x3c):

```
r = min(n, total - left); go = r > 0
ret = out + left                      # left <= 0 here: bytes already produced by an overshoot
while go:
    switch state:
      0: state = {0:5, 1:2, 2:1, 3:7}[bits(2)]
      1: read dynamic header, build tables; left = 0; state = 3; fall into 3
      2: build fixed table;              left = 0; state = 4; fall into 4
      3,4,5:
         left += r; total -= left
         run the block routine (below), which lowers left by the bytes it writes
         total += left; r = left
         if state unchanged: go = r > 0          # else keep looping into the next block
      7: assert out == end && total == 0 ("Buffer overflow!"); stop
return ret
dynamic loop (3): do { sym; literal: left -= 1, stop when left == 0;
                       match: left -= len, stop when left <= 0; EOB: state = 0, stop } 
fixed loop (4):   if left < 0 return; do { same } while (left >= 0)   # one extra symbol at 0
stored (5):       see 3.3; per call it copies min(left rounded up to even, stored_left)
```

A match or the fixed loop's extra symbol can overshoot `left`; the overshoot stays in `left` (negative)
and is credited to the next call through `ret = out + left`. For Huffman blocks this is exact, and it
is all the cart's streams need (they contain no stored blocks), so decoding a whole blob in one call
or frame by frame gives the same bytes.

Three quirks appear only with stored blocks, and a bit-exact streaming implementation must copy
them (`reference/sonicx2-proto/hyinflate.c` `hy_stream_read` does; differential fuzzing found them):

1. Cases 1 and 2 reset `left` to 0 before adding `r`, case 5 does not. When a stored block starts
   inside a call after another block ended in that call, `left` and `r` both hold the remaining
   count, so the stored block is asked for twice as much. It produces the excess, `left` returns to
   0, and the excess is never credited: the caller's next frame starts after bytes it never saw.
2. If the stored header's buffered byte (3.3) satisfies the call exactly and the block was 1 byte
   long, the call returns with `stored_left = 0` and state still 5, so the next call reads a new
   16-bit length from the data.
3. If the stored copy is entered with `left <= 0` (after an overshoot) the rounded count is
   negative: the cart memcpys a negative size, i.e. corrupts memory (unicorn faults at the end of
   mapped RAM). The C port reports `HY_UB`.

## 4. Audio stream

VLC-ADPCM, 16384 Hz, signed 8-bit mono, 16458 samples per 12 video frames. Decoder:
`reference/sonicx2-proto/audio/sx2_adpcm.c` (`sx2adec` CLI).

### 4.1 Where the audio is

Each movie is a container (see the container section): `u32 (size << 4 | count)` followed by
`count` resources, each `u32 (type << 28 | size_bytes)` + `size_bytes` of data. Audio is type 2,
placed directly after the type-1 video resource. The player finds it with
`FindResource(container, 2)` (0x08005d36).

| Stream | Container | Audio resource data | Bytes | Blocks | Video frames | Samples |
|---|---|---|---|---|---|---|
| intro | 0x01e47104 | 0x01e4f7a0 | 16624 | 5 | 59 | 80918 |
| ep103 | 0x0001ab14 | 0x00b42a64 | 4285936 | 1153 | 13828 | 18965102 |
| ep104 | 0x00f5905c | 0x01a20154 | 4353952 | 1191 | 14286 | 19593249 |

(ROM file offsets. `sx2adec ROM --list` recomputes the table from the ROM.)

### 4.2 Resource layout

```
+0x00 u32 w0            0xc1000000 in all three streams; not read by the player
+0x04 u32 w1            bits 6..9 = codec id, must be 3 ("not adpcm audio stream in adpcm object"
                        assert in SoundPlayer::SetSoundFile 0x08006670); bits 0..5 = 1
                        (channel count, unused); bits 10..31 = 0x333333 filler
+0x08 u32 w2            0xcccccc00; unused
+0x0c u16 last_frames   number of video frames covered by the last block (1..12)
+0x0e u16 nblocks
+0x10 u16 block_words[nblocks]   size of each block in 32-bit words (used only for seeking)
      pad to a multiple of 4
      block data, blocks back to back; sum(block_words)*4 ends exactly at the resource end
```

The 0xcc bytes are the MSVC debug-heap fill for uninitialised memory: the encoder wrote parts of an
uninitialised header struct to the file. It is a toolchain fingerprint (Windows, MSVC Debug build of
the encoder), not data.

Derived values (computed by the player in SetSoundFile):
- total video frames = `nblocks*12 + last_frames - 12`;
- total samples = `16458*nblocks + last_frames*1371 + (last_frames >> 1) - 16458`.

One block holds `0x404a = 16458` samples = 12 video frames (1371.5 samples per frame; the encoder
alternates 1371 and 1372). Blocks are a seek unit only: during continuous playback the bit reader
runs straight through block boundaries.

### 4.3 Bit reader

32-bit little-endian words, consumed MSB-first. State (context +0x1c..+0x2c): `word` (left-aligned
valid bits), `bits` (number valid), `next` (look-ahead word, left-aligned), `nbits`, `ptr`.
SetSoundFile loads `word = w[0]`, `next = w[1]`, `bits = nbits = 32`, `ptr = &w[2]`.

Refill (identical behaviour in Thumb 0x08005d6c and ARM 0x03002c3c; all shifts by >= 32 give 0):
```
if nbits != 0:
    word |= next >> bits
    if nbits + bits > 32: next <<= 32 - bits; nbits = nbits + bits - 32; bits = 32; return
    bits += nbits
w = *ptr++
word |= w >> bits; next = w << (32 - bits); nbits = bits; bits = 32
```
The sample loop refills when `bits <= 2`; the header code refills when fewer bits than it needs.

### 4.4 Block header (StreamBase audio driver 0x08005ea0)

Read whenever a block's 16458 samples are used up (and before the first sample):
```
if top bit of word == 1:              # continuation: keep predictor state
    consume 1 bit
else:                                 # resync point
    consume ((bits + nbits) & 31) bits     # 0 flag + pad to the next 32-bit boundary of the stream
    consume 1 bit
    x = signed top 24 bits; consume 24       # x = (int32)word >> 8
    dprev = 1; p1 = p2 = 1; a1 = a2 = 0; b = 0; s1 = s2 = 0
repeat 64 times (context j = 0..63):
    v = next 5 bits: a = v[4:3], b = v[2:1], f = v[0]   (MSB first)
    lo = lowest symbol of {0,1} not in {a,b} (2 if both are)
    hi = highest symbol of {2,3} not in {a,b} (1 if both are)
    perm[j] = a | b<<2 | (f ? hi<<4 | lo<<6 : lo<<4 | hi<<6)
state = 0; remaining = 16458
```
`perm[j]` lists the four 2-bit symbols in order of decreasing probability for context `j`. The
player computes lo/hi with a 4-byte lookup (`0c 0d 08 06` at 0x08016e44), indexed separately by the
low and high halves of the 4-bit mask `(1<<a)|(1<<b)`; the rule above reproduces it for every input,
including the degenerate `a == b`. In all three streams every block header is a resync
(intro 5/5, ep103 1153/1153, ep104 1191/1191); the continuation form never occurs on this cart, so
any block is a clean seek point.

### 4.5 Sample loop (IWRAM 0x03002a4c; context at 0x03004a14)

Context fields (byte offsets): x +0, dprev +4, p1 +8, p2 +0xc, a1 +0x10, a2 +0x14, b +0x18,
bit reader +0x1c..+0x2c, remaining +0x34, state +0x38, perm[64] +0x3c, s1 +0x7c, s2 +0x7d.
SetSoundFile initialises x = 0x8800, dprev = 1, p1 = p2 = 1, the rest 0. All arithmetic is int32,
`>>` is arithmetic, multiplications wrap.

```
per sample:
  if bits <= 2: refill
  i = word >> 29                                   # VLC on the top 3 bits
  len  = {1,1,1,1,2,2,3,3}[i]; rank = {0,0,0,0,1,1,2,3}[i]   # codes 0, 10, 110, 111
  consume len bits
  sym = (perm[state] >> 2*rank) & 3
  state = ((state << 2) & 63) | sym                # last three symbols
  # dequantise (log domain)
  t  = (x >> 8) + 0x74 + (sym & 1 ? 0xf9 : 0)
  dq = (((t & 0x7f) | 0x80) << 7) >> (14 - ((t >> 7) & 15))   # shift amount taken mod 256;
                                                    # >= 32 gives 0
  if sym & 2: dq = -dq
  # step adaptation
  l = x >> 6
  if sym & 1: f = l > 0x12e0 ? 0x1400 : l + ((0x36e0 - l) >> 5)
  else:       f = l < 0x248  ? 0x220  : l + ((-(l + 0x2c0)) >> 5)
  x = x + f + ((-x) >> 6)
  # two-pole, one-zero prediction
  pz = dprev * b
  s  = dq + ((((p2 * (a2 >> 2)) >> 11) + ((p1 * (a1 >> 2)) >> 11) + (pz >> 11)) >> 1)
  if s == 0: s = 1
  e  = dq + (pz >> 12)
  p2 = p1; p1 = s
  out = s / 64 rounded toward zero                 # (s + (s<0 ? 63 : 0)) >> 6
  store low 8 bits of out                          # PROTO: no clipping (see 7)
  # coefficient update
  a1 = a1 - (a1 >> 8); a2 = a2 - (a2 >> 7)
  sg = e < 0
  if e != 0:
      if sg == s1: r = -a1; a1 += 0xc0 else: r = a1; a1 -= 0xc0     # r uses leaked a1
      if -0x1fff <= r <= 0x2000: a2 += r >> 5
      else: a2 += (r < 0 ? -0x100 : 0xff)
      a2 += (sg != s2 ? -0x80 : 0x80)
  if a1 > 0x3c00 - a2: a1 = 0x3c00 - a2
  b = b - (b >> 8) + ((dq ^ dprev) >= 0 ? 0x20 : -0x20)
  s2 = s1; s1 = sg; dprev = dq
```
The predictor structure is G.721-like (sign-sign pole updates, leakage, stability limit on a1), but
there is no a2 range clamp and only one zero. Output samples are signed 8-bit PCM.

### 4.6 Output path and timing

- Ring buffer: 4096 bytes at SoundPlayer+4 (heap object: 0x0300576c for the intro, 0x03005774 for
  the episodes). DMA1 (src ring, dst FIFO A 0x040000a0,
  `DMA1CNT_H = 0xb640`: enable, FIFO timing, 32-bit, repeat, fixed dest).
- Timer 0: reload 0xfc00, prescaler 1 -> 16777216/1024 = **16384 Hz** (FIFO A clock).
  Timer 1: reload 0xf000, cascade + IRQ -> fires every 4096 samples to rewind DMA1 to the ring
  start. SOUNDCNT_H |= 0x0b04 (DMA A full volume, left+right, timer 0, FIFO reset);
  SOUNDCNT_X = 0x80. Output is mono to both speakers; out rate = codec rate = 16384 Hz.
- Before playback the player decodes 4096 samples (a full ring). Then once per video frame
  (every 5th VBlank, state 2 of SoundPlayer 0x08006a3a) it decodes the samples the DAC used
  during those 5 VBlanks: an accumulator in 1/512-sample units advances by 0xab720 (= 1371.5625
  samples = 5*280896 cycles / 1024) and the integer part is decoded.
- A/V sync: video runs at 59.7275/5 = 11.9455 fps; audio carries 16458 samples per 12 frames =
  1371.5 samples per frame (11.9460 fps). The decoder is clocked by the DAC, not by the frame
  count, so audio leads video by 0.0625 samples per frame (about 53 ms by the end of an
  episode); nothing resynchronises within a chapter. Chapter seeks restart audio at
  `frame*1371.5`: whole blocks via `block_words[]`, then the remaining frames are decoded with the
  output store patched to a NOP (the player writes `mov r9,r9` over the `strb` at 0x03002b94,
  self-modifying IWRAM code) and 1371 or 1372 samples per skipped frame.
- When the video ends the player stops decoding: in all three streams exactly the last 4100
  samples are never decoded (the cart decodes 76818 / 18961002 / 19589149 of 80918 / 18965102 /
  19593249). If the data runs out first, the ring is zero-filled.

### 4.7 Differences from the shipped Hydrogen player (Sonic X Vol 1)

| Element | Proto (17 Mar 2004) | Sonic X Vol 1 |
|---|---|---|
| Sample store | low 8 bits of `s/64` stored as-is: values outside -128..127 **wrap** | clamped to [-128, 127] (6 extra instructions before the store, IWRAM 0x03002c14) |
| Rest of the sample loop, refill, VLC table, block header parser 0x08005ea0, reset 0x08005e5c, ring/vblank scheduler | identical instruction sequences (function addresses shift by 0x20..0x62) | |
| Seek/resume 0x0800688c | clears the ring with a DMA3 32-bit fill from a zero word on the stack, then ORs a bit into an IO halfword | different register allocation; zero-fills differently, no IO OR |
| SetSoundFile assert | line 0x68 | line 0x6a (source moved by 2 lines) |

The missing clip is audible: decoding with and without the clip differs in 134 samples
(60 blocks) of ep103 and 260 samples (110 blocks) of ep104, none in the intro. Each is a full-scale
wrap (e.g. +128 played as -128), so it clicks. The cart's captured output confirms the wrap.

### 4.8 Verification

1. **Whole-stream capture (ground truth a).** mGBA (profiling libretro core) with a PC breakpoint on
   the sample-loop entry 0x03002a4c; each call's output range (r1, r2) is read back 3 frames later
   and concatenated (`cap_audio.py`, (a working script of the analysis; not distributed in this repository)). Hash manifests hold one md5 per 16458-sample block
   (`verify/hydrogen/sx2p_{intro,ep103,ep104}.audio_md5.csv.gz`, columns `block,samples,md5`).
   Result: intro 5/5, ep103 1153/1153, ep104 1191/1191 blocks bit-exact (`tools/sonicx2-proto/audio/verify_audio.py`).
   The capture equals the unclipped decode exactly and differs from a clipped decode in exactly
   134 (ep103) and 260 (ep104) samples, which proves the wrap on hardware-equivalent emulation.
2. **Unicorn oracle (ground truth b).** An audio unicorn oracle (a working script of the analysis; not distributed in this repository) runs the cart's code with the
   context in the player's 128-byte layout and compares output bytes and the whole context after
   the call: 20000 cases, 0 mismatches --
   proto sample loop 0x03002a4c 9906, proto Thumb block driver 0x08005ea0 (with ROM refill and
   the IWRAM veneer, block headers included) 5027, Vol 1 clipped loop 0x03002ad0 against the C
   decoder's `clip=1` mode 5067. Half of the cases start from real mid-stream states (some with the
   predictor perturbed), half from random contexts (random bit-buffer fill levels, wide predictor
   values, random permutation tables, random bitstreams).
3. **Cross-check with the shared decoder.** `reference/majesco/adpcm.c` (`MJ_CTAB_FLAT`) equals
   `sx2_adpcm.c` with `clip=1` on all 38.6 M samples of the three streams.

## 5. Verification

| What | Method | Result |
|---|---|---|
| Video, every frame of every stream | mGBA whole-stream capture: md5 of the VRAM bitmap lines the blit writes, taken at the blit's return (breakpoint on the four blit exits); OSD text suppressed by zeroing the OSD counter (context +0x1614) before each VBlank | ep103 13828/13828, ep104 14286/14286, intro 59/59 bit-exact, for both `sx2dec` and the Hydrogen family decoder `hydec` |
| Inflate, real blobs | cart routines in unicorn (the Inflate fuzzer `fuzz.py real`, (a working script of the analysis; not distributed in this repository)): every codebook blob in one call; frame-index blobs in window mode, one `UncompressBlock(blocks)` call per frame, exactly as the player | 669/669 codebook blobs and 669/669 frame-index blobs (every scene of ep103, ep104, intro; 128 MB of indices) identical to the C streaming port |
| Inflate, synthetic | model-aware random encoder (`fuzz.py synth`): stored blocks at every bit-buffer phase, fixed and dynamic blocks, matches up to 258 at distances up to 32 KiB, random call sizes 1..5000 and whole-blob calls, window and plain mode | 898/898 agree with the C streaming port; in 155 of them the cart's chunked output differs from the encoded data (stored-block quirks of 3.4, reproduced) and 10 hit the negative-size copy (flagged by both); whole-blob calls 161/161 equal the encoded data |
| Inflate, mutated | 600 real blobs with 1-4 random bit flips, one call (`fuzz.py mutate`) | 528 agree (372 identical decodes, 156 both fail); 65 where the cart reads undefined data and the C port flags it (52 distance before the buffer start, 12 unused code, 1 negative copy); 7 differ, all after a malformed (incomplete or over-subscribed) Huffman table, where the cart's lookup table holds stale pool entries. All 4563 Huffman tables on the cart are complete codes |
| Codebook | random YUV, all 8 modes, random entry counts (1..256), all 8 brightness levels vs 0x03001be0/0x03001ce4/0x03001470 (+ 0x08005904 for the tables) | 581/581 (combinations the cart asserts on are skipped) |
| Blit | random RGB codebooks and index rasters, all 8 modes vs 0x0300202c | 200/200 |
| Audio | see 4.8 | see 4.8 |
