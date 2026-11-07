# Hydrogen GBA Video: container, compression and bitstream specification

Scope: the 13 Majesco "Hydrogen" GBA Video cartridges (list in [README.md](README.md)), all 10 player
builds. Status: complete; every element below is verified bit-exact (section 8).
Addresses are run addresses in *All Grown Up! Vol 1* (build `04cbcc07fe42`) unless marked
"ROM" (file offset). Other builds: see [player.md](player.md) section 9.

Conventions: all multi-byte fields are little-endian. `w[a..b]` = bits a..b of word w, bit 0 = LSB.
"Byte n" = byte offset from the start of the structure.

The VQ layer (codebook, unprediction, colour conversion, dither, blit) is shared with the ADS
family and specified in [../majesco/vq.md](../majesco/vq.md); this document references it and
specifies everything else. The ADPCM audio bitstream is specified in
[../majesco/adpcm.md](../majesco/adpcm.md); section 7 gives only the Hydrogen framing around it.

## 1. Cartridge layout

A Hydrogen cartridge is a plain 32 MiB linear ROM (no Matrix mapper). In ROM order:

| part | where | contents |
|---|---|---|
| ARM entry, crt0 | ROM 0x000-0x194 | header, stack setup, init-table copy, branch to Thumb `main` |
| player code + rodata | ROM 0x194..~0x10000 | Thumb C++ (Hydrogen Library + "MoviePlayer Example"), UI strings, credits text |
| UI resources | after the code | fonts, menu bitmaps, sprite animations |
| movie containers | from about ROM 0x10000 | 4 to 6 containers (section 2), packed back to back with other data |
| init table + IWRAM image | ROM end (e.g. All Grown Up 0x1fe4a4c) | `{src,dst,len}` copy entries, then the 0x2fb8..0x3034-byte ARM image for 0x03000000 |

There is **no directory** of movies. Each container is followed by an 8-byte **file slot**
`{u32 pointer to the container, u32 container size}` written at build time, and the code reaches
each slot through a literal pool: the episode slots from the static constructor that fills the menu
table (4 entries of `{container pointer, title string, u16 preview segment}` in IWRAM, 0x03002fc0 in
All Grown Up; ROM literals at 0x0800d470..), the logo and intro slots from `IntroMovie` (ROM
literals 0x08006618/0x08006620). A reader can follow those literals or simply scan for containers
(section 2.3).

## 2. Movie container

### 2.1 Layout

```
u32 head            = size << 4 | count     size = total bytes incl. this word; count = entries (2)
repeat count:
    u32 entry_head  = type << 28 | len      len = bytes from this word to the next entry_head
    u8  payload[len - 4]
```

Types: **1 = video stream** (section 3), **2 = audio stream** (section 7). Every Hydrogen
container has exactly one of each, video first. The player looks an entry up by type with a
linear walk (ROM 0x08005e06: `p += 4 + (entry_head & 0x0fffffff)`).

### 2.2 Streams per cartridge

Every cartridge has, in ROM order:

1. **Logo** (59 frames, 4 segments, mode 7): the "Majesco Digital Video" animation, played at boot.
   Same size (video 0x868c, audio 0x40ec) on all 13 carts.
2. **Episodes**: 2 to 4 containers (one per episode or, on the Collection carts, one per cartoon),
   each with 6 chapters.
3. **Show intro** (359 to 733 frames, no chapters): the title sequence played before "Press Start";
   on 11 of the 13 carts (Nicktoons Collection 1 and CN Collection 1 have none).

### 2.3 Finding containers

