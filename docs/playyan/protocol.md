# Play-Yan family: the GBA-cartridge protocol

Scope: the interface between the GBA program and the media chip on the cartridge, as the GBA
code drives it. That covers registers, transfer primitives, firmware upload, commands, interrupt
responses and the data structures that cross the bus. Sections 1-9 are the original
**Play-Yan** (AGS-006, ZMAJ, also sold as the iQue MP4 Player), section 10 has the **Play-Yan
Micro** deltas, and section 11 covers the **Nintendo MP3 Player**, which uses a different chip and
protocol. The GBA program around this protocol (boot, menus, A/V pacing) is in
[player.md](player.md). What this confirms or corrects in earlier work is in
[prior-art.md](prior-art.md).

All facts come from the cartridges' own ARM/Thumb code, unless a tag says otherwise. Evidence
tags:

- **[code]**: read from the GBA code (Ghidra decompile, checked against the disassembly where it
  matters).
- **[inferred]**: our reading of the code's intent. The GBA side alone cannot prove it.
- **[fw-names]**: a name from the message-name table inside the cartridge firmware, found by the
  separate firmware analysis ([firmware.md](firmware.md)). It is used only to
  label IDs whose behaviour the GBA code already shows.
- **[prior]**: from shonumi's documentation or GBE+, and not observable in the GBA code.
- **[run]**: observed in bus traces of the real ROM running in mGBA against an emulated cartridge
  (README, "Emulator check"). This shows what the GBA does; the cartridge side of such a run is
  our model, not the hardware.

The ROM is linked to run from the wait-state-2 mirror, so all Play-Yan code addresses are
`0x0Cxxxxxx`. Subtract 0x0C000000 to get a file offset. "VC address" means an address in the media
chip's own memory (the chip is an Alphamosaic VC01 [prior]). The GBA reaches that memory only
through the register window.

| ROM | sha1 (first 1 MiB = No-Intro image) | header | link base |
|---|---|---|---|
| Play-Yan (Japan) | 7f432bbf6f0e5e1b8324c7d6bb1d81a254a6e870 | `PLAYAN` ZMAJ01 | 0x0C000000 |
| Play-Yan Micro (Japan) | 17b5f7ad459448b447fa94617d09886cd4f9c554 | `PLAYYANMICRO` ZMBJ01 | 0x0C000000 |
| Nintendo MP3 Player (Europe) | b7c517f1cb5860fec8beaa4e819bcffa8ce95a5b | `MP3PLAYER` ZMDE01 | 0x08000000 |

Each dump is 4 MiB: the 1 MiB image mirrored four times.

## 1. Address decoding and wait states

The cartridge register file sits at cartridge address 0x1000000. The GBA uses **two views of the
same registers** **[code]**:

| view | GBA address | wait state (WAITCNT = 0x5DC0, set in crt0 0x0C00024C) | used for |
|---|---|---|---|
| WS0 | 0x09000000 + reg | 4/2 | polling status bit 7 (0x09000006) while writing; FIFO reads from 0x09000000; the unused boot path (section 5.4) |
| WS1 | 0x0B000000 + reg | 2/1 | everything else |

The code runs from WS2 (0x0C000000, 3/1). WAITCNT also turns on prefetch and sets the PHI pin
output to 16.78 MHz (bits 11-12 = 3). That `0x9000006` and `0xB000006` are one register is shown by
the access pattern: the code writes the mode byte at 0xB000006 (or 0x9000006 on the boot path),
then reads bit 7 back at 0x9000006, inside one primitive (0x0C0247D4, 0x0C023334).

## 2. Registers

Offsets are from the base of either view. All are 16-bit unless noted. "Sites" lists every
function that touches the register (Play-Yan original).

| off | name (ours) | dir | meaning **[code]** | sites |
|---|---|---|---|---|
| +0x000 | DATA | W/R | Fixed-address data port. Mode 0x70: a 32-bit write as two halfwords, low first. Mode 0x28: one halfword per access (FIFO), each gated by STAT bit 7 | 0C024544 (ack), 0C024758, 0C024798, 0C024E80 (FIFO read), 0C016674 (FIFO read, no caller), 0C02330C (boot path) |
| +0x002 | GO | W | Written 0 after every address load. Also written alone (no new address) in the IRQ acknowledge. Starts the transaction **[inferred]** | 0C024544, 0C024758, 0C024798, 0C0247D4, 0C02493C, 0C024E80, 0C016674, 0C02330C, 0C023334 |
| +0x004 | ADDR | W | 32-bit VC address, two halfword writes: low half, then high half | same as GO |
| +0x006 | MODE / STAT | W byte / R byte | Write: transfer mode (table below). Read: bit 7 = 1 when the port is ready for the next write or FIFO halfword | write: as GO; read: 0C0247D4, 0C024E80, 0C024F8C, 0C0250E8, 0C0251C4, 0C016674, 0C023334, 0C023420 |
| +0x010 | CTRL | W | Control bits. The GBA keeps a shadow at 0x03001448 and always writes the whole shadow | 0C0101B8, 0C0243F4, 0C024450, 0C024544, 0C02471C, 0C02493C, 0C024AD4, 0C024CB8; boot path 0C0230C0, 0C0232D4, 0C0234C0 (shadow 0x03000E9C) |
| +0x012 | RSTAT | R | Bit 4 = read data ready in the read window. Polled up to 9999 times; error 7 is logged after more than 1000 polls | 0C0243B4 |
| +0x022 | BOOT | W | 1 during the WS0 boot upload, 0 after it. Only in the unused boot path | 0C023420 |
| +0x100-0x2FF | WIN_W | W | 512-byte write window, address-incrementing (16-bit DMA3 target) | 0C0247D4, 0C023334 |
| +0x300-0x4FF | WIN_R0 | R | 512-byte read window. Used for responses, lists, ID3 data, audio and the RAM test | 0C024544 (32 B record), 0C025454, 0C0261D0, 0C024CB8 |
| +0x500-0x6FF | WIN_R1 | R | 512-byte read window. Used for video frames and thumbnails only | 0C025454 |

MODE values **[code]**. The names are ours, and the bit meanings are **[inferred]** from usage:

| MODE | use | transfer |
|---|---|---|
| 0x68 | block write (firmware upload, commands, RAM test) | ADDR, GO, then 16-bit DMA3 of at most 0x200 bytes into WIN_W |
| 0x70 | single 32-bit write (start kick, host-sync word, IRQ acknowledge) | ADDR (optional), GO, two halfwords to DATA |
| 0x78 | block read | ADDR, GO, then CTRL bit 0 handshake, 16-bit DMA3 from WIN_R0 or WIN_R1 |
| 0x28 | FIFO access through DATA, one halfword per STAT bit-7 handshake | original: the dummy read of VC 0x80000; Micro: command writes (section 10) |

CTRL bits **[code]**. Idle value 0x0A (bits 1 and 3), set by 0x0C0243F4.

