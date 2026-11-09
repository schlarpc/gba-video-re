# Play-Yan, Play-Yan Micro and Nintendo MP3 Player

Nintendo's AGS-006 cartridges play the user's own video and music from an SD card. They are not
GBA Video cartridges: they carry no content, and the decoding is done by a media chip on the
cartridge, not by the GBA. The GBA program uploads that chip's firmware at boot, sends it commands,
and copies finished frames and audio blocks out of it.

| cart | No-Intro (main GBA DAT) | sha1 (1 MiB image) | chip | media |
|---|---|---|---|---|
| Play-Yan (Japan), 2005; also the iQue MP4 Player (same ROM) | `Play-Yan (Japan)` | 7f432bbf6f0e5e1b8324c7d6bb1d81a254a6e870 | Alphamosaic VC01 | ASF (MPEG-4 Part 2 + G.726), MP3; MP4 (AAC) with `playan.fup` |
| Play-Yan Micro (Japan), 2005 | `Play-Yan Micro (Japan)` | 17b5f7ad459448b447fa94617d09886cd4f9c554 | Alphamosaic VC01 | ASF, MP4, MP3 |
| Nintendo MP3 Player (Europe), 2006 | `Nintendo MP3 Player (Europe) (En,Fr,De,Es,It)` | b7c517f1cb5860fec8beaa4e819bcffa8ce95a5b | TI TMS320VC5416 DSP | MP3 |

Common dumps are 4 MiB, the 1 MiB image mirrored four times; the first 1 MiB matches No-Intro.

