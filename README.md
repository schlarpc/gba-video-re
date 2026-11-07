# GBA Video *Shrek*: player and VX++ codec reverse engineering

This repository documents the video player on the Game Boy Advance Video cartridge
*Shrek* (USA, Rev 6). The cartridge plays its movie with Actimagine's "VX++" codec, the
predecessor of Mobiclip. The codec itself was first reverse engineered by quatric (see
[Prior art and credits](#prior-art-and-credits)); this repository re-derives it from the
cartridge code and adds the rest of the player. The repository has:

- documentation of the player program;
- a specification of the container, video and audio bitstreams, detailed enough to
  reimplement them;
- a C99 video decoder and a Python audio decoder, both bit-exact against the cartridge;
- a cycle-level measurement of how much of the GBA the player uses.

**No ROM data or movie content is included.** Bring your own dump:
`shrek.gba`, sha1 `95d612057682b7b886441d387b167ba2493e49ef`
(header "SHREK MOVIE", game code MSKE). Put it at the repository root.

The same player and the same tools also cover the other VX++ cartridges in the table below.
Their player program is byte-identical to Shrek's; see [Other cartridges](#other-cartridges).

## Documents (`docs/`)

| File | Contents |
|---|---|
| [player.md](docs/player.md) | The player program. Covers the boot loader and its 3 programs, the 64 MiB "Matrix" bank-switching mapper and the memory map. Also the main loop, interrupts and timers, input, the on-screen display, the self-modifying YUV→RGB code, and the decoder call graph with addresses. |
| [container.md](docs/container.md) | The VX++ header, chapter and seek tables, the layout of the video and audio streams, the A/V sync formula, and the audio codec (LPC/pulse). |
| [video.md](docs/video.md) | The video bitstream: bit reader, frame header, macroblock and partition grammar, all 12 block modes, motion-vector prediction, motion compensation, intra predictors, and reference-frame rotation. |
| [residual.md](docs/residual.md) | Residual coding: coded-block pattern, the coefficient VLC (the MPEG-4 intra TCOEF table), escape codes, zigzag scan, H.264-style dequantisation and the 4×4 inverse transform. |
| [emu.md](docs/emu.md) | How the ground truth was made. Headless mGBA (libretro core driven through ctypes) captures decoded frames. A unicorn harness runs the cartridge's own decoder code one frame at a time. |
| [vxgb.md](docs/vxgb.md) | VXGB, the older codec revision on the *Shark Tale* and *Shrek 2* Rev 5 cartridges: every difference from VX++ (no frame header, header QP, H.264 CAVLC residual tables behind a different syntax, persistent nC grids, player memory map). |
| [perf.md](docs/perf.md) | CPU, memory, cartridge bus, DMA, display and audio use over the whole movie, measured with an instrumented mGBA. |
| [prior-art.md](docs/prior-art.md) | The earlier public work on these formats, compared element by element with this repository, and a test of quatric's mobipeg decoder against our verified output. |

## Code

| Directory | Contents |
|---|---|
| `decoder/` | C99 reference video decoder. `verify.py` checks it against the emulator hashes. `fuzz.py` and `test_residual.py` compare it with the cartridge's ARM code run in unicorn. |
| `container/` | `vxdemux.py`: `info`, `walk`, `extract` and `wav` commands. `vxaudio.py`: bit-exact audio decoder. `vxvwalk.py`: a walker that parses the video grammar without decoding. |
| `emu/` | The mGBA capture harness and the unicorn oracle. |
| `perf/` | The mGBA instrumentation patch, a cycle-cost model, per-frame and per-tick CSVs (gzipped), and plots. |
| `residual/` | Generators for the residual tables, and the check against the MPEG-4 codebook. |
| `tools/` | `extract_images.py` copies the player's RAM images out of your ROM. The unicorn tools and the table generators need them. |
| `decoder/` (VXGB) | `vxgb_residual.c` (CAVLC residuals), `gen_vxgb_tables.py` (copies the tables from a Rev 5 ROM at build time), `uc_oracle_vxgb.py`, `fuzz_vxgb.py`, `verify_vxgb.py`. |
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

Other cartridges: every tool takes the ROM path and a stream, either as a VX++ header offset
or as an index (`vxdec ROM --list` or `vxdemux.py info ROM` lists them):

```sh
decoder/vxdec combo.gba --stream 0x1eef800 --count 70 --y4m sharktale.y4m
python3 decoder/verify.py --rom combo.gba --stream 1 --manifest --seek --audio
python3 container/vxdemux.py wav combo.gba --stream 1 sharktale.wav
python3 container/emu_audio.py --rom combo.gba --stream 1 --count all
```

## Verification status

- **Video:** decoded output matches the emulator for all 37,837 frames. The screen image matches for all 37,833 frames the cartridge draws. All 195 seek points decode correctly from a cold start. 3,530 random valid frames give the same result from the cartridge's ARM code (run in unicorn) and from the C decoder.
- **Audio:** all 691,876 audio frames match the player's own ARM routine, run in unicorn.

## Other cartridges

| Cartridge | sha1 | Stream (header offset) | Size | Frames | Result |
|---|---|---|---|---|---|
| Shrek (USA) Rev 6 | `95d61205…` | 0x20200 movie | 240x160 | 37,837 | all match |
| Shark Tale (USA) Rev 6 | `9bc5b607…` | 0x20200 movie | 240x160 | 37,697 | all match |
| Shrek 2 (USA) Rev 6 | `3bcb4acc…` | 0x20200 movie | 240x160 | 38,816 | all match |
| Shrek + Shark Tale (USA) Rev 5 | `f23390b7…` | 0x20200 Shrek | 240x112 | 34,874 | all match |
| | | 0x1eef800 Shark Tale | 240x112 | 34,261 | all match |
| | | 0x3dd3600 Shrek credits | 240x160 | 595 | all match |
| | | 0x3e76a00 Shark Tale credits | 240x160 | 707 | all match |

Full sha1s are in `verify/streams.json`. "All match" means all of these checks pass:

- Every frame's decoder slot (YUV) matches the emulator. Every frame the cartridge draws
  also matches on screen (BGR555).
- Every seek point decodes correctly from a cold start, for up to 1,000 frames.
- Every seek point's bit offset equals the frame start found by a continuous decode.
- Every audio frame matches the player's ARM routine run in unicorn, for the whole stream.
- Every audio block the cartridge plays matches the emulator.

The Rev 5 *Shark Tale* and *Shrek 2* cartridges use the older "VXGB" format; see below and
[docs/vxgb.md](docs/vxgb.md). quatric reports that *Shrek* (USA) (Rev 5) is VXGB too; we had no
dump of it to check.

The loader, all three programs and the player's EWRAM and IWRAM images are byte-identical on
all four cartridges. Only the cartridge header and the menu data at ROM 0x20000 differ.
The streams differ from Shrek in the following ways. The decoder needed only the last fix.

- **240x112 picture (combo cartridge).** The player keeps four 0xF000-byte slots, stride
  256. It decodes rows 0..111, and the rest of each slot keeps its initial value. The
  blitter's height immediate is patched to 112, and the picture is centred at row 24. The
  rows above and below it stay black.
- **Short streams.** The two credits streams have one segment. Their chapter table holds
  only frame 0, and header fields 0x24/0x30/0x34 are 0.
- **QP.** Segment QPs are 33..37 instead of 30. *Shark Tale* also changes the QP inside a
  segment twice.
- **Encoder timing.** Some seek points' audio is one AFrame before or after
  `floor((128f+1)/7)`, and two *Shark Tale* chapters start one frame after a seek point.
  The player does not depend on either. It restarts both clocks at the seek point.
- **Blit carries.** Above the clamp range, the clamp table reads code bytes that are larger
  than 31. The blitter adds the colour fields together, so a large value carries into the
  next field. Shrek never reaches this. Shark Tale does in 4 frames, and the combo cart's
  Shark Tale in 30 frames. The decoder had modelled it with OR; it now adds.

### VXGB (Rev 5 *Shark Tale* and *Shrek 2*)

*Shark Tale* (USA) (Rev 5) and *Shrek 2* (USA) (Rev 5) use an older player whose streams start
with `VXGB` (see [docs/vxgb.md](docs/vxgb.md)). vxdec detects the magic and decodes them;
vxdemux, emu_audio and capture.py accept them too. Building VXGB support needs one Rev 5 dump
for its code tables:

```sh
make -C decoder VXGB_ROM=../sharktale.gba      # or put sharktale.gba / shrek2.gba at the root
decoder/vxdec sharktale.gba --count 70 --out out/
python3 decoder/verify_vxgb.py sharktale.gba   # whole movie vs emulator hashes + every seek point
```
Without a VXGB ROM the build still works and decodes VX++ only.

| cartridge | sha1 | status |
|---|---|---|
| Shark Tale (USA) (Rev 5) | `6128a476edb10e6839ac5bd2697e83b9b7a9b234` | video: all 37,697 frames match the emulator, all 191 seek points; audio: all 689,317 AFrames match the ARM routine |
| Shrek 2 (USA) (Rev 5) | `5cd627e205020297b25d707131883be5850515fe` | video: all 38,816 frames match the emulator, all 197 seek points; audio: all 709,779 AFrames match the ARM routine (the emulated cart underruns its audio window late in the movie, see vxgb.md 9) |

## Prior art and credits

The VX++ and VXGB codecs were reverse engineered before this repository, by **quatric**:

- [quatric/mobipeg](https://github.com/quatric/mobipeg) (August 2026): detailed notes
  (`doc/gba_video_vxpp.md`, `doc/gba_video_vxgb.md`), Python tools, and a native FFmpeg
  demuxer and decoder (`libavformat/gbavx.c`, `libavcodec/gbavxdec.c`, `libavcodec/vx.c`)
  for both revisions;
- ["The Mobiclip Formats"](https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860),
  section 8.3, a summary of that work.

Their work already covered the container, the bitstream grammar, all prediction modes, MV
prediction, the residual coding of both revisions, the transform and the audio codec. This
repository's first grammar walker took its mode semantics from mobipeg. We built mobipeg and
compared it with our verified output: its decoded pictures are identical on every frame of
Shrek (Rev 6), Shrek 2 (Rev 6) and both VXGB movies. Two bugs in its frame wrapper break the other
five streams (QPs above 31, and a QP change inside a segment); see
[docs/prior-art.md](docs/prior-art.md).

The audio codec is the DS VX codec, implemented earlier by
[CharlesVanEeckhout/actimagine](https://github.com/CharlesVanEeckhout/actimagine) and
mobipeg's `vx_audio.c`. The DS VX video codec, which VXGB's residual coding shares, goes back to
Gericom's FFmpeg decoder patch (2021) and the actimagine decoder. The VX++ coefficient code
is the table in FFmpeg's `mobiclip.c` (Florian Nouwt, Adib Surani, Paul B Mahol). Earlier
public notes: the [MultimediaWiki page](https://wiki.multimedia.cx/index.php/Actimagine_Video_Codec)
on the DS codec, and Kostya Shishkov's
[2025 note](https://codecs.multimedia.cx/2025/08/a-quick-glance-at-another-bunch-of-codecs/)
on the `VXGB` magic. The emulator ground truth relies on mGBA's Matrix mapper emulation.

What this repository adds:

- the player program: boot loader, mapper use, memory map, timers and IRQs, A/V pacing,
  input, on-screen display, and the self-modifying YUV->RGB blitter;
- the display stage exactly: dither, 5-bit clamp table, and the carries when the table runs
  into code bytes;
- the GBA audio output stage, and the player's reset of the audio state at intra AFrames;
- a frame header model that also handles QP changes inside a segment, the container
  fields the earlier work left unexplained, and the exact per-frame A/V mapping;
- smaller codec findings: the VX++ coefficient code is the MPEG-4 intra TCOEF table, the
  exact bilinear fill on corner overflow, and the bit reader on over-long codes;
- whole-movie verification of nine streams against an emulator and the cartridge's own ARM
  code, and differential fuzzing against that code;
- a cycle-level profile of the player.

## Licence

The code and documentation are under the MIT licence; see [LICENSE](LICENSE). The one exception is
`perf/patch/mgba-perf.patch`. It modifies mGBA, so it is under mGBA's licence, MPL-2.0.
*Shrek* and the cartridge contents belong to their owners and are not included here.
