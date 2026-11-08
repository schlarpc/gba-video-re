# Play-Yan family: the cartridge firmware

The GBA program uploads the media chip's firmware from its own ROM at every boot
([protocol.md](protocol.md) 5). This document covers those images as the chip receives them, the
update file `playan.fup`, what the images contain, how far the VC01 instruction set is decoded, and
the Nintendo MP3 Player's DSP image. The instruction set itself is in [vc01-isa.md](vc01-isa.md).

Tags: **[fw]** read from the firmware images; **[code]** from the GBA code; **[inferred]** our
reading. Firmware addresses are addresses in the chip's own memory ("VC addresses").

## 1. Images

### 1.1 Play-Yan (raw in ROM)

| image | ROM offset | length | sha1 | load | role |
|---|---|---|---|---|---|
| fw0 | 0x029710 | 0x31B48 | ce983ce7bac58ff438806247702438ec7b97d742 | VC 0 | video player (`vc01pp_asf`) |
| fw1 | 0x05B258 | 0x3F66C | ede92003b9aec9d18d17707d95cb61bb21444922 | VC 0 | music player (`vc01pp_mp3`) |
| fw2 | 0x09A8C4 | 0x123C0 | e351f78d491c60c0ef080475ff255f4519fc82b5 | split, below | loader and updater (`vc01pp_loader`) |

fw2 is uploaded in three parts **[code]**: bytes 0x0-0x1FF to VC 0, a 6-byte record to VC 0x200,
and bytes 0x200-0x123BF to VC 0xC0200. The record is a jump to 0xC0200 in the VC01 encoding of
section 3 **[fw]**. So the loader is linked at base 0xC0000, and execution starts at VC 0x200 in
every image. The bus carries a little-endian byte stream with no swapping or packing.

The upload ends with a write of 0xA5A50032 to VC 0x10000400. The firmware itself writes 0xA5A58020
to the same address **[fw]**, so it is a chip control register whose upper half is a 0xA5A5
password **[inferred]**.

Version strings date the ROM builds to 28 January 2005.

### 1.2 Play-Yan Micro (LZ77 chunks in ROM)

Each image is a NUL-terminated list of ROM pointers to GBA-BIOS LZ77 chunks that decode to 0x400
bytes each ([micro.md](micro.md)). Upload and load addresses are as in 1.1.

| image | pointer list | chunks | decoded length | sha1 (decoded) | role |
|---|---|---|---|---|---|
| A | 0x0E7898 | 294 | 0x4975C | f7533448ece8db54718ed290110168831a9c3a3e | video (ASF and MP4) |
| B | 0x0E7D34 | 272 | 0x43D84 | 30c85e025df51bbc1e52120e788d6784967bbfdf | music |
| C | 0x0E8178 | 96 | 0x17FD0 | 5800cbb96a93f30067e778483f2e126e5d9536a2 | loader |

Version strings date the Micro builds to 20 August 2005.

### 1.3 `playan.fup`

Nintendo's only firmware update (567,695 bytes, sha1 79289f11d2c1b85d6f8363000d3c7916fdc2ada7,
still downloadable from nintendo.co.jp/n08/playan/firmware/). It adds MP4 video with AAC audio, and
stereo sound for video, to the original Play-Yan. The loader reads it from the SD card itself; the
GBA never touches the file.

- A 0x40-byte header: the text `Nintendo Playan firmware update program\r\n2005/07/13` and 0x1A,
  the entry count at +0x3E (2) and a check byte at +0x3F.
- 16-byte entries (layout in section 4). Entry 0 is the video image (kind 1), entry 1 the music
  image (kind 2).
- The data is in 0x200-byte blocks, each followed by a check byte that makes the block's byte sum
  1 mod 256. All 1,106 blocks pass.

| section | length | sha1 (check bytes removed) | content |
|---|---|---|---|
| 0 | 306,044 | 084ad575a77dc8a2cbab876b7feb4c1548562a58 | video player v1.11, Jul 13 2005 |
| 1 | 260,448 | f04e7558b07cca2b72ef6a558e3aadc0339e0ef5 | music player v1.11, Jul 12 2005 |

**Both sections are complete images loaded at VC 0, not patches.** The header, the directory and
the check-byte rule are the same as those the Nintendo MP3 Player's GBA loader checks for
`meteor.fup` ([protocol.md](protocol.md) 11.6), so the two update formats share one design.

## 2. Contents

- **Chip.** Every build contains the strings `vc01pp_asf`, `vc01pp_mp3` and `vc01pp_loader`: the
  chip is an Alphamosaic VC01. Wikipedia lists the Play-Yan under the VC02; the firmware does not
  support that.
- **Operating system.** Nucleus PLUS (its `SEMA`, `TASK`, `HISR` and `EVNT` object IDs appear as
  constants) with Nucleus FILE for the SD card.
- **Diagnostics.** A debug shell with many format strings, an SD card register dumper, and AC'97
  codec registers (the headphone path).
- **Message names.** A debug function in the loader (VC 0xC6E30) maps every command and record ID
  to a name: 48 IDs in the original, 52 in the Micro. [protocol.md](protocol.md) uses these names.
- **Codec tables.**
  - Video images: all G.726 rate tables (the ASF audio is G.726), the MPEG-4 scan orders and the
    default quantiser matrices.
  - AAC tables appear only in the Micro's video image and in the `.fup` video section, which matches
    MP4 support being added by the update.
  - Music images: MP3 header tables.
