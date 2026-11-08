# Play-Yan family: the GBA player program

This document covers the GBA program on the Play-Yan cartridges: boot, memory map, interrupts,
scenes, firmware loading, the A/V output stage and pacing, playback controls, sleep and the Garage
Games. Sections 1-9 are the original **Play-Yan** (ZMAJ), section 10 has the **Play-Yan Micro**
deltas and section 11 covers the **Nintendo MP3 Player**. Register-level details, commands,
response records and data layouts are in [protocol.md](protocol.md), and comparison with earlier
work is in [prior-art.md](prior-art.md).

Evidence tags as in protocol.md: **[code]** from the ROM code, **[inferred]** our reading of
intent, **[fw-names]** a label from the cartridge firmware's own message-name table, **[prior]**
from shonumi or GBE+ only. Addresses are Play-Yan addresses (code runs at 0x0Cxxxxxx, the WS2
mirror) unless noted.

There is no codec on the GBA side. The cartridge decodes everything, and the GBA program copies
finished BGR555 frames and 8-bit PCM blocks out of the cartridge. So this family has no
`format.md`. The "format" is the protocol.

## 1. Builds

| cart | header | sha1 (1 MiB image) | code | data |
|---|---|---|---|---|
| Play-Yan (Japan); the iQue MP4 Player is the same ROM | `PLAYAN` ZMAJ01 | 7f432bbf6f0e5e1b8324c7d6bb1d81a254a6e870 | Thumb, ARM SDT-style, 0x0C000000-0x0C028xxx | firmware images 0x0C029710-0x0C0ACC83, graphics, tables to about 0x0C0FA7A1 |
| Play-Yan Micro (Japan) | `PLAYYANMICRO` ZMBJ01 | 17b5f7ad459448b447fa94617d09886cd4f9c554 | same toolchain, 0x0C000000 | LZ77-chunked firmware 0x0C023D08-0x0C0A1617 |
| Nintendo MP3 Player (Europe) | `MP3PLAYER` ZMDE01 | b7c517f1cb5860fec8beaa4e819bcffa8ce95a5b | linked at 0x08000000 | section 11 |

Each dump is 4 MiB, the 1 MiB image repeated four times. The cartridge decodes only the low
20 address bits for ROM [inferred from the mirroring].

## 2. Boot and memory map (original)

### 2.1 crt0

1. The header branch goes to ARM code at 0x0C0000D0 (reached through the 0x08 mirror at reset).
   It sets SP_irq = 0x03007FA0 and SP_sys = 0x03007F00. It stores the address of the ROM interrupt
   handler 0x0C00010C at 0x03007FFC as a fallback, then calls the Thumb `AgbMain` at **0x0C00024C**.
   From there on all code runs from the 0x0C mirror.
2. `AgbMain` sets **WAITCNT = 0x5DC0**: WS0 4/2, WS1 2/1, WS2 3/1, PHI output 16.78 MHz, prefetch on.
   Then it runs these DMA3 operations:
   - fill EWRAM 0x02000000-0x0203FFFF and IWRAM 0x03000000-0x03007DFF with zero;
   - copy the **interrupt handler** (ARM, 0x200 bytes) from ROM 0x0C00010C to **IWRAM 0x030017B0**;
   - copy the four-entry interrupt table from ROM 0x0C028488 to **0x03001780**;
   - write 0x030017B0 to 0x03007FFC;
   - clear VRAM 0x06000000-0x06017FFF.
3. Palette entry 0 = 0x7FFF (white), DISPCNT = 0, IME = 0. Then it runs the init chain at
   0x0C0001D8: OS/heap init, heap at EWRAM **0x0200040C, 0x38000 bytes** (0x0C0064F8), then the
   other subsystem inits.
4. **DISPSTAT = 8** (VBlank interrupt), **IE = 0x3401** (VBlank, DMA2, Keypad, Game Pak),
   IF cleared, IME = 1. The first scene is the pointer at ROM 0x0C0B4F68, which is the **title
   scene** (descriptor 0x0C0F1C34).

### 2.2 Interrupts

