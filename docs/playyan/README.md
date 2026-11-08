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
images are extracted and characterised, but their codecs are not reimplemented, so there is no
decoder, no manifests and no verification against the cartridge for this family yet. Everything
here comes from static analysis: none of it has yet been checked by running the ROMs.

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
  instruction set is not VideoCore IV's encoding; it is partly decoded. The Nintendo MP3 Player's
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
| [firmware.md](firmware.md) | The firmware images and `playan.fup`, their contents, the VC01 instruction set as far as it is decoded, the MP3 Player's C54x image |
| [micro.md](micro.md) | Play-Yan Micro: differences from the original, address by address |
| [nmp.md](nmp.md) | Nintendo MP3 Player: working notes, address by address |
| [prior-art.md](prior-art.md) | shonumi's articles, documentation and GBE+, compared element by element |

## Media

Nintendo's own sample files are still on nintendo.co.jp/n08/playan/ and /n08/play_yan_micro/
(`sample_movie.asf`, `mp4sample_movie.mp4` in two revisions, `sample_music.mp3`, the 13 Garage Game
`.ASF` files, `play_yanmicro.ini`). The ASF files are MPEG-4 Part 2 (`MP4S`) at 240x176 and 30 fps
with 8 kHz mono G.726 at 32 kbit/s; the MP4 sample is MPEG-4 Simple Profile at 240x176 with 44.1 kHz
stereo AAC-LC. The bundled converter was Panasonic's MediaStage Ver.4.2 for Nintendo (update
V4.2L310, 28 March 2005, raised the Play-Yan video bitrates from 480/352 to 736/480 kbit/s).

## Open issues

- Nothing has been run yet: an emulated cartridge in mGBA that boots the three ROMs, to check the
  protocol documents against a running program, is in progress.
- The VC01 instruction set beyond the forms in [firmware.md](firmware.md) 3, and with it the
  codecs, the 240x176 to 240x160 fit, the brightness processing and the 8-bit audio conversion.
- The update path after FIRMWARE_DOWNLOAD, the units of the video seek (0x905), how the cartridge
  learns that a frame or audio block has been read, and the roles of CTRL bit 4 and the GO register
  ([player.md](player.md) 12).
- Ground truth for decoded output needs either an emulator of the firmware or captures from real
  hardware.
