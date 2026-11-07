# FVMV codec feature matrix

Columns are the two player builds (see [player.md](player.md) section 1). The codec code is
identical in both; the usage figures come from the streams on those carts (C decoder
statistics over all frames).

| Feature | Build A (Vol 1, Vol 2) | Build B (Vol 3, Vol 4) |
|---|---|---|
| Vendor | Actimagine codec and player ("powered by actimagine" boot logo); published by Nintendo | same |
| Container | `FVMV` header (32 bytes) + one record per frame: 16-byte header (size, seek back/forward links, index), video, audio | same |
| Picture | 240 x 160, decoded straight to GBA RGB555 (bit 15 kept as decoder state); no YUV | same |
| Frame rate | 1704 scanlines per frame = 7.99171 fps (header field) | same |
| Colour space / transform | RGB555 pixel domain; no transform, no residual coefficients | same |
| Block structure | 8 x 8 cells, recursive binary split to 1 x 2 / 2 x 1 (15 block sizes) | same |
| Leaf modes | skip (co-located copy), MV copy, MV copy + 32-bit colour offset, flat fill with edge blending, 2-colour 4x4 pattern (4 colours: 2 + 1/3, 2/3 mixes), 8x8 interpolated 2-colour pattern, 2 raw pixels (2 x 1 / 1 x 2) | same |
| Pixel share of leaf modes | skip 49.6 %, MV 40.1 %, fill 8.2 %, MV+offset 1.7 %, pattern 0.5 % | skip 56.4 %, MV 33.5 %, fill 7.9 %, MV+offset 1.6 %, pattern 0.6 % |
| Leaf blocks by size | 8x8 41 %, 8x4 14 %, 8x2 10 %, 4x8 7 %, rest < 6 % each | 8x8 41 %, 8x4 12 %, 8x2 9 %, 4x8 7 %, rest < 6 % each |
| Intra / inter | no frame types; every block chooses. Seek-point frames use only intra modes (keyframes) | same |
| Motion compensation | 1 reference (previous frame), integer-pel, range -8..+7 x -8..+7, one byte per vector (16 x 16 table), no prediction of vectors. Linear addressing: vectors may wrap across the left/right edge (about 3-4 blocks per frame do) | same |
| Sub-pel / bi-prediction / OBMC | none | none |
| In-loop filtering | none as such; flat fills blend into the row above and the column to the left (in the output frame) | same |
| Entropy coding | none: fixed prefix codes read one bit at a time from 32-bit LE words (MSB first); raw u16 colours; raw u8 vector indices; three separate substreams | same |
| Compression layer | none (no LZ / Huffman / arithmetic coder) | none |
| Rate control / quantiser | no quantiser parameter in the stream; quality comes from the encoder's mode choice | same |
| Keyframes / seeking | seek points every 3-225 frames (93-97 per stream); records link to previous/next seek point; FF, rewind and 12-point skip use them | seek points every 3-225 frames (146-162 per stream) |
| Bitrate (video) | 79.7-82.4 kbit/s mean, 229-251 kbit/s p99 over 1 s (see `stats.json`) | 67.4-90.5 kbit/s mean, 244-280 kbit/s p99 over 1 s |
| Audio codec | GSM 06.10-style RPE-LPC: 8 LARs (6,6,5,5,4,4,3,3 bits, GSM decoding tables), 4 x 64-sample sub-blocks, 21 pulses x 3 bit at stride 3, 2-bit grid, 6-bit block max (GSM APCM); no LTP; no LAR interpolation; 32-bit non-saturating lattice + de-emphasis 28180 | same |
| Audio rate | 16,384 Hz mono, 40 bytes / 256 samples (20.5 kbit/s); one extra block every 125 frames to track 7.9917 fps | same |
| Audio output | 4x linear interpolation to 65,536 Hz, 8-bit with (e1 + 2e) * 15/16 noise shaping (as in VX); DirectSound A via DMA1 | same |
| A/V sync | per-frame audio interleave; ring write position resynced by half a ring on under/overrun | same |
| Decoder code | hand-written ARM in IWRAM, about 15 KB video (one unrolled function per block size, aligned and unaligned source paths) + 2.5 KB audio | identical bytes (relocated literals only) |