Scan every 4-byte-aligned offset `o` for: `rom[o+4]` has type 1, `rom[o+8]` has type 2 in its top
nibble (the video stream's first word, section 3), `rom[o+12] bits 10..19 = 240`, a count of 1..7,
and an entry chain that ends exactly at `o + size`. This finds exactly the 4..6 real containers on
every Hydrogen cart and on the two Hydrogen-packaged LZMA carts (Jimmy Neutron 1, the Noah proto);
`hycart.find_containers()` / `hy_find_containers()`.

## 3. Video stream header

```
byte 0   u32 w0:  [0..7]   nchap        chapters (6 for episodes, 0 for logo/intro)
                  [8..27]  frames       total frames of the stream (= sum of segment frames)
                  [28..31] 2            stream kind
byte 4   u32 w1:  [0..3]   codec        5 = Inflate (all Hydrogen carts); 7 = LZMA (Jimmy Neutron 1,
                                          Yu-Gi-Oh! Noah proto: same container, ADS codec)
                  [4..9]   fps          12 on every stream; PlayMovie() asserts 12 or 15
                  [10..19] width        240
                  [20..29] height       160
                  [30..31] 0
byte 8   u16 nseg                       number of segments
byte 10  u16 0
byte 12  chapter records, nchap times:
             u16 first_segment
             char name[]                NUL-terminated, then padded to an even offset
         padding to a multiple of 4
then     nseg segments, back to back
```

The chapter names are "Chapter" + one separator byte + one digit, in the substitution
alphabet shared with ADS (1..10 = digits, 11..36 = a..z, 37..62 = A..Z): "Chapter" is
`27 12 0b 1a 1e 0f 1c`. The separator is 0x4C on 37 streams and 0x4E on one.

Chapter `k` starts at segment `first_segment[k]`; its first frame is the sum of the frame counts
of the segments before it. The player's `StreamBase` builds a 12-byte record per chapter
`{segment pointer, u16 first frame, u16 first segment, name pointer}` (ROM 0x080057c0).

The `fps` field is nominal. The real rate is fixed by the player's scheduler: one frame every 5
VBlanks, 2^24 / (5 x 280896) = **11.9455 fps** ([player.md](player.md) section 5).

## 4. Segment

A segment is a run of frames with one codebook. It is the unit of random access: it is decodable
on its own (own codebook, own compressed index stream), so **every segment start is a seek point**.

```
u32 w0:  [0..2]   mode        block geometry (vq.md section 1); 1, 3, 5 or 7 in all streams
         [3..5]   6           constant on every stream; not read by the player
         [6..15]  ncb         codebook entries; 256 on every stream
         [16..31] nframes     frames in this segment (1..1100 observed, median 32)
u32 w1:  [0..12]  cb_words    codebook blob length / 4
         [13..31] idx_words   index blob length / 4
u8  codebook_blob[cb_words * 4]       compressed, section 5
u8  index_blob[idx_words * 4]         compressed, section 5
```

Next segment = this segment + 8 + 4*cb_words + 4*idx_words. The segments tile the video entry
exactly on every stream.

Decompressed sizes (the blob prefixes say so, and the player checks the codebook one):

- codebook: `ncb * (bw*bh + 2*C)` bytes (C = chroma pairs per entry, vq.md), at most 0x1800
  (the player asserts "Invalid codebook size!" above 0x1800);
- index stream: `nframes * B` bytes, B = index bytes per frame (2400 / 3180 / 4240 / 4800).

Frame `f` of the segment is index bytes `f*B .. f*B+B-1`. The codebook is applied as specified in
vq.md: unpredict, convert with the player's dither table and the current brightness gain, then
blit every frame of the segment with it.

The player also has a 2-byte-index path (when `ncb > 256` it requests `2*B` bytes per frame)
but its blitter reads bytes, so that path is unusable; no stream has `ncb > 256`.

## 5. Compression layer: the Hydrogen DEFLATE variant

`hyCompressionManager::Inflate` (IWRAM 0x03000094 Initialize, 0x030005c8 UncompressBlock,
0x03002870 dynamic-block core, 0x03000144 fixed-block core, 0x03000490 stored copy, 0x030024a0 code
lengths, 0x0300265c table build, 0x03002d44 bit read). It is DEFLATE (RFC 1951) with a different bit
packing and block framing. Everything not listed as a difference is RFC 1951 unchanged, and the
tables (length/distance bases and extra bits, code-length order, fixed code) are the RFC's.

### 5.1 Blob

```
u32 usize            uncompressed size
u16 words[]          the bit stream
```

### 5.2 Bit reader (difference 1)

Bits are taken **most-significant first from 16-bit little-endian words**:

```
buf = 0 (32-bit, MSB-aligned), n = 0
get(k):  while n < k: buf |= next_u16() << (16 - n); n += 16
         v = buf >> (32 - k); buf <<= k; n -= k; return v
```

So every multi-bit field (HLIT, extra bits, LEN, ...) is read as a k-bit integer, most significant
bit first, and every Huffman code is read MSB first. Canonical codes are assigned exactly as in RFC
1951 section 3.2.2; read MSB-first they need no bit reversal.

### 5.3 Block header (difference 2)

```
loop:
    type = get(2)            no BFINAL bit
    0: stored block          5.5
    1: fixed Huffman         RFC 1951 3.2.6 (distance codes = get(5))
    2: dynamic Huffman       5.4
    3: end of stream         stop; the output must now be exactly usize bytes
```

The player asserts (in its debug build) that the output pointer equals the buffer end and that no
bytes remain when it meets type 3.

### 5.4 Dynamic block header

```
hlit  = get(5) + 257
hdist = get(5) + 1
hclen = get(4) + 4
for i in 0..hclen-1: cl_len[ORDER[i]] = get(3)     ORDER = 16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15
code lengths for hlit + hdist symbols, one sequence:
    0..15  literal length
    16     repeat previous length get(2)+3 times
    17     get(3)+3 zero lengths
    18     get(7)+11 zero lengths
```

identical to RFC 1951 except for the bit order of the fields.

### 5.5 Stored block (difference 3)

```
len = get(16)                        no NLEN
if n >= 8:                           8 or more bits still buffered (even if len = 0, see 5.7)
    emit(buf >> 24); buf <<= 8; n -= 8; len -= 1
buf = 0; n = 0                       the rest of the buffered bits are dropped
copy (len & ~1) bytes from the read pointer in memory order; advance it
if len is odd: emit(get(8))          = the HIGH byte of the next u16; its low byte stays buffered
```

How many bits are buffered at `len` depends on the reader's history, because the Huffman decoders
**top the buffer up eagerly**: before every Huffman symbol the cartridge loads one more 16-bit word
if fewer than 15 bits are buffered (fewer than 14 before the dynamic header), whether or not the
symbol needs them. Plain `get(k)` loads a word only if fewer than k bits are buffered. A stored
block's byte layout is therefore defined by that exact refill policy (the C decoder reproduces it;
`hyinflate.py`, which refills lazily, matches only for streams without stored blocks). No retail
stream contains a stored block (section 9); this path was verified by fuzzing the cartridge's own
routine (section 8.3).

