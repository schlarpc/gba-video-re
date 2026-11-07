# ADS: comparison with prior art

Sources:

- **G** = quatric, "The Mobiclip Formats" (gist 3e2f0caa5c22a9a8b24d9cf1ffdfe860), section 8.1
  (ADS) and 8.2 (Hydrogen, Hydrogen/LZMA).
- **M** = quatric/mobipeg: `doc/gba_video_ads.md`, `libavformat/gbavideo.c`,
  `libavcodec/adsgbavideo.c`, `adsgbaaudio.c`, `adslzma.c`. Read for comparison only; no prose or
  code was copied.

Both describe Dragon Ball GT (build `e19d3efb314e`) and Dora (Hydrogen). Everything below was
re-derived from the cartridges and checked by whole-stream emulator captures; the columns say
whether the prior art covered an element, whether it agrees, and what is new or corrected.

| element | prior art | this work |
|---|---|---|
| SFCD archive layout | G, M: magic, `u16 data_offset`, `u16`, `u32 count`, entries `{size, offset, name}` | agrees. The second u16 is `0xE000/0xC800 | count` (not used) |
| `.mmstr` list, resource types 1/2/4/5 | G, M | agrees |
| obfuscated text alphabet | G, M (digits, letters, 0xFF, 0xFE, "a punctuation table") | agrees; punctuation codes observed in titles listed ([format.md](format.md) 3.1) |
| `Settings` text, `BaseBrightness` | not covered | **new**: the bfa01253c3c5 build adds `BaseBrightness * 25.6` to the brightness gain; CN Edition Platinum (F) sets 2 (+51). Without it that cart's video is wrong |
| video header `w0`, chapters, title | M | agrees. M calls `w1` a magic (0x0A03C0C7); **corrected**: it is a format word with two layouts (A: codec/fps/width/height in 4/6/10/10 bits; B 0x280F0307: width/height at bits 12/22), see [format.md](format.md) 4.1 |
| chunk header `w0` (mode, ncb, nfr), `w1` sizes | M (low half "constant per file"), G | agrees; low half = mode | 6<<3 | ncb<<6 |
| LZMA blob prefix `{usize, params}` | G, M: lc=0 lp=0 pb=2, "remainder not decoded" | agrees; **new**: bits 12-16 = dictionary log2 (17), bits 10-11 vary and are ignored; the cartridge core **hard-wires** lc=0, lp=0, pb=2 (oracle-tested) |
| LZMA probability layout, canonical | M | agrees (0x736 + 0x300<<(lc+lp) probs at 0x400) |
| how frames map to LZMA output | M: "blobB is grid_w*grid_h bytes per frame" | agrees; **new**: 128 KiB ring, matches reach the full 131072 bytes, incremental decode with match overshoot and an IRQ deadline stop, position state from the ring offset |
| codebook layout: planar Y then U then V, 2x2-quad luma order, chroma per group | M (corrected in its later sections) | agrees with M's final version and with [`vq.md`](../majesco/vq.md) |
| unprediction (running byte sum over the codebook) | M | agrees (luma and chroma sums restart separately) |
| YCbCr -> RGB555 formula, clamp table, `five[]` table | M | agrees; the dither table and the 11-level gain table are those in [`vq.md`](../majesco/vq.md) |
| mode table, even modes | M: block geometries; "odd modes carry crows per block row" | agrees; even modes never occur (and would decode with misplaced chroma) |
| line 159 of 53-row geometries | not covered | **new**: filled from BG palette entries 0..23 (kept 0) |
| 4x4 mode in ADS | G/M: DBGT uses modes 3 and 7 only | **new**: Nicktoons 2 / SpongeBob 3 use mode 1 (4x4); mode 5 (3x3) is common in ADS too |
| audio framing: header, block table in words | M | agrees; **new**: `hdr_field` (u16 at +12) varies 1..12 and is unused |
| ADPCM sample loop | G, M | agrees with M's corrected loop |
| ADPCM context table: palette VLC, escape | M (verified "64/64 bytes") | agrees |
| ADPCM restart header | M: flag bit + 24-bit level; "f1/h1 survive" | **corrected**: all of f0, f1, h0, h1 are reset; the restart first skips to a word boundary |
| Hydrogen flat 5-bit context table | M | agrees (used by Jimmy and Noah) |
| ADPCM output stages (pairs vs bytes) | not covered | **new** ([`adpcm.md`](../majesco/adpcm.md) 6) |
| block length 0x404A | M | agrees |
| frame rate | M: 12 fps nominal, 59.7275/5 = 11.9455 fps | agrees (the blit runs every 5th VBlank) |
| Noah's Final Threat proto | M: "genuine hybrid revision", Hydrogen syntax + LZMA | agrees; **new**: its intro shows 41 frames with a stale codebook (player too slow for 1-frame segments) |
| Jimmy Neutron | G lists a "Hydrogen/LZMA prototype" only | **new**: the retail Jimmy Neutron 1 is the same hybrid; its boot code adds +51 to the brightness gain after the logo |
| Matrix mapper on Disney 2 | not covered (triage: "0x08800180") | **new**: all stream reads go through a mapper-window ROM reader ([player.md](player.md) 9) |
| player builds and their differences | not covered | **new**: 10 SFCD builds + 2 Hydrogen-engine builds, function-level diff ([player.md](player.md) 11) |
| bit-exactness | M: "decodes clean", visual | **new**: every frame and every audio sample of every stream compared by md5 with the emulator |

Hydrogen-specific prior-art points (inflate, Dora's container) are in [../hydrogen/prior-art.md](../hydrogen/prior-art.md).
