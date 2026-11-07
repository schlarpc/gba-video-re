# Codec feature matrix: Sonic X Vol 2 prototype

One player build on this cart (`96a25377cb75`, 17 March 2004). The second column is the shipped
Hydrogen build of Sonic X Vol 1 (`04cbcc07fe42`) for reference only; [../hydrogen/features.md](../hydrogen/features.md)
covers all shipped builds. "same" = same algorithm and same decoded result.

| Feature | Proto `96a25377cb75` | Shipped Hydrogen (Sonic X Vol 1) |
|---|---|---|
| Container | resource list `u32 size<<4 | n`, entries `type<<28 | bytes`; type 1 video, 2 audio; files reached via `{ptr,size}` slots patched into ROM | same layout |
| Streams on cart | 3: ep103 (13828 frames), ep104 (14286), intro logo (59) | 4: 2 episodes, intro logo, opening-theme video |
| Resolution / display | 240x160, GBA mode 3 bitmap, single buffer, VRAM 0x06000000 | same |
| Frame rate | 11.9455 fps (5 VBlanks per frame; header says 12) | same |
| Colour space | YCbCr-like codebook (`R=Y+2V, G=Y-(U>>1)-V, B=Y+2U`), RGB555 output | same |
| Chroma subsampling | per codebook entry: 3x3 -> 2 Cb/Cr (rows 0-1, row 2); 4x2 -> 2x2 groups | same |
| Dithering | 4x4 ordered (Bayer/2) per pixel position inside the block, 16 LUTs | same |
| Brightness control | 8 levels, `coef` 230..409, default 256 | 11 levels 128..384, default 256 |
| Block sizes | 4x4, 4x3, 3x3, 4x2 (mode bits 0..2); used: **3x3 (ep103)**, **4x2 (ep104, intro)** | same set; Vol 1 uses modes 3 and 5 |
| Rows not covered by the grid (53-row modes) | line 159 never written (keeps stale VRAM; black in practice) | filled with 48 B of BG palette RAM repeated (extra blit code) |
| Codebook | 256 entries per scene, planar Y / Cb / Cr, running-sum delta (mod 256) over each plane group | same |
| Index width | 8 bit (a 16-bit path for >256 entries exists in the scene loader but not in the blit) | same |
| Intra / inter | intra VQ only; no motion compensation; temporal redundancy removed only by LZ matches into the previous frame's index raster | same |
| Motion compensation / precision | none | none |
| Transform | none | none |
| Entropy coding / compression layer | Hydrogen Inflate: DEFLATE re-packed MSB-first in LE halfwords, u32 size prefix, block types 0 stored / 1 fixed / 2 dynamic / 3 end; 32 KiB window | same |
| Compression scope | codebook: one blob per scene; indices: one blob per scene decoded incrementally per frame | same |
| Rate control / quantiser | none in the bitstream: quality is set by codebook size (256) and block size per scene; bits 3..5 of the scene header are constant 6 | same |
| Keyframe / seek structure | every scene (segment) is a random-access point (own codebook, LZ window restart); chapters = first scene of a chapter; ep103 321 scenes, ep104 344 | same |
| Audio codec | Hydrogen VLC-ADPCM (codec 3): 2-bit symbols via 1-3 bit VLC, 64-entry context table sent per block (5 bits/entry), two-pole one-zero adaptive predictor, log step | same |
| Audio output | 16384 Hz, signed 8-bit mono, DMA1 -> FIFO A, 4 KiB ring | same |
| Audio clipping | **none: low 8 bits stored, values wrap** (134 / 260 audible wraps in ep103 / ep104) | clamp to [-128, 127] |
| Audio block / seek | 16458 samples = 12 video frames; every block is a resync point | same |
| A/V sync | audio clocked by the DAC (1371.5625 samples per frame) vs 1371.5 in the stream: ~53 ms drift per episode, no resync within a chapter | same |
