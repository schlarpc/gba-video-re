# Hydrogen: comparison with prior art

Sources:

- quatric, "The Mobiclip Formats", section 8.2 (Hydrogen) and the shared parts of 8.1 (ADS),
  https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860 ("gist").
- mobipeg, https://github.com/quatric/mobipeg: `doc/gba_video_ads.md` ("The Hydrogen lineage (Dora
  the Explorer)"), `libavcodec/majesco.c` (Inflate), `libavcodec/adsgbavideo.c` (VQ),
  `libavcodec/adsgbaaudio.c` (audio). mobipeg is LGPL; nothing was copied, it was read for hints
  and every element below was checked against the cartridges' ARM code.
- MultimediaWiki: no Hydrogen page.

Verdict column: **agrees** (prior art is right and complete), **corrected** (prior art is wrong or
incomplete; the correction is verified), **new** (not covered by prior art).

## 1. Container and stream

| element | prior art | verdict |
|---|---|---|
| no directory; containers found by scanning | mobipeg: first "no resource directory", later ".mmstr tables" found by scanning, validated by the audio block table | agrees (scan); mobipeg's validation differs from ours (entry chain = container size) |
| container word = size<<4 \| count, sizes in bytes | mobipeg: "resource count is the low nibble", "size in bytes" | agrees; **new**: the rest of the word is the container size (they call it junk) |
| streams per cart (logo, episodes, intro) | mobipeg lists 4 movies for Dora with sizes | agrees; **new**: identification of logo/intro roles, 62 streams over 13 carts |
| video stream header: frames, chapters, codec nibble, fps, 240x160 | not described for Hydrogen | **new** (codec nibble 5 vs 7 tells Inflate from LZMA in the shared container) |
| chapter records and seek by segment | not described | **new** |
| segment header w0/w1 | gist/mobipeg (ADS): nfr, mode, sizes | agrees; **new**: ncb in bits 6-15, bits 3-5 = 6 |
| frame rate | mobipeg: 12 fps, derived from 16458-sample audio blocks | **corrected**: exactly 11.9455 fps (5 VBlanks per frame, the scheduler); header field fps = 12 is nominal |

## 2. Compression layer

| element | prior art | verdict |
|---|---|---|
| DEFLATE structure, RFC ladders and code-length alphabet | gist, mobipeg | agrees |
| bits MSB-first from 16-bit LE words | gist, mobipeg | agrees |
| u32 size prefix, data at +4 | mobipeg: 4-byte prefix | agrees with mobipeg; **corrects the gist** ("both compressors write the same 8-byte prefix": Hydrogen blobs have a 4-byte prefix) |
| no BFINAL | gist | agrees |
| block type 3 | gist: "the only thing that ends the stream is the uncompressed size"; mobipeg: "a fourth block type... no stream seen so far uses it" | **corrected**: type 3 is the end-of-stream marker and ends every blob (the player asserts the buffer is full there); prior decoders stop on the size and never read it |
| stored blocks | mobipeg: LEN then LEN bytes through the bit reader | **corrected**: no NLEN; first byte from the bit buffer if >= 8 bits are buffered, rest dropped, raw byte copy, odd last byte = high byte of the next word; and the buffered-bit count depends on the decoder's eager refill (format 5.5). Unused by retail streams, verified by fuzzing the cart |
| eager refill (top-up when < 15 bits before every Huffman symbol) | not described | **new** |
| request bookkeeping and its quirks (stored-block request doubling, 1-byte stored re-read, table-pool limit 0x740, overshooting fixed blocks) | not described | **new** (format 5.7) |
| what retail uses | mobipeg: 187 codebook + 187 index blobs of Dora decode | agrees; **new**: all 62 streams: dynamic blocks plus fixed blocks in 24 segments, no stored blocks, codes up to 15 bits, type 3 ends every blob |

## 3. VQ layer (shared with ADS; vq.md)

| element | prior art | verdict |
|---|---|---|
| mode -> block geometry, grid 240/bw | gist table; mobipeg ("grid is not always 60 wide") | agrees |
| chroma pairs per entry {1,4,1,3,1,2,1,2} | mobipeg (from UnPredictChrominance) | agrees for the unpredict step; **new**: the converter ignores it (even modes are read as odd modes, so they are unusable) |
| codebook planar: luma, then Cb, then Cr | gist, mobipeg | agrees |
| luma order inside an entry | gist/mobipeg: raster, one chroma pair per block row | **corrected**: 4-wide modes store luma as 2x2 quads with one chroma pair per quad (4:2:0), the 4x3 third group is the 1x4 bottom row; 3x3 is raster with pair 0 for rows 0-1 and pair 1 for row 2 (mobipeg's 3x3 path reads the next entry's chroma for row 2) |
| unprediction (running sums) | mobipeg | agrees |
| YCbCr -> RGB: R = Y+2Cr, G = Y-Cr-(Cb>>1), B = Y+2Cb | mobipeg | agrees |
| clamp, brightness gains, 5-bit quantisation with a 4x4 ordered dither by block position | not described (mobipeg outputs 8-bit RGB, unquantised) | **new**; needed for bit exactness |
| blit, line 159 from BG palette, single-buffered mode 3 | not described | **new** |
| "the player always decompresses the codebook into a fixed 6144-byte slot" | gist (ADS) | agrees for Hydrogen too (0x1800 buffer, asserted) |

## 4. Audio

| element | prior art | verdict |
|---|---|---|
| same codec as ADS, flat 5-bit context table | mobipeg | agrees (spec in [../majesco/adpcm.md](../majesco/adpcm.md); the shared `reference/majesco/adpcm.c` with `MJ_CTAB_FLAT` reproduces every Hydrogen ADPCM call, see [README.md](README.md)) |
| 16458-sample blocks | mobipeg | agrees; **new**: = 12 video frames at 1371.5625 samples per frame; audio/video lock by construction |
| playback: 16384 Hz, 4096-byte ring, Timer1 restarts DMA1 every 4096 samples | not described | **new** |

## 5. Player and engine

| element | prior art | verdict |
|---|---|---|
| Hydrogen Library, asserts kept | mobipeg (Dora) | agrees; **new**: class inventory, MoviePlayer Example application, state machine, menus, memory map, IRQ/timing ([player.md](player.md)) |
| 10 player builds and their differences | not covered | **new** |
| Jimmy Neutron 1 / Noah proto as Hydrogen-packaged LZMA | mobipeg: Noah is "a genuine hybrid revision" | agrees; **new**: Jimmy Neutron 1 (retail) is the same hybrid; the codec nibble in the stream header distinguishes them |

## 6. Verification depth

Prior art decodes the streams to their declared sizes and to plausible pictures. This work compares
every frame and every audio call of all 62 streams of all 13 carts with the cartridge running in
an emulator, and fuzzes the cartridge's own Inflate, converter and blit in unicorn ([README.md](README.md)).