The IWRAM handler (from ROM 0x0C00010C) checks `IE & IF` in fixed priority order: **DMA2,
VBlank, Game Pak, Keypad**. Any other source leaves it in an endless loop. It acknowledges IF and
sets IE to `(IE & ~source) & 0x0400`, so only DMA2 stays enabled. It switches to system mode with
IRQs on and calls the table entry, then restores IE and IME **[code]**. The audio DMA2 interrupt
can therefore pre-empt the VBlank and Game Pak handlers, and nothing else nests.

| slot | source | handler |
|---|---|---|
| 0 (0x03001780) | DMA2 | 0x0C025E4C: audio ring refill and underrun (6.2). Installed by the audio init 0x0C025FE0 |
| 1 | VBlank | 0x0C001304: calls the hook at 0x03000014 (set to 0x0C00422C, the display update), sets the frame flag 0x03000010 |
| 2 | Game Pak | 0x0C024544: read and queue one response record (protocol.md 3.6). Installed by 0x0C024EF4 after each firmware upload. The unused boot path installs a stub, 0x0C0230B4 |
| 3 | Keypad | 0x0C0001D4 (`bx lr`). Used only to wake from Stop (section 8) |

### 2.3 Main loop and scenes

`AgbMain` loops forever **[code]**:

1. 0x0C001324: if the display is on, spin until the VBlank flag is set. While spinning, call the
   **idle hook** at 0x03000018 if one is set. The video player puts the response dispatcher there
   (5.1).
2. 0x0C00138C: read the keys. Held keys go to 0x03002078, new presses to 0x030020A8, and the
   auto-repeat word (24-frame delay, then every 8 frames) to 0x03002944.
3. The task scheduler and the current scene's update.
4. Soft reset: if it is enabled (0x0300179C) and A+B+Select+Start are held, the loop disables IE
   bit 13, kills the scene and starts the reset scene (0x0C0F19FC). That scene returns to the
   title scene (pointer at 0x0C0B4F6C), which reloads all firmware.

Scenes are descriptors `{?, ?, ?, init, update, ?, ?, exit, ctx_size}`. 0x0C00022C(&desc.init,
arg) switches to one. The current scene's context pointer is at 0x03001C4C.

| descriptor | init / update | scene | reached from |
|---|---|---|---|
| 0x0C0F1C34 | 0C0101B8 / 0C010864 | **title**: firmware load, update check, first list | boot, soft reset |
| 0x0C0F1C8C | 0C010CA0 / 0C01180C | **main menu**: きく (listen) / みる (watch) | title |
| 0x0C0F1D04 | 0C011B58 / 0C014B84 | **music** browser and player | menu |
| 0x0C0F1DE0 | 0C017514 / 0C01759C | "no music files" screen (音楽ファイルが いっこもないヨ〜). Chosen when the GBA's own folder walk (0x200(2) and 0x201 per folder) found no music (flag 0x03000CC9, set at 0x0C016150) | menu |
| 0x0C0F1E70 | 0C017720 / 0C018934 | **video browser**: 6 thumbnails per page, live preview, Garage Game detection | menu |
| 0x0C0F2494 | 0C0217E0 / 0C022C74 | **video player**; the argument is index \| duration << 8 | browser |
| 0x0C0F1F70[n] | protocol.md 9 | Garage Game n | browser |
| 0x0C0F24B8 | 0C0231A8 / 0C023230 | WS0 boot upload | **no reference** (protocol.md 5.4) |
| 0x0C0F24DC | 0C02363C / 0C023B50 | developer test screen: "VCプログラム ダウンロード中..." (VC program downloading), "VIDEO 再生確認" (video playback check), "AUDIO 情報取得" (audio info fetch), plus joke strings and a list of ten ゲームファイル.asf names | **no reference** |
| 0x0C0F2814 | 0C0264A8 / 0C026510 | small scene with no command traffic; its purpose is unknown | **no reference** |

### 2.4 Memory map

