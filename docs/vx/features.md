# VX family: codec feature matrix (VXGB vs VX++, with DS-era Mobiclip for context)

Columns:

- **VXGB**: the Rev 5 player, classifier build `0600eda5896e`, player sha1 (ROM 0x8a00..0x18a00)
  `33a2f3a7...`. Used on *Shrek* (USA) (Rev 5), *Shark Tale* (USA) (Rev 5) and *Shrek 2* (USA) (Rev 5).
  Stream magic `VXGB`.
- **VX++**: the Rev 6 player, build `344d68b1e9aa`, player sha1 `7324ebf3...`. Used on the Rev 6
  *Shrek*, *Shark Tale* and *Shrek 2* carts and on *Shrek + Shark Tale* (USA) (Rev 5). Stream magic `VX++`.
- **DS Mobiclip**: the later Actimagine codec on Nintendo DS (`.mods`), from prior art only
  (quatric's "Mobiclip Formats" gist, FFmpeg `mobiclip.c`). We did not verify it. It is here to
  show where the GBA codecs sit in the family.

Source codes: **code** = read in the cart's ARM code; **bit-exact** = reproduced by our C decoder
on every frame of every stream, checked against mGBA and a unicorn oracle; **prior** = from prior
art only.

## Video

| feature | VXGB (Rev 5) | VX++ (Rev 6 + combo) | DS Mobiclip (prior) |
|---|---|---|---|
| colour space | YUV 4:2:0, 8-bit. Chroma is stored U/V-interleaved. The cart converts to BGR555 with a shift-only matrix (`R = Y + 2V'`, `G = Y - V' - U'/2`, `B = Y + 2U'`), a 2x2 ordered dither on Y and a clamp table read from IWRAM (**code, bit-exact**) | same blitter code. Its literal pool is 12 bytes away, so 1 clamp-table byte differs (vxgb.md 7) | YCgCo (`.mods`); YCbCr on later platforms |
| picture sizes | 240x160 | 240x160; 240x112 on the combo cart (a letterbox in fixed 256-stride slots) | various |
| frame rate | Timer3 reload 12483 -> 7.000 fps on every stream | same | various |
| macroblock / partitions | 16x16 MBs. A recursive binary split tree (H or V) goes down to 2x2. 24-entry `ue(v)` mode table at 16x16, 12 entries below (**bit-exact**) | identical | 16x16 MBs with H/V split tree (prior) |
| prediction modes | 12 modes: MC copy from ref 0/1/2, absolute-MV + DC-offset mode, "bilinear" recursive-bisection intra, whole-block intra (V/H/DC/plane), intra 4x4 (9 directions), plus residual variants at 16x16/16x8/8x16/8x8 | identical | similar mode set (prior) |
| intra/inter decision | per block; no frame type | per block; no frame type | I/P frames (prior) |
| references | 3 (the previous 3 decoded frames, 4 rotating slots) | identical | up to 5 (prior) |
| motion compensation | **integer-pel**, no interpolation and no clamping. Chroma MV = floor(mv/2) | identical | half-pel (prior) |
| MV prediction | median of left/top/top-right, once per MB, shared by all sub-blocks. The MV array keeps the last MC sub-block's MV | identical | median (prior) |
| transform | H.264 4x4 integer IDCT, +32 >> 6, add and clamp. Applied to chroma 4x4 blocks too; no chroma DC transform | identical. VX++ also has a DC-only fast path (same result) | 4x4 / 8x8 integer DCT (prior) |
| scan | H.264 4x4 frame zigzag | identical | custom zigzag (prior) |
| quantiser | **one QP per stream, in header 0x14** (32 for Shrek and Shrek 2, 34 for Shark Tale). There is no bitstream syntax to change it | **per-frame `ue(v)` header**: 0 keeps the QP, otherwise it sets a new QP. In practice the QP is set only at seek points (30 for Shrek and Shrek 2, 33..37 for Shark Tale with 2 mid-segment changes, 32..37 on the combo cart) | per-frame QP delta (prior) |
| dequant | `level * (LevelScale[QP%6][class] << QP/6)`, H.264 v0/v1/v2 classes; scales precomputed once per stream (8 words) | same values; 3 scales, recomputed when a frame sets a new QP | similar (prior) |
| residual: which blocks | 16x16/16x8/8x16/8x8 residual modes; one CBP per 8x8 | identical | |
| residual: CBP | `ue(v)` -> CBP32 (4 luma bits + **one bit for U and V together**) | `ue(v)` -> CBP64 (4 luma + separate U and V bits) | |
| residual: coefficient entropy | **H.264 CAVLC code tables** (coeff_token by nC class, total_zeros, run_before) with a VXGB-specific syntax: interleaved level/run order, explicit sign bit, its own suffix-length rule and escape. Context nC comes from **two persistent grids** (luma, and chroma shared by U/V) (**bit-exact**) | **MPEG-4 intra TCOEF (last, run, level) VLC**, 102 codes plus escape; no context | custom run/level VLCs (prior) |
| entropy, other syntax | Exp-Golomb `ue`/`se` for modes, MVs, CBP. MSB-first 16-bit-word bit reader | identical | |
| frame header | **none**; a frame starts with the first MB's mode | one `ue(v)` QP field | |
| keyframes / seek | seek table (frame, video bit, audio byte) every ~200 frames (~28 s). Decoding from a seek point is exact from a cold start: the grids are fully rewritten every frame. The 4 slots are not cleared on seek | seek table; same cold-start property | I-frames (prior) |
| rate control (observed) | constant QP, so a variable bit rate. Mean 54-79 kbit/s; the 1-s p99 is 3x the mean | constant QP per segment, so a variable bit rate. Mean 77-80 kbit/s at 240x160, 36-38 kbit/s at 240x112 | |
| compression layer | none (no LZ/Huffman outside the VLCs) | none | none |

## Audio

| feature | VXGB | VX++ | DS Mobiclip (prior) |
|---|---|---|---|
| codec | Actimagine LPC/pulse codec with a per-stream trained codebook (3,124 bytes at the start of the audio stream). The ARM routine is byte-identical to VX++'s (**code, bit-exact**) | identical | the same codec family ("SX/codebook FastAudio" in the gist); also IMA ADPCM and others |
| frame | 128 samples at 16,384 Hz, mono. VBR AFrames of 20/14/12/10 bytes (2-bit mode) | identical | 128 samples, 20/14/12/10 bytes (prior) |
| synthesis | 8-tap reflection -> direct-form all-pole filter, long-term prediction from 2 previous excitations, 3- or 2-bit pulses | identical | |
| intra / seek | an intra AFrame at every seek point; on GBA the intra AFrame also resets the synthesis, noise-shaper and interpolator state (unlike the DS decoder) | identical | |
| output | 4x linear upsampling to 65,536 Hz, s8 with error-feedback noise shaping, DirectSound A via DMA1, 128 blocks/s of 512 bytes | identical | |
| rate | 19.5-19.7 kbit/s | 18.7-19.6 kbit/s at 240x160; 15.5 kbit/s on the combo movies, 11.4 kbit/s on its credits streams | |

## Container and player

| feature | VXGB | VX++ |
|---|---|---|
| header | 0x38 bytes; 0x14 = QP | 0x38 bytes; 0x14 unused (0) |
| tables | chapters (20), seek table (one entry per segment) | same |
| A/V mapping at seek points (encoder side) | Shrek Rev 5 `floor((128f+1)/7)` (135 entries) or +1 (60); Shark Tale `floor((128f+4)/7)`; Shrek 2 `floor(128f/7)+1`. The player does not use a formula: it plays both streams from the seek entry | Shrek `floor((128f+1)/7)` (+-1 at some seek points) |
| player code placement | decoder in IWRAM; **C code (player_run, main loop, Timer ISRs) in EWRAM**, so its fetches cost 6 cycles each | everything that runs during playback is in IWRAM |
| decode wrapper | inlined into player_run | separate function 0x03006d30 |
| blit skipping | av_tick skips the blit of a frame that is >0x20 ticks late (same rule in both) | same rule |
| audio window | 2 KiB at 0x08001800 in two 1 KiB halves; a single-slot refill request (ctx+0xB4) serviced by the main loop between decodes | identical design |

## What VXGB -> VX++ changed, in one paragraph

Actimagine replaced the CAVLC residual coding with an MPEG-4-style run/level VLC that needs no
context. This drops the two nC grids, the grid bookkeeping in every mode handler, and the 4-bit
tree walks of coeff_token. They split the CBP's chroma bit into separate U and V bits, and moved
the quantiser from the header into a per-frame `ue(v)`. They also moved the player's C code from
EWRAM into IWRAM. The prediction side (modes, MC, intra, MV prediction, IDCT, blitter) and the
audio codec stayed byte-for-byte the same. Measured effect ([perf.md](perf.md) P2.3): VX++'s residual
entropy decoding costs 3.7 cycles per coded video bit, against 9.5 for VXGB's CAVLC. The VX++
encodes of *Shrek* and *Shrek 2* use 34-41 % more bits than the VXGB encodes, at QP 30 against
32, and still decode with the same mean cost and an 18-24 % lower worst case.