### 5.6 How the player uses it

- The codebook blob is decompressed in one call into a 0x1800-byte IWRAM buffer.
- The index blob is decompressed **one frame per call** (B bytes), into a window buffer with the
  "circular" flag set. When the write pointer is more than 0xffff past the buffer start, the
  player copies the last 32 KB (from `(out - 0x8000) & ~3`) back to the buffer start and rebases
  the pointers; matches never reach further back than 32768 bytes, so this is invisible in the output.
  A request may end inside a match: the match is completed and the excess counts toward the next
  request. Neither affects the output byte sequence.
- Matches cross frame boundaries freely: the LZ window spans the whole segment. This is the only
  inter-frame redundancy removal in the format (a block row unchanged from the previous frame
  becomes a length-258 match at distance B).

### 5.7 Request bookkeeping (what the player does with a partial request)

The player asks for one frame (B bytes) per call and uses the returned pointer as the frame. The
routine keeps `rem` (bytes still owed; negative = produced ahead), `left` (bytes left in the
stream), `state` and the stored-block counter as signed 32-bit values. Transcribed from IWRAM
0x030005c8 (`hyinf_request` in `reference/hydrogen/hyinflate.c`):

```
k = min(n, left - rem);  frame = out + rem;  go = k > 0
while go:
    state 0: read the 2-bit type -> 5 (stored), 2 (fixed), 1 (dynamic), 7 (end)
    state 1: read dynamic header; rem = 0; state 3, then as 3
    state 2: rem = 0; state 4, then as 4
    state 3/4/5: rem += k; left -= rem; run the block decoder against rem; k = rem; left += rem;
                 if still in this block: go = k > 0
    state 7: stop
return frame
```

