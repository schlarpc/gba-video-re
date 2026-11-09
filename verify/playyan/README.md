# Play-Yan manifests

Hashes only: no firmware, no decoded frames or audio, no media.

## Provenance

These manifests are what the **real cartridge firmware produced in our emulator**: the real GBA
ROM (*Play-Yan* or *Play-Yan Micro*) runs in mGBA with a bridge to an emulated VC01, which runs
the real firmware image the GBA uploads (or, for v1.11, the image the loader reads from
`playan.fup` on the card). Each capture boots from power-on, opens the browser, presses A on the
only file on the SD card and touches nothing else until the cartridge's end record (0x40000801
music, 0x40000701 video). Every capture reached it. The emulator is in
[tools/playyan](../../tools/playyan/README.md), and `tools/playyan/emu/capture.py` and
`make_manifests.py` make these files ([Reproducing](#reproducing)). This is not a capture from
hardware: see [assumptions](#emulator-assumptions).

## Files

`<image>_<stem>.<kind>.csv.gz`: gzip (mtime 0, the file's own name in the header), CSV with a
header line.

`image` is the cartridge firmware image that decoded the file (the GBA program only displays it):

| image | firmware | from |
|---|---|---|
| `py_fw0` / `py_fw1` | original Play-Yan, video / music | `Play-Yan (Japan)`, uploaded by the GBA |
| `fup_sec0` / `fup_sec1` | v1.11, video / music | `playan.fup` on the card, loaded by the cartridge's loader |
| `mi_fw0` / `mi_fw1` | Play-Yan Micro, video / music | `Play-Yan Micro (Japan)` |

`stem` is the media file's base name, except for the two MP4 sample revisions, whose names differ
only in letter case and are therefore named by where they were published. [media.csv](media.csv)
lists every stem with its file name, md5 and source; `gbav verify` and the manifest test find a
media file by its md5 there, so the files can keep their original names:

| stem | file | md5 |
|---|---|---|
| `sample_music` | Nintendo's `sample_music.mp3` (MPEG-1 Layer III, 44.1 kHz) | 867b1c0668b847b096c78d292a98b4f6 |
| `tone_32000`, `tone_44100`, `tone_48000`, `chirp_44100` | 12 s test tones (left 1 kHz, right 1.5 kHz) and a 10 s chirp (200 to 2,000 Hz), MP3 at those rates; ours, made bit for bit by `tools/playyan/emu/make_test_audio.py` (LAME 4.0, pinned) | in media.csv |
| `sample_movie` | Nintendo's `sample_movie.asf` (MPEG-4 SP + G.726-32) | 4ecc7e9eaae52e750ccf607224ca1120 |
| `AVOID` ... `WAVE` | the 13 Garage Game ASFs (`<NAME>.ASF`); put on the card as `X<NAME>.ASF` so the GBA plays them as video instead of starting the game | - |
| `mp4sample_movie_zip` | Nintendo's `MP4sample_movie.mp4` from the zip downloads (MPEG-4 SP + AAC-LC) | d8103545c2842e1bdeb960a1488cc3a2 |
| `mp4sample_movie_direct` | the other revision, `mp4sample_movie.mp4`, linked directly from the firmware page | 6a16fcaeb7308fd1f2c1f699acf71492 |

MP3 files play on `py_fw1`, `fup_sec1` and `mi_fw1`; ASF on `py_fw0`, `fup_sec0` and `mi_fw0`; MP4
on `fup_sec0` and `mi_fw0` (`py_fw0` has no MP4 support). That gives 61 sets.

| kind | columns | one row per |
|---|---|---|
| `frame_md5` | frame, gba_cycle, bgr555_md5 | video frame record (0x80001000 with rec[1] = frame address), in order. md5 of the 240x160 frame as the GBA reads it through the pixel window: 76,800 bytes of u16 LE BGR555, row-major, after the window's RGB565 to BGR555 swap. Frame i = coded frame i (no drops: 2,869 for `sample_movie`, 2,228 for each MP4, 301 for each Garage ASF) |
| `thumb_md5` | thumb, bgr555_md5 | 60x40 browser thumbnail (0x40000500), 4,800 bytes BGR555 |
| `audio_md5` | block, pcm_frame, frames, rate, md5 | headphone buffer the firmware queued on the AC'97 DMA: md5 of the PCM the DMA moved to the codec, s16 LE, L/R interleaved, `frames` stereo frames (MP3 1,152, AAC 1,024, G.726 800); `pcm_frame` = position in the headphone stream; `rate` = the DAC rate the firmware set. G.726 is mono, written to both channels |
| `audio8_md5` | block, bytes, gba_cycle, md5 | GBA speaker block (0x80001000 audio record): md5 of the rec[4] bytes as sent, rec[4]/2 signed 8-bit right-channel samples then as many left. 16,384 Hz music, 8,192 Hz video. The click of the key press that started playback (a 0x2BA-byte block) is excluded |
| `audio_seg_md5`, `audio8_seg_md5` | segment, first_block, blocks, md5 | about 30 s of blocks: md5 of their concatenated bytes (the repository's segment rule) |

`gba_cycle` is the GBA clock (16,777,216 Hz) when the record was posted. It is for pacing only;
the decoders do not check it.

Facts the manifests show: the three video images give identical frames, thumbnails, headphone and
8-bit audio for every ASF, and `fup_sec0` and `mi_fw0` are identical on both MP4s. The three music
images give the same headphone PCM (`mi_fw1` has one extra final buffer). Their 8-bit blocks agree
between `fup_sec1` and `mi_fw1`; `py_fw1`'s stream is the same samples shifted by one, because the
original drops the first sample after power-on ([decoders.md](../../docs/playyan/decoders.md)).

## Emulator assumptions

- CPU: our VC01 core (scalar and vector unit), checked in lockstep against an independent model
  of the instruction set ([vc01-isa.md](../../docs/playyan/vc01-isa.md)). Two decodings were
  corrected before the final captures, and the four MP4 sets were regenerated after each: the
  48-bit 0x2f op 4-7 form with offset bits 11 is a signed byte load (as a halfword store, every AAC
  frame failed and the cartridge played silence and showed black thumbnails), and REP code 7 on a
  vector memory operation is 64 rows, not 128 (with 128, 75 of 3,201 AAC frames were wrong). No
  other set changed.
- Peripherals ([vc01-hardware.md](../../docs/playyan/vc01-hardware.md)): AC'97 DMA lengths count
  32-bit transfers, and the DAC consumes exactly `rate` frames per emulated second. The WM9712
  codec has no headphones plugged in (register 0x54 bit 1 = 0, so the firmware sends the 8-bit
  speaker stream) and its alert line is high. The SD card runs in SPI mode over a FAT16 image that
  holds only the file (plus `playan.fup` for v1.11).
- Clock: 50 M VC01 instructions per emulated second, the chip coupled to the GBA every 1,024 GBA
  cycles, doorbells serviced at once. The content did not depend on the instruction rate (30 M
  gives the same hashes); only `gba_cycle` does.
- GBA side: mGBA with the Play-Yan bridge. The pixel window converts RGB565 to BGR555 (proven by
  the Garage Game hashes in the GBA ROM). The volume is the default 0xB00 value of 28 (of 32),
  which scales the 8-bit stream but not the headphone PCM.

## Routine oracles (`oracle/`)

Hashes of firmware routines run on our test inputs (not full-system captures; [decoders.md](../../docs/playyan/decoders.md)):

- `oracle/mp3/<name>.mp3_pcm_md5.csv`, `.gba_md5.csv`: the music image's MP3 frame decoder 0x222f2
  (per frame md5 of 1,152 x 2 int16) and speaker routine 0x3185e (per block, volume 28) on
  `sample_music.mp3` and a LAME corpus (`tools/playyan/mp3/gen_media.py`, `make_oracle.py`).
- `oracle/nmp/nmp_manifest.csv`, `nmp_spk_manifest.csv`, `nmp_id3_manifest.csv`: the Nintendo MP3
  Player's DSP (synthesis output per file, 8-bit speaker blocks, AUDIO_INFO block) on LAME and
  crafted streams (`tools/playyan/nmp/make_test_mp3.sh`, `make_id3_tests.py`).

## Use

```sh
cargo test --release -p gbav-playyan            # PY_ROMS (or GBAV_ROMS), PY_MEDIA, PY_VERIFY_MEDIA
target/release/gbav verify FILE... --firmware FW... --manifests verify/playyan
```

## Reproducing

With your ROMs and `playan.fup` in `PY_ROMS` and Nintendo's files in `PY_MEDIA`
([tools/playyan/README.md](../../tools/playyan/README.md) has the setup), and a fresh
`PLAYYAN_WORK`:

```sh
python3 tools/playyan/extract_firmware.py          # firmware images, checked by sha1
tools/playyan/emu/build.sh                         # the patched mGBA core (mGBA source from a pinned nixpkgs)
python3 tools/playyan/emu/make_test_audio.py       # the tones and the chirp -> $PLAYYAN_WORK/media/test, md5s checked
python3 tools/playyan/emu/capture_all.py -j 4 --out DIR   # all 61 captures, then make_manifests.py
for f in DIR/*.gz; do cmp "$f" verify/playyan/$(basename "$f"); done
```

`capture_all.py` runs `capture.py BUILD KIND FILE [--name NAME] --tag TAG` for every set (`--list`
prints them, `--only SUBSTR` picks some) and then `make_manifests.py --out DIR TAG...`. `BUILD` is
the cartridge and firmware: `orig` decodes on `py_fw0`/`py_fw1`, `v111` puts `playan.fup` on the
card (`fup_sec0`/`fup_sec1`), `micro` is the Play-Yan Micro. Garage Game files go on the card as
`X<NAME>.ASF` (`--name`); `make_manifests.py` names each set by the file's md5 in
[media.csv](media.csv). The tones and the chirp are generated: integer-only PCM and LAME 4.0 from
a pinned nixpkgs revision with every option fixed and no tags, so `make_test_audio.py` gives the
md5s in media.csv (checked twice, and once with an empty environment).

Checked on 2026-11-08: two captures of all 61 sets from two fresh work directories (firmware
extracted, core built and test audio generated in each; one run 4 captures at a time, the other 3,
under different machine load) gave byte-identical manifests, all 336 files, and those are the
files here. Two inputs used to depend on the host and are now fixed in the tools: the SD card
image (`vcm.make_sd`: volume serial and directory timestamps came from the clock unless
`SOURCE_DATE_EPOCH` was set, which the dev shell did; it is now always 1980-01-01) and the mGBA
source (`build.sh` took it from the floating `nixpkgs` registry entry; it is now pinned). Against
the manifests committed before, every hash column was unchanged except the 12 sets of the
regenerated test audio; the `gba_cycle` column of the original Play-Yan's and v1.11's video sets
and their two `sample_music` sets moved (mostly by one 1,024-cycle coupling slice, a few frames by
up to 0.05 s), because those had been captured with an earlier build of the emulator. The Micro's
other sets were already byte-identical. No decoder or test reads `gba_cycle`.
