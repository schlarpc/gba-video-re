# FVMV: the Pokemon GBA Video cartridges

The four *Pokemon* cartridges (Vol 1-4, USA) play their episodes with **FVMV**, a video and
audio codec by Actimagine. Nintendo published the carts (maker code 01), but the boot screen
shows a "powered by actimagine" logo, and the audio output stage is the same algorithm as in
Actimagine's VX player. The video codec is unrelated to VX. Each cart holds two episodes, so
the family has 8 streams, 80,972 frames and about 2 h 50 min of video.

| Cartridge | Stream 0 | Stream 1 | Player build |
|---|---|---|---|
| Pokemon - Volume 1 (USA) | A Hot Water Battle (9,936 frames) | For Ho-Oh the Bells Toll! (9,963) | `a7e6307ff34a` A |
| Pokemon - Volume 2 (USA) | Playing with Fire! (9,977) | Johto Photo Finish (9,937) | `a7e6307ff34a` A |
| Pokemon - Volume 3 (USA) | Pokemon - I Choose You! (10,213) | Here Comes the Squirtle Squad (10,215) | `a7e6307ff34a` B |
| Pokemon - Volume 4 (USA) | Beach Blank-out Blastoise (10,598) | Go West Young Meowth (10,133) | `a7e6307ff34a` B |

All four carts have the same classifier build id. Vol 1/2 (variant A) and Vol 3/4 (variant B)
differ only in the playback UI loop; the decoder code is identical ([player.md](player.md)
section 1).

## The codec in brief

- **Container.** A small header (magic `FVMV`) and a stream of frames with no per-frame index.
  Header word +0x10 is the frame period in scanlines (1704), so the frame rate is 7.99171 fps.
  There are 992 seek points, all of them keyframes. One extra audio block is inserted every
  125 frames to keep the audio locked to the frame clock.
- **Video.** 240x160, 16-bit pixels. Blocks are split by recursive halving from 8x8 down to
  1x2 / 2x1 (15 sizes). The modes are: copy in place or with a motion vector (range +-8,
  wrapping across the left/right picture edge), copy plus a colour offset (a 32-bit add on pixel
  pairs, so carries reach bit 15 and the neighbouring pixel), flat fill with neighbour blend,
  2-colour 4x4 patterns, interpolated 8x8, and raw pixel pairs. The first bit of each frame's
  bitstream is discarded. The motion-vector substream is padded to 4 bytes.
- **Audio.** A stripped-down GSM 06.10: no long-term prediction, no LAR interpolation and no
  saturation. 40 bytes give 256 samples at 16,384 Hz. The decoder state is never reset. The
  output stage is 4x interpolation and noise-shaped 8-bit PCM at 65,536 Hz, as in VX.

No ROM data is needed at build time: every table is computed from a formula, and each formula
was checked to give the table in the ROM.

## Verification

- **Whole streams against mGBA.** 80,972 / 80,972 video frames match (all 16 bits of every
  pixel) and 648,427 / 648,427 audio blocks match (int32 synthesis output and 8-bit PCM), on all
  8 episodes.
- **Unicorn oracle fuzzing** against the cart's own ARM code: 3,000 video cases covering all
  88 (block size, mode) combinations, and 82,033 audio blocks. There were no mismatches.
- **Performance.** CPU is 51-52 % busy on average, with a worst frame window of 77 %. Audio
  takes 25.8 %, video decoding 16.3 % and the blit 8.8 %. No frame is late. The unicorn cost
  model agrees with the instrumented mGBA to 0.16 %. See [perf.md](perf.md).

## Documents

| File | Contents |
|---|---|
| [player.md](player.md) | Cartridges and builds, boot, memory map, menus, episode playback, input, interrupts, and the differences from the VX player |
| [format.md](format.md) | Container, video bitstream and audio bitstream, with pseudocode and exact bit widths; mode statistics; the comparison with VX audio; verification |
| [perf.md](perf.md) | CPU and resource use over all 8 episodes, the cost-model cross-check, and the audio-ring resyncs |
| [prior-art.md](prior-art.md) | Earlier public work on FVMV, compared element by element |
| [features.md](features.md) | Codec feature matrix per player build |
| [stats.json](stats.json) | Per-stream measurements (source of the suite tables in [../carts.md](../carts.md)) |

## Build and verify

```sh
make -C reference/fvmv                    # builds fvmvdec and libfvmvdec.so
reference/fvmv/fvmvdec ROM --list
reference/fvmv/fvmvdec ROM --stream 0 --start-frame 0 --count 70 --out out/
GBAV_ROMS=/path/to/roms python3 tools/fvmv/verify.py     # all 8 streams vs verify/fvmv/
```

`tools/fvmv/verify.py` finds the Pokemon carts in `$GBAV_ROMS` (or the paths given) by sha1
and compares every frame and every audio block with `verify/fvmv/pokemon<vol>_<stream>.*`.
`tools/fvmv/fuzz.py` runs the unicorn differential fuzzer; it needs an mGBA savestate taken just
before a frame decode (see `tools/fvmv/emu/capture_fvmv.py`). The profiler in
`tools/fvmv/perf/` needs mGBA's libretro core built with `tools/mgba/mgba-prof.patch`.

## Open issues

- **Vol 3 last-frame audio.** On Vol 3 the player clears its PCM ring in the same video
  refresh as the last frame's audio, so the emulator capture could not read the 8-bit PCM of
  the last frame of each Vol 3 stream. Those 16 PCM hashes in the manifests come from the
  unicorn oracle running the cart's own audio routines, not from the emulator capture.
- **Reads below EWRAM are not modelled.** A motion vector that points above the frame buffer
  would read memory below EWRAM in the player. The decoder does not model this. No real stream
  does it.
- **Audio resyncs are measured in mGBA only.** mGBA shows 9-15 audio-ring resyncs per episode,
  each of which skips or repeats up to 1/8 s of sound ([perf.md](perf.md)). The count depends on
  the emulator's timer and DMA timing. It has not been checked on hardware.
- **Capture and perf scripts need local set-up.** `tools/fvmv/emu/` and `tools/fvmv/perf/`
  expect the patched mGBA core in `tmp/mgba/prof/` (or `GBAV_MGBA_PROF_CORE`) and write their
  outputs under `tmp/fvmv/`; they are research tools rather than a polished pipeline.