| bit | use |
|---|---|
| 0 | Read request. Set before a block read, then wait for RSTAT bit 4 and DMA. Cleared after each 0x200-byte chunk, or held for a whole transfer in streaming mode (section 3.4) |
| 1 | Read restart. The interrupt handler pulses it (set, clear) before it reads the record, and a block read pulses it before reloading the address after an interrupt. In the emulator traces every one of 4,343 pulses is followed at once by MODE 0x78 and a new address **[run]**, so it marks the start of a new read rather than only acknowledging the interrupt. After the first interrupt the idle value is 0x08 |
| 2 | Doorbell. Set then cleared after a command has been written to the command mailbox (0x0C02471C) |
| 3 | Active-low reset of the media chip. Cleared, held for a counted delay (2000 loop iterations at 0x0C024450; 1000 on the boot path), then set |
| 4 | Pulsed once at the start of the title scene, before any upload (0x0C0101B8). Purpose unknown; nothing observed depends on it **[run]**. This pulse reads CTRL back (read-modify-write at 0x0C01036C) instead of using the shadow, so CTRL must be readable: a register that reads 0 would hold the chip in reset |

## 3. Transfer primitives (original)

### 3.1 Bus lock and deferred interrupts

Every primitive except the interrupt handler takes a lock (0x0C0244CC(1), flag 0x0300144D)
around its register sequence **[code]**. If the Game Pak interrupt fires while the lock is held,
the handler sets the pending flag 0x0300144E, logs error 6 and returns without touching the
cartridge. When the lock is released (0x0C0244CC(0)) with an interrupt pending, the code saves
IME and IE, clears IE bit 13, calls the handler body directly, and restores IE and IME. The cartridge
therefore never sees an ADDR/MODE sequence interrupted by the handler's own sequence.

### 3.2 Reset handshake before an upload (0x0C024F8C, 0x0C0250E8, 0x0C0251C4)

1. Driver reset (0x0C0243F4): CTRL = 0x0A. Clear the lock, pending, overflow and restart flags,
   set 0x03001660 = 0xFFFFFFFF and 0x03001664 = 0 (their purpose is unknown).
2. Reset pulse on CTRL bit 3.
3. **Dummy FIFO read** (0x0C024E80): MODE = 0x28, ADDR = 0x00080000, GO; then one read of
   0x09000000 at once, without a STAT wait, and 0x80 more, each after a STAT bit-7 wait **[run]**.
   The data goes to a stack buffer and is not used.
4. A second reset pulse.
5. Poll STAT bit 7 (0x09000006). Pulse reset again every 0x8000 polls. After 0x100000 polls the GBA
   hangs in a tight loop (0x0C0250E8, 0x0C0251C4; 0x0C024F8C hangs the same way).
6. Write the poll count as a 32-bit value to **VC 0x000FF080** (mode 0x70): the number of STAT
   reads that found the chip not ready since the last reset release (37 polls give 0x25) **[run]**.

### 3.3 Block write (0x0C0247D4; wrappers 0x0C0248B4 without wait, 0x0C0248F8 with wait)

For each chunk of at most 0x200 bytes: take the lock, MODE = 0x68, ADDR = destination, GO = 0;
if "wait" is set, poll 0x09000006 bit 7; DMA3 the chunk as halfwords (DMA3CNT_H = 0x8000) from RAM
to 0x0B000100 upward; release the lock. Then advance the destination by the chunk size. The
wrappers split transfers into 64 KiB calls. Commands use the version **without** the bit-7 wait.
Firmware and the RAM test use the version with it.

### 3.4 Block read (0x0C02493C; wrapper 0x0C024AD4)

Arguments: VC source, RAM destination, length, window (0x0B000300 or 0x0B000500). The address is
written only at the start of a transfer, or when the restart flag 0x0300144C is set. The interrupt
handler sets that flag because it reloads ADDR. Per 0x200-byte chunk: take the lock; if a restart is
needed, toggle CTRL bit 1, then MODE = 0x78, ADDR, GO; then set CTRL bit 0, poll RSTAT bit 4, DMA3
0x100 halfwords from the window, clear CTRL bit 0, release the lock. The cartridge advances its own
address across chunks: the GBA does not rewrite ADDR between chunks.

**Streaming mode.** If the memory-test flag 0x03002B18 is 2 (test running), or is 1 (test
passed) and the window is 0x0B000500, CTRL bit 0 is set once when the address is loaded and held
for the whole transfer, with only the RSTAT bit-4 poll per chunk. So frame and thumbnail reads use
streaming only when the boot memory test passed **[code]**. That the test qualifies the burst
mode is **[inferred]**.

### 3.5 Commands (0x0C0248B4 + doorbell)

A command is a little-endian structure of 32-bit words. It is written with the block write
(3.3, no wait) to **VC 0x000FF020**, then the doorbell is rung: CTRL |= 4, write; CTRL &= ~4,
write (0x0C02471C) **[code]**. Commands longer than 0x200 bytes would be split into two block writes
at 0xFF020 and 0xFF220, but no command is that long (the longest is 0x118 bytes).

### 3.6 Interrupt record read and acknowledge (Game Pak interrupt handler 0x0C024544)

Installed at slot 2 of the interrupt table (0x03001788) by 0x0C024EF4, which also sets IE bit 13
**[code]**. On entry:

1. If the bus lock is held: set the pending flag, log error 6, return (3.1).
2. If 16 records are already queued: log error 8, set the overflow flag 0x03001658, return. The
   record is not read and not acknowledged.
3. CTRL |= 2, write; CTRL &= ~2, write (acknowledge).
4. MODE = 0x78, ADDR = **0x000FF000**, GO = 0; CTRL |= 1; poll RSTAT bit 4; CTRL &= ~1.
5. Copy **32 bytes** (8 words, with CPU LDRs) from 0x0B000300 into the ring slot
   0x03001458 + 32 x write_index. The write index is 0x03001450, the count is 0x0300144F, and the
   read index is 0x03001451.
6. MODE = 0x70, GO = 0, then write 0x0000 and 0x0000 to DATA. ADDR is **not** reloaded, so this
   clears a word at the current address. That word is presumably the response mailbox itself
   (an empty-slot marker) **[inferred]**.
7. Set the restart flag 0x0300144C, so the next block read reloads ADDR.

The records are processed later, outside the interrupt, by the dispatcher (section 7).

### 3.7 Start kick

A 32-bit write (mode 0x70) of **0xA5A50032 to VC 0x10000400** follows every firmware upload, and is
also used to wake the chip after sleep (section 7.4) **[code: 0x0C025040, 0x0C0250E8, 0x0C0251C4,
0x0C0226E0, 0x0C0115F4; boot path 0x0C023420]**. The value and address suggest a write to a VC01
control register that starts the processor **[inferred]**.

## 4. VC memory as the GBA sees it

| VC address | size | use **[code]** |
|---|---|---|
| 0x00000000- | up to 0x3F66C | the media firmware image (fw0 or fw1); the first 0x200 bytes of the loader (fw2) |
| 0x00000200 | 6 bytes | the loader patch record `7F 2F 00 02 0C 00`. Bytes 2-5 are 0x000C0200 in little-endian, so this looks like a jump to the relocated loader **[inferred]**; for the firmware analysis to confirm |
| 0x00080000 | 0x100 | dummy FIFO read after reset (3.2) |
| 0x000C0200-0x000D25BF | 0x121C0 | the rest of the loader fw2 |
| 0x000EC400-0x000FEFFF | 0x12C00 | RAM-test area (section 5.3) |
| 0x000FF000 | 0x20 | **response mailbox** (one 32-byte record) |
| 0x000FF020 | up to 0x118 | **command mailbox** |
| 0x000FF080 | 4 | host-sync word: the number of not-ready status polls after the last reset release |
| 0x10000400 | 4 | start register: 0xA5A50032 |
| record-supplied | | listing, ID3 block, thumbnails, video frames and audio blocks. Their addresses arrive in the response records (section 7); the GBA never assumes them |

