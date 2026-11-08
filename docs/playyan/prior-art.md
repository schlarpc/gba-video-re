# Play-Yan family: prior art and how this work compares

Sources:
- **[A1]**, **[A2]** shonumi, "Edge of Emulation: Play-Yan", parts 1 and 2
  (https://shonumi.github.io/articles/art32.html, https://shonumi.github.io/articles/art35.html)
- **[D]** shonumi, Dan Docs: Play-Yan section (register map, firmware read/write, SD data,
  commands, Game Pak IRQ formats, thumbnails and frames, Garage Games, key file, firmware update)
  and Nintendo MP3 Player section (https://shonumi.github.io/dandocs.html)
- **[G]** GBE+ emulator, `src/gba/play_yan.cpp`, `nmp.cpp`, `mmu.h`, `common.h` (GPLv2; read for
  comparison only, no code taken), commit 8b748c0 (2026-10-01)
- **[fw]** this repository's firmware analysis ([firmware.md](firmware.md)). It is used only for the
  firmware's own message names and image facts, and is marked [fw-names] in the other docs.
- **[lle]** the real cartridge firmware running in this repository's emulator, as in
  [protocol.md](protocol.md).

GBE+ emulates at a high level: it returns the records and data the ROM expects, and it does not
model the cartridge's memory. Most differences below come from that. Its data reads ignore the
VC addresses the ROM takes from the records. Where GBE+ behaviour is cited it is to show what the
ROM requires, not to criticise the emulation.

## Bus and registers

| Element | Prior art | Agrees? | Confirmed / corrected / new here |
|---|---|---|---|
| Register set at 0x9000000 (PY_INIT_DATA, PY_STAT) and 0xB000000 (UNK_00, UNK_02, DATA_ADDR, DATA_MODE, DATA_PARAM, FIRM_STAT) | [D][G] | Partly | **Corrected: one register file, two views.** 0x09000000 and 0x0B000000 are the same cartridge address seen with WS0 (4/2) and WS1 (2/1) timing. "PY_STAT" 0x9000006 is the read side of "DATA_MODE" 0xB000006. "PY_INIT_DATA" 0x9000000 is the data port 0xB000000 (mode 0x28 FIFO read). The boot code writes the same registers through the 0x09 view (protocol.md 1-2) |
| PY_STAT bit 7 = busy/ready | [D][G] | Yes | Extended: polled before each block write chunk, before each FIFO halfword, and after reset. The ROM pulses reset again every 0x8000 polls and hangs after 0x100000 |
| PY_DATA_ADDR: 32-bit, low half then high half | [D][G] | Yes | |
| UNKNOWN REGISTER 02 "mostly written 0" | [D] | Yes | Extended: written 0 after every address load, and alone in the IRQ acknowledge; a transaction start/commit **[inferred]** |
| UNKNOWN REGISTER 00 | [D]; [G] treats a 0xA5A5 halfword written there as "raise IRQ" | Partly | Corrected: it is the 32-bit data port for mode 0x70 (two halfwords, low first). 0xA5A5 is the high half of 0xA5A50032 written to VC 0x10000400 (3.7). The GBA also writes 0x00000000 through it in every IRQ acknowledge |
| PY_DATA_MODE 0x68 write / 0x78 read; 0x28 serial | [D][G] | Yes | New: 0x70 = single 32-bit write (kick, sync word, acknowledge) |
| PY_DATA_PARAM values 0x08, 0x09, 0x0A, 0x0C | [D]; [G] keys firmware vs SD reads on 0x09/0x0A | **No** | Corrected: CTRL is a bit field written from a shadow: bit 0 read request, bit 1 IRQ ack pulse, bit 2 doorbell pulse, bit 3 active-low chip reset, bit 4 pulsed once at boot. 0x0A is just the post-reset idle value (bits 1 and 3). 0x09/0x08 are bit 0 set and cleared after the first IRQ has cleared bit 1. 0x0C/0x08 is the doorbell pulse. No value selects "firmware" or "SD" |
| PY_FIRM_STAT bit 4 | [D][G] | Yes | Named here: read data ready (RSTAT). The ROM gives up after 9999 polls and logs error 7 after 1000 |
| Windows 0xB000100-0x2FF (in), 0x300-0x4FF and 0x500-0x6FF (out) | [D][G] | Yes | Extended: 0x500 is used only for frames and thumbnails and only it gets streaming mode. 0x300 is used for records, lists, ID3, audio and the RAM test |
| Firmware write "data into 0xB000300 through 0xB0004FF" | [D] (firmware write procedure) | No | Corrected: the write window is 0xB000100-0x2FF (16-bit DMA3). [fw] reached the same conclusion |
| Register 0x9000022 | none | | New: set to 1 only during the unused WS0 boot upload |
| Register 0x9000010 / 0x9000004 / 0x9000002 / 0x9000100 | none | | New: the same CTRL/ADDR/GO/window, written through the WS0 view by the unused boot scene |
| Reset sequence | none | | New: reset pulse, dummy 256-byte FIFO read of VC 0x80000, second pulse, wait for ready, write the poll count to VC 0xFF080 (protocol.md 3.2) |
| Bus lock and deferred IRQ | none | | New: transfers are protected by a lock; an IRQ during a transfer is replayed by hand afterwards (3.1) |

## Firmware

| Element | Prior art | Agrees? | Confirmed / corrected / new here |
|---|---|---|---|
| Firmware uploaded from ROM on every boot | [A1][D] | Yes | Extended: **three** images: video fw0 (ROM 0x029710, 0x31B48), music fw1 (0x05B258, 0x3F66C) and loader fw2 (0x09A8C4, 0x123C0). The loader is split: 0x200 bytes to VC 0, a 6-byte jump record to VC 0x200, the rest to VC 0xC0200 |
| "512 bytes at a time"; address range 0x00000-0xEFFFF | [D] | Partly | Chunk size confirmed (0x200 bytes per DMA burst). Extended: ROM-to-RAM staging in 0x400 or 0x800 bytes; incremental mode moves 16 KiB per frame. Destinations are VC 0 and 0xC0200 only |
| "Firmware read: 0xA00 bytes at a time, re-addressed after each 0xA00" | [D] | **No** | Corrected: that traffic is the boot **RAM test** (protocol.md 5.3): a 511-byte pattern is written to VC 0xEC400-0xFEFFF and read back in 0xA00-byte blocks. The GBA never reads firmware back. Also new: the test's pass/fail switches frame reads to streaming mode, and its pass-count argument is ignored because of a reused loop register |
| Write 0xA5A50032 to 0x10000400 raises an IRQ; response 0x80000100 | [D][G] | Yes | Named VC_STARTUP_INDICATION [fw]. Extended: rec[1] is a status selecting the boot error screens. The same kick follows every upload |
| "This IRQ is not generated when exiting Sleep" | [D][G] | Partly | Refined: after 0x10000 sleep the GBA writes the kick itself from a 30-frame task (21 frames after the wake key on the real firmware, because the task's timer starts before Stop) and waits for **0x80010001** (WAKEUP_INDICATION [fw]), not 0x80000100. The firmware answers 0.48 s after the kick, without rebooting **[lle]** |
| Firmware switch when entering the Music or Video menus | [D] | Yes | Explained: the music and video firmwares are separate images. Every switch reloads fw0 or fw1 after 0x800000 RESET_STANDBY. fw2 is reloaded only if the update file has a part for the target media |
| Update check 0x8000; "if both bit 0 and bit 1 are set, load" | [D] | **No** | Corrected: rec[2] of 0x40008000 is a parts mask, bit 0 = video, bit 1 = music, tested separately. 0x8001's parameter (1 or 2) names the part |
| 0x8001 "Load firmware", reply 0x80000100 | [D] | Partly | Corrected: the reply is 0x40008001 (FIRMWARE_DOWNLOAD_CONFIRM [fw]), after which the GBA sends 0x800000. 0x80000100 comes later, when a firmware starts |
| 0x800000 unknown, 2 IRQs | [D] | Partly | Named RESET_STANDBY [fw]. Its reply 0x40800000 makes the GBA upload fw0 or fw1 from ROM |
| Update "patches a small portion of default firmware" | [A2][D] | No | The GBA never patches firmware. The only small write is the loader's 6-byte jump record. [fw] finds that `playan.fup` holds two complete images |
| `playan.fup` name in firmware | [D] | Yes | It is in fw2 (loader) only |

## Commands

| Element | Prior art | Agrees? | Confirmed / corrected / new here |
|---|---|---|---|
| Command layout: ID + 32-bit parameters, LSB first, to 0xB000100 | [D][G] | Yes | Extended: written to **VC 0xFF020** as a little-endian struct, then the doorbell (CTRL bit 2). Exact lengths per command (protocol.md 6). Bytes after string terminators are uninitialised stack, which is the "unknown data after the string" |
| "PY_DATA_ADDR 0xFF020 is the command address" | [D][G] | Yes | |
| 0x500 sends 0x218 bytes, the last 0x18 wrap to 0xB000100 | [D] | **No** | Corrected: 0x500 is **0x118** bytes: 60, 40, 0, 0, name at +0x14. That fits in one 0x200 chunk. A command over 0x200 bytes would be re-addressed to 0xFF220, not wrapped |
| 0x300 brightness 0x101-0x2C0 in 0x40 steps | [D] | Partly | Range confirmed. Corrected: six values from a table, 0x101, 0x140, 0x180, 0x1E0, 0x240, 0x2C0, not evenly spaced. Firmware name VIDEO_MODE_SET [fw] |
| 0x400 unknown | [D] | | Extended: [0x400, 1, 0], sent on entering every audio scene, no reply handled. Firmware name AUDIO_MODE_SET [fw]. Word 1 switches the 8-bit speaker stream on **[lle]** |
| 0x700 width 240, height 160 | [D] | Yes | Extended: the video browser sends 60 x 40 for its live preview, and the cartridge then delivers 60x40 frames |
| 0x905 seek: param 1 speed, param 2 absolute position when param 1 = 0 (frame number) | [D]; [G]: ≤0x10 forward step, ≥0xFFF0 back step, else ×30 frames | **No** | Corrected: in the original ROM 0x905 has **one** 32-bit parameter (8 bytes total). Music: the absolute trackbar target 0..0x6400 (agrees with [D]). Video: a signed step derived from duration, hold time and elapsed frames (player.md 5.3), the resume time in seconds (5.4), or 0 / -1 in step-back. No second parameter is ever sent |
| Resume restarts "~5-6 s" before the exit; needs ≥6 s of playback | [A2][D] | Yes | Exact rule: resume = last whole second − 5, stored only once the clock passes 5 s; 6-9 s gives 5 s. Per-index table of 99 entries. On resume the GBA mutes, sends 0x905(seconds) and 0x904 on the first non-audio record, and unmutes 10 records later |
| 0x903 advance frame, always 1; step-back via unpause + seek + pause | [D] | Yes | Detailed: hide frames, 0x904, 10 frames later 0x905(0), 15 later 0x905(-1), show frames, 0x902 |
| 0x906 "Sleep during playback, 0 = sleeping, 1 = awake" | [D][G] | Partly | The values agree, but the firmware name is PLAY_POSITION_IND_SET [fw]. The GBA turns position indications off before it stops itself and on after it wakes. 0x906(1) is also sent before every 0x800 |
| 0xB00 volume 0..0x38 | [D] | Yes | Extended: level × 56 / 36 from a 0..36 setting (default 18); 0 for mute. The setting is not applied to PCM on the GBA |
| 0xD00 bass boost level 0..0xF; 0xD01 bass on/off 0x800F / 0x8F0F | [D][G] | **Partly** | Values confirmed. Corrected labels: 0xD00 is AUDIO_3D_ENHANCEMENT (the "Wide" setting 0..6 × 15 / 6) and 0xD01 is TONE_CONTROL (bass; setting 1 → 0x800F, 0 → 0x8F0F) [fw]. The Micro key file names them AudioWide and AudioBass |
| 0x2000 play sound effect, no parameters | [D] | No | Corrected: [0x2000, sound ID, volume], volume = clamp(level × 63 / 36, 16, 63) |
| 0x10000 sleep, reply 0x80010001 when awake | [D] | Partly | The GBA enters Stop right after 0x10000 without waiting. 0x80010001 answers the wake kick (above) |
| 0x8000/0x8001/0x800000/0x3000-3003 names | [D] | | Firmware names [fw]: FIRMWARE_LIST, FIRMWARE_DOWNLOAD, RESET_STANDBY, FILE_OPEN/READ/SEEK/CLOSE |

## Responses and data

| Element | Prior art | Agrees? | Confirmed / corrected / new here |
|---|---|---|---|
| IRQ data read: PARAM 8, MODE 0x78, ADDR 0xFF000, PARAM 9, wait bit 4, PARAM 8, read 0xB000300-31F | [D] | Yes | Extended: preceded by the CTRL bit-1 acknowledge pulse and followed by a 32-bit zero write (mode 0x70) at the latched address. Records go into a 16-entry ring (overflow drops the record unacknowledged) and are processed later by a dispatcher with per-ID callbacks |
| 0x40/0x80 prefix: response vs self-generated | [D] | Yes | |
| 0x200 reply: bytes 4-7 = 1 or 2 | [D]; [G] leaves irq_data[1] = 0 | **No** | Corrected: rec[1] is the **VC address of the list**. The GBA reads 0x67AC bytes from it. GBE+ works because its read path ignores the address |
| SD data read with PY_DATA_ADDR = 0 | [D] | No | Corrected: the address comes from the record (list, ID3, audio, frame, thumbnail). Where [D]'s procedures show 0, the value most likely came from a session where no real address was supplied [inferred] |
| List: +4 count, 268-byte entries, name 0x100, +0x100 "hash", +0x108 type (bit 0 video, bit 1 music) | [D] | Mostly | Confirmed size, count and name. +0x100-0x107 are not read by the GBA. The ROM tests type bit 1 (music) and 0 = folder, and sorts folders first by Shift-JIS-widened name. The GBA copies at most 99 entries (0x67AC bytes) |
| 0x500 reply: +4 ?, +8 ?, +0xC length ms | [D] | Partly | Extended: **+8 is the VC address of the thumbnail**; +4 is unused; +0xC confirmed (ms) |
| 0x600 reply: +4 non-zero to grab data | [D]; [G] sets irq_data[1] = 1 | Partly | Extended: +4 is the **VC address** of the 0x8C-byte ID3 block |
| ID3 block: title 0x04-0x44, artist 0x45-0x8B | [D] | Partly | Title confirmed (0x41 bytes). Artist is 0x45-0x87; **+0x88 is the song length in seconds**, which sets the trackbar and seek steps |
| 0x700 reply (80001000): +4 "video length counter", +8 frame size, +0x10 audio size, +0x18 position (ms) | [D]; [G] sets [1] = 0x31AC0, [2] = 0x12C00 | **No** | Corrected: **+4 = VC address of the frame** (0 = no frame), **+0xC = VC address of the audio block**, +0x10 = audio byte count (≤0x480), +0x14 = trackbar (music), +0x18 = seconds (music) or ms (video). +8 is not read. "Audio size non-zero → audio only" is how [D] read it; the ROM handles a frame and a block in the same record |
| 0x800 reply: +0x14 trackbar 0..0x6400, +0x18 seconds | [D][A1] | Yes | Latching rule: only from records without audio |
| Audio: 0x480 samples total, split L and R | [D][A2] | Partly | Corrected units: 0x480 is the **byte count**, 0x240 8-bit samples per channel, with each half rounded up to 16 bytes. The **first half goes to FIFO A, which SOUNDCNT_H routes to the right speaker**. Ring 0x900 bytes per channel ([A2] confirmed) |
| Music 16384 Hz, video 8192 Hz, sound effects 8192 Hz | [D][G] | Yes | Exact switch points: 16384 on 0x40000800, 8192 on 0x40000801, video and menus 8192, title intro 16384 (the original too, not only the Micro). New: adaptive timer trim of −3..+3 cycles from the averaged fill level, and an underrun hold of the last sample |
| Video frames 0x12C00 to Mode 3, read like thumbnails | [D] | Yes | Extended: DMA'd straight into VRAM with the Game Pak IRQ masked, from the idle hook while the GBA waits for VBlank. Streaming mode if the RAM test passed. Paced only by the cartridge |
| 0x80010001, 0x40800000 | [D] | Partly | Purposes established (wake, reset-standby confirm) |
| Unhandled indications | none | | New: the firmware defines ALERT 0x80100000, HP_JACK_STATUS_CHANGE 0xC0000000, AC_ALERT 0xC0000001 [fw]. Only ALERT is ever sent as a record; the original GBA code has no handler, so it counts as "unknown ID" (error 1). The other two are internal messages of the VC firmware **[lle]**. The GBA has no headphone input at all |
| Error codes 1-5 on 0x8001's reply | [D] | Partly | The screens are driven by rec[1] of **0x80000100**, with the same five texts confirmed. In the menu a non-zero status shows "AC97 ERROR!" |

## Garage Games

| Element | Prior art | Agrees? | Confirmed / corrected / new here |
|---|---|---|---|
| 13 names + hashes; `rotr(h + p, 31)` over 0x960 pixels; name must match, case-sensitive | [D] | Yes | Table at ROM 0x0C0F1EA8: {game index, name, hash} with a −1 terminator. Same 13 hashes. Hash = rotl 1 (= rotr 31) |
| "Each Garage Game already exists in the ROM" | [D][A2] | Yes | Game index → descriptor table at 0x0C0F1F70 (14 entries; index 3 = CREDIT is the staff roll; index 13 is a stub no file reaches). Code at 0x0C0198F4-0x0C0217DF |
| Thumbnail generated from the first frame | [D] | Not checkable | Cartridge side |
| Garage Games run in the ASF's 60x40 thumbnail window | [D][A1] | **No** (games 1-5) | Run in an emulated cartridge, games 1-5 send only 0x2000 sound effects and no video commands **[run]** |
| New | | | The video browser plays a 60x40 live preview of the highlighted file through 0x700 with 60 x 40, using the window blit path |

## Micro

| Element | Prior art | Agrees? | Confirmed / corrected / new here |
|---|---|---|---|
| Serial commands: MODE 0x28, ADDR 0xFF020, 16-bit writes to 0xB000000, PARAM 0x0C/0x08 | [D][G] | Yes | Extended: bit-7 poll per halfword (through 0x9000006), Game Pak IRQ masked, no DMA |
| Micro parameters are 16-bit; 0x905 rewind = 0xFFFF | [D][G] | **No** | Corrected: identical 32-bit structures sent as halfword pairs. A 16-bit reader sees only the low halves |
| 0x3000/0x3001/0x3003 | [D][A1] | Yes | Extended: 0x3001 reply +0xC = VC address of the text, +8 = length (not clamped by the GBA); exact parser rules, limits and defaults (player.md 10). New commands 0x3002 (unused), 0x4000/0x4001 auto-play with replies |
| Key file semantics | [D] | Yes | Extended: clamp vs reject per key, UTF-16LE BOM, 16 lines / 255 bytes, trailing newline required, L/R at boot inverts Design [inferred] |
| 272-byte entries | [D] | Yes | Type byte at +0x10C; 0x6938-byte copy |
| Micro sound effects 16384 Hz in the intro, 8192 after | [D] | Yes, but not Micro-specific | The original does the same |
| `play_yanmicro.fup` | [D] | Yes | Present only in firmware image C; the GBA flow is the original's |
| Firmware storage | none | | New: BIOS LZ77 chunk lists, three images (A video 0x4975C, B music 0x43D84, C loader 0x17FD0 decoded) |

## Nintendo MP3 Player

| Element | Prior art | Agrees? | Confirmed / corrected / new here |
|---|---|---|---|
| Program structure | [D][G]: one program | | New: a stub, a boot loader (0x090000, run from EWRAM 0x02028000) and a player app (0x010000, run from EWRAM 0x0201C000) linking the same driver, with a hand-off block at 0x0203FF80. This is the "second near-identical GBA program" [fw] noted |
| Registers 0xE000000/4000/6000/8000, 16-bit MSB first, even byte first | [D][G] | Yes | Extended: CONTROL is written as (v << 8) \| v; 0x10/0x00 select the address high/low phase; 0x04 doorbell; 0x08 start and IRQ acknowledge. Read helpers for 0xE002000/0xE00A000 exist, unused. The layout matches a C54x HPI-8 **[inferred]** |
| "INPUT_PARAMETER 0-3, then INDEX" | [D] | Partly | Corrected: it is the high and low half of one word address, after a bank fold. Writes use address − 1 (HPI pre-increment **[inferred]**) |
| Index 0x00FF "data flush" | [D] | **No** | Corrected: it is the clear of the status record at 0x0100 (0x0100 − 1 because it is a write) |
| Index 0x010F "send command / get ticks" | [D][G] | Partly | Explained: the command mailbox is 0x0110 (written as 0x010F); 0x010F read is the tick counter; 0x0110 read is a busy flag polled before every command |
| Index 0x0101 for ID3; "arbitrary the first time" | [D][G] | **No** | Corrected: the data address is the u32 A in the record; there is no fixed 0x0101 |
| Chip reset | none | | New: the cartridge GPIO port 0x080000C4, bit 3 |
| Firmware upload by index | [D][A2] | Yes | Extended: a C54x boot table at ROM 0x080A8F34 (0xF730 words): keyword 0x08AA, entry 0x42FC, 91 sections (all destinations listed in protocol.md 11.3), entry written to 0x7E then CONTROL 0x08. [fw] identified the DSP |
| Boot retry "if ticks don't advance for a second" | [D][A2] | Partly | Corrected: 8 s per attempt, 4 attempts, then boot status 2. The tick watchdog is armed by 0x8001 and needs about 0.867 ticks per frame over 60-frame windows |
| Status data 16 bytes, first 8 checked | [D] | Yes | The ID is checked from 4 words, then 8 words are read |
| 0x4010/0x4011 "End of list flag non-zero = found" | [D] | **No** | Corrected: status 0 means an entry was returned. [G] has it right |
| 528-byte entry, flag at 0x20C | [D][G] | Yes | |
| ID3: title 0x00-0x87, artist 0x88-0x10F | [D] | Partly | Title starts at +4 ([G] agrees); artist +0x88; **u32 song length in seconds at +0x10C** (new) |
| 0x8100: 16-bit index at bytes 4-5; trackbar byte 8 = 0-99 %; timestamp "24-bit from bytes 0x0F, 0x0C, 0x0D, 0x0E adds 1216 s" | [D][G] | **No** | Corrected: three u32 fields, each sent low word first. A = data address, B = trackbar 0..0x6400 (byte 8 is its high byte, hence the percent reading), C = seconds. The odd timestamp byte order is just the low-word-first order |
| L/R buffers: right half at index + 0x120 for 0x480 | [D] | Yes | Extended: A + 2n with n = size / 2 rounded up to 16; the first half plays on the **right** speaker |
| Samples "MSB = first sample" | [D] | Disputed | The GBA stores the odd-address byte first. This is proven for file data, unproven on hardware for audio |
| 0x0060 seek: byte 0 = position %, byte 1 = direction ±0x10 | [D][G] | **No** | Corrected: one word = absolute target 0..0x6400. The "direction" byte is the low byte of a step that grows by 0x10 per send |
| 0x0080 volume 0-0x2E | [D] | Yes | From the 0..36 level |
| 0x0200 sound effect, no parameters | [D][G] | **No** | Corrected: sound ID and level 0x10-0x3F |
| 0x0300/0x0301/0x0303 | [D][G] | Yes | Extended: 0x0301 takes a byte count; new 0x0302 seek |
| meteor.fup: header string; max 65,536 bytes | [D][A2] | Partly | Header confirmed. Corrected: images up to **0x1FFFF** bytes (0x20000 buffer). New: 0x40-byte header with entry count, 16-byte entries, per-chunk check bytes (sum = 1 mod 256), 0x0800/0x4800 before loading |
| Responses 0x4052, 0x4053, 0x4060, 0x4080, 0x4200 | [D] | **No** | The dispatcher has no case for them |
| 0x0030, 0x0070, 0x0400/0x0401, 0x0500/0x0501, 0x0800; replies 0x4400, 0x4401, 0x8501, 0x4800 | [D]: some unknown | | New layouts (protocol.md 11.4) |
| Sample rate | none | | New: 16384 Hz only, with the ±3-cycle adaptive trim |

## Emulator support

| Emulator | Play-Yan / Micro / NMP | Notes |
|---|---|---|
| GBE+ | Yes (HLE) [G] | Boots and plays from a host folder. Firmware uploads are stored but never run; records are synthesised; VC addresses in records are ignored on read-back |
| mGBA (0.11-dev), others | No | The ROM hangs at the first STAT bit-7 poll |
