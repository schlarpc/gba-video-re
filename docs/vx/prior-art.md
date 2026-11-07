# Prior art, and how this repository compares

This repository was not the first to reverse engineer the GBA Video VX codecs. By the time
this work started (November 2026), quatric had already published a working decoder and
detailed notes for both GBA revisions. This document lists that prior art and compares it
with this repository element by element. It says what the prior art had, where this
repository agrees, where it corrects it, and what is new here. It also reports a direct
test: quatric's decoder, built from source and run on every stream, compared with this
repository's verified output.

Short version:

- **The codec was already reverse engineered, and its decoder was already bit-exact on the
  pixels for most streams.** mobipeg's native decoder reproduces the decoded picture of
  every frame of Shrek (Rev 6), Shrek 2 (Rev 6) and both VXGB movies exactly. On the other
  five streams, two bugs in its frame-level wrapper stop it (section 2). Its prediction,
  residual and transform code is correct on every frame we could compare.
- **What is new here** is mostly outside the codec core: the player program, the display
  stage (dither, 5-bit clamp table, and the carry behaviour), the audio output stage, the
  container fields that the prior art left unexplained, whole-movie verification against
  an emulator and the cartridge's own ARM code, and the hardware profiling. Inside the
  codec, the new findings are smaller: the correct frame-header model (needed for streams
  that change QP inside a segment), the identification of the VX++ coefficient code as the
  MPEG-4 intra TCOEF table, the exact behaviour of the bilinear fill when its corner
  overflows, and the bit reader's behaviour on over-long codes.

## 1. The prior art