## 5. Firmware images and upload

### 5.1 Images in the original ROM (raw, contiguous)

| image | ROM offset | GBA address | length | sha1 | content (strings) | VC destination |
|---|---|---|---|---|---|---|
| fw0 | 0x029710 | 0x0C029710 | 0x31B48 (203,592) | ce983ce7bac58ff438806247702438ec7b97d742 | ASF strings: **video** firmware | 0x00000000 |
| fw1 | 0x05B258 | 0x0C05B258 | 0x3F66C (259,692) | ede92003b9aec9d18d17707d95cb61bb21444922 | MP3/ID3 strings: **music** firmware | 0x00000000 |
| fw2 | 0x09A8C4 | 0x0C09A8C4 | 0x123C0 (74,688) | e351f78d491c60c0ef080475ff255f4519fc82b5 | `playan.fup`, "firmware update": **loader / updater** | bytes 0x0-0x1FF to VC 0x0; the 6-byte record to VC 0x200; bytes 0x200-0x123BF to VC 0xC0200 |

The lengths are hard-coded in 0x0C026408 (fw0), 0x0C026410 (fw1) and 0x0C026418 (fw2). The
selector argument 0 = fw0, 1 = fw1 (0x0C024F8C, 0x0C0250E8) **[code]**. That fw0 is video and fw1
is music follows from the callers: the menu passes 0 for "watch" and 1 for "listen" (0x0C0113F4,
user argument of the 0x40800000 callback) **[code]**, and the strings agree. The images end at
0x0ACC84.

### 5.2 Upload procedures

| routine | image | chunking | after |
|---|---|---|---|
| 0x0C0251C4 (one-shot) | fw2 | reset handshake (3.2); DMA3 (32-bit) ROM -> stack 0x200 bytes, block write with wait to VC 0; the 6-byte record to VC 0x200; then fw2[0x200:] in 0x400-byte pieces to VC 0xC0200 upward | kick (3.7); interrupt handler installed and enabled (0x0C024EF4); returns 0, or -1 if the chip never became ready |
| 0x0C024F8C + 0x0C025040 (incremental) | fw0 or fw1 | 0x0C024F8C: reset handshake, set source and length; 0x0C025040 is called once per frame and copies at most **8 x 0x800 bytes** (16 KiB). Each 0x800 bytes: DMA3 (16-bit) ROM -> stack, then block write with wait (0x200-byte DMA bursts) | when done: kick, handler installed, returns 1 |
| 0x0C0250E8 (one-shot) | fw0 or fw1 | reset handshake, 0x400-byte pieces, all in one call | kick, handler installed. **No caller found** |
| 0x0C023420 (unused boot path, section 5.4) | fw1 | reset via 0x09000010, BOOT = 1, 0x400-byte pieces through the WS0 view (0x09000100) | BOOT = 0, kick through the WS0 view |

fw1 takes 16 frames to upload incrementally (259,692 / 16,384, rounded up).

### 5.3 RAM test (0x0C024CB8, called with 0x32 at boot)

Called with the Game Pak interrupt disabled, right after the fw2 upload at boot (title state 1)
**[code]**:

- Flag 0x03002B18 = 2 (streaming reads, 3.4).
- Write VC 0xEC400-0xFEFFF in 0xA00-byte blocks. The pattern is a 511-byte cycle read from ROM
  0x0C0F25DC (index 0x0300166C wraps after 0x1FE).
- Read the area back in 0xA00-byte blocks through WIN_R0 and compare byte by byte. Any mismatch:
  flag = 0, return. All equal: flag = 1.
- The argument looks like a pass count. But the outer loop compares the byte index left over from
  the compare loop (0xA00, plus 1) against it, so the test always runs **exactly one pass**
  **[code: 0x0C024E20]**.

Flag = 1 enables streaming for frame and thumbnail reads (3.4). The test leaves its pattern in
VC memory.

### 5.4 Unused WS0 boot path

The scene at descriptor 0x0C0F24B8 (init 0x0C0231A8) sets the Game Pak interrupt to a stub
(0x0C0230B4) that only sets 0x03002D8C = 1. It then uploads **fw1** through the WS0 view: CTRL at
0x09000010 = 0x0A, a reset pulse with a 1000-iteration delay, wait for STAT bit 7, BOOT
(0x09000022) = 1, the image in 0x400-byte pieces with mode 0x68 into 0x09000100, BOOT = 0, and the
kick to 0x10000400 through 0x09000000/0x09000004. **No reference to this scene exists**: the
initial scene pointer (0x0C0B4F68) and the soft-reset scene pointer (0x0C0B4F6C) both point to the
title scene, and no code or table holds the descriptor's address **[code]**. It looks like an early
boot method that the fw2 + per-media scheme replaced **[inferred]**. The Micro has no counterpart.

## 6. Commands (GBA to cartridge)

All commands go to VC 0xFF020 (3.5). Layout: word 0 is the ID, the rest are 32-bit fields, and
"name" is a 260-byte field filled by `strcpy` (0x0C0283F8). Bytes after the terminating NUL are
uninitialised stack and are sent as-is. "Len" is the byte count written. "Reply" is the record ID
the GBA waits for, or "-" if it waits for nothing.

