# Play-Yan, Play-Yan Micro and Nintendo MP3 Player (partial)

Nintendo's AGS-006 cartridges play the user's own video and music from an SD card. They are not
GBA Video cartridges: they carry no content, and the decoding is done by a media chip on the
cartridge, not by the GBA. The GBA program uploads that chip's firmware at boot, sends it commands,
and copies finished frames and audio blocks out of it.

| cart | No-Intro (main GBA DAT) | sha1 (1 MiB image) | chip | media |
|---|---|---|---|---|
| Play-Yan (Japan), 2005; also the iQue MP4 Player (same ROM) | `Play-Yan (Japan)` | 7f432bbf6f0e5e1b8324c7d6bb1d81a254a6e870 | Alphamosaic VC01 | ASF (MPEG-4 Part 2 + G.726), MP3; MP4 (AAC) with `playan.fup` |
| Play-Yan Micro (Japan), 2005 | `Play-Yan Micro (Japan)` | 17b5f7ad459448b447fa94617d09886cd4f9c554 | Alphamosaic VC01 | ASF, MP4, MP3 |
| Nintendo MP3 Player (Europe), 2006 | `Nintendo MP3 Player (Europe) (En,Fr,De,Es,It)` | b7c517f1cb5860fec8beaa4e819bcffa8ce95a5b | TI TMS320C54x DSP | MP3 |

Common dumps are 4 MiB, the 1 MiB image mirrored four times; the first 1 MiB matches No-Intro.

**This lane is partial.** The GBA side is documented from the code of all three ROMs. The firmware
images are extracted and their instruction set decoded, but their codecs are not reimplemented
(except G.726, checked against Sun's reference code), so there is no decoder, no manifests and no
verification of decoded output for this family yet. Everything
here comes from static analysis of the code, checked by running all three ROMs in mGBA against an
emulated cartridge built from [protocol.md](protocol.md) (below).

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
  instruction set, a relative of VideoCore IV with its own encodings, is decoded: 99.99 % of the
  reachable code, with an emulator that runs firmware routines bit-exact against independent
  references, including the G.726 codec ([vc01-isa.md](vc01-isa.md)). The vector unit is partly
  decoded. The Nintendo MP3 Player's
  chip, unidentified before, is a TI TMS320C54x DSP booted from a standard C54x boot table.
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
| [vc01-hardware.md](vc01-hardware.md) | The VC01's peripherals as the firmware uses them: interrupts, timer, GPIO, SD card over SPI, AC'97 audio, DMA, the clock |
| [micro.md](micro.md) | Play-Yan Micro: differences from the original, address by address |
| [nmp.md](nmp.md) | Nintendo MP3 Player: working notes, address by address |
| [prior-art.md](prior-art.md) | shonumi's articles, documentation and GBE+, compared element by element |

## Real firmware

The real VC01 firmware runs in an emulator: a decoded instruction set ([vc01-isa.md](vc01-isa.md))
in a C core, and a model of the chip's peripherals ([vc01-hardware.md](vc01-hardware.md)), coupled
to a patched mGBA running the real GBA ROM. Neither side is simulated at a high level.

- The original Play-Yan boots to its menu on its own firmware: loader, RAM test, FIRMWARE_LIST,
  RESET_STANDBY, music image, folder walk. With `playan.fup` on the SD card, the loader reads and
  checks every block and starts the v1.11 image, and the GBA carries on with it.
- Music: browse, ID3 tags parsed by the firmware, play, fast-forward, rewind, pause, end of song
  and the move to the next song, all driven by the firmware's own records. The audio content is
  not yet right, because the MP3 synthesis is vector code the emulator cannot run yet.
- Video: the video image loads and lists files, but the thumbnail request is never answered: the
  MPEG-4 bitstream unpacker is vector code. Once it runs, the 13 Garage Game thumbnail hashes are a
  ready-made end-to-end check.
- The Micro boots and reads its key file through the firmware.

Claims tagged **[lle]** in the other documents come from these runs. The emulator,
its mGBA patch and the traces are local and not in this repository.

## Emulator check (high-level cartridge model)

All three ROMs were run in a patched mGBA against an emulated cartridge built from
[protocol.md](protocol.md): register behaviour in C inside the core, the firmware's behaviour
modelled at a high level in Python, media decoded with ffmpeg from a host folder acting as the SD
card. The emulator, its patch and the traces are local and not in this repository.

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

## Open issues

- The high-level cartridge model's ffmpeg thumbnails never match the Garage Game hashes (about
  2,300 crop, sampling and rounding variants tried); the real firmware's thumbnail path is blocked
  on the vector unit. Not yet exercised: music-mode sleep, the Micro's auto-play
  (0x4000/0x4001), the MP3 Player's background play and sleep, and the update path with a real
  `.fup`.
- The VC01 vector instructions beyond the G.726 subset, and with them the MPEG-4, MP3 and AAC
  codecs, the 240x176 to 240x160 fit, the thumbnail scaling, the brightness processing and the 8-bit
  audio conversion.
- How the cartridge paces frames and audio blocks that
  the GBA never acknowledges, and the roles of CTRL bit 4 and the GO register
  ([player.md](player.md) 12).
- The VC01's real clock rate is unknown (the firmware's own PLL formula gives 8-16 MHz, which looks
  too slow), so the emulator's instruction rate is a free choice.
