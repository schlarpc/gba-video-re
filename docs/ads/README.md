# ADS family (Majesco, LZMA lineage)

ADS is the later of Majesco's two GBA Video engines. Its cartridges carry an "SFCD" archive
whose `main.bin` member is the player and whose `.mmstr` members hold the movies. The video is
pure vector quantisation (the Majesco VQ layer shared with the Hydrogen family, see
[../majesco/vq.md](../majesco/vq.md)) compressed with stock LZMA; the audio is the Majesco ADPCM
codec ([../majesco/adpcm.md](../majesco/adpcm.md)) at 16384 Hz. Video runs at 11.9455 fps
(one frame every 5 VBlanks), 240x160.

## Coverage

- **19 ADS carts** (SFCD archive) in 10 player builds, and **2 hybrids**: *The Adventures of
  Jimmy Neutron Boy Genius - Volume 1* and the *Yu-Gi-Oh! - Noah's Final Threat* prototype use
  the Hydrogen engine and container ([../hydrogen/](../hydrogen/README.md)) with the ADS codec
  (codec nibble 7, LZMA). 12 player builds in total; [player.md](player.md) 0 lists which cart
  has which, [features.md](features.md) compares them.
- **96 streams**, 621,462 frames, 14.45 hours: episodes, the Majesco logo, and the intro
  "jingles" of 8 carts.
- *Cartoon Network Collection - Special Edition (USA) (Rev 5)* was not available to this project
  and is unverified; its player build is unknown.

## Results

| check | result |
|---|---|
| video, whole streams vs the cart in mGBA (md5 of the framebuffer after every blit) | **621,421 / 621,462 frames**. The other 41 are all in the Noah proto intro, where the cart itself falls behind and shows a stale codebook from 8 segments earlier; `tools/ads/explain_stale.py` reproduces those 41 frames exactly |
| audio, whole streams (md5 of every 16458-sample block) | **51,791 / 51,793 blocks**; the other 2 are never decoded by the cart |
| seeking | 921 chunk starts decode the same as a continuous decode |
| LZMA in unicorn (the cart's own routine) | real, synthetic and corrupted streams: same output and same bytes consumed as `reference/ads/lzma_dec.c` |
| ADPCM in unicorn | matches `reference/majesco/adpcm.c` |
| perf | CPU busy mean 49.9 %, worst frame window 98.7 %; LZMA 19-27 %, audio about 15 %, blit 10-12 %; no dropped frames; 16 late windows in 12 streams, all on the A-layout builds ([perf.md](perf.md)) |

Main findings (details and comparison with quatric's work in [prior-art.md](prior-art.md)):

- The "two audio variants" of the prior art are one decoder with two output paths (sample
  pairs to VRAM or bytes to RAM). The real difference is between engines: ADS codes the
  per-block context table with a 7-entry palette code, the Hydrogen engine with flat 5-bit
  entries. Every audio block starts with a full reset of the decoder state.
- LZMA is stock, with one hard-wired parameter set (lc 0, lp 0, pb 2) and a 128 KiB dictionary,
  decoded into a 128 KiB ring that the blit reads in place. A decode call can overshoot the
  requested length, or stop early on an IRQ deadline flag.
- The second video header word is a format word with two layouts (codec, fps, width, height),
  not a magic number.
- Brightness differs per cart: *CN Edition Platinum (France)* sets `BaseBrightness 2` in its
  `Settings` text (+51 gain); Jimmy Neutron adds +51 after its logo (`adsdec --gain-bias 51`).
  The colour tables are read from the ROM.
- *Disney Channel Collection - Volume 2 (Rev 5)* is a 64 MiB Matrix-mapper cart and reads every
  stream byte through the mapper window.

## Documents

| file | contents |
|---|---|
| [player.md](player.md) | Builds, boot, memory map, decode schedule, interrupts, timing, display, input, menus, Matrix mapper use, codec entry points and build differences |
| [format.md](format.md) | SFCD archive and Hydrogen packaging, `.mmstr` resources, text encoding and `Settings`, video header and chunks, the LZMA layer and its 128 KiB ring, the audio resource, timing |
| [perf.md](perf.md) | CPU load per stream and per build, measured whole-stream in an instrumented mGBA and cross-checked with a unicorn cost model |
| [prior-art.md](prior-art.md) | quatric's gist and mobipeg, element by element |
| [features.md](features.md) | Codec feature matrix per player build |
| [stats.json](stats.json) | One record per stream: size, rates, CPU load, verification counts |
| [../majesco/vq.md](../majesco/vq.md), [../majesco/adpcm.md](../majesco/adpcm.md) | The VQ layer and the ADPCM codec shared with the Hydrogen family |

## Build and verify

```sh
make -C reference/ads                               # adsdec and libads.so
export GBAV_ROMS=/path/to/your/roms                 # No-Intro names or short ids (verify/carts.csv)
python3 tools/ads/verify.py                         # every ADS cart found, against verify/ads/
python3 tools/ads/verify.py srmt jimmy              # some carts, by short id
reference/ads/adsdec ROM --list
reference/ads/adsdec ROM --stream movie_1 --count 100 --out out/
```

The manifests are in `verify/ads/`: `<cart>_<stream>.frame_md5.csv.gz`,
`.audio_seg_md5.csv.gz` and, where present, the per-block `.audio_md5.csv.gz` (hashes only; see
the [README](../../README.md#manifests)), plus `<cart>.capture.json` with the stream boundaries of each
capture. Captures were made with `tools/ads/emu/capture.py` on an mGBA libretro core patched
with `tools/ads/emu/mgba-hooks.patch`. That patch modifies mGBA and is under mGBA's licence,
MPL-2.0; build the core into `tmp/mgba/ads-hooks/` or point `ADS_CORE` at it.

mGBA enables its Matrix mapper emulation only for ROM files larger than 32 MiB. The No-Intro
dump of *Disney Channel Collection - Volume 2 (Rev 5)* is the 32 MiB that holds data, so the
capture tools give mGBA a copy padded with 0xFF to 64 MiB (`tools/ads/carts.py`,
`emu_rom_path`, written under `tmp/ads/emu/roms/`); with the plain dump the cart shows a white
screen. The decoders read the plain dump.

### Rust

The Rust crate `gbav-ads` decodes the SFCD/.mmstr container and LZMA, on `gbav-majesco`. With your ROMs in `$GBAV_ROMS`:

```sh
cargo test --release -p gbav-ads          # every frame (the 41 Noah proto stale-codebook frames as known cart-side differences, also reproduced with the old codebook), every audio block, every chunk from a cold start; with `GBAV_CDIFF=1` also the LZMA differential test against `reference/ads`
cargo run --release -p gbav-cli -- verify "$GBAV_ROMS"   # every cart of every family
```

## Open issues

1. The Noah proto's late display (41 intro frames with a stale codebook) is reproduced from the
   decode schedule by `tools/ads/explain_stale.py`, not by the decoder: matching it inside a
   decoder needs a model of the player's timing.
2. Only the start-up brightness was captured. The other 10 brightness levels follow the same
   gain table (shared with Hydrogen) but are not verified against the carts.
3. Selecting episodes in the emulator needs per-cart menu tweaks (Nicktoons 2 and SpongeBob 3
   reach the fourth episode with UP; Jimmy Neutron needs about 90 frames between presses).
4. The 19 bytes of ADPCM tables are literals in `reference/majesco/adpcm.c`; they define the
   bitstream rather than being cart data.
5. *Cartoon Network Collection - Special Edition (USA) (Rev 5)* is unverified (no dump).