| ID | builder | len | fields after the ID **[code]** | reply | firmware name **[fw-names]** |
|---|---|---|---|---|---|
| 0x200 | 0C025A4C | 8 | u8 type: 1 = video (recursive), 2 = music (current folder), 3 = the unused test scene; the other 3 bytes are uninitialised | 0x40000200 | FILE_LIST_REQUEST |
| 0x201 | 0C025A80 | 0x108 | name: folder, or `..` | 0x40000201 | FILE_SELECT_REQUEST |
| 0x300 | 0C025AB0 | 8 | u32 brightness from the table at 0x0C0F2500: 0x101, 0x140, 0x180, 0x1E0, 0x240, 0x2C0 (index 0-5, default 0) | - | VIDEO_MODE_SET_REQUEST |
| 0x400 | 0C025BB0 | 0xC | u32 1, u32 p (always 0) | - | AUDIO_MODE_SET_REQUEST |
| 0x500 | 0C025AD4 | 0x118 | u32 width 60, u32 height 40, u32 0, u32 0, name at +0x14 | 0x40000500 | VIDEO_INFO_REQUEST |
| 0x600 | 0C025B84 | 0x108 | name | 0x40000600 | AUDIO_INFO_REQUEST |
| 0x700 | 0C025B0C | 0x110 | u32 width, u32 height, name at +0xC. The player sends 240 x 160; the video browser sends 60 x 40 for its live preview. Clears the stored position words and arms a start timeout (0x03002B10 = 60) | 0x40000700 | VIDEO_DECODE_START_REQUEST |
| 0x701 | 0C025B54 | 4 | - ; stop timeout 0x03002B14 = 60 | 0x40000701 | VIDEO_DECODE_STOP_REQUEST |
| 0x800 | 0C025BD8 | 0x108 | name; clears the stored position words; start timeout = 60 | 0x40000800 | AUDIO_DECODE_START_REQUEST |
| 0x801 | 0C025C18 | 4 | - ; stop timeout = 60 | 0x40000801 | AUDIO_DECODE_STOP_REQUEST |
| 0x902 | 0C025C48 | 4 | - (pause) | - | PAUSE_REQUEST |
| 0x903 | 0C025CE0 | 8 | u32 1 (step one frame while paused) | - | STEP_REQUEST |
| 0x904 | 0C025C6C | 4 | - (resume) | - | RESTART_REQUEST |
| 0x905 | 0C025C90 | 8 | s32 position. **Music**: absolute trackbar target 0..0x6400. **Video**: a signed step in seconds, relative to the current position (player.md 5.3): +1/-1 per send for short files, the resume time, or 0 / -1 during step-back | - | PLAY_POSITION_SET_REQUEST |
| 0x906 | 0C025CB8 | 8 | u32 1 = position indications on, 0 = off (sent 0 before the GBA stops itself during music, 1 after) | - | PLAY_POSITION_IND_SET_REQUEST |
| 0xB00 | 0C025D08 | 8 | u32 volume 0..56: user level (0..36, default 18) x 56 / 36; 0 = mute (pause, resume seek) | - | VOLUME_SET_REQUEST |
| 0xD00 | 0C025D54 | 0x10 | u32 level, u32 0, u32 0. Level = setting (0..6) x 15 / 6 | - | AUDIO_3D_ENHANCEMENT_REQUEST |
| 0xD01 | 0C025D7C | 8 | u32 (b << 8) \| 0x800F, b from the 2-byte table at 0x0C0F2518 = {15, 0}: setting 0 sends 0x8F0F, setting 1 sends 0x800F | - | TONE_CONTROL_REQUEST |
| 0x2000 | 0C025D2C | 0xC | u32 sound ID, u32 volume = clamp(level x 63 / 36, 16, 63), with level = user volume x 3 / 4 (0x0C0240A4, 0x0C024080) | records 0x80001000 with audio | OPERATION_SOUND_REQUEST |
| 0x8000 | 0C025DA8 | 4 | - (list the update file's parts) | 0x40008000 | FIRMWARE_LIST_REQUEST |
| 0x8001 | 0C025DCC | 8 | u8 part: 1 = video, 2 = music (other bytes uninitialised) | 0x40008001 | FIRMWARE_DOWNLOAD_REQUEST |
| 0x10000 | 0C025E04 | 4 | - ; the GBA enters BIOS Stop right after (player.md 8) | none; 0x80010001 after the wake kick | SLEEP_REQUEST |
| 0x800000 | 0C025E28 | 4 | - ; sent before every reload of fw0 or fw1 | 0x40800000 | RESET_STANDBY_REQUEST |

The firmware name table also lists 0x3000-0x3003 (FILE_OPEN/READ/SEEK/CLOSE) in the original's
loader, but the original GBA code has no builder for them. The Micro uses them (section 10).

## 7. Responses (cartridge to GBA)

### 7.1 Record format

Every Game Pak interrupt delivers one 32-byte record, `rec[0..7]`, eight little-endian u32 words,
read from VC 0xFF000 (3.6). rec[0] is the ID. The ID is the command ID OR 0x40000000 for a
confirm, and has bit 31 set for an unsolicited indication.

### 7.2 Dispatcher (0x0C025454)

Called from scene code, not from the interrupt. It pops records from the ring (0x0C024B68, with IE
saved and IE bit 13 cleared around the pop) until the ring is empty **[code]**. For each record it
copies any data the record points at (table below), then calls a registered callback. The 14 IDs
that can carry a callback are listed in order at ROM **0x0C0F27DC**:

0x80000100, 0x40000200, 0x40000201, 0x40000500, 0x40000700, 0x40000701, 0x40000600, 0x40000800,
0x40000801, 0x80001000, 0x40008000, 0x40008001, 0x80010001, 0x40800000.

`0x0C0253A4(id, fn, user)` registers a callback. A zero `fn` disables it. The function pointers are
at 0x030016C0 and the user words at 0x030016F8. The call is `fn(user, value)`. The **value**
argument is set only for some IDs:

| ID | value passed |
|---|---|
| 0x80001000 | flags: bit 0 = the record carried audio, bit 1 = a video frame was copied |
| 0x40000500 | rec[3] (duration in ms) |
| 0x80000100, 0x40008000, 0x40008001 | rec[1] (status) |
| all others | **stale**: whatever the previous record left in the value register (r9). No callback uses it **[code: 0x0C0258D4 onward]** |

While a stream plays (state byte 0x03001731 != 0) only the callbacks for 0x80001000,
0x40000700/701/800/801 and 0x80010001 run. The others are still copied but not called back.

Before the callback, each start or stop confirm is checked against the GBA's own state: the play
state byte 0x03001731 (0 idle, 1 video, 2 music), the start and stop timeouts and a
stream-nesting counter (0x030016AD, 0x0C0253E0). The checks log errors 0x02, 0x0C-0x13. If a check
fails, the state is not changed and no callback runs.

**Error log [code].** 0x0C0259D4 pushes a code into a 16-byte FIFO at 0x03001698 (count
0x03001691), and 0x0C0259FC pops one. No caller of the pop was found in reachable code, so the log
is diagnostic only.

| code | meaning |
|---|---|
| 1 | unknown record ID (the ID is also stored at 0x030016A8, initialised to 0x12345678) |
| 2 | stop confirm while idle |
| 3 | audio byte count 0 or > 0x480 |
| 5 | interrupt handler re-entered |
| 6 | interrupt during a locked transfer (deferred) |
| 7 | RSTAT bit 4 took > 1000 polls |
| 8 | ring overflow (record dropped) |
| 0x0C, 0x0D | stream-nesting counter out of range |
| 0x0E, 0x0F | start or stop confirm with rec[1] != 0 (failure status) |
| 0x10-0x13 | start or stop confirm that does not match the GBA's start/stop state |

### 7.3 Responses the original handles

| ID | firmware name [fw-names] | record fields and GBA action **[code]** |
|---|---|---|
| 0x80000100 | VC_STARTUP_INDICATION | rec[1] = status. 0 = running. 1-4 and other values select the boot error screens (player.md 4.2). From the menu, non-zero shows "AC97 ERROR!" (0x0C01134C) |
| 0x40000200 | FILE_LIST_CONFIRM | rec[1] = VC address of the list. The GBA always copies **0x67AC bytes** (8 + 99 x 268) through WIN_R0 to *0x03002D98 |
| 0x40000201 | FILE_SELECT_CONFIRM | no data. The callback's user word tracks the folder depth (+1, -1, 0) |
| 0x40000500 | VIDEO_INFO_CONFIRM | rec[2] = VC address of the thumbnail, copied through **WIN_R1** to the buffer set by 0x0C025310(buf, w, h) (w x h x 2 bytes; 60 x 40 -> 0x12C0). rec[3] = duration in ms (callback value; 0x0C018D28 stores rec[3] / 1000). rec[1] is not used |
| 0x40000600 | AUDIO_INFO_CONFIRM | rec[1] = VC address of the ID3 block, or 0 for none. If non-zero, **0x8C bytes** go through WIN_R0 to 0x03002CE0 (layout in 8.3) |
| 0x40000700 | VIDEO_DECODE_START_CONFIRM | rec[1] = status (non-zero: error 0x0E, rejected). Play state = 1 (video) |
| 0x40000701 | VIDEO_DECODE_STOP_CONFIRM | rec[1] = status (non-zero: error 0x0F). Play state = 0. Also sent by the cartridge at the end of the file **[inferred]** |
| 0x40000800 | AUDIO_DECODE_START_CONFIRM | as 0x700. Play state = 2. Audio timer reload for **16384 Hz** (0x0C02610C(0x4000)) |
| 0x40000801 | AUDIO_DECODE_STOP_CONFIRM | as 0x701. Audio timer back to **8192 Hz**, both audio rings cleared (0x0C025F88), play state = 0. Also the end-of-song event |
| 0x80001000 | DECODE_DATA_INDICATION | the stream record (7.4) |
| 0x40008000 | FIRMWARE_LIST_CONFIRM | rec[1] = status. If rec[1] == 0, the byte 0x03001690 = (u8)rec[2] = update parts present: bit 0 = video, bit 1 = music. The two bits are tested separately |
| 0x40008001 | FIRMWARE_DOWNLOAD_CONFIRM | rec[1] = status (callback value). The GBA then sends 0x800000 |
| 0x80010001 | WAKEUP_INDICATION | reply to the wake kick after 0x10000 sleep |
| 0x40800000 | RESET_STANDBY_CONFIRM | the GBA then reloads fw0 or fw1 |

IDs in the firmware's name table that the GBA does **not** handle, so they would log error 1:
0x80100000 ALERT_INDICATION, 0xC0000000 HP_JACK_STATUS_CHANGE, 0xC0000001 AC_ALERT
[fw-names]. The original GBA code has no headphone-state input. The GBA never learns whether
headphones are plugged in; the cartridge decides whether to send audio data [inferred].

### 7.4 The stream record 0x80001000

| word | meaning **[code]** |
|---|---|
| rec[1] | VC address of a video frame, or 0 for none |
| rec[2] | not read by the GBA |
| rec[3] | VC address of an audio block, or 0 for none |
| rec[4] | audio block size in bytes, total for both channels, at most 0x480 (larger values are clamped and log error 3; 0 logs error 3 and is skipped) |
| rec[5] | music: trackbar position 0..0x6400. Stored at 0x03001680 |
| rec[6] | music: elapsed seconds; video: elapsed **milliseconds**. Stored at 0x03001684 |
| rec[7] | not read |

The position words rec[5] and rec[6] are latched only when (rec[3] == 0 and state == music) or
(rec[1] != 0 and state == video). Music therefore updates the trackbar from records without audio,
and video updates the clock from records with a frame.

**Audio** (rec[3] != 0): 0x0C0261D0(rec[3], rec[4], gain). The gain is 0x180 in video state, 0x100
in music state and 0x60 otherwise (sound effects). Per channel the GBA reads
`n = ((rec[4] >> 3) + 3 & ~3) x 4` bytes: rec[4] / 2 rounded up to a multiple of 16. It copies
`n` bytes from rec[3] into ring A and the next `n` bytes from rec[3] + n into ring B, through WIN_R0.
Each sample is a signed 8-bit value. The GBA scales it in place: `s = clamp((s x (gain >> 3)) >> 5,
-128, 127)`, which is x1.5 for video, x1.0 for music and x0.375 for effects. Ring A feeds **FIFO A,
routed to the right speaker only**; ring B feeds FIFO B, left only (SOUNDCNT_H = 0xA90E). So the
first half of each block plays on the right **[code]**. The output stage is in player.md 6.

**Video** (rec[1] != 0, state == video, frame display not suppressed by 0x030016AC):

- Full-screen mode (0x03001688 == 0): with the Game Pak interrupt disabled, read **0x12C00 bytes**
  (240 x 160 BGR555) from rec[1] through **WIN_R1 straight into VRAM 0x06000000** (BG mode 3). There
  is no double buffer.
- Window mode (0x03001688 == 1, the video browser preview and Garage Games): read w x h x 2 bytes
  (0x12C0 for 60 x 40) into the buffer from 0x0C025310, then copy it row by row to the VRAM
  destination set by 0x0C025304, with a row stride of 0x1E0 bytes (240 pixels).

## 8. Data structures

### 8.1 File list (reply to 0x200)

| offset | size | field **[code]** |
|---|---|---|
| +0x000 | 4 | not read |
| +0x004 | 4 | entry count (at most 99, which is what the 0x67AC copy holds) |
| +0x008 | 268 x n | entries; entry i at +8 + 0x10C x i (0x0C0259B8) |

Entry (268 bytes):

| offset | size | field |
|---|---|---|
| +0x000 | 0x100 | NUL-terminated name. Sent back unchanged as the "name" of 0x201/0x500/0x600/0x700/0x800, so for video lists it may carry a path |
| +0x100 | 8 | not read by any code found |
| +0x108 | 1 (u8) | type flags: bit 1 = music file (tested at 0x0C017518 and others); 0 = folder. The music list is sorted with folders first, then by name (0x0C0241A4). The sort keys are the names widened to Shift-JIS full-width through the table at 0x0C0F251A. `..` keeps its place |

### 8.2 Thumbnails and frames

Plain BGR555 halfwords, row-major, the GBA's bitmap format: 60 x 40 = 0x12C0 bytes for thumbnails
and previews, 240 x 160 = 0x12C00 for frames. The GBA asks for these sizes in 0x500 and 0x700.
Fitting the 240 x 176 source video into 240 x 160 is the cartridge's job.

### 8.3 ID3 block (reply to 0x600, 0x8C bytes)

| offset | size | field **[code]** |
|---|---|---|
| +0x00 | 4 | not read |
| +0x04 | 0x41 | title, NUL-terminated (drawn from 0x03002CE4) |
| +0x45 | 0x43 | artist, NUL-terminated (drawn from 0x03002CE4 + 0x41) |
| +0x88 | 4 | **song length in seconds**. The player stores it, computes the trackbar step 0xC800 / length and makes the seek step at least 0x355 (0x0C0127A0) |

## 9. Garage Games

The video browser asks for a thumbnail (0x500) for each visible file. In the thumbnail callback
(0x0C018D28) it calls **0x0C019410(name, pixels)**, which walks the table at ROM **0x0C0F1EA8**:
12-byte entries `{s32 game_index, char *name, u32 hash}`, ended by game_index < 0 **[code]**. For
each entry whose name equals the file's base name (strcmp, so case-sensitive), it hashes the
2400 thumbnail pixels:

```
h = 0
for each of the 0x960 u16 pixels p:  h = rotl32(h + p, 1)
```

The first entry whose name and hash both match gives the game index. Otherwise the result is -1.

| game index | file | hash |
|---|---|---|
| 0 | INSECT.ASF | 0x4D5E5AEB |
| 1 | AVOID.ASF | 0xF8CB9008 |
| 2 | BAT.ASF | 0xD911FE57 |
| 3 | CREDIT.ASF | 0x02F86041 |
| 4 | JUMP.ASF | 0x748C5BAF |
| 5 | FIRE.ASF | 0x0D78A9FE |
| 6 | KEEPER.ASF | 0x3FEBEFE5 |
| 7 | MEMORY.ASF | 0x90119AE6 |
| 8 | NEKOROID.ASF | 0xD839D809 |
| 9 | NOSE.ASF | 0x86D3BA78 |
| 10 | FISH.ASF | 0x5741DE47 |
| 11 | TRIPLE.ASF | 0xD5C0A40B |
| 12 | WAVE.ASF | 0xE7DCD82B |

The game index selects a descriptor from the 14-entry pointer table at ROM **0x0C0F1F70**. Each
descriptor is `{init, update, exit, ctx_size, data, 100, name}`, and its name field always points
to the placeholder string ゲームファイル.asf. Game code spans about 0x0C0198F4-0x0C0217DF:

| idx | descriptor | init / update / exit | ctx |
|---|---|---|---|
| 0 | 0C0F1FC0 | 0C019C60 / 0C01AD34 / 0C019F40 | 0xE0 |
| 1 | 0C0F2048 | 0C01AD74 / 0C01B44C / 0C01AEB0 | 0x6C |
| 2 | 0C0F20C0 | 0C01B498 / 0C01BB50 / 0C01B5C0 | 0x2C |
| 3 | 0C0F20F8 | 0C01BBA0 / 0C01BBF8 / 0C01BBE0 | 0x990 (staff credits; data includes the credit strings) |
| 4 | 0C0F21E8 | 0C01CE40 / 0C01D148 / 0C01CF44 | 0x64 |
| 5 | 0C0F2244 | 0C01D538 / 0C01E67C / 0C01D78C | 0x39C |
| 6 | 0C0F22A8 | 0C01E6D0 / 0C01EAEC / 0C01E794 | 0x68 |
| 7 | 0C0F22E0 | 0C01EB38 / 0C01EBD4 / 0C01EBBC | 0x410 |
| 8 | 0C0F2318 | 0C01EF3C / 0C01F0D0 / 0C01F05C | 0xE8 |
| 9 | 0C0F2350 | 0C01FC9C / 0C020754 / 0C01FE04 | 0x30 |
| 10 | 0C0F2388 | 0C0207D8 / 0C020DE0 / 0C02090C | 0x98 |
| 11 | 0C0F23C0 | 0C020E30 / 0C0212FC / 0C020FA8 | 0x4C |
| 12 | 0C0F23F8 | 0C021354 / 0C0216B4 / 0C021420 | 0x2C |
| 13 | 0C0F2424 | 0C021724 / 0C02175C / 0C021744 | 0x4 (a stub; no file maps to it) |

The hash is a 32-bit rotate-and-add, so a thumbnail can be built to match any entry. The
thumbnail comes from the cartridge's decoder, so a homemade unlock file must produce exactly
those pixels after decoding [inferred].

## 10. Play-Yan Micro deltas

Detailed notes with every address: [micro.md](micro.md). The driver is the original's source,
moved to 0x0C01DA00-0x0C01FDD4. The interrupt handler, ring, dispatcher logic, read and write
primitives, streaming mode, audio and video paths are unchanged **[code]**. The differences:

- **Command transport ("serial", 0x0C01DF88).** The GBA masks IE bit 13 and takes the lock. It
  writes MODE = 0x28, ADDR = 0x000FF020 (low half, then high), GO = 0. Then for each of
  (len + 1) / 2 halfwords: poll 0x09000006 bit 7, store the halfword to **0x0B000000**. Then it
  releases the lock, rings the doorbell and restores IE. The command **structure is byte-identical
  to the original's**: 32-bit fields sent as two halfwords, low first. There is no DMA and no
  0x200-byte chunking. Block write mode 0x68 remains only for firmware and the RAM test.
- **New commands**: 0x3000 FILE_OPEN (0x108: name, only `play_yanmicro.ini`), 0x3001 FILE_READ
  (8: u32 max bytes = 0x100), 0x3002 FILE_SEEK (0xC: builder present, no caller), 0x3003
  FILE_CLOSE (4), 0x4000 VC_AUTO_PLAY_START (0x1A8: play mode, two counts, two u16[100] arrays:
  track flags and play order), 0x4001 VC_AUTO_PLAY_STOP (4). The names are [fw-names]. The
  playlist reading of 0x4000/0x4001 is [inferred].
- **Callback table**: ROM 0x0C0E7848, 20 IDs. The new IDs are 0x40003000-0x40003003,
  0x40004000 and 0x40004001.
  - **0x40003001**: if rec[1] == 0, copy rec[2] bytes (not clamped) from VC **rec[3]** into the
    key-file buffer.
  - **0x40004001**: read 0xC6 bytes (99 x u16, the play order) from VC rec[5]. The packed value
    is rec[1] & 0xFF \| (rec[3] & 0xFF) << 8 \| (rec[4] & 0xFF) << 16 \| (rec[2] & 1) << 24. If
    rec[2] == 0 (playback over), the stream state is reset and the rate goes back to 8192 Hz.
  - 0x40000600, 0x40004000 and 0x40004001 also reach their callbacks during playback.
- **File list**: 0x6938 bytes copied (8 + 99 x **272**). The type byte moves to **+0x10C**. The 4
  inserted bytes (in +0x100-0x10B) are not read.
- **Firmware storage**: three images, each a NUL-terminated list of ROM pointers to GBA-BIOS LZ77
  chunks. They are decoded 0x400 bytes at a time with SWI 0x11, then uploaded exactly as in the
  original. A (video) is at list 0x0C0E7898, ROM 0x023D08-0x05ADAD, 0x4975C bytes decoded, to VC 0.
  B (music) is at list 0x0C0E7D34, ROM 0x05ADB0-0x08BC09, 0x43D84 bytes, to VC 0. C (loader,
  contains `play_yanmicro.fup`) is at list 0x0C0E8178, ROM 0x08BC0C-0x0A1617, 0x17FD0 bytes. C
  uses the same split as the original's fw2: 0x0-0x1FF to VC 0, the same 6-byte record to VC 0x200,
  the rest to VC 0xC0200.
- **RAM test**: VC 0x00010000-0x00022BFF instead of 0xEC400-0xFEFFF. It is retried once if it fails.
- No WS0 boot path, no BOOT register, no Garage Game table.

The key-file protocol and parser are in player.md 10.

## 11. Nintendo MP3 Player

Full working notes with every address: [nmp.md](nmp.md). NMP addresses are 0x08xxxxxx (ROM) or
EWRAM copies (below). A different chip (a TI TMS320C54x DSP, per the firmware analysis [fw]) and a
different bus protocol, but the **same GBA driver architecture**: an interrupt-time record copy into
a 16-entry ring, a main-loop dispatcher with per-ID callbacks, a bus lock with deferred interrupts,
and the same audio output stage.

### 11.1 ROM layout: three programs

| ROM range | content **[code]** |
|---|---|
| 0x000000-0x00015F | header and a stub crt0 (0x080000D8). It clears RAM, stores 0x08090000 at EWRAM 0x0203FF80, then repeatedly jumps through 0x0203FF80 |
| 0x010000-0x068C6B | **player app**: crt0 at 0x08010000 copies ROM 0x047C5C-0x068C6B (0x21010 bytes) to EWRAM 0x0201C000 and enters Thumb 0x0201C10D |
| 0x090000-0x0D9433 | **boot loader**: crt0 at 0x08090000 copies ROM 0x0CB394-0x0D9433 (0xE0A0 bytes) to EWRAM 0x02028000 and enters 0x02028535 |
| 0x0A8AB8 / 0x0A8AE8 | update header string `Nintendo MP3 Player firmware update program\r\n` and the name `meteor.fup` |
| 0x0A8F34-0x0C7D93 | built-in DSP firmware: 0xF730 halfwords (0x1EE60 bytes) |

The loader runs first. When it finishes it writes `{0x08010000, 0x08090000, boot_status,
headphone_flag}` to 0x0203FF80 and returns, and the stub then starts the app. The two programs
link the same driver library: loader copy at 0x02031310-0x020340BC, app copy at
0x02036890-0x02039680. This is the "second near-identical GBA program at 0x90000" seen by the
firmware analysis. WAITCNT = 0x45C0: WS0 4/2, WS1 2/1, WS2 3/1, prefetch, no PHI output.

### 11.2 Registers (SRAM region, 8-bit bus)

Every access is a byte access, even address first **[code: 0x02031310-0x0203138C]**:

| address | dir | use |
|---|---|---|
| 0x0E000000 | W | CONTROL, written as the halfword (v << 8) \| v so both bytes carry v |
| 0x0E008000/01 | W | address port: 16-bit, MSB to +0, LSB to +1 |
| 0x0E004000/01 | W | data in: 16-bit, MSB to +0 |
| 0x0E006000/01 | R | data out: the GBA forms (b0 << 8) \| b1 |
| 0x0E002000, 0x0E00A000 | R | 16-bit read helpers exist; no caller |
| GPIO 0x080000C4/C6 | W | chip **reset** on bit 3: DATA = 0, DIR = 8, delay of about 100 loop iterations, DATA = 8 |

**Byte order.** 16-bit quantities (IDs, counts, string characters) are big-endian on the bus.
Byte streams carried in halfwords (file data, audio) are kept by the GBA as the little-endian u16
(b0 << 8) \| b1. So the byte read from the **odd** address comes first in memory. The update-file
header compare only works this way, which proves the order for file data **[code]**. For audio it
is the same code path; that the chip emits samples in this order is unchecked on hardware.

**Address set** (0x020313B0 `set_addr(addr, write)`) **[code]**:

1. Bank fold: 0x8000-0xFFFF gets bit 16 set; 0x10000-0x17FFF, 0x20000-0x27FFF, 0x30000-0x37FFF and
   0x40000-0x47FFF are masked to their low 16 bits.
2. For a write, the address minus 1 is used.
3. CONTROL = 0x10, address port = high half; CONTROL = 0x00, address port = low half.

Then N halfwords are streamed through data in or data out, with the Game Pak interrupt masked.

**CONTROL values** **[code]**: 0x10 / 0x00 address high / low phase; **0x04** after a command
(doorbell); **0x08** after the firmware entry point is written, and at the start of every Game Pak
interrupt (acknowledge).

The layout matches the **C54x 8-bit host port interface (HPI-8)** **[inferred]**: HPIC is written
with the same value in both bytes; XHPIA (bit 4) selects the extended address half; DSPINT
(bit 2) interrupts the DSP; writing HINT (bit 3) clears the host interrupt; the address is
pre-incremented on writes, hence the "address - 1". Those bit names come from TI's HPI
documentation, not from this ROM.

**Addresses used** (16-bit-word addresses; dandocs' "indices") **[code]**:

| address | access | meaning |
|---|---|---|
| 0x0100 | read 8 words | status record (one interrupt) |
| 0x0100 (sent as 0x00FF) | write 1 word = 0 | clear the record after processing: dandocs' "0x00FF flush" |
| 0x010F | read 1 word | free-running **tick** counter |
| 0x0110 (sent as 0x010F) | write N words | **command mailbox**: dandocs' "0x010F send command" |
| 0x0110 | read 1 word | busy; polled until 0 before every command |
| 0x007E (sent as 0x007D) | write 2 words | firmware entry point (high, low) |
| from records | read | directory entries, ID3, file data, audio |

### 11.3 Firmware upload

`fw_load_builtin` (0x02032C20) runs `upload(0x080A8F34, 0xF730 words)` (0x02032B30) **[code]**:

1. Pulse reset.
2. Check word 0 = **0x08AA**, otherwise fail with -1. Skip words 1-2 (0x7FFF, 0xF800). Read the
   entry point from words 3-4 (high, low): **0x000042FC**.
3. Records `{count, addr_hi, addr_lo, count words}` until count = 0. Each record is one
   `set_addr(addr, write)` plus a stream of count words.
4. If the entry point is non-zero: write it to 0x7E, then CONTROL = 0x08.
5. Enable the Game Pak interrupt (IE |= 0x2000) with the handler at slot 2 of the table at
   0x03001160 (0x02032781).

There is no read-back and no checksum.

This is the C54x boot-table format [fw]: keyword 0x08AA, the SWWSR and BSCR values (0x7FFF,
0xF800), the entry point, then sections. The built-in table has **91 sections**, 0xF619 data words
in total. Destination word ranges, before the bank fold, with the ROM source of each run's first
data word:

| words | count | sections | source |
|---|---|---|---|
| 0x03000-0x03145 | 0x146 | 1 | 0x080A8F44 |
| 0x38000-0x3BADC | 0x3ADD | 1 | 0x080A91D6 |
| 0x00400-0x004FF | 0x100 | 1 | 0x080B0796 |
| 0x08F74 | 0x1 | 1 | 0x080B099C |
| 0x28000-0x282F6 | 0x2F7 | 3 | 0x080B09A4 |
| 0x094EC-0x094F7 | 0xC | 1 | 0x080B0FA4 |
| 0x282F7-0x2A574 | 0x227E | 58 | 0x080B0FC2 |
| 0x0B416-0x0B45F | 0x4A | 3 | 0x080B561A |
| 0x2A575-0x2A5BF | 0x4B | 1 | 0x080B56C0 |
| 0x00080-0x000FF | 0x80 | 1 | 0x080B575C |
| 0x00062-0x00065 | 0x4 | 1 | 0x080B5862 |
| 0x02000-0x021FF | 0x200 | 1 | 0x080B5870 |
| 0x01F60-0x01F7F | 0x20 | 1 | 0x080B5C76 |
| 0x0DF7E-0x0E676 | 0x6F9 | 1 | 0x080B5CBC |
| 0x08FEE-0x08FF1 | 0x4 | 1 | 0x080B6AB4 |
| 0x09600-0x09783 | 0x184 | 1 | 0x080B6AC2 |
| 0x0E878-0x0EE77 | 0x600 | 2 | 0x080B6DD0 |
| 0x09567-0x09572 | 0xC | 1 | 0x080B79DC |
| 0x0EF48-0x0EFAF | 0x68 | 1 | 0x080B79FA |
| 0x03146-0x06C53 | 0x3B0E | 8 | 0x080B7AD0 |
| 0x2A5C0-0x2EBF7 | 0x4638 | 2 | 0x080BF11C |

**Boot** (loader main 0x02028534) **[code]**. There are at most 4 attempts. Each attempt:

1. Driver init: rings, Timer 0 at 16384 Hz, volume 0x16.
2. Register a callback for 0x8001.
3. Upload the firmware.
4. Run the dispatcher every frame until "done", or until **0x1E0 frames (8 s)** pass.

After 4 timeouts, boot_status = 2. The 0x8001 record ends the boot (word 1 = status) and arms the
**tick watchdog** (0x02031BDC). At least every 60 frames the watchdog reads address 0x010F. With
dt = frames elapsed and dk = tick delta mod 0x10000:

- dk < 2 counts as a stall;
- otherwise the check passes when (((0x10AAAA x dt) >> 8) x 0xD5) <= dk << 20, that is,
  **dk >= about 0.867 x dt**.

A failure sets error flag bit 0. A zero-ID record while the watchdog is armed sets bit 1. The app's
recovery routine re-uploads the firmware with 120-frame timeouts, up to 8 retries, then replays
volume, folder path and play position. What triggers it is [inferred].

### 11.4 Commands

Send (0x02033534; app 0x02038AE0) **[code]**:

1. Note the command for recovery replay.
2. Mask the Game Pak interrupt.
3. Poll address 0x0110 until it reads 0.
4. Write N words to the mailbox.
5. CONTROL = 0x04, then restore IE.

Strings are 16-bit characters ending in 0. String commands always send 0x106 words: the ID plus
0x105 characters, with stack garbage after the NUL. Word 0 is the ID.

| ID | words | layout (word: value) | reply handled | meaning |
|---|---|---|---|---|
| 0x0010 | 2 | 1: junk | 0x4010 | open folder listing, return the first entry |
| 0x0011 | 2 | 1: junk | 0x4011 | next entry |
| 0x0020 | 0x106 | name (`\` root, `..` up, `.` stay) | 0x4020 | change folder; the GBA keeps a path stack 4 deep |
| 0x0030 | 4 | 1: 1, 2: 0 | - | sent at every scene change, then Timer 0 = 16384 Hz [inferred: audio reset; the Play-Yan's 0x400(1, 0) is the same pattern] |
| 0x0040 | 0x106 | file name | 0x4040 | read ID3 |
| 0x0050 | 0x106 | file name | 0x4050 | play |
| 0x0051 | 2 | - | 0x4051 | stop |
| 0x0052 / 0x0053 | 2 | - | - | pause / resume |
| 0x0060 | 2 | 1: **absolute position 0..0x6400** | - | seek: fast-forward and rewind send pos ± step every 15 frames; the step grows by 0x10 per send up to max(0x355, 0xC800 / song seconds), or the maximum at once with A held. Also resumes a saved position |
| 0x0070 | 2 | 1: 0 or 1 | - | 1 before play, 0 before background play [inferred: sample streaming to the GBA on/off; cf. Play-Yan 0x906] |
| 0x0080 | 2 | 1: volume 0..0x2E = level (0..36) x 0x2E / 0x24 | - | volume; x13/16 in one mode |
| 0x0200 | 4 | 1: sound ID, 2: clamp(level x 0x3F / 0x24, 0x10, 0x3F) | 0x8100 records | sound effect |
| 0x0300 | 0x106 | file name (loader: `meteor.fup` only) | 0x4300 | open file |
| 0x0301 | 2 | 1: byte count (≤ 0x201) | 0x4301 | read; the destination is held on the GBA side |
| 0x0302 | 4 | 1: 0, 2-3: u32 offset, low word first | 0x4302 | seek in the open file |
| 0x0303 | 2 | - | 0x4303 | close file |
| 0x0400 | 0xCE (app) | 1: play mode, 2: n1, 3: n2, 4: current index, 5-104: table A, 105: p5, 106-205: table B | 0x4400, then 0x4401 | background play: after about 30 s idle while playing, the GBA hands over the play list and blanks the screen [inferred tables] |
| 0x0401 | 2 | - | 0x4401 | leave background play |
| 0x0500 | 2 | - | - | sleep; the GBA then enters Stop |
| 0x0501 | 2 | - | 0x8501 | wake |
| 0x0800 | 2 | - | 0x4800 | sent before loading a validated meteor.fup image [inferred: prepare for firmware replacement] |

### 11.5 Status records

The interrupt handler (0x02032780) **[code]**:

1. Read 4 words at 0x0100. If the ID is 0, return (setting the watchdog flag if it is armed).
2. CONTROL = 0x08.
3. If the bus is locked, mark the interrupt pending.
4. Otherwise read **8 words** into the 16-entry ring (0x03000E50, error 8 on overflow).

The dispatcher (0x02032ED4; app 0x02038478) clears each record after handling it (writes 0 to
0x0100) and calls the callback registered for its ID (16-entry table, 0x02032D5C).

Record: w0 = ID, w1 = status or size, then three u32 fields, each **low word first**:
A = w2 \| w3 << 16, B = w4 \| w5 << 16, C = w6 \| w7 << 16.

| ID | handling **[code]** |
|---|---|
| 0x4010 / 0x4011 | w1 = 0: an entry was returned; read 0x108 words (0x210 bytes) from A |
| 0x4020 | w1 = status; 0 updates the path depth |
| 0x4040 | A != 0: read 0x88 words (0x110 bytes) of ID3 from A |
| 0x4050 | started: Timer 0 = 16384 Hz, playing = 1 |
| 0x4051 | stopped: rings cleared, playing = 0 |
| 0x4300 / 0x4302 / 0x4303 | w1 = status |
| 0x4301 | w1 = 0: read (B & 0xFFFF + 1) / 2 words from A |
| 0x4400 | w1 = status |
| 0x4401 | value = w1 \| (A bits 8-15) << 8 \| (B & 0xFF) << 16 \| (A & 1) << 24; read 4 words from C; A & 0xFFFF == 0 resets the audio path |
| 0x4800 | w1 = status |
| 0x8001 | boot complete, w1 = status, arms the watchdog |
| 0x8100 | audio / position (below) |
| 0x8501 | wake complete |
| 0x8600 | headphones: w1 = 1 plugged in, 0 not |
| other | error 1, ID kept |

The dispatcher does not handle 0x4030, 0x4052, 0x4053, 0x4060, 0x4070, 0x4080, 0x4200 or 0x4500.
Dandocs lists some of them as replies.

**0x8100** **[code]**:

- **A != 0: samples.** w1 = byte count (clamped to 0x480; 0 logs error 3). Per channel
  n = ((w1 >> 3) + 3) & ~3 words, so w1 / 2 bytes rounded up to 16. Channel 1 is read from A and
  channel 2 from **A + 2n**: A + 0x120 for 0x480 bytes. The samples are signed 8-bit, two per
  word, odd byte first. Gain x1.0 while music plays, x0.375 otherwise (sound effects).
- **A == 0 while playing: position.** B = trackbar 0..0x6400, C = elapsed seconds.

**Data blocks** **[code]**:

- Directory entry, 0x210 bytes: 16-bit-character name at +0, u16 type at +0x20C (folder or file,
  folders sort first).
- ID3 block, 0x110 bytes: title (16-bit characters) at +4, artist at +0x88, **u32 song length in
  seconds at +0x10C**.

### 11.6 meteor.fup update (loader 0x02028034-0x02028414)

This runs only after a good 0x8001, and only if the 0x20000-byte buffer was allocated (first
attempt) **[code]**:

1. 0x0300 `meteor.fup`. A non-zero status ends the update.
2. 0x0301(0x40). The header must be exactly 0x40 bytes, start with the 0x2E-byte string, and have a
   byte sum of 1 mod 256. Byte +0x3E holds the number of 16-byte directory entries.
3. 0x0301(n x 16). Select the first entry with a valid sum, byte +8 == 1, bit 1 of byte +0xD set
   and size (+4) ≤ **0x1FFFF**. Then 0x0302 to its offset (+0).
4. Repeat 0x0301(min(remaining + 1, 0x201)). Each chunk's last byte is a check byte that makes the
   chunk's sum 1 mod 256; the other bytes are appended.
5. 0x0303.
6. If everything passed, send 0x0800. On 0x4800, upload the buffer with the same 0x08AA parser. On
   a bad magic number, upload the built-in image again. Then wait for 0x8001 once more.

The app holds the same reader, but its 0x0300 sender is unreferenced.

The per-block check-byte rule is the one the firmware analysis found in `playan.fup` [fw], so the
two update formats are related.
