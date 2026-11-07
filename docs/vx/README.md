# VX family: Actimagine VX++ and VXGB (Shrek, Shark Tale, Shrek 2)

Seven cartridges, ten streams. Actimagine's VX codec is the predecessor of Mobiclip. It comes in
two revisions with two player builds:

| build (gbav-rom id) | stream magic | cartridges |
|---|---|---|
| VX++ `344d68b1e9aa` | `VX++` | *Shrek* (USA) (Rev 6), *Shark Tale* (USA) (Rev 6), *Shrek 2* (USA) (Rev 6), *Shrek + Shark Tale* (USA) (Rev 5) |
| VXGB `0600eda5896e` | `VXGB` | *Shrek* (USA) (Rev 5), *Shark Tale* (USA) (Rev 5), *Shrek 2* (USA) (Rev 5) |

All are 64 MiB carts on the Matrix bank-switching mapper. The video is YUV 4:2:0 at 240x160
(240x112 on the combo cart), 7 fps, with 16x16 macroblocks, a binary partition tree, 12 block
modes, integer-pel motion compensation from 3 reference frames and an H.264-style 4x4 transform.
VX++ codes residuals with the MPEG-4 intra TCOEF VLC; VXGB with H.264 CAVLC tables behind its own
syntax. Audio is Actimagine's LPC/pulse codec at 16,384 Hz, played at 65,536 Hz after 4x
interpolation and noise shaping.

## Documents

| file | contents |
|---|---|
| [player.md](player.md) | The player program: boot loader and its 3 programs, Matrix mapper, memory map, main loop, interrupts and timers, input, OSD, the self-modifying YUV->RGB blitter, the decoder call graph. Section 11: builds per cart, code placement, the audio window and its single-slot refill request, blit skipping |
| [format.md](format.md) | Index of the format specification by layer, and the coding parameters of every stream |
| [container.md](container.md) | VX++ header, chapter and seek tables, stream layout, A/V sync, the audio codec |
| [video.md](video.md) | Video bitstream: bit reader, frame header, MB and partition grammar, all 12 modes, MV prediction, MC, intra predictors, reference rotation |
| [residual.md](residual.md) | VX++ residual coding: CBP, the coefficient VLC (MPEG-4 intra TCOEF), escapes, zigzag, dequantisation, 4x4 IDCT |
| [vxgb.md](vxgb.md) | VXGB: every difference from VX++ (no frame header, header QP, CAVLC residuals, persistent nC grids, player memory map) |
| [emu.md](emu.md) | How the ground truth was made: headless mGBA via libretro, and the unicorn oracle |
| [perf.md](perf.md) | Part 1: cycle-level resource use for *Shrek* (Rev 6). Part 2: all 10 streams, VXGB vs VX++, the *Shrek 2* (Rev 5) audio failure |
| [prior-art.md](prior-art.md) | Earlier public work compared element by element, and a test of quatric's mobipeg against our output |
| [features.md](features.md) | Codec feature matrix: VXGB vs VX++ (and DS Mobiclip for context) |
| [stats.json](stats.json) | Per-stream measurements (input of [../carts.md](../carts.md)) |

## Build and verify

You need gcc and Python 3; the unicorn tools also need `unicorn` and `capstone`, the emulator
harness the mGBA libretro core (`nix build nixpkgs#libretro.mgba`). Name your dumps as No-Intro
does, or by the short ids in [verify/carts.csv](../../verify/carts.csv), in one directory:

```sh
export GBAV_ROMS=/path/to/your/roms
ln -s "$GBAV_ROMS/Game Boy Advance Video - Shrek (USA) (Rev 6).gba" shrek.gba      # tables for VX++
ln -s "$GBAV_ROMS/Game Boy Advance Video - Shark Tale (USA) (Rev 5).gba" sharktale.gba   # tables for VXGB
make -C reference/vx/decoder          # generates the clamp and CAVLC tables from the ROMs, builds vxdec
python3 tools/vx/verify/verify_vx.py  # every VX stream: frames and audio blocks vs verify/vx/
```

The decoder builds without a VXGB ROM, but then decodes VX++ only. Other entry points:

```sh
reference/vx/decoder/vxdec ROM --list
reference/vx/decoder/vxdec ROM --stream 0x1eef800 --count 70 --y4m out.y4m
python3 tools/vx/verify/verify.py --rom ROM --stream 1 --manifest --seek --audio   # + seek points
python3 tools/vx/verify/verify_vxgb.py ROM --manifest --seek                    # VXGB carts
python3 tools/vx/container/vxdemux.py wav ROM --stream 1 out.wav
python3 tools/vx/oracle/emu_audio.py --rom ROM --stream 1 --count all          # ARM audio routine
python3 tools/vx/extract_images.py ROM   # player RAM images for the unicorn tools (reference/vx/player/)
```

## Verification status

Every stream is checked whole against an mGBA capture: the decoder's YUV slot of every frame, the
displayed BGR555 picture of every frame the cart draws, every audio block the cart plays, every
seek point from a cold start, and the ARM decoder and audio routine run in unicorn.

| cart | stream | size | frames | video | audio blocks |
|---|---|---|---|---|---|
| Shrek (Rev 6) | 0x20200 | 240x160 | 37,837 | all match | 691,796 / 691,796 |
| Shrek (Rev 5) | 0x20200 VXGB | 240x160 | 37,837 | all match | 691,796 / 691,796 |
| Shrek 2 (Rev 6) | 0x20200 | 240x160 | 38,816 | all match | 709,698 / 709,698 |
| Shrek 2 (Rev 5) | 0x20200 VXGB | 240x160 | 38,816 | all match (8 blits the player skips) | 296,180 / 709,698: every block before the cart's own audio failure matches (see Open issues); the ARM audio routine matches all 709,779 AFrames |
| Shark Tale (Rev 6) | 0x20200 | 240x160 | 37,697 | all match | 689,240 / 689,240 |
| Shark Tale (Rev 5) | 0x20200 VXGB | 240x160 | 37,697 | all match (26 blits the player skips) | 689,240 / 689,240 |
| Shrek + Shark Tale (Rev 5) | 0x20200 Shrek | 240x112 | 34,874 | all match | 637,614 / 637,614 |
| | 0x1eef800 Shark Tale | 240x112 | 34,261 | all match | 626,405 / 626,405 |
| | 0x3dd3600 Shrek credits | 240x160 | 595 | all match | 10,802 / 10,802 |
| | 0x3e76a00 Shark Tale credits | 240x160 | 707 | all match | 12,850 / 12,850 |

The per-stream counts are in [stats.json](stats.json) and [../carts.md](../carts.md). Besides the
captures: 3,530 random valid VX++ frames and the VXGB residual paths agree between the C decoder and
the cartridge's ARM code under unicorn, and the ARM audio routine matches the Python audio
decoder on every AFrame of every stream.

## Open issues

- **Shrek 2 (Rev 5) loses an audio refill request** in mGBA at AFrame 296,039 (VX frame 16,191,
  38 min 33 s) and plays misaligned audio for the rest of the movie. The cause is a single-slot
  request mailbox that is overwritten during a 297.6 ms decode ([perf.md](perf.md) P2.4). It is
  deterministic in mGBA, and the timing analysis says a real GBA very likely fails the same way
  (about 90 %), but nobody has measured it on hardware. A capture of a real cart's audio around
  38:30 would settle it.
- **Matrix mapper latency is unmeasured.** mGBA copies instantly; the hardware does not. The
  only figure is the memory part's quoted read rate (about 1 ms per 1 KiB command). It can only
  make the Shrek 2 (Rev 5) failure more likely, but the near-miss margins of the other streams
  depend on it too.
- **Two definitions of late frames.** `late_or_dropped_frames` in stats.json counts frames shown
  after their Timer3 tick; part 1 of perf.md counted lateness onsets (41 on Shrek Rev 6 vs 262
  late frames). Part 2 explains the difference; the numbers are not interchangeable.
- **The memory and bus deep dive is for Shrek (Rev 6) only.** The per-stream runs measure CPU,
  decode cost, lateness and the audio window, but the per-region bus and PPU analysis of part 1
  was not repeated per stream.
