# Majesco "Hydrogen" GBA Video: player, format and decoder

The 13 GBA Video cartridges that Majesco built on its **Hydrogen Library** with the Inflate-based
codec. This directory has the player documentation, a reimplementation-grade specification of the
container, the compression layer and the VQ video, and per-stream CPU measurements. The C99
reference decoder in `reference/hydrogen/` is verified bit-exact against the cartridges.

No ROM data or decoded content is included; every tool takes your own dump.

## Cartridges

| cart (No-Intro, "Game Boy Advance Video - " omitted) | code | short id | player build | streams |
|---|---|---|---|---|
| All Grown Up! - Volume 1 (USA) | MGUE | agu1 | `04cbcc07fe42` | logo, 2 episodes, intro |
| Sonic X - Volume 1 (USA) | MSHE | sonicx1 | `04cbcc07fe42` | logo, 2, intro |
| Yu-Gi-Oh! - Yugi vs. Joey (USA, Europe) | MYGE | yugioh | `f73d177a8155` | logo, 2, intro |
| SpongeBob SquarePants - Volume 1 (USA) (Rev 1) | MSSE | sponge1 | `92e428ed134d` | logo, 4, intro |
| SpongeBob SquarePants - Volume 2 (USA) (Rev 1) | MS2E | sponge2 | `92e428ed134d` | logo, 4, intro |
| The Fairly OddParents! - Volume 1 (USA) | MFOE | fop1 | `1c25494320ba` | logo, 3, intro |
| The Fairly OddParents! - Volume 2 (USA) (Rev 1) | MF2E | fop2 | `92e428ed134d` | logo, 4, intro |
| Teenage Mutant Ninja Turtles - Things Change (USA, Europe) | MTME | tmnt | `93fe5e2e96b3` | logo, 2, intro |
| Nicktoons Collection - Volume 1 (USA) | MNCE | nick1 | `c1313a3736b4` | logo, 3 (no intro stream) |
| Strawberry Shortcake - Volume 1 (USA) | MSBE | strawb1 | `c3b335b0b286` | logo, 2, intro |
| Codename - Kids Next Door - Volume 1 (USA, Europe) | MKDE | knd1 | `dff48b69f680` | logo, 4, intro |
| Cartoon Network Collection - Volume 1 (USA, Europe) | MCTE | cn1 | `f5f0564f89e3` | logo, 4 (no intro stream) |
| Dora the Explorer - Volume 1 (USA) | MDRE | dora1 | `ff6e0e4279e0` | logo, 2, intro |

62 streams, 402,649 frames (9.4 hours at 11.9455 fps). The sha1s are in [stats.json](stats.json)
and `verify/carts.csv`.

Related carts documented elsewhere:

- **Sonic X - Volume 2 (USA) (Proto)** is an early Hydrogen build (`96a25377cb75`), the common
  ancestor of the shipped builds: [../sonicx2-proto/README.md](../sonicx2-proto/README.md). hydec
  decodes it bit-exactly; its manifests are `verify/hydrogen/sx2p_*`.
- **Jimmy Neutron 1** and the **Yu-Gi-Oh! Noah's Final Threat proto** use the Hydrogen engine and
  container (documented here) with the LZMA codec (codec nibble 7), which is documented and
  verified with the ADS family: [../ads/README.md](../ads/README.md),
  [../majesco/ads-vs-hydrogen.md](../majesco/ads-vs-hydrogen.md).

## Documents