| Work | Author | Date | What it covers |
|---|---|---|---|
| [quatric/mobipeg](https://github.com/quatric/mobipeg) (FFmpeg fork). GBA files: `doc/gba_video_vxpp.md`, `doc/gba_video_vxpp_handoff.md`, `doc/gba_video_vxgb.md`, `libavcodec/vx.c`, `libavcodec/gbavxdec.c`, `libavcodec/gba_vx.h`, `libavcodec/vx_audio.c`, `libavformat/gbavx.c`, `tools/gba_video/*.py` | quatric | VX++ notes and decoder 2026-08-06 to 2026-08-08 (commits `3cfc27e`..`c04464b`); VXGB 2026-08-08 (`400c6d1`, `c1fbe10`); hardening 2026-09-22 (`a9832e2`, `136e1f9`). Compared here at `d5bb54f` (2026-11-04) | Full reverse engineering of VX++ and VXGB from the Shrek + Shark Tale (Rev 5) and Shrek (Rev 5) carts. A native FFmpeg demuxer and decoder for both revisions. Python reference tools that check the grammar against the seek tables |
| ["The Mobiclip Formats"](https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860), section 8.3 | quatric | gist first published 2026-07-30, revised to 2026-08-26 | A summary of the mobipeg GBA notes: the two revisions, the decoder images in ROM, CAVLC vs. the proprietary VLC, the framing differences, the H.264 zigzag in VXGB |
| [CharlesVanEeckhout/actimagine](https://github.com/CharlesVanEeckhout/actimagine) | Charles Van Eeckhout | 2024-12 to 2026-05 | Python reference decoder for the DS `.vx` format. mobipeg's shared VX core (and so its VXGB residual path) is derived from it |
| ffmpeg-devel patch "add Actimagine VX video decoder" ([March 2021](https://lists.ffmpeg.org/pipermail/ffmpeg-devel/2021-March/277989.html)) | Gericom (Florian Nouwt) | 2021-03 | The original DS VX video decoder (not merged). The actimagine README credits it |
| FFmpeg `libavcodec/mobiclip.c` | Florian Nouwt, Adib Surani, Paul B Mahol | in FFmpeg since 2020-08 | The MobiClip video decoder. Its coefficient table set 1 and escape tables are the same as the VX++ coefficient code (`residual.md` section 3) |
| [MultimediaWiki: Actimagine Video Codec](https://wiki.multimedia.cx/index.php/Actimagine_Video_Codec) | MultimediaWiki contributors | last edit 2024-07-27 | The DS `.vx` codec family: H.264-like, recursive 16x16 subdivision, three references, the LPC audio codec and its 3124-byte codebook. Names the GBA SDK but documents no GBA format |
| ["A quick glance at another bunch of codecs"](https://codecs.multimedia.cx/2025/08/a-quick-glance-at-another-bunch-of-codecs/) | Kostya Shishkov | 2025-08-23 | Notes that a GBA Video cart's video starts with `VXGB`, like the DS `VXDS`. mobipeg's VXGB notes cite this post as the original public note |
| ["Reverse Engineering GBA Video"](https://abahbob.com/reverse-engineering-gba-video) | Abahbob | 2024-04 | The Majesco "ADS" cartridges (SFCD and MMSTR archives, image codec). It notes that the other cartridges use Actimagine VX, but does not analyse them |
| mGBA `src/gba/cart/matrix.c` | mGBA developers | | Emulation of the 64 MiB Matrix Memory mapper. This repository's emulator ground truth depends on it |

No other public analysis of the GBA VX formats was found.

How this repository used the prior art: the container work started from the mobipeg notes,
and the first version of the parse-only walker (`tools/vx/container/vxvwalk.py`) took the mode
semantics and the CBP table from mobipeg's `vx.c` (`container.md` 5.2a). After that,
every element listed below was re-derived from the cartridge's ARM code and checked
against the ARM code or the emulator. No mobipeg or FFmpeg code is in this repository.

## 2. Test: is mobipeg's decoder bit-exact?

### Method

mobipeg was built from source at `d5bb54f` with a minimal configuration (the `gbavx`
demuxer and the `gba_vx` and `vx_audio` decoders). It was run on every VX stream of the
seven cartridges in this repository's tables. Two comparisons were made against
`reference/vx/decoder/vxdec`, which matches the emulator's frame hashes for every frame of every stream
(README, "Verification status"):

1. **Unmodified mobipeg.** Its output is RGB24 (`yuv_to_rgb24` in `gbavxdec.c`). The `framemd5`
   of each frame was compared with the md5 of vxdec's YUV run through the same formula.
2. **Decoded YUV.** A local, uncommitted diagnostic build of mobipeg writes the visible
   part of each frame slot (luma rows, then the interleaved chroma rows) before the RGB
   conversion. This was compared byte for byte with vxdec's visible picture. That build also
   had the one-line QP fix described below.

As a negative control, vxdec's output shifted by one frame mismatches on 164 of 200 frames
(the rest are identical static frames).

### Results

| Stream | QP | Unmodified mobipeg, frames identical (RGB24) | With the QP fix, frames identical (YUV) |
|---|---|---|---|
| Shrek (Rev 6), VX++ | 30 | **37,837 / 37,837** | 37,837 / 37,837 |
| Shrek 2 (Rev 6), VX++ | 30 | **38,816 / 38,816** | 38,816 / 38,816 |
| Shark Tale (Rev 6), VX++ | 33..37, two changes inside segments | 0 / 37,697; 564 frames not output (decode errors) | 37,133 / 37,133 output; the same 564 frames not output |
| Shrek + Shark Tale (Rev 5) #0 Shrek, 240x112 | 34 | 0 / 34,874 | 34,874 / 34,874 |
| #1 Shark Tale, 240x112 | 37 | 0 / 34,261 | 34,261 / 34,261 |
| #2 Shrek credits | 32 | 0 / 595 | 595 / 595 |
| #3 Shark Tale credits | 32 | 0 / 707 | 707 / 707 |
| Shark Tale (Rev 5), VXGB | 34 (header) | **37,697 / 37,697** | 37,697 / 37,697 |
| Shrek 2 (Rev 5), VXGB | 32 (header) | **38,816 / 38,816** | 38,816 / 38,816 |

The two failures are both in the frame-level wrapper `gbavxdec.c`, not in the codec core:

- **QP above 31 is read wrongly.** The segment QP is read with FFmpeg's
  `get_ue_golomb_31()`, which is only valid for values up to 31. It consumes the right number
  of bits for larger values but returns 31 or 32. Every VX++ stream with QP 32..37 therefore
  decodes every frame with the wrong dequantisation scale. (mobipeg's own notes, section 25,
  found the same limit for the CBP index and fixed it there, but not for the QP.) Replacing
  that one call with `get_ue_golomb()` makes all four combo-cart streams exact.
- **A QP change inside a segment is not supported.** mobipeg models the stream as one QP
  `ue(v)` per segment and a 1-bit "marker" between frames, and skips the marker without
  reading it. The player instead reads one `ue(v)` at the start of every frame, where
  `ue = 0` (the single bit `1`) means "keep the QP" (`video.md` 2.1). The two models
  agree only while the QP never changes inside a segment. Shark Tale (Rev 6) changes it at
  frames 32893 and 34241. There mobipeg desynchronises, reports "macroblock (0,0) failed",
  and drops the rest of the segment: frames 32893-33173 and 34241-34523.

The comparison covers the visible picture only. mobipeg fills its frame memory with 0x80
at each segment start, where the player fills luma with 0x00 and never clears it on a seek
(`video.md` 3). The padding columns therefore differ, but no visible pixel in these
streams depends on them.

**Audio.** For Shrek (Rev 6), mobipeg's 16-bit 16384 Hz output was compared with
`vxdemux.py wav` (the same codec stage, before the GBA output stage). 691,476 of the
691,840 AFrames that mobipeg outputs are identical. The other 364 form 194 short runs (1 to
9 AFrames), and each run starts exactly at an intra AFrame (a seek point). The DS-derived
decoder keeps the synthesis history across an intra AFrame. The player clears it
(`container.md` 6), and the player's ARM routine matches `vxaudio.py` on all 691,876
AFrames. mobipeg also stops 36 AFrames before the end of the stream with a decode error.
mobipeg's notes say that its decoder flush clears all history, so mobipeg's own seeks do
match the cartridge. It does not model the GBA output stage (4x interpolation to 65536 Hz
and 8-bit noise-shaped quantisation).

## 3. Element-by-element comparison

"Prior art" means mobipeg (`vxpp.md` = `doc/gba_video_vxpp.md`, `vxgb.md` =
`doc/gba_video_vxgb.md`) and the gist unless another source is named.

| Element | Prior art | Agrees? | Where this repository corrects it | New here |
|---|---|---|---|---|
| Decoder location in ROM (EWRAM/IWRAM images, codebook at ROM 0x9ae4) | Yes: vxpp.md 15, vxgb.md, gist 8.3 | Yes | | The loader, its program table at ROM 0x8e4 and the load-header format that place the images (`player.md` 1) |
| Container header fields | `gbavx.c` header comment, vxpp.md 12/24 | Offsets, frame count, size, audio/chapter/seek offsets: yes | 0x10 is the Timer 3 reload, not a frame rate (prior art reached 7 fps from the audio cadence instead). 0x30 is the last seek-point frame, not a copy of 0x24. 0x14 is unread in VX++ and is the stream QP in VXGB (prior art has the VXGB meaning) | Which fields the player reads; 0x34 = forward chapter-skip limit without a chapter table; 0x2c and 0x18 unread; the "Unable to init YUV conversion code" check on width/height |
| Chapter table | Read as chapters by `gbavx.c` | Yes | | The player's chapter-skip rules and limits (`container.md` 3, `player.md` 8) |
| Seek table | `{frame, video_bit, audio_byte, 0}`, used as the grammar oracle (vxpp.md 12-13) | Yes | | The player's search and restore procedure, the reader state after a seek (`container.md` 4) |
| Segments and the per-frame QP | One `ue(v)` QP per segment plus a 1-bit marker per frame (vxpp.md 13, `gbavxdec.c`) | Same bits whenever QP is constant inside a segment | One `ue(v)` per frame; `1` = keep. Proven by Shark Tale (Rev 6), which mobipeg cannot decode (section 2) | |
| A/V mapping | 128/7 AFrames per video frame; sample index built from the seek table (vxpp.md 24) | Yes | | The exact per-frame AFrame formula from the two timers, and each movie's encoder offset from it (`container.md` 5.4, `vxgb.md` 8) |
| Bit reader | LE halfwords read MSB-first (vxpp.md 7) | Yes | | A register-exact model: one refill per primitive, so codes longer than the buffer are corrupted in a defined way; the intra-4x4 reader's late refill (`video.md` 1, 7.3). Only matters for invalid streams |
| Frame header and QP scales | H.264 LevelScale table and `<< QP/6` (vxpp.md 14) | Yes | | QP range accepted by the player (1..47) and the zero scales before the first QP |
| Partition tree and mode grammar | Complete (vxpp.md 11, 18; 16 dispatchers, `+12` = residual) | Yes | | Which entries of the small tables are null or no-ops (`video.md` 5) |
| Inter modes 0/3/8-11 | Full-pel, three references, ring of four slots (vxpp.md 18, 21) | Yes | | Out-of-picture and out-of-slot reads reproduced against the full EWRAM image |
| Mode 5 (absolute MV + DC offsets) | Yes, including "does not store its MV" (vxpp.md 18, 21) | Yes | | |
| MV prediction | Median of left, above, above-right; arithmetic packing with the y borrow (vxpp.md 19, 21) | Yes | | Stated as rules: one predictor per MB shared by all sub-blocks, the stored MV is the last MC sub-block's, no top-left fallback. mobipeg's code already behaves this way; its notes do not state it |
| Chroma MV | `bic #1` = floor(mv/2) (vxpp.md 18) | Yes | | |
| Intra 16x16, chroma, 4x4 | Complete: H.264 modes and numbering, DC edge rounding, availability from the block offset, no neighbour substitution, mode context with border 9 (vxpp.md 16-17, 23) | Yes | | |
| Bilinear fill (modes 4 and plane) | Recursive bisection with the parity rule for the centre (vxpp.md 16); `strb` truncation and "corner in a register" (handoff, vxpp.md 20) | Yes on in-range data | The native C decoder re-reads the stored byte everywhere, so it differs from the ARM when a corner overflows. No stream does this | Per-size operation lists generated from the ARM by symbolic execution, giving the exact register-vs-memory operand for every average (`fill_tables.h`), fuzzed against the ARM with overflow |
| VX++ residual: CBP, VLC, escapes | Complete: CBP64 permutation, 12-bit LUT format, all three escapes, value/run offset tables (vxpp.md 2, 12, 19) | Yes | | Identification of the code: it is the MPEG-4 Part 2 intra TCOEF table (102 codes) with the MPEG-4 three-mode escape, minus the marker bits in mode 3, and equal to FFmpeg `mobiclip.c` table 1 (`residual.md` 3). Overflow behaviour of positions >= 16 |
| Scan, dequant, IDCT | H.264 zigzag, scale classes, H.264 4x4 IDCT with +32 and >>6, the DC-only path (vxpp.md 14, 19) | Yes | | |
| VXGB residual | CAVLC with H.264 tables, nC = rounded mean, shared chroma nC, CBP32, H.264 zigzag (vxgb.md, `vx.c`, inherited from the DS VX decoder) | Yes | | A written description of each difference from H.264 CAVLC (interleaved order, level code, suffix-length rule, FLC for nC >= 8); the grid layout and when the player zeroes it, and why its persistence is unobservable (`vxgb.md` 4-5) |
| VXGB framing | No frame header; QP from header 0x14 (vxgb.md) | Yes | | |
| Audio codec | DS VX LPC codec, AFrame grammar, 3124-byte codebook (`vx_audio.c`, vxpp.md 24) | Yes for the codec | An intra AFrame clears the synthesis, noise-shaper and interpolator state on the GBA; mobipeg keeps it (section 2: 364 AFrames differ). The gist (5.4) says the gain is never reset on intra frames; the GBA player does reset it, as does mobipeg's code | The GBA output stage (4x linear interpolation, error-feedback 8-bit quantisation, 512 bytes per AFrame, DMA/FIFO set-up), the ping-pong buffer quirk, verification of every AFrame against the player's ARM routine (`container.md` 6) |
| YUV to RGB | `R = Y + 2V'`, `G = Y - V' - U'/2`, `B = Y + 2U'`, clipped to 8 bits (`gbavxdec.c`) | Same matrix | The cartridge does not output 8-bit RGB: it indexes a 5-bit clamp table | 2x2 ordered dither on luma; the clamp table at 0x030001F0 and its overrun into code bytes; the field carry from word addition; brightness patching (`video.md` 8, `player.md` 4) |
| Player program | Not covered, beyond the ROM-to-RAM mapping and the decoder context layout (vxpp.md 19, 21) | | | Boot loader and programs, the Matrix mapper registers as the player uses them, memory map, main loop, timers and IRQs, A/V pacing, input, OSD, self-modifying blitter, state struct (`player.md`) |
| Multiple streams, 240x112 | All four combo-cart streams parsed; letterboxing noted (vxpp.md 13) | Yes | | How the player centres the picture (height patched into the blitter, row 24) and the per-cart differences (README, "Other cartridges") |
| Which carts use which revision | Shrek (Rev 5) = VXGB, Rev 6 = VX++ (gist 8.3, vxgb.md) | Not checked: no Shrek (Rev 5) dump was available here | | Shark Tale and Shrek 2 (Rev 5) are VXGB; the Rev 6 releases and the combo cart are VX++; the player program is identical across them |
| Verification | Grammar checked against the seek tables on four combo streams (353 segments) and the Shrek (Rev 5) VXGB stream (195 segments). Pixels: nine VX++ frames and one VXGB frame against mGBA; native C against the Python model over 330 (VX++) and 160 (VXGB) frames | | | Every frame of nine streams against the emulator (decoded slot and screen); every seek point decoded cold; every frame of Shrek and of both VXGB movies against the cartridge's own decoder run in unicorn; differential fuzzing against the ARM; every audio AFrame of every stream against the ARM routine |
| Performance | Not covered | | | Cycle-level profile of the whole movie, cross-checked by an independent cost model (`perf.md`) |

## 4. Corrections to the prior art, in one place

These are the points where this repository's evidence contradicts a published statement.
Each item gives the evidence.

1. **Frame header.** "One QP per segment plus a 1-bit inter-frame marker" is a special case of
   one `ue(v)` per frame. Evidence: the code at 0x03000520 (`video.md` 2.1), and Shark Tale
   (Rev 6), where mobipeg desynchronises at the two in-segment QP changes and vxdec matches
   the emulator on all 37,697 frames.
2. **QP read.** mobipeg's native decoder reads QPs above 31 wrongly (section 2). Its notes
   report the combo-cart stream as hardware-exact. Its Python tools did match the nine
   hardware frames it captured, but the native decoder gets every frame of the four combo
   streams wrong.
3. **Audio across intra AFrames.** The player clears the synthesis history at an intra
   AFrame; the DS-derived decoder does not. Evidence: the ARM routine in unicorn, and the
   364 differing AFrames in section 2.
4. **Header fields.** 0x10 is a timer reload (`player.md` 5); 0x30 is not a copy of 0x24 (in
   Shrek 0x24 = 34865 and 0x30 = 37834); 0x34 is a chapter-skip limit.
5. **Display.** The cartridge's RGB is 5-bit with a 2x2 dither and a clamp table that runs into
   code, not 8-bit clipped RGB.

## 5. Reproducing the comparison

```sh
git clone https://github.com/quatric/mobipeg && cd mobipeg && git checkout d5bb54f
mkdir ../build && cd ../build
../mobipeg/configure --disable-everything --disable-x86asm --disable-autodetect \
  --enable-decoder=gba_vx,vx_audio,rawvideo --enable-demuxer=gbavx \
  --enable-muxer=framemd5,rawvideo,null,wav --enable-encoder=rawvideo,pcm_s16le \
  --enable-protocol=file,pipe --enable-filter=format,null,aformat,anull,scale,aresample
make -j ffmpeg
./ffmpeg -resource 0 -i ROM.gba -map 0:v -c:v rawvideo -f framemd5 mobipeg.md5
reference/vx/decoder/vxdec ROM.gba --stream 0 --count 999999 --y4m ours.y4m
```

Then convert each y4m frame with the formula in mobipeg's `yuv_to_rgb24` (chroma sample
`x & ~1`, `y >> 1`; U and V centred at 128; `>>` arithmetic; clip to 0..255) and compare its
md5 with the `framemd5` line of the same frame (match on pts, since mobipeg drops frames
after an error). `-resource N` selects the Nth stream in ROM order, the same order as
`vxdec --list`.

## 6. Family summary, element by element (all VX carts)

Sources:
- **[G]** quatric, "The Mobiclip Formats" gist, section 8 (VX / VX++ / VXGB):
  https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860
- **[M]** quatric/mobipeg: `doc/gba_video_vxpp.md`, `doc/gba_video_vxgb.md`, `libavcodec/vx.c`,
  `libavcodec/vx_audio.c`, `libavformat/gbavx.c`: https://github.com/quatric/mobipeg
- **[A]** CharlesVanEeckhout/actimagine (Python VX reference):
  https://github.com/CharlesVanEeckhout/actimagine
- **[F]** FFmpeg `mobiclip.c` (DS/Wii Mobiclip), for the family resemblance only.
- **[mGBA]** "Dumping the Undumped", mgba.io 2015-10-20, and endrift/duplo: the Matrix mapper registers.

We used these only as hints. Every element below was derived from, or checked against, the
cart's ARM code: by the C decoder being bit-exact against mGBA on all 10 streams, by the unicorn
oracles, and by the fuzzers. The detailed corrections are in
`container.md` 7, `video.md` 10, `residual.md` "Prior art" and `vxgb.md`. This table is the
family-level summary.

Verdicts: **agrees**: prior art matches the ARM; **corrected**: prior art was wrong or
incomplete for these carts; **new**: not in the prior art we found.

| element | prior art | verdict |
|---|---|---|
| Which carts are VXGB | [M] documents VXGB for *Shrek* (USA) (Rev 5) only | **extended**. All three Rev 5 single-movie carts (*Shrek*, *Shark Tale*, *Shrek 2*) carry the identical VXGB player. The Rev 5 *Shrek + Shark Tale* cart is VX++ |
| Matrix mapper registers (0x08800180..0x0880018C, busy bit 12 of 0x08000180, 512-B pages) | [mGBA], duplo | **agrees**. The copy latency is undocumented anywhere; see [perf.md](perf.md) P2.5 |
| Header layout, 0x38 bytes | [G][M] | **agrees**. Field roles pinned to code: 0x10 is the Timer3 reload (not a "frame rate"); 0x14/0x18/0x2c are unread in VX++ |
| Header 0x14 = QP in VXGB | [M] "active quantizer (32)" | **agrees** (32 on *Shrek* and *Shrek 2*, 34 on *Shark Tale*) |
| VX++ per-frame QP | [G][M] "per-segment quantiser delta + 1-bit inter-frame marker" | **corrected**: one `ue(v)` per frame, 0 = keep (`container.md` 5.2) |
| VXGB frame header | [M] "segment begins directly with its first macroblock" | **agrees**, and it holds for every frame, not only segment starts |
| Partition tree, 24/12-entry mode tables, mode set | [G][M][A] | **agrees** in structure. **Corrected** details: one MV predictor per MB shared by all sub-blocks; the MV array stores the last MC sub-block's MV; intra 4x4 mode context is restricted to the current block; DC availability is by picture position only (`video.md` 10) |
| Motion compensation precision | [G] half-pel (the DS codecs) | **corrected** for GBA: integer-pel only; chroma MV = floor(mv/2) |
| Bilinear (mode 4) intra fill | [G][F] Mobiclip-style recursive fill | **agrees** in geometry. **New**: overflowed corners follow a code-layout-dependent mix of stored bytes and full-precision registers. It is reproduced from the ARM by symbolic execution (`fill_tables.h`) |
| VX++ coefficient VLC | [G] "proprietary codebook" | **identified**: it is the MPEG-4 intra TCOEF (last, run, level) table, with its escape (`residual.md`) |
| VXGB residual = H.264 CAVLC | [M] CAVLC with nC = rounded mean of left/top | **agrees** on the code tables and the nC rule. **Corrected/new**: the syntax is not H.264's (total_zeros right after coeff_token, level and run interleaved, explicit sign bit, a different suffix-length rule and escape); nC >= 8 uses a 6-bit FLC; chroma shares one nC and one grid entry for U and V; the grids persist with border reads 0 (`vxgb.md` 4-5) |
| VXGB CBP | [M] 32-entry table at IWRAM 0x03005e90, 4 luma + paired chroma bit | **agrees** |
| Zigzag, dequant, IDCT | [G][M] H.264-style | **agrees**. **New**: dequant uses 32-bit wrap, and the VX++ DC-only path gives the same result as the full path |
| Colour conversion | [M] R=Y+2Cr, G=Y-Cr-Cb/2, B=Y+2Cb | **agrees**. **New**: 2x2 ordered dither; the clamp table runs into code bytes, whose values carry between colour fields (the blitter adds fields, it does not OR them; seen on *Shark Tale*) |
| Audio codec | [G] lists IMA ADPCM for VX; [M] VX audio = LPC/pulse codec with a 3,124-byte trained codebook, VBR 20/14/12/10-byte AFrames | [M] **agrees**; the IMA ADPCM attribution in [G] is **corrected** for the GBA carts. **New**: on the GBA an intra AFrame also resets the synthesis, noise-shaper and interpolator state, so every seek point is an exact audio restart (the DS decoder carries state across) |
| AFrame size | [M] 20/14/12/10 (the reference repo's own first `player.md` draft said a fixed 20) | **agrees** with [M]; 20 is only the bounce-buffer size |
| A/V mapping at seek points | no per-movie offset in [G]/[M] beyond the 128/7 AFrames-per-frame rate | **new**: the encoder's alignment differs per movie (`container.md` 5.4, `vxgb.md` 8); the player has no formula and restarts both streams at the seek entry |
| Player timing: blit skipping, audio-window refill protocol, the single-slot request and its failure mode | none | **new** ([player.md](player.md) 11.3-11.4, [perf.md](perf.md) part 2) |
| CPU / bus / mapper load per stream | none | **new** ([perf.md](perf.md) part 2) |