| region | contents **[code]** |
|---|---|
| ROM 0x0C000000-0x0C028xxx | Thumb code (crt0, OS and scenes, driver 0x0C0243B4-0x0C026590, Garage Games 0x0C0198F4-0x0C0217DF), C library |
| ROM 0x0C028488 | initial interrupt table |
| ROM 0x0C028A00-0x0C029710 | Shift-JIS UI strings, staff credits, test-screen strings |
| ROM 0x0C029710-0x0C0ACC83 | firmware fw0, fw1, fw2 (protocol.md 5.1) |
| ROM 0x0C0F1A00-0x0C0F28B0 | scene descriptors, Garage Game tables (0x0C0F1EA8, 0x0C0F1F70), video seek table (0x0C0F2478), brightness table (0x0C0F2500), tone table (0x0C0F2518), sort table (0x0C0F251A), RAM-test pattern (0x0C0F25DC), callback ID table (0x0C0F27DC) |
| IWRAM 0x03000000-0x0300001F | scene switch (0x03000000/04), scheduler, frame flag 0x03000010, VBlank hook 0x03000014, idle hook 0x03000018/1C |
| IWRAM 0x030012B0-0x030012B5 | user settings: volume 0..36 (default 18), brightness 0..5 (default 0), tone 0..1, 3D 0..6, a byte set to 1, loaded media (1 video, 2 music) |
| IWRAM 0x030012B8 | resume table: 99 x u32 seconds, one per video-list index |
| IWRAM 0x03001448-0x03001664 | driver state: CTRL shadow, lock and pending flags, the response ring (16 x 32 bytes at 0x03001458) |
| IWRAM 0x03001680-0x030016BA | latched positions, blit mode and destination, thumbnail buffer descriptor |
| IWRAM 0x030016C0 / 0x030016F8 | callback function and user tables (14 entries each) |
| IWRAM 0x03001730-0x03001758 | audio output state (6.2) |
| IWRAM 0x03001780 / 0x030017B0 | interrupt table / interrupt handler |
| IWRAM 0x03002B10-0x03002B18 | start and stop timeouts, RAM-test flag |
| IWRAM 0x03002CE0 | ID3 block (0x8C bytes) |
| IWRAM 0x03002D78, 0x03002D98 | pointers to two 0x67AC-byte list buffers |
| IWRAM 0x03007F00 / 0x03007FA0 | system and IRQ stacks |
| EWRAM 0x0200040C-0x0203840B | heap: list buffers, audio rings (2 x 0x900), thumbnail buffer (0x12C0), scene contexts (the browser's is 0x50E4) |
| VRAM 0x06000000 | mode-3 frame buffer that the cartridge's frames are copied into |

## 3. Firmware loading

### 3.1 Title scene sequence

The title scene's init (0x0C0101B8) sets CTRL to 0x0A and pulses bit 4. It allocates the list
buffers, resets the dispatcher and starts the audio output at 8192 Hz (0x0C023E68), then runs a
state machine in the low byte of ctx+4 (bits 1-4) **[code: switch at 0x0C0108C8, table
0x0C0108D8]**:

1. **State 1** (after the fade-in): register the 0x80000100 callback. Upload **fw2** (loader) with
   0x0C0251C4. If the chip never became ready, hang. Then disable the Game Pak interrupt, run the
   **RAM test** (protocol.md 5.3), re-enable it, and go to state 0 (wait).
2. 0x80000100 VC_STARTUP_INDICATION from the loader: rec[1] != 0 shows an error screen (4.2).
   Otherwise register 0x40008000 and send **0x8000** FIRMWARE_LIST.
3. 0x40008000: keep rec[2] as the update-parts byte (bit 0 video, bit 1 music). Register
   0x80000100 and 0x40800000. If bit 1 (music) is set, send **0x8001(2)**; its confirm
   0x40008001 then sends 0x800000. Otherwise send **0x800000** RESET_STANDBY directly.
4. 0x40800000: start the incremental upload of **fw1** (music) with 0x0C024F8C(1), state 3.
5. **State 3**: 0x0C025040 copies 16 KiB per frame. When it is done it writes the kick and
   installs the handler, state 0.
6. 0x80000100 from the music firmware: rec[1] == 0 sends **0x200(2)** (music list), and sets
   loaded media = 2.
7. 0x40000200: state 4 shows the logo (B skips it). State 5 sends **0x400(1, 0)**
   AUDIO_MODE_SET and sets the audio rate to **16384 Hz** for the intro. States 6 and 7 animate,
   then the main menu starts.

**Update path [inferred].** If an update part exists, the GBA still sends 0x800000 after 0x8001,
and has a 0x80000100 callback registered at the same time as the 0x40800000 one. Firmware
analysis found that `playan.fup` holds two complete images, not patches. The most consistent
reading is this: after FIRMWARE_DOWNLOAD, the loader answers RESET_STANDBY by starting the
downloaded image, which sends VC_STARTUP_INDICATION, instead of answering 0x40800000. The ROM image
is then never uploaded. Without a download the loader answers 0x40800000 and the GBA uploads the
ROM image. Confirming this needs the loader's 0x800000 handler.

### 3.2 Switching between music and video

The main menu tracks the loaded media type (0x030012B5) **[code: 0x0C0118E0-0x0C011970,
0x0C0113F4]**:

- Same type as loaded: no firmware traffic.
- Different type, and no update part for it: send **0x800000** to the running firmware. On
  0x40800000, upload fw0 (video) or fw1 (music) incrementally. The callback's user word is 0 for
  video and 1 for music.
- Different type, with an update part for it: re-upload **fw2** (0x0C0251C4). On its
  0x80000100, send **0x8001(1)** for video or **0x8001(2)** for music. Then 0x40008001 leads to
  0x800000, as in 3.1.

A non-zero 0x80000100 status here shows "AC97 ERROR!" (0x0C01134C).

### 3.3 Timing

The incremental upload copies 16 KiB per frame: 13 frames for fw0 and 16 for fw1. The one-shot
fw2 upload and the RAM test (writes and read-back of 75 KiB) run inside one frame's code without
yielding. We have no measurement of the cartridge-side delays.

## 4. Status and error screens

### 4.1 Response bookkeeping

See protocol.md 7.2 for the dispatcher. The scenes register and unregister callbacks around each
operation. For example, the video player registers 0x40000700, then on it 0x80001000 and
0x40000701, and on 0x40000701 unregisters 0x80001000.

### 4.2 Error screens (0x0C010478)

0x80000100 with a non-zero rec[1] during boot selects the text (strings at ROM 0x0C028A98-0x0C028B7F):
1 カートリッジが調子悪いみたい... (cartridge problem); 2 SDカードが調子悪いみたい... + 電源を切って
SDカードをチェックしてネ。 (SD card problem; turn off and check the card); 3 SDカードが入ってないヨ！ +
電源を切って SDカードを入れてネ。 (no SD card); 4 ファイルがおかしいみたい。 (bad file); any other
value: エラーが出ちゃいました... (an error occurred). The program then waits forever **[code]**.

## 5. Video playback

### 5.1 Pacing

All video timing comes from the cartridge. Each DECODE_DATA_INDICATION (0x80001000) whose rec[1]
is non-zero names a finished frame in VC memory. The GBA copies it with the Game Pak interrupt
disabled: **0x12C00 bytes through window 0xB000500 straight into VRAM 0x06000000** (protocol.md
7.4). There is no GBA-side frame clock, no double buffer and no frame dropping. The copy happens
when the dispatcher runs. The video player installs the dispatcher as the **idle hook** while it
waits for VBlank (0x0C021C80 sets the hook to call 0x0C025454 in the scene context). So records are
drained continuously during the idle part of each frame, as well as from the scene update. The
response ring holds 16 records. The cartridge must keep a frame's buffer valid until the GBA has
copied it, but nothing in the GBA code tells the cartridge when a frame has been read
[inferred].

The 0x80001000 callback (0x0C021B8C) counts frames (ctx+0x4A) and audio blocks (ctx+0x4C). The
clock shows rec[6] / 1000 seconds as H:MM:SS, and the progress bar is `seconds x 0xE0 / duration`.
The duration comes from the thumbnail record (rec[3] / 1000) and is passed in the scene argument.

### 5.2 Controls **[code: 0x0C0221C4, 0x0C022464/0x0C022570, 0x0C022810, 0x0C022B0C, 0x0C022BE0]**

| input (state) | effect |
|---|---|
| Up / Down (auto-repeat) | volume -/+ 1 (0..36), sends 0xB00 |
| L / R | brightness index -/+ 1 (0..5), sends 0x300 with the table value |
| START | pause: 0xB00(0) mute, 0x902 PAUSE. The auto-sleep timer restarts at 30 s |
| B | stop: register 0x40000701 with the current position as user word, send 0x701 |
| Right / Left held (playing) | fast-forward / rewind: 0x905 with a signed step (5.3) |
| Right (paused) | 0x903(1) STEP, repeated every 15 frames while held |
| Left (paused) | step back: hide frames (0x030016AC = 1), 0x904 RESTART; 10 frames later 0x905(0); 15 frames later 0x905(-1), show frames again, then 0x902 PAUSE again |
| A, B or START (paused) | resume: 0x904 and restore the volume |
| L + R | turn on auto-sleep (section 8) |

### 5.3 Seek step (0x0C0223A4)

The step is computed once per seek message:

- d = video length in seconds. u = d / 120, at least 8. If u x 240 / d > 10, then u = d / 24.
  If u is 0, then u = 1.
- The hold time h counts frames the button has been held, capped at 9999. Holding A sets h to
  9999. The table at 0x0C0F2478 gives a cap: h < 150: 1; < 240: 2; < 360: 4; < 600: 8; else no
  cap.
- step = min(u, cap) x clamp(frames since the last seek, 4, 60) / 4, negated for rewind.

A new 0x905 is sent only after at least 4 frames **and** after a video record has arrived since
the last seek (flag ctx+8 bit 4, set by the 0x80001000 callback). The message rate therefore
follows the cartridge's frame rate. The units of the step are not proven on the GBA side. They
are seconds if, as for resume (5.4), 0x905 takes seconds **[inferred]**.

### 5.4 Resume ("the 6-second rule")

Every frame while playing, if the clock reads more than 5 s, the player stores the whole-second
position in ctx+0x50 (0x0C0221C4). On stop, the 0x40000701 callback (0x0C021C34) receives that
position as its user word and stores `p - 5`, clamped: below 0 it becomes 0, 0..4 becomes 5. The
value goes into the 99-entry resume table at 0x030012B8, indexed by list position **[code]**.

The result:

- a video stopped at 5 s or earlier stores 0, and the next start is from the beginning;
- stopped at 6-9 s it resumes at 5 s;
- stopped later it resumes 5 s before the stop point.

At the next start of that index (0x0C021CE8), a non-zero entry mutes (0xB00(0)) and sets flag 8.
On the first stream record without audio, the player sends **0x905(resume seconds)** and **0x904**.
Ten such records later it restores the volume (0x0C021B8C). The resume value is an absolute time,
and playback has just started from 0. So either 0x905 is absolute for video, or it is relative and
the two cannot be told apart at this point. Together with 5.3 (small signed steps), a **relative,
signed seconds** reading is the only one consistent with both uses **[inferred]**.

## 6. Audio

### 6.1 Sources and rates

The cartridge produces all sound. Music, the video soundtrack and the menu sound effects (0x2000
OPERATION_SOUND) all arrive as 8-bit PCM blocks in DECODE_DATA_INDICATION records. They are
scaled by the gain for the play state: x1.5 video, x1.0 music, x0.375 otherwise (protocol.md
7.4). The GBA's software volume (0x030012B0) is **not** applied to the PCM. The user volume goes
to the cartridge as 0xB00 [code: 0x0C026158 reads the setting and discards it]. Rates
**[code]**:

| when | rate | set by |
|---|---|---|
| audio init, video player, menus, after a music stop | **8192 Hz** | 0x0C026048(0x2000), 0x0C02610C(0x2000) |
| title intro, after a music start confirm (0x40000800) | **16384 Hz** | 0x0C02610C(0x4000) |

With headphones plugged in, prior work reports that the cartridge sends no audio blocks and
plays through its own jack [prior]. The GBA code has no headphone state and just plays whatever
blocks arrive.

### 6.2 Output stage (0x0C025FE0, 0x0C026048, 0x0C025E4C, 0x0C0261D0, 0x0C026324)

- Two rings of **0x240 words = 0x900 bytes = 2304 samples** each, A at *0x03001734 and B at
  *0x03001738. The write index 0x03001750 and read index 0x0300174C are in words.
- **SOUNDCNT_H = 0xA90E**: FIFO A to the right speaker only, FIFO B to the left only, both at full
  volume on Timer 0. SOUNDCNT_X = 0x80, SOUNDCNT_L = 0x77.
- DMA1: ring A to FIFO A, DMA1CNT = 0xB6000000. DMA2: ring B to FIFO B, DMA2CNT = 0xF6000000, the
  same plus the IRQ enable.
- **Timer 0** at prescaler 1. Reload = `trim - 0x1000000 / rate`: 1024 cycles per sample at
  16384 Hz, 2048 at 8192 Hz.
- **Block arrival** (0x0C0261D0): each block adds n bytes per channel (protocol.md 7.4: n =
  rec[4] / 2 rounded up to 16) at the write index, splitting at the ring end. A full 0x480-byte
  block is 576 samples per channel: 35 ms at 16384 Hz, 70 ms at 8192 Hz. A full ring is 4 blocks.
- **DMA2 interrupt** (every FIFO B refill, 16 bytes = 4 words): read index += 4, wrapping at
  0x240. At the wrap, both DMA source registers are reloaded to the ring start. If the read index
  catches the write index (**underrun**), the handler steps back one 16-byte unit in both rings,
  fills it with copies of its last sample, and points both DMAs at it. That holds the last level
  until new data arrives.
- **Rate trim** (0x0C026324, called every frame by the music and video players). The fill level
  in 1/256 of the ring is averaged as `avg = avg - avg / 64 + fill`. Every 60 frames, with
  f = avg / 64:

  | f | trim (cycles) |
  |---|---|
  | 0-42 | 0 |
  | 43-81 | -3 |
  | 82-84 | -2 |
  | 85-120 | unchanged |
  | 121-135 | 0 |
  | 136-170 | unchanged |
  | 171-173 | +2 |
  | 174-255 | +3 |

  The timer period is `base - trim`. A ring running full plays faster, a ring running low plays
  slower: about ±0.15-0.3 % at 8192 Hz. This is the only GBA-side A/V coupling. It locks the GBA
  sample clock to the cartridge's delivery rate.

## 7. Music playback

The music scene (0x0C011B58) **[code]**:

1. Lists with 0x200(2) and 0x201 (folder changes, with `..`). The list is sorted folders first
   (0x0C0241A4).
2. Before playing: **0x600** AUDIO_INFO with the file name. On 0x40000600 (ID3 block copied when
   rec[1] != 0), it keeps the length in seconds (+0x88). The trackbar step is 0xC800 / length, at
   least 0x355.
3. **0x906(1)** (position indications on), the volume (0xB00), then **0x800** with the name. On
   0x40000800 the rate becomes 16384 Hz; then 0xD00(3D level x 15 / 6) and 0xD01(tone) are sent.
4. Each 0x80001000 without audio latches the trackbar (rec[5], 0..0x6400) and the elapsed seconds
   (rec[6]).
5. **Seek** (0x0C013B90): while Right is held, every 15 frames the step grows by 16 up to the
   maximum (holding A uses the maximum at once). It sends **0x905(min(trackbar + step, 0x6400))**,
   an absolute trackbar target. Rewind is the mirror image.
6. Stop: 0x801. At the end of a song the cartridge sends 0x40000801. The rate returns to 8192 Hz
   and the rings are cleared. The play mode (repeat and shuffle, ctx+9 bits) picks the next file.

## 8. Sleep

Auto-sleep is armed by START in the main menu (toggle 0x03002D94) or by L+R in a player. Once
armed, 30 s without input (0x708 frames) leads to sleep, with a warning icon for the last 6 s
(0x168 frames). Any key press restarts the 30 s **[code: 0x0C014844, 0x0C022748]**.

- **Music playing** (state 4): **0x906(0)** turns position indications off, so the cartridge stops
  raising interrupts for the trackbar. The GBA then calls 0x0C0240C8:
  1. KEYCNT = 0x40FF, an interrupt on any of A, B, Select, Start or the D-pad;
  2. timer 0 off, DISPCNT = 0, backdrop colour 0x7C00 (0x0C0263C4);
  3. SoundBias(0), then BIOS **Stop** (SWI 3), then SoundBias(1);
  4. display restored, KEYCNT = 0.

  The Game Pak interrupt stays enabled, so a cartridge record also wakes the GBA. After waking,
  0x0C024C18 runs the interrupt handler body once by hand and looks at the newest record. If it is
  0x40000801 (the song ended) the player marks it. The player then sends **0x906(1)**. Over the
  cartridge's own jack the music goes on while the GBA is stopped [prior]. Over the GBA speaker
  nothing plays while it is stopped [code].
- **Otherwise**: **0x10000** SLEEP_REQUEST, then the same Stop sequence at once, without waiting
  for a reply. After a key wakes the GBA, a task 30 frames later writes the **kick 0xA5A50032 to
  VC 0x10000400** again and waits for **0x80010001** WAKEUP_INDICATION. That callback restarts
  the inactivity timer and re-enables soft reset **[code: 0x0C01161C/0x0C0115F4/0x0C0115B0;
  0x0C0147FC; 0x0C018704; 0x0C022708/0x0C0226E0]**.

## 9. Video browser and Garage Games

The browser (0x0C017720) sets the window blit mode (0x0C0252F8(1)) and a 60x40 thumbnail buffer
(0x0C025310(buf, 60, 40)). It lists all videos with **0x200(1)**, which the cartridge returns
recursively [prior]. For each of the 6 visible entries it sends **0x500(60, 40, 0, 0, name)**. On
each 0x40000500 the thumbnail is copied (window 0xB000500) and drawn into its cell, the duration
rec[3] / 1000 is kept, and the Garage Game check runs (protocol.md 9).

The highlighted entry also gets a **live preview**: **0x700(name, 60, 40)**, with frames copied as
0x12C0-byte 60x40 images into the cell (destination set by 0x0C025304 at 0x0C018C34) **[code:
0x0C018BCC-0x0C018C3E]**. So the cartridge decodes at the size the GBA asks for in 0x700.
Selecting a normal file starts the player scene with the argument index | duration << 8. Selecting a
file whose hash matched starts Garage Game n from the table at 0x0C0F1F70. The game code is in the
ROM, and the ASF file only unlocks it. That the games use the playing ASF in the 60x40 window comes
from prior work [prior]. The GBA code agrees, since the window blit path exists only for this.

## 10. Play-Yan Micro

All addresses in this section are Micro addresses. Full detail: [micro.md](micro.md).

- Same driver, scene framework and output stage, moved: the driver is at 0x0C01DA00-0x0C01FDD4.
- **Commands** go through the FIFO port: mode 0x28 to 0x0B000000, one halfword per STAT bit-7
  handshake, with the Game Pak interrupt masked. The command structures are byte-identical to the
  original's, 32-bit words included (protocol.md 10).
- **Firmware**: the three images are stored as BIOS LZ77 chunk lists and decoded 1 KiB at a time
  (SWI 0x11). The boot order is the same: fw C (loader), RAM test (VC 0x10000-0x22BFF, retried
  once), 0x8000, then 0x8001(2) or 0x800000, then fw B (music). Switching between the music and
  video menus re-uploads C and then A or B. There is no WS0 boot path.
- **Key file** (title state 10, only if the settings were not loaded yet):
  1. **0x3000** FILE_OPEN with `play_yanmicro.ini`.
  2. On 0x40003000 with rec[1] == 0: **0x3001** FILE_READ with max 0x100.
  3. 0x40003001 copies rec[2] bytes from VC rec[3].
  4. **0x3003** FILE_CLOSE. The parse runs on 0x40003003.

  Parser (0x0C011888):
  - a UTF-16LE BOM is accepted (every second byte is taken);
  - at most 255 bytes and 16 lines;
  - each of the 8 `PLAY_YAN_*` keys is matched by exact, case-sensitive prefix;
  - spaces, tabs and `=` are skipped;
  - a value is an optional `-` and 1-4 digits, and **must be followed by a space, tab, CR or LF**,
    so the last line needs a newline.

  Any lexical error discards the whole file. A Design or StartMode outside 0..1, or an
  AudioPlayMode or VideoPlayMode outside 0..3, skips only that key. In both cases the "bad key
  file" icon shows (status 0xFE). A missing file shows nothing (0xFF). Other mappings:
  - VolumeAdd sets the volume level to clamp(v + 18, 0, 27);
  - Bass is clamped to 0..1, Wide to 0..6 and VideoBright to 0..5;
  - the defaults are Design 0, StartMode 1, volume 18, all else 0;
  - L or R held at boot inverts Design [inferred].
- **File lists**: 272-byte entries, type byte at +0x10C, 0x6938-byte copy.
- **Auto-play** (new): 0x4000 VC_AUTO_PLAY_START [fw-names] hands the cartridge the play mode and
  two 100-entry u16 arrays (track flags, play order), and the GBA forces a blank screen. 0x4001
  and 0x40004001 return the play order and the current track on wake, after which the GBA sends
  0x906. The reading as "keep playing a queue while the GBA sleeps" is [inferred].
- No Garage Games, and no test or WS0 scenes.

## 11. Nintendo MP3 Player

Protocol details are in protocol.md 11, and full notes with addresses in [nmp.md](nmp.md).

- **Structure** **[code]**. A header stub runs the **boot loader** (ROM 0x090000, copied to EWRAM
  0x02028000). The loader uploads the DSP firmware, checks for `meteor.fup` and returns through a
  hand-off block at 0x0203FF80: {next 0x08010000, 0x08090000, boot_status, headphone flag}. The
  stub then starts the **player app** (ROM 0x010000, copied to EWRAM 0x0201C000). If the app sees
  that it came from the loader, it keeps the screen, takes over the boot status (non-zero gives the
  error screen at 0x0202CB08) and takes the headphone flag. It also has its own copy of the upload
  code for recovery. Both programs run from EWRAM; the code is not executed from ROM.
- **Interrupts**: the same nested handler as the Play-Yan, copied from 0x08090130 to IWRAM
  0x030011A0, with the four-slot table (DMA2, VBlank, Game Pak, Keypad) from 0x080A8AA8 to
  0x03001160.
- **Boot timing**: each attempt waits up to 8 s for 0x8001, and there are at most 4 attempts.
  Afterwards a watchdog reads the DSP tick counter at least every 60 frames and expects about
  0.87 ticks per GBA frame, so the tick counter must run at about 52 Hz or faster. The app recovers by
  re-uploading the firmware and replaying volume, folder and position (protocol.md 11.3).
- **Audio**: the same output stage as the Play-Yan: two 0x900-byte rings, FIFO A (first half of
  each block) on the right speaker and FIFO B on the left, the DMA2 underrun hold and the ±3-cycle
  Timer 0 trim. The only rate is **16384 Hz** (there is no video). Gain x1.0 for music, x0.375 for
  sound effects. The volume (0x0080) acts only on the cartridge output; the GBA PCM is not scaled
  by it.
- **Music**: listing goes one entry per command (0x0010, then 0x0011 until the status is non-zero)
  with a path stack 4 deep. Then 0x0040 (ID3; the song length at +0x10C sets the seek step
  0xC800 / length, at least 0x355), 0x0070(1) and 0x0050. Position records (0x8100 with A = 0) carry
  the trackbar 0..0x6400 and the elapsed seconds. Seeking is 0x0060 with an absolute target every
  15 frames, the same scheme as the Play-Yan music player.
- **Background play** **[inferred]**: after about 30 s idle while playing, the app sends 0x0400
  with the play mode, current index and two 100-entry tables, then blanks the screen. 0x0401 /
  0x4401 bring back the current track. This is the counterpart of the Micro's 0x4000/0x4001.
- **Sleep**: 0x0500, then BIOS Stop; 0x0501 after waking, answered by 0x8501.
- **Headphones**: unlike the Play-Yan GBA code, the NMP handles a headphone record (0x8600, w1 = 1
  plugged in).

## 12. Open questions

- What the two read windows (0xB000300 and 0xB000500) differ in, and why only frames and
  thumbnails use the second one, and only that one gets streaming mode.
- CTRL bit 4 (pulsed once at boot), the GO register's exact role, and which VC word the IRQ
  acknowledge clears.
- The purpose of the dummy 256-byte FIFO read of VC 0x80000 and of the poll count written to
  VC 0xFF080 after every reset.
- 0x400 AUDIO_MODE_SET(1, 0): sent on entering every audio scene, never confirmed. What do its
  two words select?
- The units of the video 0x905 argument, and whether it is relative (5.3, 5.4). Also the meaning
  of 0x905(0) followed by 0x905(-1) in step-back.
- The update path after 0x8001 (3.1, [inferred]). This needs the loader's 0x800000 handler from
  the firmware side.
- How the cartridge knows a frame or audio block has been consumed. The GBA code never signals
  it, so the cartridge presumably double-buffers or paces by its own clock.
- Whether anything shows the error-log FIFO, for example a debug build.
- NMP: which byte the DSP really sends first for audio samples. The GBA treats the odd-address
  byte as first, which is proven for file data only.
- NMP: what 0x0030 and 0x0070 do, the contents of the 0x0400 tables and of the 4 words read on
  0x4401, and whether the DSP sends records for 0x52, 0x53, 0x60, 0x70, 0x80, 0x200 and 0x500 at
  all.