The GBA side is documented from the code of all three ROMs, and the cartridges' own firmware runs in
our emulator under the real GBA ROMs ([Real firmware](#real-firmware)). **Every codec the cartridges
decode has a standalone decoder, in Rust and C, bit-exact against that firmware**: MPEG-4 Part 2,
G.726, MP3 and AAC-LC on the VC01 images, with the ASF and MP4 containers, the frame, thumbnail and
8-bit audio output stages, and the Nintendo MP3 Player's DSP MP3 decoder
([decoders.md](decoders.md)). The ground truth is the real firmware in our emulator, not a capture
from hardware: [verify/playyan](../../verify/playyan/README.md) holds hash manifests of 61 whole
files played that way (29,258 frames, 41,180 headphone buffers, 30,133 8-bit blocks) and lists the
emulator assumptions they rest on. `gbav verify` and `cargo test` check the decoders against them.

## Findings

- **One register file, two views.** The cartridge's registers at 0x09000000 and 0x0B000000 are the
  same registers behind two wait-state windows. Commands are little-endian structures written to a
  mailbox in the chip's memory (VC 0xFF020), followed by a doorbell. Every Game Pak interrupt
  delivers one 32-byte record from VC 0xFF000. Records carry the chip-memory addresses of their
  payloads: the file list, ID3 block, thumbnail, video frame and audio block
  ([protocol.md](protocol.md)).
- **Three firmware images per cart**: video, music and a loader. Switching between the music and
  video menus reloads the other image. `playan.fup` holds two complete replacement images, not a
  patch ([firmware.md](firmware.md)).
- **The cartridge sets all timing.** The GBA copies each 240x160 BGR555 frame straight into mode-3
  VRAM with no double buffer, and plays 8-bit audio from two 0x900-byte rings at 16,384 Hz (music)
  or 8,192 Hz (video, menus). The only GBA-side coupling is a ±3-cycle trim of Timer 0 from the
  averaged ring fill. The first half of each audio block plays on the right speaker
  ([player.md](player.md) 6).
- **The chips.** The firmware names itself `vc01pp_*` (VC01, not VC02) and runs Nucleus PLUS. Its
  instruction set, a relative of VideoCore IV with its own encodings, is decoded, vector unit
  included: an emulator runs the firmware's routines and its MPEG-4, MP3, AAC and G.726 kernels
  bit-exact against independent references ([vc01-isa.md](vc01-isa.md)). The Nintendo MP3 Player's
  chip, unidentified before, is a TI TMS320VC5416 DSP running DSP/BIOS, booted from a standard C54x
  boot table ([c54x-hardware.md](c54x-hardware.md)).
- **Corrections to the prior art** ([prior-art.md](prior-art.md)), among them: the "firmware
  read-back" is a RAM test, 0x500 is 0x118 bytes, 0x905 has one parameter, 0x906 is
  PLAY_POSITION_IND_SET rather than a sleep command, the Micro sends the same 32-bit structures as
  the original, and the MP3 Player's "indices" are memory addresses.

## Documents

| file | contents |
|---|---|
| [protocol.md](protocol.md) | Registers, transfer primitives, firmware upload, every command and response record, data structures, Garage Games; Micro deltas; the Nintendo MP3 Player's protocol |
| [player.md](player.md) | The GBA program: boot, memory map, interrupts, scenes, firmware loading, video and audio pacing, controls, resume, sleep, Garage Games, Micro key file, MP3 Player structure |
| [firmware.md](firmware.md) | The firmware images and `playan.fup`, their contents, the loader's update path, the MP3 Player's C54x image |
| [vc01-isa.md](vc01-isa.md) | The VC01 instruction set: scalar encodings with evidence, coverage, the vector subset decoded so far |
| [c54x-hardware.md](c54x-hardware.md) | The Nintendo MP3 Player's DSP board as the firmware uses it: HPI, clocking, McBSPs (DAC, codec control and jack, SD card over SPI), DMA, timer; the audio result |
| [vc01-hardware.md](vc01-hardware.md) | The VC01's peripherals as the firmware uses them: interrupts, timer, GPIO, SD card over SPI, AC'97 audio, DMA, the clock |
| [micro.md](micro.md) | Play-Yan Micro: differences from the original, address by address |
| [nmp.md](nmp.md) | Nintendo MP3 Player: working notes, address by address |
| [decoders.md](decoders.md) | The decoders: what each reproduces of the firmware (its departures from the standards), exactness per codec and the oracle behind it, commands, inputs that stay undefined |
| [prior-art.md](prior-art.md) | shonumi's articles, documentation and GBE+, compared element by element |

## Real firmware

The real VC01 firmware runs in an emulator: a decoded instruction set ([vc01-isa.md](vc01-isa.md))
in a C core, and a model of the chip's peripherals ([vc01-hardware.md](vc01-hardware.md)), coupled
to a patched mGBA running the real GBA ROM. Neither side is simulated at a high level.

- The original Play-Yan boots to its menu on its own firmware: loader, RAM test, FIRMWARE_LIST,
  RESET_STANDBY, music image, folder walk. With `playan.fup` on the SD card, the loader reads and
  checks every block and starts the v1.11 image, and the GBA carries on with it.
- Music: browse, ID3 tags parsed by the firmware, play, fast-forward, rewind, pause, end of song
  and the move to the next song, all driven by the firmware's own records, including the position
  record once per second.
- Video: the firmware decodes the ASF and MP4 files itself. **Its thumbnails of the Garage Game
  files match the hashes in the GBA ROM's table**, an end-to-end check of the MPEG-4 decoder, the
  colour conversion and the 240x176 to 60x40 crop and scale (centred 240x160, then a 4:1 box
  filter), and the GBA ROM itself recognises them and starts the games. This needed one cartridge
  fact: the pixel window converts the firmware's RGB565 to BGR555 ([protocol.md](protocol.md) 8.2).
- Pacing: the cartridge paces everything by its AC'97 DAC, which needs no acknowledgement from the
  GBA. The headphone output runs at exactly the codec rate, the music speaker stream at
  28.43-28.48 blocks/s (16,384 / 576 = 28.44), video at 30.02 frames/s (ASF) and 29.97 (MP4) with
  the A/V offset within ±0.06 s and no drift ([protocol.md](protocol.md) 7.4).
- Sleep, the Micro's auto-play and the MP3 Player's background play and sleep all run; they need
  arming (L+R, or START held), and music-player sleep needs headphones, because every speaker block
  resets the countdown ([player.md](player.md) 8, 11). The headphone jack only switches the
  speaker stream; the GBA is never told ([protocol.md](protocol.md) 7.3).

Accuracy of the real firmware against independent decoders (identical across the images that
play each file; our decoders reproduce the firmware itself bit-exactly, [decoders.md](decoders.md)):

| codec / file | output | reference | SNR | max error | identical |
|---|---|---|---|---|---|
| MP3 `sample_music.mp3`, 44.1 kHz | headphone | minimp3 | 95.3 dB | 1 | 98.31 % |
| | | ffmpeg mp3float | 95.2 dB | 1 | 98.27 % |
| | | ffmpeg mp3 (fixed point) | 83.7 dB | 1 | 75.20 % |
| MP3 tones, 32 / 44.1 / 48 kHz | headphone | minimp3 | 77.0 / 82.8 / 76.5 dB | 1 | 95.07 / 98.72 / 94.55 % |
| MP3 chirp, 44.1 kHz | headphone | minimp3 | 94.1 dB | 2 | 97.90 % |
| MP3, all files | 8-bit blocks | the firmware's reduction of its own headphone PCM | - | 0 | 100 % |
| G.726-32, `sample_movie.asf` and the 13 Garage ASFs | headphone | Sun g72x (high nibble first) | exact | 0 | 100 % |
| | | ffmpeg adpcm_g726 | 23.8-53.7 dB | - | 25-99.7 % (not ITU-exact here) |
| AAC-LC, both MP4 samples | headphone | ffmpeg aac, aac_fixed, FAAD2 | 82.0 dB | 2 | all 3,201 frames within 2 LSB |
| MPEG-4 SP, `sample_movie.asf` | frames | ffmpeg (xvid IDCT) + the firmware's colour maths | 50.8 dB mean, 43.9 min | 2 levels | 97.31 % of pixels (I-frames 99.03 %) |
| MPEG-4 SP, Garage ASFs | frames | same | 54-64 dB mean | 2 levels | 97.7-99.9 % |
| MPEG-4 SP, MP4 samples | frames | same | 45.1 dB mean, 39.5 min | 2 levels | 89.8 % |

The MP3 residual is the ±1 LSB rounding of fixed-point synthesis. The video differences are IDCT
drift inside each GOP: intra frames are bit-exact against the firmware's own IDCT, which is not one
of ffmpeg's.
- The Micro boots and reads its key file through the firmware.
- **Nintendo MP3 Player:** the real DSP firmware runs on an emulated TMS320VC5416 under the real
  GBA ROM through upload, boot, mount, menu, ID3, playback, seek and the `meteor.fup` update. **Its
  decoded MP3 matches ffmpeg to within 1 LSB** (SNR 96.3 dB, 98.6 % of samples identical) on
  Nintendo's sample file ([c54x-hardware.md](c54x-hardware.md)). Its speaker stream runs at 28.40
  blocks/s. This run corrected four points of
  the static reading: 0x0030 and 0x0070, the `meteor.fup` entry flag and the directory entry type.

Claims tagged **[lle]** in the other documents come from these runs. The emulator is in
[tools/playyan](../../tools/playyan/README.md): the VC01 instruction-set model and its test suites,
the C cores of the VC01 and the C54x with their peripheral models, the mGBA patch (MPL-2.0) and the
harness, and the capture tools. It needs your own ROMs and `playan.fup`, from which
`tools/playyan/extract_firmware.py` builds the firmware images (checked against the hashes in
[firmware.md](firmware.md)). To reproduce the manifests in [verify/playyan](../../verify/playyan/README.md),
capture a file with `tools/playyan/emu/capture.py` and convert it with `make_manifests.py --out DIR`.

## Emulator check (high-level cartridge model)

All three ROMs were run in a patched mGBA against an emulated cartridge built from
[protocol.md](protocol.md): register behaviour in C inside the core, the firmware's behaviour
modelled at a high level in Python, media decoded with ffmpeg from a host folder acting as the SD
card. This model is `tools/playyan/emu/pyan_model.py` and `nmp_model.py`: `run.py` without `--lle`.

- All three boot to their menus and play video (Play-Yan, Micro: ASF and MP4) and music. The
  original's browse, seek, pause, frame step, step-back, brightness, volume, stop, resume, end of
  file, Garage Games 1-5, sleep and wake, and the "no SD card" screen all work; so do the Micro's
  key file and the MP3 Player's listing, ID3, seek and stop.
- All six VC01 firmware images reach cartridge memory byte-exact, and the MP3 Player's DSP image
  word-exact (91 sections, entry 0x42FC).
- Every register access site and all 26 command lengths that were exercised match the documents.
  Corrections from the traces, marked **[run]**: CTRL is read back by one pulse and bit 1 is a
  read-restart strobe; the dummy FIFO read is 0x81 reads; video 0x905 is relative seconds; the
  Garage Games send no video commands; an odd-length key file loses its last byte (harmless with CRLF
  endings, as in Nintendo's sample; fatal with LF endings); fast-forward and rewind lower the volume to 3/4.
- Choices the GBA code does not fix, so the model made them: the 240x176 to 240x160 crop and scale,
  the thumbnail scaling, frame timing at the source rate, brightness as a plain gain.

## Media

Nintendo's own sample files are still on nintendo.co.jp/n08/playan/ and /n08/play_yan_micro/
(`sample_movie.asf`, `mp4sample_movie.mp4` in two revisions, `sample_music.mp3`, the 13 Garage Game
`.ASF` files, `play_yanmicro.ini`). The ASF files are MPEG-4 Part 2 (`MP4S`) at 240x176 and 30 fps
with 8 kHz mono G.726 at 32 kbit/s; the MP4 sample is MPEG-4 Simple Profile at 240x176 with 44.1 kHz
stereo AAC-LC. The bundled converter was Panasonic's MediaStage Ver.4.2 for Nintendo (update
V4.2L310, 28 March 2005, raised the Play-Yan video bitrates from 480/352 to 736/480 kbit/s).

**MediaStage output.** Run under Wine, MediaStage's Play-Yan profile writes 240x176 MPEG-4 Simple
Profile with H.263 quantisation and no resync markers, at 480/352/224 kbit/s and 30/30/15 fps for
its high/normal/low settings (736/480/224 after the update, which changes only these numbers), with
G.726-32 mono at 8 kHz. The streams use I and P frames only, an I frame every second, half-pel
motion vectors, no 4MV, no dquant and no not-coded frames; ASF packets are 2,048 bytes, or 4,096
above about 512 kbit/s. Every G.726 payload is 400 bytes (800 samples), in MediaStage's files and
in all of Nintendo's. Nintendo's `sample_movie.asf` has MediaStage's high-setting headers and
Nintendo's profile ID; the Garage Game ASFs have the same header layout but Panasonic's profile ID,
so they came from the same toolchain with another profile. 40 conversions of our own test clips,
before and after the update, play to the end on the original, v1.11 and Micro firmware in the
emulator, and the decoders match the firmware on all 120 (frames, thumbnails, headphone and 8-bit
audio). The encoder is not deterministic (two conversions of one clip differ in the video bits),
so these files are not part of `verify/`.

## Open issues

- The ground truth is our emulator, not hardware. Its assumptions (CPU model, DAC rate, peripheral
  model, the pixel window's colour swap) are listed in [verify/playyan](../../verify/playyan/README.md).
- Whether a key selected in KEYCNT ends BIOS Stop when the keypad interrupt bit is clear: the
  Micro's auto-play and the MP3 Player's background play and sleep enter Stop with KEYCNT = 0x0008,
  and the cartridges send no record that would wake the GBA ([player.md](player.md) 12).
- The roles of CTRL bit 4 and the GO register ([player.md](player.md) 12).
- Inputs on which the firmware's output is not a function of the file (mixed blocks on the VC01
  music image, oversized G.726 payloads, a few corrupt-stream cases): [decoders.md](decoders.md).
- The VC01's real clock rate is unknown (the firmware's own PLL formula gives 8-16 MHz, which looks
  too slow), so the emulator's instruction rate is a free choice.
