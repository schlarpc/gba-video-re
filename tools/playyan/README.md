# Play-Yan tools and emulators

Everything here runs the cartridges' own firmware, so you need your own dumps: the three GBA ROMs
and Nintendo's `playan.fup`. Nothing in the repository holds firmware bytes, tables extracted from
it, or media.

| directory | what it is |
|---|---|
| [`vc01/`](vc01) | The Alphamosaic VC01 (Play-Yan, Play-Yan Micro): the instruction-set model in Python (`vc01.py` scalar, `vc01v.py` vector unit), the C core (`vc01core.c`) and the peripheral model (`vc01hw.c`), the lockstep check of one against the other, the disassembler, and the test suites that run firmware routines against independent references ([vc01-isa.md](../../docs/playyan/vc01-isa.md), [vc01-hardware.md](../../docs/playyan/vc01-hardware.md)) |
| [`c54x/`](c54x) | The TI TMS320VC5416 of the Nintendo MP3 Player: the C54x core (`c54x.c`), the board model (`nmphw.c`), a standalone machine, a minimal host and the instruction tests ([c54x-hardware.md](../../docs/playyan/c54x-hardware.md)) |
| [`emu/`](emu) | The full system: an mGBA patch that adds the cartridge (`mgba-pyan.patch`, MPL-2.0), the libretro harness, the scenarios, the high-level Python models of the cartridges (HLE) and the bridges that put the real firmware on the cores above (LLE), and the capture and manifest tools behind [verify/playyan](../../verify/playyan/README.md) |
| `aac/`, `g726/`, `mp3/`, `nmp/`, `video/` | Oracles, fuzzers and comparison scripts for the decoders ([decoders.md](../../docs/playyan/decoders.md)); they use the emulators here |
| `extract_firmware.py`, `workdir.py` | Firmware extraction, and the paths every tool shares |

Our code is under the repository's MIT licence. `emu/mgba-pyan.patch` modifies mGBA and is under
mGBA's licence, the Mozilla Public License 2.0, as its SPDX header says.

## Setup

Python 3 with numpy (Pillow for screenshots), gcc, ffmpeg, mtools and dosfstools (`mkfs.fat`), and
Nix for the mGBA source. The tools read these environment variables:

| variable | meaning | default |
|---|---|---|
| `PY_ROMS` | your dumps, under their No-Intro names: `Play-Yan (Japan).gba`, `Play-Yan Micro (Japan).gba`, `Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba`, and `playan.fup` | `$GBAV_ROMS`, then `roms/` |
| `PY_MEDIA` | Nintendo's sample downloads under their download names (`playan_support_sample_movie/sample_movie.asf`, `playan_garage_avoid/AVOID.ASF`, ..., as in [decoders.md](../../docs/playyan/decoders.md)) | - |
| `PLAYYAN_WORK` | the work directory: extracted images, built libraries, SD cards, runs, captures | `tmp/playyan` (gitignored) |
| `PY_CAPTURE_DIR` | where `emu/capture.py` writes | `$PLAYYAN_WORK/capture` |

```sh
python3 tools/playyan/extract_firmware.py   # -> $PLAYYAN_WORK/firmware/extract/*.bin, each checked by sha1
tools/playyan/emu/build.sh                  # patched mGBA libretro core -> $PLAYYAN_WORK/build/mgba-pyan/
python3 tools/playyan/emu/make_sd.py        # SD card folders (symlinks into PY_MEDIA) -> $PLAYYAN_WORK/sd/
python3 tools/playyan/vc01/vec/prep_media.py   # Garage Game first frames for the pixel tests (ffmpeg)
```

The extraction writes `py_fw0`, `py_fw1`, `py_fwldr` (Play-Yan), `mi_fw0`, `mi_fw1`, `mi_fwldr`
(Micro), `fup_sec0`, `fup_sec1` (`playan.fup` v1.11) and `nmp_boottable` (the MP3 Player's C54x
boot table), and refuses any image whose sha1 is not the one in
[firmware.md](../../docs/playyan/firmware.md). The C cores need no separate build: `cvc.py`,
`nmpm.py` and `vec/mp3ref.py` compile them into `$PLAYYAN_WORK/build` on first use and again when
a source changes.

## Test suites

Run from `tools/playyan/vc01` (each prints one line per test and a total):

```sh
python3 tests.py                  # 23 scalar routines of the loader: libgcc, soft-float, FAT, .fup check (needs playan.fup)
python3 g726_test.py              # 6000 G.726 cases, scalar and vector, against Sun's g72x
python3 vec/mp3_test.py           # 98 MP3 kernels (py_fw1)
python3 vec/mp4_test.py           # 2500 MPEG-4 kernels (py_fw0)
python3 vec/aac_test.py           # 4491 AAC kernels (mi_fw0; AAC_IMG=fup_sec0 for v1.11)
python3 vec/pix_test.py           # pixel pipeline, including the Garage Game thumbnail hashes end to end
python3 vec/bitstream_test.py     # 24 bitstream unpacker cases
python3 vec/gap_test.py           # 200 remaining video kernels (IMG=mi_fw0 or fup_sec0 for the other images)
python3 test_ld11.py              # the 48-bit lane load/store forms, C core against the Python model
python3 lockstep.py tests         # the C core against the Python model, instruction by instruction
python3 lockstep.py g726          #   ... likewise, vector unit included
python3 lockstep.py suite vec/mp3_test.py   # ... any kernel suite
```

