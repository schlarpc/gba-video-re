# ADS (Majesco, LZMA lineage): container and bitstream specification

Status: v1, 2026-11-07. Every statement here was checked against the cartridges' own code
(addresses below are SRMTHFG, build `bde72f6aba53`, unless another build is named) and against
whole-stream emulator captures of all 19 ADS cartridges and the two Hydrogen-engine carts that
use the ADS codec (see [README.md](README.md) for the verification totals).

The VQ layer (codebook layout, unprediction, YCbCr to RGB555 conversion, blit) is shared with
the Hydrogen family and specified in [`vq.md`](../majesco/vq.md). The
ADPCM audio codec is shared too and specified in [`adpcm.md`](../majesco/adpcm.md). This document
covers what is specific to ADS: packaging, resource container, stream headers, the LZMA layer,
how frames map onto LZMA output, and the ADS-only details of the VQ and audio paths.

ROM tables are not reproduced. The only constants given literally are standard ones (LZMA) or
small enough to be part of the format (the 7 x 2 VQ geometry values, the 16-byte dither table,
which [`vq.md`](../majesco/vq.md) also gives).

## 1. Decoding pipeline

```
ROM ──SFCD archive──> movie_N.mmstr ──resources──> type 1 video, type 2 audio, type 5 text
video: header ─> chunk (segment) list
  chunk = { codebook blob A, index blob B }          both: 8-byte prefix + raw LZMA
  A ─LZMA─> codebook bytes ─unpredict─> YCbCr ─convert─> 256 RGB555 entries (VRAM 0x06012C00)
  B ─LZMA (128 KiB ring)─> nfr index planes, one per frame ─blit─> 240x160 mode-3 framebuffer
audio: header + block-size table ─> ADPCM bit stream, 16458 samples per block, 16384 Hz s8
```

There is no inter-frame prediction other than the LZ window: an index plane can copy bytes from
the previous planes of the same chunk (match distances up to 128 KiB). Every chunk starts a new
LZMA stream and a new codebook, so every chunk start is a keyframe.

## 2. Packaging

### 2.1 SFCD archive (all ADS carts)

The cartridge starts with a small bootstrap (ROM header branch to 0x200, about 3.6 KB of code;
[player.md](player.md) 1). The archive follows it, 4-byte aligned, at ROM 0xC54..0x1148 depending on
the cart. A reader finds it by its magic in the first 2 MiB.

```
u8[4]  "SFCD"
u16    hdr_len         bytes from offset 8 to the start of the data area
u16    w2              0xE000 | count on most carts, 0xC800 | count on Nicktoons 2 and
                       SpongeBob 3; not used by the player
u32    count           members
count x { u32 size; u32 offset; char name[]; NUL; pad to 4 }   offset relative to the data area
data area at SFCD + 8 + hdr_len
```

Members seen: `movie_1..4.mmstr` (episodes), `jingle.mmstr` (intro, on 8 carts),
`logo majesco.mmstr` (59-frame logo), `menuresources.mmstr`, `commonresources.mmstr`,
`main.bin` (the player program, [player.md](player.md) 1). Names are plain ASCII.

### 2.2 Hydrogen packaging (Jimmy Neutron 1, Noah's Final Threat proto)

These two carts use the Hydrogen engine and its container with the ADS codec inside. There is
no archive; movie containers are found by scanning aligned words (the same rule as `tools/hydrogen/hycart.py`'s
`find_containers`):

```
u32 size << 4 | n      size = whole container in bytes, including this word
n x { u32 type << 28 | len; u8 data[len] }
```

A candidate is accepted when entry 1 is type 1, its first word has type 2 in the top nibble, its
second word has width 240 in bits 10-19, and the entries tile `size` exactly. Jimmy has 6
containers (logo, 4 episodes, a 434-frame intro), Noah 4 (logo, 2 episodes, a 738-frame intro).

## 3. `.mmstr` resource list

```
u32 total             = member size
u32 count_word        low byte = resource count (only used as a hint)
repeat until total:
    u32 w             type = w >> 28, words = w & 0x0FFFFFFF
    u8  data[words*4]
```

| type | content |
|---|---|
| 1 | video stream (section 4) |
| 2 | audio stream (section 6) |
| 4 | image: `u32 width:12 | height:12 | flags:8`, obfuscated name, LZMA blob of raw BGR555 |
| 5 | text: `u32`, obfuscated name, NUL, pad to 4, then an LZMA blob (section 5.1 format) |

Episodes hold one each of types 5, 2, 1 (Nicktoons 2 and SpongeBob 3 put the audio first; the
Shredder Strikes proto has no text resource). `menuresources.mmstr` holds the menu images and a
type-5 resource named `Settings` (section 3.2).

### 3.1 Text encoding

