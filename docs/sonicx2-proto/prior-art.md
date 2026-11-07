# Prior art and the Sonic X Vol 2 prototype

Sources compared (hints only; every element below was re-derived from the cart's ARM/Thumb code):

- quatric, "The Mobiclip Formats", sec. 8 (GBA Video), gist
  https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860.
- quatric/mobipeg (https://github.com/quatric/mobipeg):
  `doc/gba_video_ads.md` (ADS, Hydrogen, prototypes), `libavcodec/majesco.c` (Hydrogen inflate).
- MultimediaWiki: nothing on Majesco GBA Video beyond the cart list.

No prose or code was copied. Status per element: **agrees** (prior art says it and the cart
confirms it), **corrected** (prior art is wrong or incomplete for this cart), **new** (not in
prior art).

## The cart itself

| Element | Prior art | This work | Status |
|---|---|---|---|
| Identity | mobipeg lists "Sonic X Volume 2" as a Hydrogen prototype, 3 resources, Hydrogen Inflate, maker `01`, blank title/code | Same. 3 movie files (ep103, ep104, intro), each video+audio. Maker 01, no title or game code | agrees |
| "Unknown codec" (our own classifier) | - | False negative: the codec is plain Hydrogen. The classifier's VQ-blit windows came from carts whose 3x3 blit body differs | corrected (internal) |
| Dump completeness | not discussed | Complete: the ROM ends exactly at the end of the IWRAM image, the last item the build appended; size is just not a power of two | new |
| Boot/playback | not discussed | Boots and plays both episodes in mGBA; see player doc | new |

## Container

| Element | Prior art | This work | Status |
|---|---|---|---|
| Resource list `u32 size<<4 | count` + `{type<<28 | bytes}` entries, byte sizes, count in low nibble | mobipeg (Dora) | same | agrees |
| How files are found | mobipeg: scan for `.mmstr`-style tables validated by the audio block sum | Player reads `{ptr,size}` **file slots** patched into ROM after each file (0xf59054, 0x1e470f4, 0x1e53890); slot scan finds exactly the 3 files | new |
| Video header w0/w1 | gist/mobipeg: frame rate derived from audio block math ("nothing in the container states a frame rate") | w1 holds fps (bits 4..9 = 12), VBlanks per frame (bits 0..3 = 5), width, height; w0 holds total frames and chapter count | corrected |
| Chapter table | not described | u16 first scene + font-alphabet name, padded; 6 chapters per episode | new |
| Scene (segment) header, sizes `(w1 & 0x1fff)*4` / `(w1>>13)*4`, mode in low bits, count in bits 6..15 | mobipeg | same; bits 3..5 always 6, unread | agrees (+ bits 3..5 new) |

## Video

| Element | Prior art | This work | Status |
|---|---|---|---|
| Pure VQ, byte indices, 256-entry codebook per segment, intra only, no motion | mobipeg | same; LZ matches into the previous frame's indices are the only temporal redundancy removal | agrees |
| Geometries 4x4/4x3/3x3/4x2, grid `240/w x floor(160/h)` | mobipeg (after its Dora fix) | same; proto uses modes 5 and 7 | agrees |
| Codebook planar across entries, running-sum delta on luma and on Cb->Cr, chroma counts {1,4,1,3,1,2,1,2} | mobipeg | same | agrees |
| Luma order within an entry and chroma sharing | mobipeg: chroma "one per block row" | 3x3: raster, rows 0-1 share chroma 0, row 2 chroma 1. Others: groups of 4 luma in 2x2 sub-block order (ROM maps), one Cb/Cr per group | corrected / new |
| YUV->RGB `R=Y+2V, G=Y-(U>>1)-V, B=Y+2U` through a clamp table | mobipeg (ADS), single `five[]` reduction table | same matrix; Hydrogen proto uses **16 position-dependent tables**: ordered dither with offsets `7 - Bayer4/2` per pixel position in the block | agrees (matrix) / new (dither) |
| Brightness | not described | clamp table = `min(255, coef*x>>8)`; proto has 8 levels 230..409, default 256 (shipped Hydrogen: 11 levels) | new |
| Even modes | mobipeg: one Cb/Cr pair per block | the converter reads them like odd modes; unused | agrees with the Hydrogen family finding ([../hydrogen/format.md](../hydrogen/format.md)) |
| `ncb > 256` | not described | 2-byte indices inflated but blit reads bytes: dead path | new |
| Line 159 in 53-row modes | triage: filled from palette RAM (shipped) | proto never writes it (stays black) | new (proto difference) |
| 5-VBlank state machine, 11.9455 fps | mobipeg mentions 11.9455 fps | full state assignment, no slip in 28173 frames | agrees / new |

## Compression (hyCompressionManager::Inflate)

| Element | Prior art | This work | Status |
|---|---|---|---|
| DEFLATE structure, MSB-first bits from LE halfwords, u32 size prefix, data at +4 | mobipeg `majesco.c` | same | agrees |
| gist: "a block header is a bare 2-bit type with no BFINAL, and the only thing that ends the stream is the uncompressed size"; "both compressors write the same 8-byte prefix" | gist | **Block type 3 is an end-of-stream code** and every blob on the cart ends with EOB + `11`. Prefix is 4 bytes for inflate blobs (the 8-byte prefix is LZMA's) | corrected |
| mobipeg: "fourth block type (state 7) never used, rejected rather than guessed" | mobipeg | state 7 = end: asserts output end reached and nothing left. Never *read* by the player during playback, but present in every blob | corrected |
| Stored block | mobipeg: "stored-block copy" routine located, semantics not given | exact semantics (16-bit length, no NLEN; a byte from the bit buffer if >= 8 bits buffered; odd last byte = high byte of its halfword), confirmed with synthetic streams against the cart routine; never used on this cart | new |
| Refill points | mobipeg: refill when `nbits < n` | per-site thresholds (2/3/7/14/15/n) matter for stored blocks; tabulated | new |
| Window rebasing (64 KiB / 32 KiB, two CpuSet halves) | not described | documented; invisible in output | new |
| Table pool in VRAM 0x06017140 (0x740 B) | not described | documented | new |

## Audio

| Element | Prior art | This work | Status |
|---|---|---|---|
| VLC table `{01 01 01 01 12 12 23 33}`, escape table `0c 0d 08 06`, flat 5-bit context entries in Hydrogen | mobipeg | same | agrees |
| 16458 samples per block, 16384 Hz | mobipeg | same; 12 video frames per block | agrees |
| Output clamp | mobipeg/ADS shared decoder clamps to +-127 | **proto does not clamp**: low 8 bits stored; 134 + 260 audible wraps on this cart | new (proto difference) |
| Header filler 0xcc | not described | MSVC debug-heap fill in the audio resource header: the encoder was a Windows debug build | new |
| A/V drift 0.0625 sample/frame, last ~4100 samples never decoded, seek by self-modifying NOP of the store | not described | documented | new |
