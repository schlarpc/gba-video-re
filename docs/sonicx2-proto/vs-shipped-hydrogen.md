# Sonic X Vol 2 (Proto) against the shipped Hydrogen builds

Summary of every difference between the prototype (build `96a25377cb75`, 17 March 2004) and the
shipped Hydrogen builds that matters to a decoder, with pointers to the full text in
[player.md](player.md) and [format.md](format.md). The shipped side is described in
[../hydrogen/](../hydrogen/README.md).

## 1. Classification

The proto is an ordinary Hydrogen cart, not an unknown codec. The static classifier reported it as
UNKNOWN because its "VQ blit" coverage was 0 %: the blit dispatch at IWRAM 0x0300202c has the same
four geometries (4x4/4x3/3x3/4x2, RGB555 codebook at VRAM 0x06012C00, raster to 0x06000000), but
the 3x3 path (modes 4/5) is coded differently from the shipped builds, and that is the only blit
path that runs on episode 103. Inflate (0x030027ec/0x0300241c/0x030025d8) and the codebook path
(UnPredictLuminance 0x03001be0, UnPredictChrominance 0x03001ce4, YUV->RGB555 0x03001470) are the
same code as in Sonic X Vol 1.

All five shipped builds compared (Sonic X 1, Yu-Gi-Oh!, TMNT, SpongeBob 1, Dora) carry identical
codec code (blit, YUV->RGB, unpredict, inflate, ADPCM). The proto differs from all of them in the
same places, so it is their common ancestor.

## 2. Video

`reference/hydrogen/hydec` decodes the proto bit-exactly as it is: its full 240x160 frames are
identical to the independent `reference/sonicx2-proto/sx2dec` on all 28,173 frames (ep103 13,828,
ep104 14,286, intro 59), and both match the cart. `tools/sonicx2-proto/verify.py ROM --hydec PATH`
checks hydec against the cart-captured manifests `verify/hydrogen/sx2p_{intro,ep103,ep104}.frame_md5.csv.gz`
(md5 of the VRAM lines the blit writes: 159 for mode 5, 160 for mode 7; captured at the blit's
return with the OSD counter ctx+0x1614 zeroed so the "Play" text is not drawn into the bitmap).

Streams: ep103 (container ROM 0x1ab14, mode 5 only), ep104 (0xf5905c, mode 7 only) and the intro
(0x1e47104, the Majesco DIGITAL VIDEO logo, mode 7). They are reached only through `{ptr,size}` file
slots at ROM 0xf59054 / 0x1e470f4 / 0x1e53890; a container scan finds them all.

Differences that do not change the decoded pixels of the proto's streams:

- **Row 159.** In modes 2-5 the shipped builds copy 48 bytes of BG palette RAM (0x05000000,
  colours 0-23) ten times across row 159. The proto never writes row 159 in those modes; it stays
  black.
- **3x3 blit (modes 4/5).** The shipped builds blit block pairs with packed 32-bit `stm`. They form
  the bottom-left pixel of every odd block as `(w & 0xffff0000) + (next >> 16)`, which adds RGB
  codebook halfword 9 (padding: 0x03001470 writes only 9 halfwords per 32-byte entry). The output
  equals the proto's only while halfword 9 is 0, which it always is in practice. In a unicorn test
  with a nonzero pad, 2,120 pixels per frame differ. `reference/majesco/vq.c` would have to model
  this if the VRAM padding were ever nonzero. Modes 0/1 and 6/7 are byte-identical.
- **Brightness table** (ctx+0x10dd: 512 x 0, a 256-entry ramp `min(255, coef*i>>8)`, 512 x 255):
  the proto has 8 levels {230, 256, 281, 307, 332, 358, 384, 409}, default index 1 (= 256); the
  shipped builds have 11 levels 128..384, default index 5 (= 256), plus an additive bias global
  (Vol 1: 0x030032c0). Both start at identity.
- **Dither table** (ROM, 16 bytes before the dims table): identical. It is the 4x4 Bayer matrix
  halved, offsets O = 7 - B.

## 3. Inflate

The blob prefix is 4 bytes (u32 size) and every bitstream ends with block type 3 (end); the player
never reads it during playback, but every blob has it. Stored blocks (type 0) never occur on the
proto. Their semantics (a byte comes from the bit buffer when 8 or more bits are buffered; an odd
final byte is the high byte of its halfword, and the low byte stays in the bit buffer) were
confirmed against the cart routine with synthetic streams.