- **Locations, from the functions that reference those tables:** MPEG-4 decoding in fw0 around
  VC 0x2ED2-0xC8BA; MP3 decoding in fw1 around VC 0x20CEC-0x23FD0; AAC in the Micro's A image
  around VC 0xD4C4 and 0xD6A4. The command dispatchers are at about VC 0xC82F8 (loader), 0x127EC
  (fw0), 0x208EA (fw1) and 0x1F3FC (Micro A).

## 3. The VC01 instruction set

Decoded in [vc01-isa.md](vc01-isa.md). In short: a relative of VideoCore IV with the same register
roles (r24 gp, r25 sp, r26 lr, r31 pc), condition codes and several 32-bit layouts, but its own ALU
numbering and load/store forms, so VideoCore IV tools do not decode it. 99.99 % of the reachable
code in every image decodes to a known scalar instruction or a vector instruction of known length.
An emulator built from the decoding runs real firmware routines bit-exact against independent
references: 64-bit and soft-float arithmetic, sprintf, FAT and Nucleus code, the `.fup` block check,
the whole firmware download, and the G.726 codec (6,000 cases against Sun's reference code). The
vector unit, 8 % of the video code and 44 % of the music code, is decoded too: its MPEG-4, MP3,
AAC and pixel kernels run bit-exact against references, and the firmware's own decode reproduces
the Garage Game thumbnail hashes.

## 4. The update path (loader)

Traced in the loader and checked by running its FIRMWARE_LIST and FIRMWARE_DOWNLOAD code in the
emulator on the real `playan.fup` **[fw]**:

- Commands arrive in a host-interface interrupt that copies the request from VC 0xFF020 and
  queues it (16 entries); the loader's main task handles FIRMWARE_LIST, FIRMWARE_DOWNLOAD,
  RESET_STANDBY, SLEEP, OPERATION_SOUND and the two alerts, and ignores everything else.
- **FIRMWARE_LIST (0x8000)** opens `playan.fup` (the Micro's loader opens `play_yanmicro.fup`),
  checks the header text (41 bytes), the header check byte and each entry's check byte, and replies
  with status 0 and the OR of the kind bytes of all valid entries whose platform byte is 1 (the
  Micro's loader requires 2). Status 5 = file not found, 4 = bad header or table.
- **FIRMWARE_DOWNLOAD (0x8001)**: the parameter's low byte is a **kind mask** (1 video, 2 music),
  not an index. The loader takes the first valid entry whose kind matches, reads blocks 1 onward to
  VC 0x200 upward, checking every block, then reads block 0, disables interrupts, copies it to VC 0
  and jumps to 0x200. Writing the vectors last means a failed download leaves the loader running.
  **FIRMWARE_DOWNLOAD_CONFIRM is sent only on failure** (status 4 or 5). On success the new image
  sends its own VC_STARTUP_INDICATION.
- **RESET_STANDBY (0x800000)** only switches a power control off and confirms (0x40800000). It
  does not reset or reload anything. The downloaded images handle it the same way, and answer
  FIRMWARE_LIST with "not found" and FIRMWARE_DOWNLOAD with an error, so once an update image runs,
  only a reset and a new loader upload by the GBA can change images.

Entry layout, as the loader reads it: offset (+0), payload size without check bytes (+4), platform
(+8, 1 = Play-Yan, 2 = Micro), kind mask (+9), unused (+10-14), check byte (+15). The header's check
byte is at +0x3F.

## 5. MP3 sample rates and the 8-bit speaker stream

The music firmware accepts **MPEG-1 Layer III only** (32, 44.1 and 48 kHz), as Nintendo's
specification says. Its header check (0x20D74 onward in the original's music image) needs the
12-bit sync, so MPEG-2.5 frames (8-12 kHz) never sync (error 4), and it requires the MPEG-1 ID bit,
so MPEG-2 frames (16-24 kHz) fail (error 2). In the emulator such files are left out of the list
(the GBA then shows "no music files") or end at once when played. The decoder nevertheless holds
tables for all nine MPEG rates **[fw]**.

The scalar routine at 0x3185E makes the 8-bit stream for the GBA speaker when no headphones are
plugged in: volume (s x vol / 32, clamped), then the high byte of each sample, no dither or filter,
in 576-sample blocks. Above 16,384 Hz it decimates by nearest neighbour to 16,384 samples/s. For
sources at or below 16,384 Hz it repeats samples by a phase test with the wrong scale (a shift of
13 instead of 14 at 0x21BFE), so a source of N Hz between 8,192 and 16,384 Hz comes out at
2N - 8,192 samples/s (an 11,025 Hz tone would play 1.18x fast), and lower rates at a flat 8,192.
This is a real bug, measured by running the routine in the emulator, but **unreachable**: only the
disabled low-rate formats could feed it. It is unchanged in the Micro's music image and in the
v1.11 update. **[fw]**

## 6. Nintendo MP3 Player: a TI TMS320C54x DSP

The GBA loader (0x08063D30 in the boot program) uploads a standard **C54x boot table** from ROM
0x0A8F34 **[code]**: keyword 0x08AA, wait-state settings 0x7FFF and 0xF800 (SWWSR, BSCR), entry
point 0x42FC, then 91 sections ([protocol.md](protocol.md) 11.3). The image confirms the
architecture **[fw]**: the interrupt vectors at 0x80 are far branches, 0xF495 (the C54x `NOP`) is
the most common word, and 0xF4E4 (far return) is common. Some sections load into extended program
pages, so it is a C54x with extended program memory, of the VC5409/VC5416 class **[inferred]**.
The image's codec has not been examined.

## 7. Open questions

- What an AES-like GF(2^8) table in the loader is for.
- The mode-0x28 read of VC 0x80000 before every upload, and the host-interface registers at
  VC 0x10000000.
- A few vector details no kernel tells apart ([vc01-isa.md](vc01-isa.md), Open).

