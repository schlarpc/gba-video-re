# Hydrogen family: codec feature matrix

Columns are the 10 player builds (classifier build id; carts in brackets). Rows marked
"same in all builds" were checked function by function ([player.md](player.md) section 9):
the codec code is identical modulo relocation in every build, so the differences between
columns are in what the *streams* of each build use, and in the player shell.

Builds: A1 = `04cbcc07fe42` (All Grown Up 1, Sonic X 1), `c1313a3736b4` (Nicktoons Coll. 1),
`c3b335b0b286` (Strawberry Shortcake 1); A2 = `92e428ed134d` (SpongeBob 1, SpongeBob 2,
Fairly OddParents 2), `1c25494320ba` (Fairly OddParents 1), `ff6e0e4279e0` (Dora 1);
B = `f73d177a8155` (Yu-Gi-Oh!), `93fe5e2e96b3` (TMNT), `dff48b69f680` (Kids Next Door 1),
`f5f0564f89e3` (CN Collection 1).

## Codec (same in all builds)

| feature | all 10 builds |
|---|---|
| codec type | vector quantisation, 1 byte per block indexing a 256-entry codebook |
| colour space | codebook coded as Y (unsigned) + Cb/Cr (signed), converted once per segment to RGB555 with a fixed integer matrix (R = Y+2Cr, G = Y-Cr-Cb/2, B = Y+2Cb) |
| chroma subsampling | 4-wide modes: one Cb/Cr pair per 2x2 luma quad (4:2:0); 3x3: pair 0 for rows 0-1, pair 1 for row 2 |
| block sizes | 4x4, 4x3, 3x3, 4x2 (3-bit mode per segment; even modes declared but broken) |
| output quantisation | 8 -> 5 bits per channel with a 4x4 ordered dither (16-entry threshold table), brightness gain 0.5..1.5 in 11 steps |
| intra / inter | every frame is a full raster of indices; no skip blocks |
| motion compensation | none |
| transform / residual | none |
| inter-frame redundancy | only through LZ matches in the index stream (window = the whole segment, 32 KiB distance) |
| entropy coding | DEFLATE variant: MSB-first bits from 16-bit LE words, 2-bit block type without BFINAL, type 3 = end, canonical Huffman (dynamic + fixed), RFC length/distance ladders |
| compression layer | separate blobs per segment: codebook (delta-coded planes, one Huffman block) and index stream |
| rate control / quantiser | none in the bitstream; quality is set by codebook size (always 256) and block size per segment |
| keyframe / seek structure | segment = keyframe = seek point (own codebook and index stream); chapters = 6 named segment indices per episode |
| frame rate | 2^24 / (5 x 280896) = 11.9455 fps (5 VBlanks per frame; header field 12) |
| resolution | 240x160 (4x4, 4x2) or 240x159 + black line (4x3, 3x3) |
| display | mode 3, single-buffered, blit at VBlank start, faster than the beam (no tearing) |
| audio codec | Majesco ADPCM: 2-bit codes, 1/2/3-bit VLC, 64-entry context table per 16458-sample block (flat 5-bit coding), two-pole adaptive predictor; 16384 Hz, 8-bit, mono, DirectSound A |
| A/V sync | locked by construction (both clocks are the CPU clock; 1371.5625 samples per frame) |

## Per build: what the streams use, and the player shell

| | `04cbcc07fe42` | `c1313a3736b4` | `c3b335b0b286` | `92e428ed134d` | `1c25494320ba` | `ff6e0e4279e0` | `f73d177a8155` | `93fe5e2e96b3` | `dff48b69f680` | `f5f0564f89e3` |
|---|---|---|---|---|---|---|---|---|---|---|
| carts | AGU1, Sonic X 1 | Nicktoons 1 | Strawberry 1 | SpongeBob 1, 2, FOP 2 | FOP 1 | Dora 1 | Yu-Gi-Oh! | TMNT | KND 1 | CN 1 |
| group | A1 | A1 | A1 | A2 | A2 | A2 | B | B | B | B |
| frames (all streams) | 61,564 | 33,306 | 29,077 | 97,343 | 32,964 | 32,587 | 27,964 | 29,205 | 31,220 | 27,419 |
| mode 1 (4x4) frames | - | 13,020 | 12,382 | 31,529 | - | - | 10,609 | 1,263 | - | - |
| mode 3 (4x3) frames | 17,723 | 17,475 | 11,660 | 44,281 | 16,191 | - | 17,296 | 26,605 | 14,396 | 5,436 |
| mode 5 (3x3) frames | 33,102 | 1,157 | 2,000 | 21,224 | 16,600 | 15,513 | - | 1,278 | 16,192 | 20,862 |
| mode 7 (4x2) frames | 10,739 | 1,654 | 3,035 | 309 | 173 | 17,074 | 59 | 59 | 632 | 1,121 |
| segments | 1,593 | 864 | 665 | 2,181 | 935 | 287 | 713 | 749 | 761 | 565 |
| mean segment (frames) | 38.6 | 38.5 | 43.7 | 44.6 | 35.3 | 113.5 | 39.2 | 39.0 | 41.0 | 48.5 |
| segments with a fixed-Huffman block | 6 | 2 | 1 | 6 | 1 | 1 | 2 | 0 | 4 | 1 |
| stored blocks | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| dispatcher states | 0x13..0x18 | 0x13..0x18 | 0x13..0x18 | 1..6 | 1..6 | 1..6 | 0x13..0x18 | 0x13..0x18 | 0x13..0x18 | 0x13..0x18 |
| IWRAM image | 0x302c | 0x3030 | 0x3028 | 0x3034 | 0x3030 | 0x3028 | 0x2fb8 | 0x2fb8 | 0x2fc0 | 0x2fc0 |
| Inflate debug symbol dump | yes | yes | yes | yes | yes | yes | removed | removed | removed | removed |
| episodes (containers) | 2 | 3 | 2 | 4 | 3 | 2 | 2 | 2 | 4 | 4 |

(Episode counts per cart: All Grown Up 2, Sonic X 1 2, SpongeBob 1/2 4, Fairly OddParents 1 3, 2 4.)
