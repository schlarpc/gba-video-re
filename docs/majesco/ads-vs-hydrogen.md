# ADS and Hydrogen: cross-family notes

The ADS and Hydrogen families share two codec components, the VQ video layer
([vq.md](vq.md), `reference/majesco/vq.c`) and the ADPCM audio decoder ([adpcm.md](adpcm.md),
`reference/majesco/adpcm.c`). The compression layer is not shared: Hydrogen uses a DEFLATE
variant ([../hydrogen/format.md](../hydrogen/format.md)), ADS uses LZMA
([../ads/format.md](../ads/format.md)). This document collects the facts that concern both
families: how the shared code is used by each, and the two carts that mix them.

## 1. The VQ layer in the ADS players

The ADS decoder (`reference/ads/ads.c`) calls `mvq_unpredict` / `mvq_convert` / `mvq_blit`
directly. Default tables: dither `{0,4,1,5,6,2,7,3,1,5,0,4,7,3,6,2}` (the 16 bytes in `main.bin`
before the dims table, the same as in the Hydrogen carts) and brightness gain 256. The dither table
and the 11 brightness gains are read from the ROM by a structural search (the 16 bytes before the
dims table; 11 increasing s32 values with 256 in the middle), not compiled in.

Result: all 96 ADS-codec streams (19 ADS carts plus Jimmy Neutron 1 and the Noah proto) give
621,421 of 621,462 frames equal to the emulator framebuffer after every blit. The other 41 frames
are the Noah proto intro showing a stale codebook: the player falls behind on a burst of 1-frame
segments and its 8-slot codebook ring wraps. Rendering those frames with the codebook of the
segment 8 earlier reproduces them exactly (`tools/ads/explain_stale.py`). Modes 1, 3, 5 and 7 are
all exercised.

### 1.1 ADS-specific VQ differences

There are none in the output. Code-level differences, for comparing the players:

- The ADS players convert a codebook only when the segment changes. The LZMA output goes to a
  0x1800-byte EWRAM slot, `UnPredictLuminance` / `UnPredictChrominance` run there, the slot is
  DMA3-copied (32-bit, 0x600 words) to the working buffer, and the converter writes VRAM
  0x06012C00 at the first blit of the segment.
- The French builds (`bfa01253c3c5`, `e3117652326c`) blit from a codebook pointer kept in the video
  object (always 0x06012C00 in practice) and take the 4x4 row count from the stream header
  (`w1 >> 22` = height, then `/4`). The output is identical.
- Mode 1 (4x4) occurs in ADS (Nicktoons Collection 2 and SpongeBob 3); modes 3, 5 and 7 occur
  elsewhere. Even modes never occur in either family.

### 1.2 Stream header word 1 has two layouts in ADS

- Layout A, e.g. `0x0A03C0C7` (the same as Hydrogen's): bits 0-3 codec (7 = LZMA), 4-9 fps (12),
  10-19 width, 20-29 height. Used by the CN USA, Nicktoons 2, SpongeBob 3 and DBGT builds and by
  Jimmy Neutron / Noah.
- Layout B, e.g. `0x280F0307`: bits 0-3 codec (7), 4-11 = 0x30, 12-21 width (240), 22-31 height
  (160). Used by SRMTHFG, Proud Family, Disney 1/2, Nicktoons 3, the French carts and the TMNT proto.

## 2. Hydrogen engine with the LZMA codec (Jimmy Neutron 1, Noah proto)

These two carts use the Hydrogen engine and its container, with LZMA in place of Inflate. The
container and engine are documented in the Hydrogen docs; the codec in the ADS docs. Everything
below holds for Hydrogen-Inflate and Hydrogen-LZMA alike.

- Movie container (no directory; the player's menu table in IWRAM is filled at run time):
  `u32 (size<<4 | n)`, then n entries `u32 (type<<28 | len)` + `len-4` payload bytes;
  `size` = 4 + sum of (4 + len). Type 1 = video, 2 = audio. `tools/hydrogen/hycart.py`
  (`find_containers()`) and `hy_find_containers()` in `reference/hydrogen/hydec.c` find them by
  scanning aligned words.
- Video entry header: w0 = `2<<28 | total_frames<<8 | nchapters`; w1 = `codec | fps<<4 |
  width<<10 | height<<20`, codec 5 = Inflate (all 13 Hydrogen carts), 7 = LZMA (Jimmy, Noah);
  fps 12 (the player also accepts 15). Then u16 segment count, u16 0, then nchapters records
  `{u16 first segment, NUL-terminated name in the substitution alphabet, padded to even}`,
  padded to 4; then the segments.
- Segment = 8-byte header (`w0` as in [vq.md](vq.md); `w1 = idx_words<<13 | cb_words`) + codebook
  blob (cb_words*4 bytes) + index blob (idx_words*4 bytes). Hydrogen blobs: u32 size + bit stream.
- Each Hydrogen cart has a 59-frame logo stream (4 segments, mode 7, the same size in every cart),
  2..4 episodes, and on most carts one short no-chapter stream (359..733 frames).
- Jimmy Neutron starts playback at brightness level 7 (gain 307), not 5: the clamp table at the
  converter's object+0x300 is `i*307>>8` from a clean boot (title START, menu A, any key on the
  instructions screen). The Noah proto starts at level 5 (identity). Pressing B during Jimmy
  playback drops it to level 6 (gain 281), so the level model is the same as Hydrogen's.
- Their audio uses `MJ_CTAB_FLAT`, like every Hydrogen cart.

## 3. Brightness bias

Two cases are known, both ADS-codec:

- CN Edition Platinum (France), build `bfa01253c3c5`: `BaseBrightness 2` in the `Settings` text of
  menuresources gives a gain bias of trunc(2*10000*256/100000) = 51 for every stream.
- Jimmy Neutron: boot code at 0x08008032 stores 51 to 0x03003738 after the logo
  (`adsdec --gain-bias 51`).

No Hydrogen-Inflate cart is known to carry a `BaseBrightness` setting; if one does, the same
mechanism would apply.

## 4. ADPCM in the two families

- Hydrogen audio uses the flat context-table coding (`MJ_CTAB_FLAT`). The Hydrogen routine is at
  IWRAM 0x03002ad0 in All Grown Up (`(ctx, dst, nsamples)`); the outer framing is the ROM Thumb
  SoundPlayer at 0x08005f78: per block a 64-entry 2-bit context table at ctx+0x3c and a 24-bit
  predictor reset.
- Feeding a Hydrogen type-2 entry: `mj_adpcm_init(..., MJ_CTAB_FLAT)` on the payload at
  `entry + ((16 + 2*n + 3) & ~3)`, n = u16 at entry+14. With the player's call sizes (4096
  prefill, then 1371/1372, split at the 4096-byte ring wrap) it reproduces every ADPCM call of
  every Hydrogen stream (md5 of each call's output against the emulator).
- **Reading past the end of the entry.** On some Hydrogen streams (for example the SpongeBob 2 logo
  stream) the cartridge's last ADPCM call consumes bits past the end of the type-2 entry (1 bit on
  that stream): its 32-bit word reader runs into the next ROM bytes. With the input limited to the
  entry, that call sets `overrun` and its output differs. Passing the ROM bytes that follow (both
  decoders pass 64 more bytes) reproduces the cart exactly.
- **All blocks are restarts.** All 51,793 audio blocks in the ADS-codec streams start with a
  restart header; the continuation branch is never used. The same holds for the Sonic X Vol 2
  proto.
