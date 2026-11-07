# GBA Video *Shrek*: player and VX++ codec reverse engineering

This repository documents the video player on the Game Boy Advance Video cartridge
*Shrek* (USA, Rev 6). The cartridge plays its movie with Actimagine's "VX++" codec, the
predecessor of Mobiclip. The repository has:

- documentation of the player program;
- a specification of the container, video and audio bitstreams, detailed enough to
  reimplement them;
- a C99 video decoder and a Python audio decoder, both bit-exact against the cartridge;
- a cycle-level measurement of how much of the GBA the player uses.

**No ROM data or movie content is included.** Bring your own dump:
`shrek.gba`, sha1 `95d612057682b7b886441d387b167ba2493e49ef`
(header "SHREK MOVIE", game code MSKE). Put it at the repository root.

## Documents (`docs/`)

| File | Contents |
|---|---|
| [player.md](docs/player.md) | The player program. Covers the boot loader and its 3 programs, the 64 MiB "Matrix" bank-switching mapper and the memory map. Also the main loop, interrupts and timers, input, the on-screen display, the self-modifying YUV→RGB code, and the decoder call graph with addresses. |
| [container.md](docs/container.md) | The VX++ header, chapter and seek tables, the layout of the video and audio streams, the A/V sync formula, and the audio codec (LPC/pulse). |
| [video.md](docs/video.md) | The video bitstream: bit reader, frame header, macroblock and partition grammar, all 12 block modes, motion-vector prediction, motion compensation, intra predictors, and reference-frame rotation. |
| [residual.md](docs/residual.md) | Residual coding: coded-block pattern, the coefficient VLC (the MPEG-4 intra TCOEF table), escape codes, zigzag scan, H.264-style dequantisation and the 4×4 inverse transform. |
| [emu.md](docs/emu.md) | How the ground truth was made. Headless mGBA (libretro core driven through ctypes) captures decoded frames. A unicorn harness runs the cartridge's own decoder code one frame at a time. |
| [perf.md](docs/perf.md) | CPU, memory, cartridge bus, DMA, display and audio use over the whole movie, measured with an instrumented mGBA. |

## Code

| Directory | Contents |
|---|---|
| `decoder/` | C99 reference video decoder. `verify.py` checks it against the emulator hashes. `fuzz.py` and `test_residual.py` compare it with the cartridge's ARM code run in unicorn. |
| `container/` | `vxdemux.py`: `info`, `walk`, `extract` and `wav` commands. `vxaudio.py`: bit-exact audio decoder. `vxvwalk.py`: a walker that parses the video grammar without decoding. |
| `emu/` | The mGBA capture harness and the unicorn oracle. |
| `perf/` | The mGBA instrumentation patch, a cycle-cost model, per-frame and per-tick CSVs (gzipped), and plots. |
| `residual/` | Generators for the residual tables, and the check against the MPEG-4 codebook. |
| `tools/` | `extract_images.py` copies the player's RAM images out of your ROM. The unicorn tools and the table generators need them. |
| `verify/` | Hashes only: the md5 of every decoded frame from the emulator, and the start bit of every frame. |

## Quick start

You need gcc and Python 3. The unicorn-based tests also need `unicorn` and `capstone`. The emulator harness needs the mGBA libretro core (`nix build nixpkgs#libretro.mgba`).

```sh
cp /path/to/your/dump.gba shrek.gba
make -C decoder                      # generates iwram_lut.h from the ROM, builds vxdec
decoder/vxdec shrek.gba --start-frame 0 --count 70 --out out/   # raw YUV420 (+ --rgb)
python3 decoder/verify.py --manifest --seek    # whole movie vs emulator hashes
python3 container/vxdemux.py info
python3 container/vxdemux.py wav audio.wav
```

## Verification status

- **Video:** decoded output matches the emulator for all 37,837 frames. The screen image matches for all 37,833 frames the cartridge draws. All 195 seek points decode correctly from a cold start. 3,530 random valid frames give the same result from the cartridge's ARM code (run in unicorn) and from the C decoder.
- **Audio:** all 691,876 audio frames match the player's own ARM routine, run in unicorn.

## Licence

The code and documentation are under the MIT licence; see [LICENSE](LICENSE). The one exception is
`perf/patch/mgba-perf.patch`. It modifies mGBA, so it is under mGBA's licence, MPL-2.0.
*Shrek* and the cartridge contents belong to their owners and are not included here.