Titles, chapter names and text resources use a substitution alphabet: 1..10 = `0`..`9`,
11..36 = `a`..`z`, 37..62 = `A`..`Z`, 0xFF = space, 0xFE = newline. Punctuation codes observed
in titles and settings (confirmed against the menus the carts draw): 0x3F `.`, 0x40 `,`,
0x41 `:`, 0x45 `&`, 0x48 `'`, 0x49 `-`, 0x4A `/`, 0x4B `+`, 0x4E `_`. The settings parser in
`main.bin` (0x02003998 in build bfa01253c3c5) reads numbers from codes 1..10 with 0x49 as the
minus sign.

### 3.2 The `Settings` text

`menuresources.mmstr` carries a text resource named `Settings`: lines `key value` separated by
0xFE, `//` (0x4A 0x4A) starts a comment. Keys known to the players: `ThumbnailPlayingTime`,
`ThumbnailStart_N`, `ThumbnailTime_N`, `Instructions_N`, `MenuText_N`, `Player_N`,
`BaseBrightness`. Only `BaseBrightness` affects decoding: build bfa01253c3c5 adds
`trunc(BaseBrightness * 10000 * 256 / 100000)` (= 25.6 per step) to the brightness gain (VQ
conversion, [`vq.md`](../majesco/vq.md) 2.2). The only cart that sets it is *Cartoon Network Collection - Edition
Platinum (France)*: `BaseBrightness 2`, gain 256 + 51 = 307 for every stream including the logo.
The same line is commented out in the TMNT (France) Settings text. The other builds have no
code that reads the key.

## 4. Video resource (type 1)

### 4.1 Header

```
u32 w0        bits 0-7 chapter count, 8-27 frame count, 28-31 = 2 (stream kind)
u32 w1        stream format word, two layouts (below)
ADS packaging:      u32 chunk_count; title (obfuscated, NUL, padded to align4(len+4) from its start)
Hydrogen packaging: u16 chunk_count; u16 0; no title
chapter_count x { u16 first_chunk; name (obfuscated, NUL) }
    ADS: record padded to 2 + align4(len+4) bytes; Hydrogen: padded to an even address
align to 4, then chunk_count chunks (4.2), then u32 0
```

`w1` layout A, value 0x0A03C0C7 (CN USA collections, Disney 1, DBGT, Nicktoons 2,
SpongeBob 3, Jimmy, Noah; also every Hydrogen stream with codec 5):

| bits | field |
|---|---|
| 0-3 | codec: 7 = LZMA (ADS), 5 = Hydrogen inflate |
| 4-9 | 12 (nominal frame rate) |
| 10-19 | width 240 |
| 20-29 | height 160 |

`w1` layout B, value 0x280F0307 (SRMTHFG, Proud Family, Disney 2, Nicktoons 3, French carts,
TMNT proto): bits 0-3 codec 7, 4-11 = 0x30, 12-21 width 240, 22-31 height 160. The French
builds' blit takes the 4x4 row count from `w1 >> 22`; nothing else reads these fields.

The frame count in `w0` always equals the sum of the chunk frame counts.

### 4.2 Chunk (segment)

```
u32 w0     bits 0-2 mode, 3-5 = 6, 6-15 ncb (256), 16-31 nfr (frames in this chunk)
u32 w1     bits 0-12 = len(A)/4, bits 13-31 = len(B)/4
u8  A[len(A)]     codebook blob
u8  B[len(B)]     index blob: nfr index planes back to back
```