| file | contents |
|---|---|
| [player.md](player.md) | the program: Hydrogen Library classes, boot, state machine and menus, memory map, the VBlank-driven 5-state frame schedule, IRQs/timers/DMA, the 10 builds and how they differ, function map |
| [format.md](format.md) | containers, stream header, chapters, segments (= keyframes = seek points), the DEFLATE variant (bit order, block framing, stored blocks, the player's request bookkeeping and its quirks), audio framing, verification |
| [../majesco/vq.md](../majesco/vq.md) | the VQ layer shared with ADS: codebook layout, unprediction, YCbCr->RGB555 with ordered dither, blit |
| [../majesco/adpcm.md](../majesco/adpcm.md) | the ADPCM audio bitstream shared with ADS |
| [perf.md](perf.md) | CPU, VBlank deadlines, memory, bus, DMA and display use, every stream |
| [prior-art.md](prior-art.md) | element-by-element comparison with quatric's gist and mobipeg |
| [features.md](features.md) | codec feature matrix per player build |
| [stats.json](stats.json) | one record per (cart, stream) |

## Results

| check | result |
|---|---|
| video, whole streams against the cart in mGBA (framebuffer md5 after every blit) | **402,649 / 402,649 frames**, 62/62 streams, all 10 builds |
| audio, whole streams (md5 of every ADPCM call's output, shared `adpcm.c`, `MJ_CTAB_FLAT`) | **570,471 / 570,471 calls** (552.1 M samples) |
| timing | every blit exactly 5 VBlanks after the previous: 0 late or dropped frames |
| seek: every chapter of episode 1 on one cart per build, cold start through the player's SeekToChapter | 60/60 chapters: 1,440/1,440 frames, 2,509/2,509 ADPCM calls |
| Inflate fuzz against the cart's routine in unicorn (stored/fixed/dynamic blocks, random request sizes) | All Grown Up: 1995/1995 agree (+5 the cart rejects itself, table-pool assert); Yu-Gi-Oh! (layout B): 793/793 agree (+7 rejected). Agree = same bytes and per-request frame offsets (32-bit), or, after the stored-block quirk, a cart crash or read past the blob where the C decoder reports an error (66+30 / 4+2 cases) |
| VQ fuzz (unpredict + converter + blit, all 8 modes, all 11 brightness levels) | 600/600 (All Grown Up), 300/300 (Yu-Gi-Oh!) |
| perf cross-check: unicorn GBATEK cost model / instrumented mGBA | blit 0.982-0.989, Inflate 0.985-0.994, ADPCM about 0.99-1.00 |

Manifests: `verify/hydrogen/<short>_<stream>.{frame,audio}_md5.csv.gz` (124 files, hashes only).

### Format in brief

- No directory: containers `{size<<4|n; type 1 video, type 2 audio}`, each followed by a `{ptr,size}`
  file slot that the code reaches through literal pools. Logo, 2-4 episodes, intro (11 of 13 carts).
- Segments (= keyframes = seek points, median 32 frames): a 256-entry codebook and an index stream,
  both in a DEFLATE variant (MSB-first bits from little-endian halfwords, bare 2-bit block type,
  type 3 = end, no NLEN, u32 size prefix, eager bit refill).
- Pure VQ with 4x4/4x3/3x3/4x2 blocks, 1 byte per block, no motion compensation and no transform;
  inter-frame redundancy only through LZ matches. The codebook is YCbCr (4:2:0 over 2x2 quads),
  converted to RGB555 per segment with 4x4 ordered dither and a brightness gain. 240x159 plus a black
  line in the 3-row-block modes. 11.9455 fps exactly; 16384 Hz ADPCM locked to it.

### Builds

The codec code is identical modulo relocation in all 10 builds (IWRAM compared word by word;
StreamBase and SoundPlayer in the decompilation). The differences are state numbering (A2 builds
1..6), input handling, menus/IntroMovie, and the group B IWRAM shift (-0x68/-0x74) after Inflate's
debug symbol dump was removed. See [features.md](features.md) and [player.md](player.md) section 9.

### Perf

CPU busy 31.7 % on average (29-37 % per stream; frame-window maximum 70 %): ADPCM 15.0 %, blit
9.8 %, Inflate 5.0 %, convert 0.3 %. One state per VBlank; the heaviest VBlank is 93.1 % (an Inflate
request), and none of 2.0 M VBlanks overran. Single-buffered mode 3 without tearing.

## Build and verify

You need gcc and Python 3; the oracle and fuzz tools also need `unicorn` and `capstone`.

```sh
make -C reference/hydrogen                       # hydec, hystat, libhydec.so (with reference/majesco)
reference/hydrogen/hydec ROM --list
reference/hydrogen/hydec ROM --stream 1 --count 120 --out frames.bgr555   # raw 240x160 BGR555 frames
python3 tools/hydrogen/hydemux.py wav ROM 1 ep1.wav
GBAV_ROMS=/path/to/roms python3 tools/hydrogen/verify.py           # video, every cart found
GBAV_ROMS=/path/to/roms python3 tools/hydrogen/verify.py --audio   # also every ADPCM call
```

hydec options: `--list`, `--stream IDX/0xOFF`, `--start-frame`, `--count`, `--out FILE or DIR/`,
`--md5`, `--stats`. ROMs are found in `$GBAV_ROMS` (else `roms/`) under their No-Intro names.

| tool | |
|---|---|
| `reference/hydrogen/hyinflate.c` | the DEFLATE variant, a literal transcription of the cartridge's request bookkeeping |
| `reference/hydrogen/hystat` | per-segment / per-frame statistics |
| `tools/hydrogen/hycart.py`, `hydemux.py` | Python container parser; `info`, `segments`, `extract`, `wav` (audio through the shared `adpcm.c`) |
| `tools/hydrogen/hyinflate.py`, `hyvq.py` | slow Python reference models (readable companions to the spec) |
| `tools/hydrogen/emu/capture.py`, `capture_all.py` | ground truth: the cart's own PlayMovie() in mGBA, hashes at every blit and ADPCM return |
| `tools/hydrogen/emu/uc_oracle.py`, `fuzz.py`, `hyenc.py` | unicorn oracle running the cart's IWRAM routines; differential fuzzing with a test-vector encoder |
| `tools/hydrogen/perf/` | instrumented-mGBA cycle accounting and the independent unicorn cost model |
| `tools/hydrogen/ghidra/` | build-comparison scripts (Ghidra project build, decompile dump, function-by-function diff, IWRAM word compare) |
| `tools/hydrogen/mkstats.py` | builds stats.json |

The emulator tools need patched mGBA libretro cores, built locally from the nixpkgs mGBA source:
`tmp/mgba/hydrogen-cb/` (the triage profiling core, `tools/mgba/mgba-prof.patch`, plus the
synchronous PC-hook callback, `tools/hydrogen/emu/mgba-cb-on-triage-prof.patch`; or set
`HY_MGBA_CORE`) and `tmp/mgba/perf/` (the Shrek perf patch, `tools/vx/perf/patch/mgba-perf.patch`,
unchanged; or set `HY_PERF_CORE`). The patches are MPL-2.0, like mGBA.

## Open issues

1. Brightness levels other than the default (level 5, used by all 13 carts) are verified only by the
   unicorn fuzz, not by whole-stream captures.
2. The cart's result for over-subscribed Huffman code sets is implementation-defined; `hyinflate.c`
   rejects them. They never occur in valid streams.
3. The stored-block quirks, the fixed-block overshoot and the 0x740-entry table pool constrain
   encoders ([format.md](format.md) 5.7). Retail streams have no stored blocks, so those paths are
   proven by fuzzing only.
4. The main-menu thumbnail path (1 pixel per block, IWRAM 0x03001a54/0x03000c68) is documented but
   not reimplemented.
5. Ghidra decompiles, captures and perf CSVs were working files of the analysis and are not
   distributed; the scripts that regenerate them from your own dump are in `tools/hydrogen/`.