The block decoders:
- dynamic: emits literals until `rem` hits exactly 0, or copies matches while `rem > 0`, so a match
  may overshoot (rem < 0); the next request starts that much earlier in the output (`frame = out + rem`).
- fixed: returns at once if `rem < 0`, otherwise decodes while `rem >= 0`, i.e. it always produces
  at least one byte more than asked. Harmless for the same reason.
- stored: copies `min(len_left, rem rounded up to even)` per call.

Quirks of the cartridge, reproduced by the C decoder and confirmed by fuzzing; none occurs in a
retail stream, so they only constrain encoders:
1. When a block ends inside a request and the next block is **stored**, `rem` is not reset to 0
   (states 1/2 reset it, state 5 does not), so the remaining request is counted twice: the stored
   copy produces up to twice what was asked and the following frames start late.
2. A stored block of length 1 whose byte comes from the bit buffer (5.5) and exactly completes a
   request leaves the block counter at 0 in state 5; the next request reads a new stored header from
   the following bits.
3. A stored block with `len = 0` and 8 or more bits buffered still emits a byte and underflows the
   counter.
4. `rem` can go to -2 or below inside a stored block; the copy size is then negative and the
   cartridge's memcpy runs off the end of memory.
5. The Huffman tables (8-bit primary table plus sub-tables) share a pool of 0x740 entries
   (`StreamBase` passes it to Inflate::SetTablePool); a literal/length + distance table pair that
   needs more fails a (debug) assert and overwrites memory. The retail maximum is about 950 entries.
6. A code-length run (16/17/18) that crosses the end of the HLIT+HDIST lengths never meets the
   loop's `count == 0` exit and runs away.

So a conforming encoder emits dynamic (or fixed) blocks only, keeps every table pair within 0x740
entries, and ends with block type 3. Majesco's encoder did exactly that on all 62 streams (fixed
blocks do occur, in 24 segments, and the player's request pattern handles their overshoot).

## 6. Decoding procedure (whole stream)

```
for each segment s:
    cb  = inflate(s.codebook_blob)                         5
    unpredict(cb, s.mode, s.ncb)                           vq.md 2.1
    VRAMCB = convert(cb, s.mode, s.ncb, dither, gain)      vq.md 2.2 (entries ncb..255 keep old values)
    idx = inflate(s.index_blob)
    for f in 0 .. s.nframes-1:
        frame = blit(idx[f*B : (f+1)*B], s.mode, VRAMCB, pal24)   vq.md 3
```