Differential fuzzing of the cart's `UncompressBlock(n)` (proto 0x030005c8, the same code in all
shipped builds) with random call sizes found three bugs in its byte accounting, all in stored blocks
([format.md](format.md) 3.4):

1. Case 5 (stored) does not reset obj+0x38 before adding the request, unlike the Huffman headers.
   A stored block that starts inside a call after another block ended is asked for twice the
   remainder, produces it, and the excess is never credited, so the next frame skips those bytes.
2. A 1-byte stored block whose byte comes from the bit buffer and exactly ends a call leaves
   `stored_left = 0` in state 5, so the next call reads a new 16-bit length from the data.
3. A stored copy entered with obj+0x38 <= 0 (after an overshoot) does a memcpy with a negative size.

`reference/sonicx2-proto/hyinflate.c` (`hy_stream_read`) reproduces all three. They never trigger
on streams without stored blocks, and decoding each segment's index blob in one call is exact for
such streams.

## 4. Audio

- Hydrogen VLC-ADPCM with the flat 5-bit context table (`MJ_CTAB_FLAT`). The shared
  `reference/majesco/adpcm.c` equals the cart everywhere **except** where a sample leaves
  [-128, 127]: the proto stores the low 8 bits of `s/64` unclipped (IWRAM 0x03002b94, `strb`
  directly after `asr #6`), while the shipped builds have a 6-instruction clamp (Vol 1 IWRAM
  0x03002c14). Hits: ep103 134 samples in 60 blocks, ep104 260 samples in 110 blocks, intro none.
  Each is a full-scale wrap (+128 played as -128), an audible click.
- `reference/sonicx2-proto/audio/sx2_adpcm.c` is an independent implementation in the player's
  context layout with a `clip` flag. With `clip` on it equals `adpcm.c` byte for byte on all 38.6 M
  samples; with `clip` off it equals the cart.
- Every block in the three streams starts with a resync header (no continuation headers).
- Resource header words w0/w1/w2 = 0xc1000000 / 0xccccccc1 / 0xcccccc00: codec 3 in w1 bits 6..9;
  the 0xcc bytes are MSVC uninitialised-memory fill from the encoder.
- All other audio code (the Thumb block-header parser, refill, reset, the VBlank scheduler, and the
  ARM loop apart from the clamp) is instruction-identical to Sonic X Vol 1; only seek/resume
  (0x0800688c) differs.
- Ground truth: per-block md5 of the cart's decoded output in
  `verify/hydrogen/sx2p_{intro,ep103,ep104}.audio_md5.csv.gz`. The last block holds only the samples
  the cart decodes: it stops about 4,100 samples before the end of the data when the video ends.

## 5. Player and engine

- UnPredictLuminance/Chrominance, StreamBase::Initialize/SeekToScene/DecompressCodebook differ only
  in assert line numbers and literal offsets. The shipped builds add `GetSceneFrameID` and an unused
  `DecompressCodebook(unsigned long)` argument.
- MoviePlayer::Process: the same 5-state per-VBlank decode. Key handling rewritten, fast-forward
  2 vs 3 frames per tick, a rewind rate limit, no input while paused (shipped). No bitstream impact.
- ROM file slots: each file is followed by `{u32 ptr, u32 size}` with `ptr + size` = slot address.
  The proto has 25 files; Vol 1 has 28 (it adds an opening-theme video of 0x1916f8 bytes and two small
  files). The proto ROM ends exactly at the end of the IWRAM image (0x1e8a158), the same layout as
  the shipped carts minus the 0xFF padding to 32 MiB, so nothing is truncated.
- Toolchain: ARM ADS-style `__scatterload` crt, ARM C/C++ library messages, the C++ "Hydrogen
  Library" engine plus the "MoviePlayer Example" tutorial sources (22 `C:\dev\...` paths; Vol 1,
  SpongeBob 1 and Dora spell it `C:\Dev`). Asserts print through Nintendo AGBPrint
  (0x09FE20F8/0x09FE2FFE); none fire in normal use. Dead debug code: a memory-block dump, an OAM
  dump, "Dumping symbols" (absent in Yu-Gi-Oh! and TMNT), and hyKeys key-script record/playback.