Modes 1 (4x4, Nicktoons 2 / SpongeBob 3), 3 (4x3), 5 (3x3) and 7 (4x2: the logo, jingles, and
some episode segments of Disney 1/2 and Proud Family; Jimmy Neutron's intro) occur; [features.md](features.md) lists
them per build. Index plane size = grid_w x grid_h = 2400 / 3180 / 4240 / 4800 bytes ([`vq.md`](../majesco/vq.md) 1).
Chunks hold 1..389 frames; the mode can change from chunk to chunk within a stream.

### 4.3 Chapters and seeking

A chapter names a chunk. Seeking is by chunk only: the player starts the chunk's two LZMA
streams from their beginnings, and the audio at the block covering the chunk's first frame
(section 6.2). `adsdec --start-frame` decodes from the start of the containing chunk.

## 5. LZMA layer

### 5.1 Blob

```
u32 usize       decompressed size
u32 params      bits 0-2 pb, 3-5 lp, 6-9 lc, 10-11 0..3 (varies, ignored),
                12-16 dictionary log2 (17 on every blob), bit 16 always set
u8  data[]      raw LZMA1 stream, no end marker, the first byte is 0
```

All blobs in all carts have lc = 0, lp = 0, pb = 2, dictionary 2^17 (params 0x0001x002 with
x in {0, 4, 8, C}). Decompression is standard LZMA (Pavlov's specification; `reference/ads/lzma_dec.c` is
written from it) and must stop after exactly `usize` bytes.

The cartridge's decoder (init 0x03000404, decode 0x03000554, core 0x03001C58, ARM in IWRAM) is a
hand-optimised LZMA with the canonical probability layout (0x736 + 0x300 << (lc+lp) probabilities,
initialised to 0x400). Its core hard-wires lc = 0, lp = 0 and pb = 2: it reads `params` only to
size the probability array. The unicorn oracle confirms this: streams with any other lc/lp/pb
decode wrongly on the cartridge (`tools/ads/emu/uc_lzma.py`), while with 0/0/2 its output and the
number of input bytes it consumes equal a standard decoder's on every tested stream.

### 5.2 Codebook blob

Decompressed in one go into a 0x1800-byte EWRAM slot (8 KiB window, never wraps). `usize` =
ncb x (bw x bh + 2 x chroma pairs) = 6144 (4x4), 4608 (4x3), 3328 (3x3), 3072 (4x2). The
following steps (unpredict, conversion, entry layout) are in [`vq.md`](../majesco/vq.md) 2.

### 5.3 Index blob and the 128 KiB ring

The index blob is decompressed into a 128 KiB ring buffer (EWRAM, `mask = 0x1FFFF`), a little at
a time: each call of the decode routine asks for about one index plane. Frame `k` of the chunk is
bytes `[k*N, (k+1)*N)` of the decompressed stream, read in place from the ring by the blit (the
blit's pointer wraps at 128 KiB). Matches reach back up to exactly 131072 bytes (the encoder's
dictionary = the ring), so a decoder needs at least a 128 KiB window. The LZMA position state
(`pos & 3`) is taken from the ring offset, which equals the stream offset modulo 4 because the
ring size and every plane size are multiples of 4.

Two details of the cartridge's incremental decoding matter for a reimplementation that also
decodes in slices:

- **Match overshoot.** A decode call for `n` bytes finishes the match it is in, so it can write
  up to 272 bytes past `n`; the excess is kept as a negative count in the decoder object (+0x1C)
  and subtracted from the next request. Total output is unaffected.
- **Deadline stop.** The core polls a flag (pointer at 0x0300060C) that the player's IRQ code sets
  when the next display deadline is due, and returns early. The player then requests the
  remainder later. This only changes when bytes are produced, never which bytes.

At a chunk start the player prefills the ring (130,795 bytes in SRMTHFG's first chunk) and then
keeps about one plane ahead of the blit.

### 5.4 Byte-exact consumption

With lc = 0, lp = 0, pb = 2 the cartridge reads exactly the bytes a standard range decoder reads:
5 bytes at init (the first is 0), then one byte per normalisation (range < 2^24). The unicorn
oracle compares the input pointer after every call.

## 6. Audio resource (type 2)

### 6.1 Layout

```
u32 a, u32 b        encoder state left in the header (0x60000000 / 0xC1000000 style values,
                    and 0xCCCCCCC1-like fill); not read by the player
u32 0
u16 hdr_field       1..12, varies per stream; not read by the player
u16 nblocks
u16 block_words[nblocks]   size of each coded block in 32-bit words
pad to 4
u8  payload[sum(block_words) * 4]
```

`sum(block_words) * 4` equals the payload length exactly on every stream. Every block decodes to
16458 (0x404A) samples; the player plays signed 8-bit samples at 16384 Hz (Timer0 reload 0xFC00).
The bit stream and the decoder are specified in [`adpcm.md`](../majesco/adpcm.md). ADS uses the
context-table coding `MJ_CTAB_VLC`; the Hydrogen-engine carts (Jimmy, Noah) use `MJ_CTAB_FLAT`.

### 6.2 Seeking

Block `i` starts at byte `4 * sum(block_words[0..i-1])` of the payload, on a 32-bit word
boundary. Every one of the 51,793 blocks in the 96 streams is a restart block ([`adpcm.md`](../majesco/adpcm.md) 4),
so decoding can start at any block. The player's seek (0x020015C8) converts a time to a block index and a sample offset
inside the block (dividing by 16458), sums the block table, and decodes and discards the
leading samples.

### 6.3 End of a track

The player stops calling the audio decoder when the video ends (logo: 80,918 samples of a
80,938-sample track; an episode: up to one block before the end of the last block). The last
block of every track is therefore only partly decoded by the cartridge, and the manifests cover
it only up to that point.

## 7. Timing

- Video: one frame every 5 VBlanks (59.7275 / 5 = 11.9455 fps). The stream header's nominal
  "12 fps" is not used for timing.
- Audio: 16384 Hz, one 16458-sample block = 1.00452 s, i.e. 1371.6 samples per video frame.
  A frame and a block are not aligned to each other; the player keeps them in step by
  scheduling both from VBlank and the Timer1 sample count ([player.md](player.md) 5).
- 11.9455 fps x 1371.6 samples = 16384 Hz exactly, so A/V do not drift.

## 8. Decoder output and verification target

`adsdec` produces, per frame, the 240x160 BGR555 framebuffer the cartridge displays (row 159 of
the 53-row geometries is black), and per track the signed 8-bit PCM the cartridge's ADPCM
routine produces. Both are compared with the emulator by md5 (`tools/ads/verify.py`).