`dither` = 16 bytes from the ROM: the 16 bytes immediately before the block-geometry table
`{4,4,4,4,4,3,4,3,3,3,3,3,4,2,4,2}` (identical in all 13 carts). `gain` = 256 (default brightness;
the player's 11 levels are {128,...,384} from the 11 x s32 table just before the dither table).
`pal24` = 0 (the BG palette during playback).

Random access: decode from the first frame of the segment that contains the target frame.
Seeking costs at most one segment's index stream (median 42 frames, see stats).

## 7. Audio stream (type 2): Hydrogen framing

The audio bitstream (adaptive ADPCM, 2-bit codes through a 1/2/3-bit VLC, 64-entry 2-bit context
table per block) is specified in [../majesco/adpcm.md](../majesco/adpcm.md). Hydrogen uses the
**flat context-table coding** (`MJ_CTAB_FLAT` in `adpcm.h`): every table entry is a 5-bit escape
form, no palette VLC.

```
byte 0   u32  0x71000000 / 0xc1000000 / 0x01000000      (encoder leftovers; not read)
byte 4   u32  [6..9] codec = 3 (ADPCM)                    SoundPlayer::SetSoundFile asserts it
              (other bits: 0xfffffcc1 / 0xccccccc1 / 0x000000c1 -- uninitialised fill)
byte 8   u32
byte 12  u16  ?            (used only in the seek arithmetic)
byte 14  u16  nblocks
byte 16  u16  block_words[nblocks]     size of each coded block in 32-bit words
         padding to 4
payload: nblocks coded blocks of 16458 (0x404a) samples each, 8-bit signed, 16384 Hz mono
```

16458 samples = 12 video frames x 1371.5625 samples/frame (to within 0.75 sample), so block k
starts at video frame 12k; the player seeks audio by `frame / 12` blocks plus a remainder decoded
and discarded in 1371/1372-sample steps. The header word decoding and the predictor reset at a
block start are in adpcm.md.

Playback: 4096-sample prefill, then 5 x 280896 x 256 / 512 / 256 = 1371.5625 samples per video frame
(a 21-bit phase accumulator; 1371 or 1372 samples per call), written into a 4096-byte ring that
DMA1 feeds to FIFO A at 16384 Hz ([player.md](player.md) section 6). Audio and video run off the same
CPU clock, so they stay locked without any correction.

## 8. Verification

### 8.1 Whole streams in the emulator (ground truth a)

`tools/hydrogen/emu/capture.py` makes the cartridge's own PlayMovie() play each stream from frame 0 (state
injection at the top-level dispatcher, no UI input) and hashes, at every return from the VQ blit,
the 240x160 framebuffer and the frame's index bytes, and at every return from the ADPCM routine
its output bytes. `tools/hydrogen/verify.py` compares `hydec` (C) and the shared `reference/majesco/adpcm.c` with these
manifests. Results per stream are in [stats.json](stats.json) and [README.md](README.md).

### 8.2 Unicorn oracle (ground truth b)

`tools/hydrogen/emu/uc_oracle.py` loads a playback savestate into unicorn and calls the cartridge's IWRAM
routines directly: Inflate::Initialize + UncompressBlock (in arbitrary request sizes),
UnPredictLuminance/Chrominance + the converter (after rebuilding the brightness table with the
cartridge's own Thumb routine), and the blit.

### 8.3 Fuzzing

`tools/hydrogen/fuzz.py inflate`: random streams from `tools/hydrogen/hyenc.py`, a test-vector encoder that emits every
block type (stored blocks at every alignment and parity, fixed, dynamic with all code-length
codes), all length/distance codes, decoded by the cartridge in random request sizes and compared
with hyinf_decode (C) and hyinflate.py. `tools/hydrogen/fuzz.py vq`: random codebooks in all 8 modes, random
brightness level, random index frames and VRAM codebooks, through the cartridge's converter and blit
vs mvq_convert/mvq_blit. Results in [README.md](README.md).

## 9. What the retail streams use

Measured over all 62 streams (`reference/hydrogen/hydec --stats`, stats.json):

| element | used |
|---|---|
| block types | dynamic Huffman almost everywhere: 9310 of 9313 codebook blobs are one dynamic block, 3 are one fixed block; index blobs have 1 to 14 blocks, and 21 of them contain one fixed block (usually the first); **no stored blocks** |
| end marker | every one of the 18626 blobs ends with block type 3 right after its last end-of-block code |
| Huffman code lengths | literal/length codes up to 15 bits, distance codes up to 15 bits |
| modes | 1, 3, 5, 7 (all four odd modes), at most 4 distinct per stream |
| ncb | 256 always |
| segment length | 1 to 2118 frames |
| codec nibble | 5 on every Hydrogen stream |
