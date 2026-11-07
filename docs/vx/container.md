# VX++ container layer (Shrek GBA Video) -- v2 (complete)

Scope: everything between the cartridge bytes and the two codec inputs (video bitstream,
audio AFrames). Confidence tags: **(code)** read in the player's ARM code, **(data)**
verified over the whole ROM by `tools/vx/container/vxdemux.py walk`, **(emu)** verified by running the
player's own routine under Unicorn, **(prior)** only from prior art.

Tools (all in `tools/vx/container/`):
- `vxdemux.py` -- parser/demuxer/validator/audio decoder front end (`info`, `walk`, `extract`, `wav`).
- `vxaudio.py` -- bit-exact model of the player's audio decoder + 8-bit output stage.
- `emu_audio.py` -- runs IWRAM `0x03006a14` (the player's AFrame decoder) in Unicorn and diffs it against `vxaudio.py`.
- `vxvwalk.py` -- parse-only walker of the video macroblock grammar -> exact per-frame bit boundaries.
- `armdis.py` -- capstone helper with the EWRAM/IWRAM image mappings below.
- `vxcart.py` -- finds the player's RAM images through the loader's program table and lists the VX++ streams in a ROM.
- Every tool takes `--stream OFF|IDX` (the header offset, or an index into the stream list); default = first stream.
- Outputs in `tools/vx/container/out/`: `frame_bits_<H>_<tag>.json` (per stream: nframes+1 frame start bits + per-frame QP field),
  `extract/` (`video.bin`, `audio.bin`, `audio_codebook.bin`, `segments/seg_NNN.vxv`, `segments.csv`,
  `index.csv`, `frames/NNNNN.vxv` + `frames/NNNNN.aud` for all 37837 frames),
  `shrek_audio_16k.wav` (whole movie, 16-bit 16384 Hz), `shrek_first60s_hw8.wav` (exact 8-bit 65536 Hz FIFO stream).

## 1. Where things live

| ROM range | Runtime | What |
|---|---|---|
| 0x008a00..0x00bcc8 | EWRAM 0x02000000..0x020032c8 | movie player "EWRAM image" (C code, strings). `ROM = addr - 0x02000000 + 0x8a00` **(code)** |
| 0x00bcc8..0x013cc8 | IWRAM 0x03000000..0x03008000 | 32 KiB IWRAM image: IRQ dispatcher, container parser, video decoder, audio decoder. `ROM = addr - 0x03000000 + 0xbcc8` **(code)** |
| 0x020200..0x020238 | read into cart window 0x08001000 | VX++ header (0x38 bytes) |
| 0x020238..0x0330fa00 | | video elementary stream (53,409,736 bytes; 476 trailing zero bytes) |
| 0x0330fa00..0x03faf600 | | audio elementary stream (3124-byte codebook + 691,876 AFrames; 464 trailing zero bytes) |
| 0x03faf600..0x03faf800 | | chapter table (one 0x200 sector) |
| 0x03faf800..0x03fb0440 | | seek table, 196 x 16 bytes |
| 0x03fb044c..0x03ff7cf4 | | non-movie assets (menu graphics/palettes, referenced by pointer tables at ROM 0x20000/0x20044 using 0x0Bxxxxxx-style addresses). Not VX++ |
| 0x03ff7cf4..end | | 0xFF fill |

Only **one** VX++ stream exists: the only sector-aligned `VX++` is at 0x20200 (the other hit,
ROM 0x135f8, is the magic literal in the parser's pool). No VXGB/second movie/credits stream.
Each region is 0x200-aligned because the cartridge is read in 512-byte sectors (below).

### Cartridge I/O (why everything is sector-aligned) **(code)**
This is the Matrix Memory 64 MiB GBA Video mapper (player.md sec. 1; mGBA `src/gba/cart/matrix.c`).
The upper 32 MiB is not memory-mapped at all. The player copies sectors into a 4 KiB window inside the
ROM address space through a cart-side DMA engine (`FUN_020003a8`):

```
*(u32*)0x08800184 = src        // 0x08000000 + byte offset in the 64 MB image (0x10000000 base also seen; low bits = offset)
*(u32*)0x08800188 = dst        // window address: 0x08001000 / 0x08001800 / 0x08001c00
*(u32*)0x0880018c = nsectors   // 512-byte units (4 = 2 KiB, 2 = 1 KiB)
*(u32*)0x08800180 = 1          // go
while (*(u16*)0x08000180 & 0x1000) ;   // busy
```
Windows: `0x08001000..0x080017ff` = video ring (2 x 1 KiB halves, refilled 1 KiB at a time
by the bit reader at IWRAM 0x030007a8 whenever its halfword pointer crosses a 0x400 boundary);
`0x08001800..0x08001fff` = audio ring (same scheme, refilled from the main loop using flag
ctx+0xb4). During init/seek the same windows also hold the header, seek table and chapter table.

## 2. Header (base H = ROM 0x20200), 0x38 bytes, u32 LE

Parser: IWRAM `0x03006df4` (ROM 0x12abc) = `play_movie(ctx=0x06013e80, hdr_off, start_frame)`,
called from `main` (EWRAM 0x02000158) with `hdr_off = *(u32*)0x0203fff8` and
`start_frame = *(u32*)0x0203fff4`, both left there by the cart's menu program; return value
goes to `0x0203fffc`. The player context struct lives in VRAM at 0x06013e80.

| off | value | field | used by player? |
|---|---|---|---|
| 0x00 | `VX++` | magic; mismatch -> "File Format Error" hang | yes **(code)** |
| 0x04 | 37837 | frame count; play loop runs until cur_frame == this (ctx+0x34) | yes **(code)** |
| 0x08 | 240 | width (ctx+0x104); patched into YUV->RGB code as ARM immediates; must be encodable or "Unable to init YUV conversion code" | yes **(code)** |
| 0x0c | 160 | height (ctx+0x108); same. Picture is centred in the 240x160 mode-3 screen | yes **(code)** |
| 0x10 | 12483 (0x30c3) | **Timer 3 reload** (TM3 = -v, prescaler F/64, IRQ). One video frame per 3 ticks -> fps = 2^24/(64*3*v) = **7.00003**. Also the time-display scale: secs = (frame*v*3 + 0x20000) >> 18 | yes **(code)** |
| 0x14 | 0 | quantizer field (VXGB revision stores 32 here; VX++ carries QP in the bitstream). Not read | no |
| 0x18 | 16384 | audio sample rate. Not read: rate is hard-wired (TM1 reload 0xf800 @F/64 = 128 AFrames/s x 128 samples) | no |
| 0x1c | 0x032ef800 | audio stream offset from H (ctx+0x94/0x90) | yes **(code)** |
| 0x20 | 0x03f8f400 | chapter table offset from H (ctx+0x38) | yes **(code)** |
| 0x24 | 34865 | last chapter start frame (ctx+0x3c). Nonzero = chapters present; forward chapter-skip is refused at/after this frame | yes **(code)** |
| 0x28 | 0x03f8f600 | seek table offset from H (ctx+0x40) | yes **(code)** |
| 0x2c | 196 | seek entry count incl. sentinel. Not read (search stops on frame compare; sentinel frame = nframes) | no |
| 0x30 | 37834 | last seek-point frame (ctx+0x44): forward-skip limit used when there is no chapter table | yes **(code)** |
| 0x34 | 36083 | forward chapter-skip limit used when there is no chapter table (ctx+0x48). Dead in this ROM (chapters present) | yes **(code)** |
| 0x38 | -- | first video halfword; video bit 0 = H+0x38 bit 15 | yes **(code)** |

Init also: fills the 4 reference frame slots (EWRAM 0x020032c8 + n*0xf000: 0xa000 B luma
pitch 256 + 0x5000 B interleaved chroma) with luma 0x00 and chroma 0x80, clears the 0x2000-byte PCM ring at VRAM
0x06015f40, copies the 3124-byte audio codebook to VRAM 0x06015300, then seeks if
start_frame != 0, pre-decodes up to 4 frames (`FUN_020002c4`), starts timers/DMA and enters the
play loop.

## 3. Chapter table (H+0x20 -> ROM 0x03faf600) **(code)(data)**
`u32 count; u32 start_frame[count]` (read as one 2 KiB window, so <= 511 chapters).
20 chapters: 0, 2070, 4913, 6706, 8476, 11054, 13300, 14793, 16969, 18910, 20697, 23298,
25570, 27540, 29100, 30057, 31448, 32587, 33967, 34865. Every chapter frame is a seek point.
Chapter skip (`FUN_020006c0(ctx, dir, 1)`): i = first chapter with start > cur;
forward -> chap[i]; back -> chap[max(i-2,0)]; then seek to the seek entry containing it.

## 4. Seek table (H+0x28 -> ROM 0x03faf800) = the only frame index **(code)(data)**
196 x `{u32 frame, u32 video_bit, u32 audio_byte, u32 zero}`; last = sentinel `{37837,0,0,0}`.
- `video_bit`: bit offset from H+0x38 (player adds 0x1c0 = 0x38*8 to get a header-relative
  bit, then sector = bit>>12, halfword = (bit&0xfff)>>4, skip = bit&15).
- `audio_byte`: byte offset into the audio stream (from H+hdr[0x1c]); entry 0 = 3124 = codebook size.
- 4th word never read (always 0).
- Each entry is a **keyframe / independent segment**: video decoding restarts there with a QP
  (see 5.2) and the audio AFrame there is the only kind of intra AFrame (all 195 intra AFrames
  are exactly the 195 seek points) **(data)**.
- Search (`FUN_02000400(ctx,&idx,mode,n)`): scans entries through a 256-entry ring (two 2 KiB
  window halves), g = first entry with frame > cur. Result: mode 1 -> g+n; mode 0 -> max(g-2-n, 0).
  Start-frame seek and chapter seek use (0,-1) -> g-1 = the entry containing the frame.
  Right/Left (seek-point step) use (1,0) -> next entry / (0,0) -> the entry before the current one.
  With no chapter table, R/L use n = 9 (jump 10 seek points), limited by hdr+0x34.
- Segment lengths: 3..~800 frames (26,529..8,007,302 bits).

Seek restore (`FUN_02000584`): sets cur_frame = entry.frame; video reader = sector
(bit+0x1c0)>>12 loaded to 0x08001000, accum = (hw[i]<<16 | hw[i+1]) << skip, bits left 16-skip;
audio reader = window 0x08001800 + (audio_byte & 0x1ff) after loading sector-aligned
`audio_byte & ~0x1ff`. Audio state counters (ctx+0x9c..0xb4) are cleared; the reference frames
are NOT reset on seek (the segment's first frame is self-contained).

## 5. Packet layout: there is no per-frame packet

Video and audio are **two separate elementary streams**, not interleaved. Neither has per-frame
sizes, markers, alignment or padding. Frame boundaries in the video exist only implicitly (the
decoder consumes exactly one frame's macroblocks); the seek table provides the only explicit
boundaries. A/V sync is purely clock-based (below) and re-anchored at every seek point.

### 5.1 Video bit reader **(code)** (IWRAM 0x030007a8)
LE halfwords, MSB-first within each halfword (equivalently: byte-swap each halfword, then
ordinary MSB-first). 32-bit accumulator; refill one halfword when count < 0. No byte/word
alignment anywhere, including between frames and at the end of segments.

### 5.2 Per-frame header **(code)** (IWRAM 0x03000520, called once per frame from 0x03006d30)
```
frame():
   q = ue(v)                     // Exp-Golomb, H.264 style
   if q != 0: QP = q; set dequant scale = LevelScale[QP%6][0..2] << (QP/6)   (tables IWRAM 0x03000680/0x030006c8/0x030006f8:
                                  {10,13,16},{11,14,18},{13,16,20},{14,18,23},{16,20,25},{18,23,29}; QP < 48)
   // q == 0 (a single '1' bit) = keep previous QP
   for mby in 0..height/16-1: for mbx in 0..width/16-1: macroblock()   // 0x03001dac, 15x10 = 150 MBs
```
So the "1-bit inter-frame marker" and "per-segment quantiser delta" of prior art are the same
`ue(v)` field: `1` = no change, `0...` = new QP. In this ROM **every segment's first frame codes
QP = 30 and no other frame changes QP** (37642 frames start with the single bit `1`) **(data)**.
There is no frame-type bit; intra/inter is per macroblock. A segment start is a keyframe only in
the sense that the encoder uses no references across it; the decoder does not reset its 4
reference slots on seek.

### 5.2a Macroblock grammar, bit-consumption level **(data)** (`vxvwalk.py`)
Enough to find frame boundaries; pixel reconstruction is the video team's job.
```
mb(w,h): m = ue(v) (< 24); action = GBA_MODE_MAP[m] (map = 1,2,0,4,7,3,11,15,9,5,14,6,12,13,8,16,23,10,22,19,20,17,21,18)
  split L/R or T/B -> mb(halves)    inter, no MV delta -> nothing    inter + MV delta -> se, se
  abs MV + Y/U/V DC offsets -> 5 x se    plane/bilinear intra -> 3 x se    intra16 -> ue luma, ue chroma
  intra4x4 -> per 4x4: 1 bit (use predicted) else 1+3 bits; then ue chroma
  residual variants (+12 in GBA numbering): per 8x8: ue -> 64-entry CBP table (6 bits: 4 luma, U, V);
  per coded 4x4: VLC tokens (12-bit LUT at ROM 0x9ae4 = EWRAM 0x020010e4, 4096 LE u16:
  len[3:0] level[8:4] run[14:9] last[15]; escapes on top-7-bits 0000011: +8 bits & level offset table
  LUT+0x2000, +9 bits & run offset table LUT+0x2080, or +9 bits then raw 1+6+12) until last
```
Mode semantics and the CBP table come from mobipeg `libavcodec/vx.c` (written for the Shrek + Shark
Tale cart, whose player is identical) and match player.md sec. 0; the proof is the seek table: walking every frame from each seek point lands
**exactly** on the next seek point's `video_bit` for all 194 interior segments, and the last segment
ends at bit 427,274,067 = inside the last nonzero halfword (bits up to 427,274,080), followed only by zeros.
Per-frame video sizes: 151..115,302 bits, mean 11,292 bits (1.4 KB/frame, ~79 kbit/s).

### 5.3 Video segments and frames **(data)**
195 segments, segment i = bits [seek[i].video_bit, seek[i+1].video_bit), last one ends at bit
427,274,067 (rest of the region is zero padding up to the 0x200 boundary). Frame f = bits
[frame_start_bits[f], frame_start_bits[f+1]) from `out/frame_bits_*.json` / `index.csv`.

### 5.4 Audio-to-frame mapping **(code)(data)**
Timer 1 (reload 0xf800, F/64, IRQ) = exactly 128 Hz -> ctx+0xa0 += 1 ("AFrames due").
The Timer 3 handler (`FUN_03007ab0`, 21 Hz) shows a frame every 3rd tick and decodes AFrames
until ctx+0xa4 == ctx+0xa0. So AFrame k plays at k/128 s, video frame f at f/7 s, and

**video frame f owns AFrames [floor((128f+1)/7), floor((128(f+1)+1)/7))** -- 18 or 19 per frame.

This formula reproduces the AFrame index of all 195 seek entries and the total
(floor((128*37837+1)/7) = 691,876 = number of AFrames in the stream) **(data)**.

Other movies' encoders were less regular. Some seek points' audio is one AFrame after this
formula (Shrek 2: 50 of 197, combo Shrek: 153 of 180) or one before it (combo Shark Tale: 26
of 171). The player does not compute the formula. It restarts both clocks at the seek point.
`vxdemux.py` therefore maps frames to AFrames from the seek point at or before the frame:
`a_seek + floor((128 (f - f_seek) + 1) / 7)`. For Shrek this gives the same result as the
global formula.

## 6. Audio stream **(code)(emu)(data)**

Codec: Actimagine VX audio (the DS `.vx` LPC/pulse codec) -- NOT ADPCM, not PCM. Mono.
Layout: `codebook[3124]` then back-to-back AFrames until 464 zero bytes of padding.

Codebook (copied to VRAM 0x06015300 by two CpuSets of 0x200 + 0x10d words):
```
+0     s16 lpc_cb[3][64][8]       (0x06015300, +0x400, +0x800)
+3072  s16 scale_mod[8]           (0x06015f00)
+3088  s32 lpc_base[8]            (0x06015f10)  reflection coeffs, Q15
+3120  s32 scale_initial          (0x06015f30)  = 649
```
AFrame (all little-endian u16; fields MSB-first inside each):
```
w1: [15:9] pfo (prev-frame offset; 0x7f = intra, 0x7e = no long-term prediction)
    [8:6]  scale_mod index      [5:0] lpc_idx0
w2: [15:14] pulse start pos (0..3)   [13:12] mode   [11:6] lpc_idx1   [5:0] lpc_idx2
mode 0: 8 words, 42 3-bit pulses (5 per word from bit 13 down, + 2 built from the 8 LSBs), stride 3, value (2v-7)*scale
mode 1/2/3: 5/4/3 words, 8 2-bit pulses per word, stride 3/4/5, value (2v-3)*scale
size = 20/14/12/10 bytes
```
Decode (per AFrame, 128 samples at 16384 Hz), as implemented by the player (IWRAM 0x03006a14 + callees 0x03006260..0x03006c9c):
1. scale = (intra ? scale_initial : scale) * scale_mod[i] >> 13 (32-bit wrap).
2. filter[k] = (intra ? lpc_base : filter)[k] + cb0[i0][k] + cb1[i1][k] + cb2[i2][k].
3. reflection -> direct form (in-place lattice, `x += (y*c)>>15`), influence = -(psi>>1).
4. excitation: intra -> zeros; pfo==0x7e -> zeros; else long-term copy from the previous two
   frames' excitation, window starting 0x7f-pfo, with gain ramp min(8,i+1,128-i)/16; then add pulses.
5. 8-tap all-pole synthesis, `s = (exc<<14 + sum infl[j]*s[n-1-j]) >> 14`; inter frames
   use 4 coefficient sets for samples 0-31/32-63/64-95/96-127: `q0=(prev+q1)>>1`,
   `q1=(prev+cur)>>1`, `q2=(q1+cur)>>1`, `q3=cur` (intra: cur for all four).
6. Output: 4x linear interpolation to 65536 Hz, 8-bit quantisation with error feedback
   (`fb = (e[n-1] + 2 e[n]) * 15/16`), 512 bytes per AFrame into the 8 KiB PCM ring at VRAM
   0x06015f40, played by DMA1 -> FIFO A, Timer 0 @ 65536 Hz, both speakers (SOUNDCNT_H 0x0b04).

Hardware-exact quirks vs. the DS decoder (`libavcodec/vx_audio.c` in mobipeg), all **(code)(emu)**:
- An intra AFrame also zeroes the synthesis history, the noise-shaper state and the interpolator
  (the DS decoder carries samples_prev across intra frames). This makes every seek point an exact
  audio restart: decoding from a seek point with fresh state equals continuous decoding.
- The excitation history is two ping-pong 0x400-byte buffers (VRAM 0x06013680/0x06013a80,
  each = [prev-prev excitation | excitation]); the output samples are written over the
  "prev-prev" half. Consequence: the first AFrame after an intra reads, for its oldest window,
  whatever that half held (older excitation or output samples), not zeros / not frame n-2.
  Moot in this ROM: the AFrame after every intra AFrame has pfo = 0x7e (no long-term prediction).
- No int16 clamp internally; all arithmetic wraps at 32 bits; clamp only at the 8-bit stage.
`vxaudio.py` models all of this; `emu_audio.py` diffs it against the real ARM routine:
see section 8 for results.

## 6a. Other VX++ cartridges **(code)(data)(emu)**

The player program is byte-identical on Shrek, Shark Tale and Shrek 2 (Rev 6) and on
Shrek + Shark Tale (Rev 5). That includes the loader, the program table at 0x8e4, the load
headers, and the EWRAM and IWRAM images at ROM 0x8a00 and 0xbcc8. The streams use the same
container. All header table offsets are relative to H. The differences are:

| cart | H | frames | size | segments | chapters | segment QP |
|---|---|---|---|---|---|---|
| Shark Tale Rev 6 | 0x20200 | 37697 | 240x160 | 191 | 20 (2 of them one frame after a seek point) | 33..37, plus 2 QP changes inside segments |
| Shrek 2 Rev 6 | 0x20200 | 38816 | 240x160 | 197 | 20 | 30 |
| Shrek + Shark Tale #0 (Shrek) | 0x20200 | 34874 | 240x112 | 180 | 20 | 34 |
| Shrek + Shark Tale #1 (Shark Tale) | 0x1eef800 | 34261 | 240x112 | 171 | 20 | 37 |
| Shrek + Shark Tale #2 (Shrek credits) | 0x3dd3600 | 595 | 240x160 | 1 | 1 (frame 0); hdr 0x24/0x30/0x34 = 0 | 32 |
| Shrek + Shark Tale #3 (Shark Tale credits) | 0x3e76a00 | 707 | 240x160 | 1 | 1 (frame 0); hdr 0x24/0x30/0x34 = 0 | 32 |

A 240x112 stream still uses the four fixed 0xF000-byte slots with stride 256. Only rows
0..111 and chroma rows 0..55 are decoded. The player centres the picture at screen row 24.
The menu passes the stream to the player as `0x08000000 + H` in 0x0203FFF8, and the player
stores `0x10000000 + H` in ctx+0x20. On the combo cart, each movie's menu has a "Credits"
item that plays its 240x160 credits stream.

## 7. Prior art (and where it differs)
- quatric, "The Mobiclip Formats", sec. 8.3: https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860
- quatric/mobipeg `doc/gba_video_vxpp.md` (sec. 12, 13, 24), `doc/gba_video_vxgb.md`,
  `libavformat/gbavx.c`, `libavcodec/vx_audio.c`: https://github.com/quatric/mobipeg
- CharlesVanEeckhout/actimagine (Python VX reference): https://github.com/CharlesVanEeckhout/actimagine
- mobipeg already had the header layout, the seek table semantics, the segment structure, the
  AFrame framing and the 128/7 AFrames-per-frame cadence. Its notes were written on the
  "Shrek + Shark Tale" cart (4 streams); its demuxer handles every cart here.
- Header fields are now pinned to code: 0x10 is a Timer-3 reload (mobipeg: "frame rate"),
  0x14/0x18/0x2c are unread by the VX++ player, 0x30 is the last seek-point frame (mobipeg: same
  as 0x24), 0x34 is the forward chapter-skip limit (mobipeg: unknown).
- Their "QP delta per segment + 1-bit marker per frame" is one `ue(v)` per frame (5.2). The two
  agree until a stream changes QP inside a segment; Shark Tale (Rev 6) does, and mobipeg loses
  those two segments.
- Their audio decoder (DS-derived) keeps the synthesis history across intra AFrames; the player
  clears it (section 6). 364 of Shrek's AFrames differ for that reason. See `prior-art.md`.

## 8. Validation status
- `vxdemux.py walk`: seek table monotonic, pad words 0, all chapters on seek points,
  hdr 0x24/0x30 match tables, every segment starts with a nonzero ue(v) QP, all 195 audio seek
  offsets land on AFrame starts, intra AFrames == seek points exactly, frame->AFrame formula
  matches all seek entries and the stream total, zero tails in both streams -> **WALK OK**.
- `vxdemux.py walk --video` (or `tools/vx/oracle/walk_video_all.py`): all 37,837 frames parse; all 194
  interior seek offsets bit-exact; last segment ends in the final nonzero halfword.
- `emu_audio.py` (player's ARM decoder in Unicorn vs `vxaudio.py`, comparing the 512 output bytes
  and consumed length per AFrame): 200,000 AFrames from the start (56 intra boundaries) 0 mismatches;
  8,631 AFrames from seek point 37365 to the end of the stream 0 mismatches; **full stream, all 691,876
  AFrames continuously from the start: 0 mismatches** (output bytes and consumed length).
- Decoded audio is sane: speech/music spectrum (97% of energy < 2 kHz), no clipping, HF energy above
  16 kHz = the noise shaper's pushed quantisation noise.

## 9. Corrections to player.md sec. 5 (audio)
- AFrames are **not** fixed 20 bytes: `0x03006a14` returns `len_table[mode]` from IWRAM 0x03006b8e =
  {20,14,12,10}; 20 is only the bounce-buffer copy size (max AFrame).
- Ping-pong buffers are 0x06013680 and 0x06013a80 (0x400 apart), each [excitation n-2 | excitation n-1/n].
- The noise shaper is second-order-ish: `fb = (e[n-1] + 2 e[n]) - ((e[n-1] + 2 e[n]) >> 4)`.
- Audio codebook copy is 0x200 + 0x10d words = 3124 bytes, so 0x06015f30 (scale_initial) does come from the stream.