The references are independent of the firmware: Python integers and floats, Sun's G.72x algorithm,
ISO formulas, the GBA ROM's thumbnail hashes, and for MP3 the C reference decoder's kernels
(`reference/playyan/mp3`, built by `vec/mp3ref.py`; constants read from the image). With
`FFMPEG_SRC` set to an FFmpeg `libavcodec` directory, the MP3 synthesis test also compares against
ISO floating-point synthesis with FFmpeg's copy of the standard window. `vrun.py KEY=VAL script`
runs a suite with a vector-model switch changed (ablation).

The C54x core's tests are in `tools/playyan/c54x`: `python3 c54test.py` runs our 41 instruction
cases from recorded words. With `C54X_AS` (GNU as built with `--target=tic54x-coff`) it assembles
them itself, and with `C54X_CASES` (the Ghidra-Rare TMS320C54x module, Apache-2.0, not included) it
adds that module's 94 cases from TI's manuals, three of which the core does not follow.

Tools: `vc01dis2.py IMG [START END]` disassembles an image (recursive descent with function and
string annotations), `reach.py` / `cov.py IMG` give decode coverage, `findfuncs.py` and `xrefs.py`
find functions and references, `boot.py IMG` boots an image on the Python model with stub
peripherals, `vcm.py boot IMG --sd DIR` boots it on the C core with the full peripheral model and no
GBA. `c54x/c54dis.py` disassembles the DSP image with binutils (`objdump -m tms320c54x`) and checks
the core's decoder against it; `c54x/nmpm.py boot` runs the DSP without a GBA.

## Running the ROMs

From `tools/playyan/emu`:

```sh
python3 run.py orig boot --lle        # Play-Yan: loader, RAM test, music image, folder walk, main menu
python3 run.py micro boot --lle       # Play-Yan Micro (reads its key file)
python3 run.py nmp boot_nmp --lle     # Nintendo MP3 Player: DSP upload, boot, mount, menu
python3 run.py orig garage --lle      # Garage Games: the GBA ROM accepts the firmware's thumbnails
python3 win1_test.py                  # that check as a test (PASS); --ablate shows it failing without the colour bridge
```

`--lle` runs the cartridge's real firmware on the emulated chip; without it, a high-level Python
model answers the protocol (`pyan_model.py`, `nmp_model.py`, media decoded by ffmpeg), as used for
the first reading of the protocol. Scenarios are the functions in `scenarios.py` (`music`, `video`,
`micro_video`, `nmp_music`, `sleep`, ...); `--sd DIR` picks another SD card folder. Each run writes
its bus trace, event log, summary and screenshots under `$PLAYYAN_WORK/runs`.

## Captures and manifests

`emu/capture.py BUILD KIND FILE` plays one file from power-on to the cartridge's end record and
writes everything the cartridge produced; `emu/make_manifests.py` turns captures into the
[verify/playyan](../../verify/playyan/README.md) manifests. BUILD is `orig` (`py_fw0`/`py_fw1`),
`v111` (the Play-Yan with `playan.fup` on the card: `fup_sec0`/`fup_sec1`) or `micro`:

```sh
cd tools/playyan/emu
python3 capture.py micro video "$PY_MEDIA/playan_garage_avoid/AVOID.ASF" --name XAVOID.ASF --tag micro_garage_AVOID
python3 capture.py v111 music "$PY_MEDIA/playan_support_sample_music/sample_music.mp3" --tag v111_sample_music
python3 capture.py micro video "$PY_MEDIA/play_yan_micro_support_mp4sample_movie/MP4sample_movie.mp4" --tag micro_MP4sample_movie
python3 make_manifests.py --out ../../../tmp/check micro_garage_AVOID v111_sample_music micro_MP4sample_movie
for f in ../../../tmp/check/*.gz; do cmp "$f" ../../../verify/playyan/$(basename "$f"); done
```

Garage Game files must be renamed (`--name XAVOID.ASF`), or the GBA starts the game instead of
playing the video. Without `--out`, `make_manifests.py` writes into `verify/playyan`. The manifests
are gzip with mtime 0, so a capture that reproduces the committed one gives identical files. The
`gba_cycle` column (pacing only; nothing checks it) of the original Play-Yan's and v1.11's sets
was recorded with an earlier build of the emulator and now differs, mostly by one 1,024-cycle
coupling slice; every hash column is unchanged, and the Micro's sets reproduce byte for byte
([verify/playyan](../../verify/playyan/README.md#reproducing)). The test tones and the chirp of the music sets are not
published files: `media.csv` gives their md5, and a fresh encode with another ffmpeg or LAME build
differs from them.
