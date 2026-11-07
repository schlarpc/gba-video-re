# ADS family: codec feature matrix

Columns are player builds (classifier build id). "same" = identical to the first column.
B1 = `bde72f6aba53` (SRMTHFG, Proud), B2 = `17d6ab8a82ba` (Disney 2), B3 = `882a917e99c5` (Disney 1),
B4 = `e0080e16dac3` (CN USA x5), B5 = `bfa01253c3c5` (CN Platinum F, TMNT F, Yu-Gi-Oh F),
B6 = `e3117652326c` (CN Premium F, CN Speciale F), B7 = `69261e8948ae` (Nicktoons 2, SpongeBob 3),
B8 = `f364f46e4bef` (Nicktoons 3), B9 = `e19d3efb314e` (DBGT), B10 = `73b2d7da03af` (TMNT proto),
H1 = `22db83f74bdf` (Jimmy Neutron, Hydrogen engine), H2 = `8831b3ba8f06` (Noah proto, Hydrogen engine).

| feature | B1 | B2 | B3 | B4 | B5 | B6 | B7 | B8 | B9 | B10 | H1 | H2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| container | SFCD + .mmstr | same | same | same | same | same | same | same | same | same | Hydrogen resource tables | same as H1 |
| ROM access | linear | Matrix mapper window (64 MiB cart) | linear | linear | linear | linear | linear | linear | linear | linear | linear | linear |
| stream header w1 | layout B | B | A | A | B | B | A | B | A | B | A | A |
| colour space | YCbCr-like (Y + 2V, Y - U/2 - V, Y + 2U), 8-bit, clamped, gain LUT | same | same | same | same | same | same | same | same | same | same | same |
| output | RGB555, 4x4 ordered dither, mode-3 framebuffer 240x160 | same | same | same | same | same | same | same | same | same | same | same |
| block sizes used (mode) | 4x3 (3), 3x3 (5), 4x2 logo (7) | 3x3, 4x2 (episodes too) | 3x3, 4x2 (episodes too) | 3x3, 4x2 | 3x3, 4x3, 4x2 | 3x3, 4x2 | 4x4 (1), 4x3, 3x3, 4x2 | 3x3, 4x2 | 4x3, 3x3, 4x2 | 3x3, 4x2 | 4x3, 3x3, 4x2 | 3x3, 4x2 |
| chroma | one U/V pair per 2x2 luma quad (4x3: 1x4 strip for row 2; 3x3: 6+3 px) | same | same | same | same | same | same | same | same | same | same | same |
| codebook | 256 entries per segment, byte-delta coded (running sum) | same | same | same | same | same | same | same | same | same | same | same |
| intra / inter | intra VQ only; inter redundancy only through LZ matches across planes of a segment | same | same | same | same | same | same | same | same | same | same | same |
| motion compensation | none | none | none | none | none | none | none | none | none | none | none | none |
| transform | none | none | none | none | none | none | none | none | none | none | none | none |
| entropy / compression | LZMA lc0 lp0 pb2, 128 KiB dictionary, separate streams for codebook and index planes | same | same | same | same | same | same (core without input budget) | same | same | same | same (core as B7) | same as H1 |
| rate control / quantiser | none in the bitstream (VQ codebook per segment, segment length 1..389 frames) | same | same | same | same | same | same | same | same | same | same | same |
| keyframe / seek | every segment (chunk) is a keyframe; chapters point at chunks | same | same | same | same | same | same | same | same | same | same | same |
| frame rate | 59.7275/5 = 11.9455 fps | same | same | same | same | same | same | same | same | same | same | same |
| brightness default | gain 256 (level 5) | same | same | same | 256 + BaseBrightness*25.6 (CN Platinum F: 307) | 256 | 256 | 256 | 256 | 256 | 256 logo, 307 after (bias 51) | 256 |
| audio codec | Majesco ADPCM, ctx table VLC (`MJ_CTAB_VLC`), 16458-sample blocks | same | same | same | same | same | same | same | same | same | ctx table flat (`MJ_CTAB_FLAT`) | same as H1 |
| audio output | 16384 Hz s8, sample pairs to a VRAM ring | same | bytes to RAM ring | bytes | pairs | pairs | bytes | pairs | bytes | pairs | bytes | bytes |
| channels | mono (DirectSound A) | same | same | same | same | same | same | same | same | same | same | same |
| player quirks seen | none | none | none | none | none | none | menu: 4th episode via UP wrap | none | none | none | brightness bias after logo | intro: 41 frames with a stale codebook |
