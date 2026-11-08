# Nintendo MP3 Player (ZMDE, Europe): GBA program and cartridge protocol (working notes)

Scope: `Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba`, first 1 MiB (sha1 `b7c517f1...`), mapped
at 0x08000000. Everything below is read from the ROM's ARM/Thumb code unless tagged.
Tags: **[code]** read from the code (Ghidra decompile + capstone), **[inferred]** our reading of
names/purpose, not provable from the GBA side alone. "idx" = the 16-bit-word address the GBA puts
on the cartridge bus (dandocs calls it the "index").


## 1. ROM layout: three programs, not one

| ROM range | Content |
|---|---|
| 0x000000-0x00015F | Header + ARM crt0 at 0x080000D8: clears IWRAM/EWRAM, stores `[0x080000D4]` (=0x08090000) at EWRAM 0x0203FF80, then loops: `bx [0x0203FF80]`, and re-reads 0x0203FF80 when the callee returns (0x08000124-0x0800013C) [code] |
| 0x010000-0x068C6B | **Main application** ("app"). ARM crt0 at 0x08010000; copies ROM 0x047C5C-0x068C6B (0x21010 B) to EWRAM 0x0201C000 and enters Thumb 0x0201C10D (crt literals at 0x08010274-0x0801028C) [code] |
| 0x090000-0x0D9433 | **Loader** (boot/firmware program). ARM crt0 at 0x08090000; copies ROM 0x0CB394-0x0D9433 (0xE0A0 B) to EWRAM 0x02028000, entry Thumb 0x02028535 (literals 0x08090274-0x0809028C). IWRAM copy is empty (0x030036B0, length 0) [code] |
| 0x0A8AB8 | ASCII `Nintendo MP3 Player firmware update program\r\n` (0x2E bytes compared) [code] |
| 0x0A8AE8 | `meteor.fup` [code] |
| 0x0A8F30 | `..` (byte string, used by the loader's directory sort) |
| 0x0A8F34-0x0C7D93 | **Built-in cartridge firmware image**, 0xF730 halfwords (0x1EE60 B); length from `0x02034060` (returns 0xF730) [code] |

Hand-off (loader 0x02028534 tail) [code]: loader writes `0x0203FF80 = {0x08010000, 0x08090000,
boot_status, headphone_flag}` and returns; crt0 then jumps to the app. The app (0x0201C10C) checks
`0x0203FF84 != 0` ("came from loader"): it keeps the screen (skips the VRAM clear), takes
`boot_status` (0x0203FF88; non-zero -> error screen via 0x0202CB08) and the headphone flag
(0x0203FF8C -> 0x030041C4), then clears 0x0203FF84 (app 0x0202D8xx region). The app also contains its
own copy of the boot/upload code for the case it is started without the loader [code].

Both images link the **same cartridge driver library**; loader copy at 0x02031310-0x020340BC, app
copy at 0x02036890-0x02039680 (byte similarity 0.92; differences: a larger 0x400 command and a
different 0x4401 handler in the app). Addresses below are loader addresses unless marked "app".
App equivalents: register prims 0x02036890..0x02036930, block write 0x020369DC, block read 0x02036A50,
dispatcher 0x02038478, command send 0x02038AE0.

WAITCNT = 0x45C0 in both programs (SRAM region 4 cycles, WS0 4/2, WS1 2/1, WS2 3/1, prefetch on,
PHI off) [code]. The cart registers sit in the 8-bit SRAM region (0x0E000000).

## 2. Registers

All registers are byte-wide at even/odd pairs in the SRAM region; every access is `ldrb/strb`, even
address first [code: 0x02031310-0x0203138C].

| Address | Dir | Use [code] |
|---|---|---|
| 0x0E000000 | W | CONTROL. Written as a halfword `(v<<8)|v` (so the byte lands whatever the bus lane). Values used: 0x10, 0x00, 0x04, 0x08 (below) |
| 0x0E008000 / 0x0E008001 | W | PARAMETER (address) port. 16-bit value, **MSB to +0, LSB to +1** |
| 0x0E004000 / 0x0E004001 | W | DATA IN. 16-bit, MSB to +0 then LSB to +1 |
| 0x0E006000 / 0x0E006001 | R | DATA OUT. GBA reads +0 then +1 and forms `(b0<<8)|b1` |
| 0x0E002000 / 0x0E002001 | R | 16-bit read helper at 0x02031320 (same pattern); **no callers** |
| 0x0E00A000 / 0x0E00A001 | R | 16-bit read helper at 0x02031350; **no callers** |
| ROM GPIO 0x080000C4/C6 | W | **Cartridge chip reset**, bit 3: DATA=0, DIR=8, ~100-iteration delay, DATA=8, delay (0x020312C8; app 0x02036848) |

Byte order consequence: a 16-bit quantity (IDs, lengths, chars of a string) is big-endian on the
bus (+0 = MSB). A **byte stream** carried in halfwords (file data, audio samples) is stored by the
GBA as the little-endian u16 `(b0<<8)|b1`, so **the byte read from the odd address (+1) comes first
in memory/time**. This is proven by the update-file header compare (section 6) working on bytes
read this way [code].

### Address set (0x020313B0, `set_addr(idx, is_write)`) [code]
1. Bank fold of the 32-bit idx: 0x8000-0xFFFF -> `| 0x10000`; 0x10000-0x17FFF, 0x20000-0x27FFF,
   0x30000-0x37FFF, 0x40000-0x47FFF -> `& 0xFFFF`; everything else unchanged.
2. If writing: idx -= 1 (write accesses are issued one word early; reads are not) [inferred: the
   chip pre-increments on write].
3. CONTROL=0x10; PARAM = idx>>16; CONTROL=0x00; PARAM = idx & 0xFFFF.

Then N halfwords are streamed through DATA IN (0x0203145C) or DATA OUT (0x020314D0), with the Game
Pak IRQ (IE bit 13) masked for the duration. CONTROL 0x10 / 0x00 therefore select "high word" /
"low word" of the address; the low-word write starts the access [inferred]. dandocs'
INPUT_PARAMETER 0x0000-0x0003 is this high word (page); the firmware uses pages 0-3.

### idx values (raw value on the bus in brackets) [code]
| idx | access | meaning |
|---|---|---|
| 0x0100 | R | status record (8 halfwords) |
| 0x0100 (raw 0x00FF) | W 1 word = 0 | clear status record after it was processed (0x02032E70). dandocs' "0x00FF data flush" |
| 0x010F | R 1 word | tick counter (0x02031A70) |
| 0x0110 (raw 0x010F) | W N words | command mailbox. dandocs' "0x010F send command" |
| 0x0110 | R 1 word | busy; polled until 0 before every command (0x02033518) |
| 0x007E (raw 0x007D) | W 2 words | firmware entry point (hi, lo) |
| any u32 from a status record | R | data payload (dir entry, ID3, file chunk, audio) |

### CONTROL values [code]
| value | where | meaning |
|---|---|---|
| 0x10 / 0x00 | set_addr | address high / low phase |
| 0x04 | after a command's words (0x020313A4) | command doorbell |
| 0x08 | after the entry-point write (0x02032B30), and at the start of every Game Pak IRQ (0x02032780) | start firmware / acknowledge IRQ [inferred] |

## 3. Firmware upload [code]

`fw_load_builtin` 0x02032C20 = `upload(0x080A8F34, 0xF730 hw)` (0x02032B30) then `irq_init`
(0x020329EC: clear ring, IE |= 0x2000, Game Pak slot of the IRQ table 0x03001160+8 = 0x02032781).
`upload_from_buffer(buf, bytes)` 0x02032B9C is the same parser over a RAM buffer (used for meteor.fup).

Image format (u16 little-endian in ROM, each sent MSB first):
`hw0 = 0x08AA` magic (else fail, return -1) ; hw1, hw2 skipped (0x7FFF, 0xF800 in the built-in
image) ; hw3:hw4 = entry (hi:lo, 0x000042FC built-in) ; then records `{count, addr_hi, addr_lo,
count data words}` until `count == 0`. Each record: `set_addr(addr, write)` + stream. Afterwards, if
entry != 0: `set_addr(0x7E, write)`, DATA = entry_hi, entry_lo, CONTROL = 0x08. Reset (GPIO bit 3)
is pulsed before parsing.

Built-in image: 91 records, 0xF619 data words, terminator at hw 63279 of 63280. Contiguous
destination ranges (word addresses before bank fold; source = ROM address of first data word):

| words | count | records | src |
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

There is no chunking beyond one record per address set, no read-back verify and no checksum.

### Boot sequence (loader main 0x02028534) [code]
1. WAITCNT, copy IRQ handler 0x08090130 -> 0x030011A0 (0x200 B), IRQ table 0x080A8AA8 -> 0x03001160
   (4 slots: DMA2, VBlank, Game Pak, Keypad: the same nested-priority handler as the Play-Yan).
2. Attempt loop, at most 4 attempts: `attempt_init(first)` (0x02028414): on the first attempt only,
   allocate a 0x20000-byte update buffer; driver init (0x02031544: state reset, audio rings,
   Timer0 for 16384 Hz, volume 0x16); register callback for 0x8001; `fw_load_builtin`; state 0.
   Then run the dispatcher every frame until state 8 ("done") or 0x1E0 frames (8 s) pass. On timeout
   free the buffer and retry (retries never attempt an update). After 4 timeouts boot_status = 2.
3. Keep running the dispatcher until 90 frames after loader start, then mask everything (IME, DMA0-3,
   IE=0, IF cleared, DISPSTAT) and hand off (section 1).

The **0x8001** record ends the boot: its hw1 is the boot status (0 = OK; copied to boot_status).
With hw1 = 0 and an update buffer present the loader starts the meteor.fup path (section 6),
otherwise state 8.

### Tick watchdog (0x02031BDC, called at the top of every dispatcher run) [code]
Armed by the 0x8001 record (0x02031B20(1)); disarmed state = -1. Every >= 60 GBA frames (frame
counter 0x03001CA4) it reads idx 0x010F. Ticks are a free-running u16. With `dt` = frames elapsed and
`dk` = tick delta (mod 0x10000): `dk < 2` -> -1 (stalled); else OK iff
`(((0x10AAAA*dt) >> 8) * 0xD5) <= dk << 20`, i.e. **dk >= ~0.8667 * dt** (about 52 ticks/s at
59.73 Hz); otherwise -2. Any negative result sets error flag bit 0 (0x02031AF4(1)). [inferred] The
cart tick is a ~60 Hz counter. In the IRQ handler, a record whose ID is 0 while the watchdog is armed
sets error flag bit 1 (0x020327C6). The app's recovery routine (app 0x020377F4 + 0x02031C70)
re-uploads the firmware (state machine with 120-frame timeout, 8 retries) and replays volume,
directory path and the playing position when these flags are set [code; trigger wiring inferred].

## 4. Commands (GBA -> cart)

Send (0x02033534; app 0x02038AE0) [code]: record the command in the replay tracker (0x02031CA8),
mask Game Pak IRQ, poll idx 0x0110 until 0, write N halfwords to the command mailbox, CONTROL=0x04,
restore IE. Each sender also sets a "pending" bit (0x02031A0C(n)); the matching response clears it.
Strings are 16-bit characters (u16 per char, 0-terminated) taken from the GBA's own wide strings;
string commands always send 0x106 halfwords (ID + 0x105 chars incl. NUL, rest stack garbage).

| ID | words | layout (hw index: value) | pending bit | response | app caller(s) / meaning |
|---|---|---|---|---|---|
| 0x0010 | 2 | 1: junk | 0 | 0x4010 | open directory listing, return first entry |
| 0x0011 | 2 | 1: junk | 1 | 0x4011 | next entry |
| 0x0020 | 0x106 | 1..: name | 2 | 0x4020 | change directory. `\` = root, `..` = up, `.` = stay; GBA keeps a path stack of at most 4 levels (0x02031DC0) |
| 0x0030 | 4 | 1: 0x0001, 2: param (always 0), 3: junk | 18 | none handled | sent as (1,0) at every scene change, followed by Timer0 = 16384 Hz [inferred: audio-stream reset] |
| 0x0040 | 0x106 | 1..: filename | 3 | 0x4040 | read ID3 tags |
| 0x0050 | 0x106 | 1..: filename | 4 | 0x4050 | play file |
| 0x0051 | 2 | - | 5 | 0x4051 | stop |
| 0x0052 | 2 | - | 19 | none handled | pause |
| 0x0053 | 2 | - | 20 | none handled | resume (sender has one BL caller, 0x0203075E) |
| 0x0060 | 2 | 1: absolute position 0x0000-0x6400 | 21 | none handled | seek. FF/REW (app 0x02030838/0x02030944): every 15 frames send `pos +/- step`, clamped to 0..0x6400; step grows by 0x10 per send up to `max(0x355, 0xC800/song_seconds)` (or jumps straight to max with A held). Also used to resume at a saved position |
| 0x0070 | 2 | 1: 0/1 | 22 | none handled | 1 before play, 0 before background play (0x0400) [inferred: enable sample streaming to the GBA] |
| 0x0080 | 2 | 1: volume 0x00-0x2E | 23 | none handled | volume = `ui_vol(0..0x24) * 0x2E / 0x24`; scaled by 13/16 in one mode (app 0x02036B0C/0x02036B40) |
| 0x0200 | 4 | 1: sound id, 2: clamp(ui_vol*0x3F/0x24, 0x10, 0x3F) | 24 | 0x8100 records | sound effect with level (app 0x02036BBC) |
| 0x0300 | 0x106 | 1..: filename | 6 | 0x4300 | open file (loader only: `meteor.fup`) |
| 0x0301 | 2 | 1: byte count to read (<= 0x201) | 7 | 0x4301 | read from open file; destination remembered GBA-side |
| 0x0302 | 4 | 1: param2 (0), 2-3: u32 offset (low hw first) | 8 | 0x4302 | seek in open file |
| 0x0303 | 2 | - | 9 | 0x4303 | close file |
| 0x0400 | 0xCE (app) / 0xC (loader lib) | 1: play mode, 2: n1, 3: n2, 4: current index, 5-104: table A (n1 <= 100 words), 105: p5, 106-205: table B (n2 <= 100) | 10 | 0x4400, later 0x4401 | **background play**: after ~30 s idle while playing (app 0x02031880) the GBA hands the cart the play list and current index and blanks [inferred: tables are play order / file list] |
| 0x0401 | 2 | - | 11 | 0x4401 | leave background play (app 0x02031228) |
| 0x0500 | 2 | - | 12 | none handled | cart sleep (app 0x02031598); GBA enters Stop (SWI 3) |
| 0x0501 | 2 | - | 13 | 0x8501 | cart wake |
| 0x0800 | 2 | - | 14 | 0x4800 | sent after a good meteor.fup read [inferred: "prepare for firmware replacement"] |

Commands whose responses the dispatcher does not know (0x4030, 0x4052, 0x4053, 0x4060, 0x4070,
0x4080, 0x4200, 0x4500) would be logged as error 1 with the ID kept at 0x03001080; the record is
still cleared [code]. So either the cart sends no record for them, or the GBA ignores it.

## 5. Status records and the dispatcher

### IRQ handler (0x02032780; app equivalent) [code]
Read 4 halfwords at idx 0x100. If the ID word is 0: set error flag 2 if the watchdog is armed and
return. Else CONTROL=0x08 (ack), then if the GBA is inside a driver access (lock flag 0x03000E49) set
"pending" and return (the access re-runs the handler on unlock, as on the Play-Yan); else read **8
halfwords** at idx 0x100 into a 16-entry ring of 32-byte slots (0x03000E50, write 0x03000E4C, read
0x03000E4D, count 0x03000E4B, overflow > 15 -> error 8). Only 16 bytes per slot are used.

### Record layout (16 bytes = 8 BE halfwords; GBA stores them as LE u16)
`hw0` ID, `hw1` status/size, then three u32 fields **each made of two BE halfwords, low halfword
first**: `A = hw2 | hw3<<16`, `B = hw4 | hw5<<16`, `C = hw6 | hw7<<16`.

### Dispatcher (0x02032ED4 loader, 0x02038478 app), main loop, per record [code]
| ID | handling |
|---|---|
| 0x4010 | hw1 = 0: entry present -> read 0x108 words (0x210 B) from idx A into the list buffer slot (stride 0x214, count at +2); else end/error code |
| 0x4011 | same; buffer holds only 2 entries (count >= 2 -> status 5) |
| 0x4020 | hw1 = status; 0 -> update the path-depth state |
| 0x4040 | if A != 0: read 0x88 words (0x110 B) from idx A into the ID3 block |
| 0x4050 | play started: checks state, Timer0 = 16384 Hz, "playing" flag = 1 |
| 0x4051 | stopped: Timer0 = 16384 Hz, audio rings cleared, playing = 0 |
| 0x4300 / 0x4302 / 0x4303 | hw1 = status (0 = OK) |
| 0x4301 | hw1 = status; if 0: bytes = B & 0xFFFF, read (bytes+1)/2 words from idx A into the 0x0301 buffer (Game Pak IRQ disabled) |
| 0x4400 | hw1 = status |
| 0x4401 | app: callback gets `hw1 | (byte2 of A)<<8 | (B & 0xFF)<<16 | (A & 1)<<24` (current index in bits 8-15, bit 24 = still playing) and reads 4 words from idx C; if (A & 0xFFFF) == 0 the audio path is reset |
| 0x4800 | hw1 = status; extra record clear |
| 0x8001 | boot complete; hw1 = status; arms the watchdog |
| 0x8100 | audio / position (below) |
| 0x8501 | wake complete |
| 0x8600 | headphones: hw1 = 1 plugged in, 0 not |
| other | error 1, ID saved |

After each record the registered callback for that ID (16-entry table at 0x02036060, set with
0x02032D5C) is called with a per-ID value (mostly hw1), and the record is cleared (write 0 to idx
0x100 / raw 0xFF).

### 0x8100 (audio and trackbar) [code]
- `A != 0`: sample block. `hw1` = size in bytes (clamped to 0x480, 0 -> error 3). Per channel
  `n = ((size>>3)+3) & ~3` words, i.e. size/2 bytes rounded up to 16. Channel 1 is read from idx A,
  channel 2 from **idx A + n*2** (16-bit-word units; for 0x480: A+0x120). 8-bit signed PCM, two
  samples per halfword, odd-address byte first. Gain: 0x100 (x1.0) while playing music, 0x60
  (x0.375, i.e. `s*12>>5` clamped) otherwise (sound effects).
- `A == 0` while playing: position update. `B` = trackbar 0x0000-0x6400, `C` = elapsed seconds.

### Data blocks [code]
- Directory entry (0x210 B, wide): name from +0 (0x106 chars max), u16 type at +0x20C (nonzero sorts
  first in the loader's sort; GBE+/dandocs: 1 folder, 2 file), +0x20E/+0x210 used GBA-side.
- ID3 block (0x110 B): title wide string at +4, artist wide string at +0x88, **u32 song length in
  seconds at +0x10C** (used for the seek step `0xC800/len` and end detection). +0..3 unused.

## 6. meteor.fup (loader 0x02028034-0x02028414) [code]
After 0x8001 OK and with the 0x20000-byte buffer allocated:
1. `0x0300 "meteor.fup"`. 0x4300 hw1 != 0 -> done.
2. `0x0301 0x40`: header must be exactly 0x40 bytes; bytes 0..0x2D == `Nintendo MP3 Player firmware
   update program\r\n`; checksum: `(0xFF + sum(bytes)) & 0xFF == 0` (i.e. byte sum = 1 mod 256).
   Header byte +0x3E = number of 16-byte directory entries.
3. `0x0301 n*16`: first entry with valid checksum (same rule over its 16 bytes), byte +8 == 1,
   byte +0xD bit 1 set, and u32 size at +4 <= 0x1FFFF is selected; `0x0302` seek to u32 offset at +0.
4. Repeated `0x0301 min(remaining+1, 0x201)`: each chunk carries its own checksum byte (chunk byte sum
   = 1 mod 256); the first `count-1` bytes are appended to the buffer.
5. `0x0303`. If everything passed: `0x0800`, and on 0x4800: `upload_from_buffer(buf, len)` (same 0x08AA
   record format as section 3); if that fails (bad magic) the built-in image is uploaded again. Then
   wait for 0x8001 again (the second 0x8001 does not restart the update).

So the update image is **up to 0x1FFFF bytes** (128 KiB), not 64 KiB; only the per-read count is
16-bit. The app contains the same reader (app 0x0202C2C0-0x0202C58C) but its 0x0300 sender (app
0x02038F04) has no BL or pointer reference.

## 7. Audio output (driver 0x02033A74-0x02034010) [code]
Identical to the Play-Yan player's stage:
- Two rings of 0x240 words (0x900 B, 2304 samples) at *0x0300110C (channel 1) and *0x03001110
  (channel 2), write pointer 0x03001128, read pointer 0x03001124 (words).
- SOUNDCNT_X = 0x80, SOUNDCNT_H = 0xA90E (FIFO A -> **right** only, FIFO B -> **left** only, both
  Timer0), SOUNDCNT_L = 0x77. DMA1: ring 1 -> FIFO A, 0xB6000000; DMA2: ring 2 -> FIFO B,
  0xF6000000 (IRQ). So channel 1 (the first half of each block) plays on the right speaker.
- DMA2 IRQ (table slot 0, 0x02033A75): read pointer += 4 words, wrap at 0x240 by re-pointing both
  DMA sources; on underrun the next 16 bytes of each ring are filled with the last sample.
- Timer0 reload = trim - 0x1000000/0x4000 (16384 Hz, the only rate the program ever sets).
- Rate trim (0x02033F7C, once per frame): `avg = avg - avg/64 + fill`, fill = ring fill in 1/256;
  every 60 frames with f = avg/64: 85-170 -> trim 0 only inside 121-135 (else unchanged);
  43-81 -> -3; 82-84 -> -2; 171-173 -> +2; >= 174 -> +3; < 43 -> 0.
- Master: SOUNDCNT_L-style PSG volume helper 0x02033D98 ([inferred] unused for DMA audio).

## 8. Against dandocs-nmp.txt and GBE+ nmp.cpp

| Topic | Prior art | Code says | Verdict |
|---|---|---|---|
| Program structure | single program | header -> loader (0x08090000, EWRAM 0x02028000) -> app (0x08010000, EWRAM 0x0201C000), hand-off block at 0x0203FF80 | new |
| Register byte order (MSB at +0, even byte first) | dandocs | yes | confirm |
| PARAM "INPUT_PARAMETER 0-3" then index | dandocs | high word = page after bank fold; low word = word address; writes use idx-1 | extend |
| 0x00FF "data flush" | dandocs | write 0 to status record (raw 0xFF = 0x100-1) after each record | correct |
| 0x010F send command / get ticks | dandocs, GBE+ | write mailbox is "0x110" pre-decremented to 0x10F; read 0x10F = tick | confirm, explain |
| CONTROL 0x0404 ends command, 0x0808 after firmware | GBE+ | yes; 0x08 also acks every IRQ; 0x10/0x00 address phases | extend |
| Chip reset | not documented | GPIO 0x080000C4 bit 3 pulse | new |
| Firmware upload "index ... write values" | dandocs | 0x08AA-format image at 0x080A8F34, 0xF730 hw, 91 records, entry 0x42FC written to idx 0x7E then CONTROL 8 | new detail |
| Boot retry "after a second without ticks" | dandocs | 8 s per attempt, 4 attempts; tick watchdog needs >= 0.867 ticks/frame per 60-frame window | correct/extend |
| Status data "16 bytes, first 8 checked" | dandocs | handler reads 4 hw (ID check) then 8 hw | confirm |
| 0x4010/0x4011 "End of List Flag non-zero = found" | dandocs | hw1 == 0 means an entry was returned; GBE+ also uses 0 = entry, 1 = end | **dandocs text inverted**; GBE+ right |
| Dir entry 528 B, flag at 0x20C | dandocs, GBE+ | 0x210 B read; u16 type at +0x20C | confirm |
| 0x0040 "returns access index 0x0101" | dandocs, GBE+ | data index is the u32 A (bytes 4-7); no special 0x0101 | correct |
| ID3 title 0x00-0x87, artist 0x88-0x10F | dandocs | title starts at +4; artist +0x88; **u32 length (s) at +0x10C** | correct/extend (GBE+ writes title at 4: right) |
| 0x8100 index "16-bit at bytes 4-5" | dandocs | u32 A, low halfword first | extend |
| 0x8100 trackbar "byte 8, 0-99 percent" | dandocs, GBE+ | B = 0..0x6400 (byte 8 is its high byte, so the percent reading is an approximation) | correct |
| 0x8100 timestamp "24-bit, bytes 0x0F,0x0C,0x0D, 0x0E adds 1216 s" | dandocs | C = hw6 | hw7<<16, i.e. bytes 0x0E,0x0F,0x0C,0x0D from MSB to LSB | **explained** |
| L/R buffers: index+0x120 for 0x480 | dandocs | yes (A + n*2) | confirm; channel 1 is played on the **right** speaker |
| Sample order "MSB = first sample" | dandocs | odd-address byte is first in memory | **correct** (needs hardware check of which byte the chip emits first; the update-file compare proves the odd byte is the file's first) |
| 0x0060 "byte 0 position %, byte 1 direction +-0x10" | dandocs, GBE+ | hw1 = absolute target 0..0x6400; FF/REW step grows by 0x10 each 15 frames | **correct** (dandocs observed the low byte of a growing step) |
| 0x0080 volume 0-0x2E | dandocs | yes, from ui 0..36 | confirm |
| 0x0200 "no parameters" | dandocs, GBE+ | sound id + level 0x10-0x3F | correct |
| 0x0300/0x0301/0x0303 | dandocs | plus 0x0302 seek (u32 offset); 0x0301 takes a byte count | extend |
| meteor.fup max 65,536 B | dandocs | entry size <= 0x1FFFF; 0x20000 buffer; header 0x40 B, 16-B entries, per-chunk checksums | **correct** |
| 0x0030, 0x0070, 0x0400/0x0401, 0x0500/0x0501, 0x0800 | listed as unknown or sleep | layouts above; 0x400 = background play with 2x100-word tables; 0x4400/0x4401/0x8501/0x4800 responses | new |
| Responses 0x4052/0x4053/0x4060/0x4080/0x4200 | dandocs | not handled by the dispatcher (would be logged error 1) | correct |
| Sample rate | dandocs: n/a | 16384 Hz Timer0, adaptive +-3 cycle trim | new |

## 9. Open questions
- Which byte the cart really puts first on the wire for samples (confirms the odd-first reading).
- Meaning of image header words hw1/hw2 (0x7FFF, 0xF800) and of the 0x30/0x70 commands.
- Exact contents of the 0x0400 tables and the 4 words read from idx C on 0x4401.
- Whether the cart sends records for 0x52/0x53/0x60/0x70/0x80/0x200/0x500 at all.
- Registers 0x0E002000 / 0x0E00A000: present in the driver, never called.
