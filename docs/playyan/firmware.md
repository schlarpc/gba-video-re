# Play-Yan family: the cartridge firmware

The GBA program uploads the media chip's firmware from its own ROM at every boot
([protocol.md](protocol.md) 5). This document covers those images as the chip receives them, the
update file `playan.fup`, what the images contain, how far the VC01 instruction set is decoded, and
the Nintendo MP3 Player's DSP image. It is a first pass. Decoding the VC01 instruction set and
emulating the firmware are in progress and are not part of this document yet.

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
  then a directory entry count at +0x3E (2).
- 16-byte directory entries: data offset (+0), length (+4), and type bytes. Entry 0 is the video
  image, entry 1 the music image.
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

## 3. The VC01 instruction set (partly decoded)

No VC01 instruction-set documentation is public. The VideoCore IV encoding, which has been reverse
engineered, does **not** fit: decoded with its length rules from about 250-470 known function starts
per image, it lands on independently confirmed instruction starts only 49.6-52.4 % of the time,
which is chance. The VC4 return opcode 0x005A occurs only 14-28 times per image.

A length rule derived from the images themselves lands on 100 % of 1,300-3,000 such anchors per
image. Instructions are sequences of little-endian 16-bit words, 16, 32 or 48 bits long.

| encoding | instruction | status |
|---|---|---|
| `2f6d` + 32 bits | `mov rd, #imm32` (48-bit) | confirmed |
| `2f2d` + 32 bits | gp- or pc-relative address load (48-bit) | confirmed |
| 0x1F80-0x1FFF + 16 bits | `bl`, 23-bit offset | confirmed |
| 0x1080-0x10FF + 16 bits | `bl`, short | confirmed |
| 0x1E00-0x1E7F, `3eXX` | `b` | confirmed |
| 0x1000-0x107F + 16 bits | conditional branch (condition field not yet located) | confirmed |
| `2e00` + 16 bits | `cmp r0, #imm16` | confirmed |
| `4430` | `rts` | confirmed |
| `f0xx` / `e0xx` | push / pop | probable |

What carries over from VideoCore IV is the register roles (r24 gp, r25 sp, r31 pc), the
16/32/48-bit instruction lengths and pc-relative address loads; the bit layouts differ. A 2003 ARC
brochure on Alphamosaic's use of the MetaWare toolchain shows VC01-era disassembly with VideoCore
IV-style mnemonics (`addcmpbge`, `ld %r1,(sp+180)`, `bl vclib_memcpy`), which fits this picture.
Arithmetic, logic, load/store and condition encodings, and any vector instructions, are still open.

## 4. Nintendo MP3 Player: a TI TMS320C54x DSP

The GBA loader (0x08063D30 in the boot program) uploads a standard **C54x boot table** from ROM
0x0A8F34 **[code]**: keyword 0x08AA, wait-state settings 0x7FFF and 0xF800 (SWWSR, BSCR), entry
point 0x42FC, then 91 sections ([protocol.md](protocol.md) 11.3). The image confirms the
architecture **[fw]**: the interrupt vectors at 0x80 are far branches, 0xF495 (the C54x `NOP`) is
the most common word, and 0xF4E4 (far return) is common. Some sections load into extended program
pages, so it is a C54x with extended program memory, of the VC5409/VC5416 class **[inferred]**.
The image's codec has not been examined.

## 5. Open questions

- How the loader selects and checks a `.fup` section, what an AES-like GF(2^8) table in the loader
  is for, and what the loader does with RESET_STANDBY after a FIRMWARE_DOWNLOAD
  ([player.md](player.md) 3.1).
- The mode-0x28 read of VC 0x80000 before every upload, and the host-interface registers at
  VC 0x10000000.
- The rest of the VC01 instruction set (section 3).
- How the firmware fits 240x176 video into the 240x160 frames it returns.
