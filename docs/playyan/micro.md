# Play-Yan Micro (ZMBJ): differences from the original Play-Yan (ZMAJ)

Image: the first 1 MiB of the dump, sha1 17b5f7ad459448b447fa94617d09886cd4f9c554. Linked at
0x0C000000 (WS2 mirror) like the original. Addresses are Micro addresses unless written "orig".

Tags: **[code]** read from the ROM code; **[inferred]** a reading of the code's intent that the
GBA side alone cannot prove.

## 0. Summary of what is the same

The cartridge driver is the same source as the original, relocated (orig 0x0c0243b4..0x0c0263c4 ->
Micro 0x0c01da00..0x0c01fdd4). These parts are identical in logic **[code]**:

| Function | orig | Micro |
|---|---|---|
| wait 0xB000012 bit 4, error 7 after >1000 polls | 0c0243b4 | 0c01da00 |
| driver reset: control shadow = 0x0A (shadow at 0x3001010), ring/flags clear | 0c0243f4 | 0c01da40 |
| VC reset pulse (control bit 3 low, ~2000-iteration delay, high) | 0c024450 | 0c01da9c |
| busy lock / deferred-IRQ replay | 0c0244cc | 0c01db18 |
| GamePak ISR: ack bit 1 pulse, mode 0x78 addr 0x000FF000, bit 0 strobe, copy 32 B from 0xB000300 into 16-entry ring (0x3001020, wr 0x3001018, rd 0x3001019, count 0x3001017), then mode 0x70 + two 0 halfwords to 0xB000000 | 0c024544 | 0c01db90 |
| doorbell (control bit 2 pulse) | 0c02471c | 0c01dd68 |
| 32-bit write (mode 0x70) / 16-bit write (mode 0x68) | 0c024758 / 0c024798 | 0c01dda4 / 0c01dde4 |
| block write mode 0x68 + DMA3 to 0xB000100, 0x200-B chunks, optional bit-7 wait | 0c0247d4 / 0c0248b4 / 0c0248f8 | 0c01de20 / 0c01df00 / 0c01df44 |
| block read mode 0x78 via window 0xB000300 or 0xB000500, streaming mode when RAM-test flag (0x3002738) is 1 and window is 0x500, or flag is 2 | 0c02493c / 0c024ad4 | 0c01e068 / 0c01e200 |
| FIFO read mode 0x28 from 0x9000000, bit-7 wait per halfword | 0c024e80, 0c016674 | 0c01e5d4, 0c018064 |
| IRQ enable, ISR install at IRQ table +8 (0x30013a0+8 = 0x0c01db91) | 0c024ef4 | 0c01e648 |
| error-code FIFO (16 entries) push / pop | 0c0259d4 / 0c0259fc | 0c01f2c4 / 0c01f2ec |
| audio: FIFO A/B, SOUNDCNT_H 0xA90E, DMA1/DMA2 0xB6000000/0xF6000000, 0x240-word rings, DMA2-IRQ underrun handler, gain 0x180 video / 0x100 music / 0x60 other | 0c025e4c..0c0261d0 | 0c01f814..0c01fb98 (init 0c01fa10, rate 0c01fad4, copy 0c01fb98) |
| video frame in 0x80001000: 0x12C00 B from rec+4 via window 0x500 to VRAM 0x06000000, or 60x40 path (0x12C0 B, stride 0x1E0) | in 0c025454 | in 0c01ec34 |
| thumbnail 0x40000500: read rec+8 into buffer set by (buf,w,h) setter | 0c025310 | 0c01eaf0 |
| ID3 0x40000600: if rec+4 != 0, read 0x8C B from VC rec+4 to 0x3002920 | | |

Garage Games: the original's hash table (0xF8CB9008 etc. found at orig 0x0c0f1eb0..) is absent
from the Micro ROM **[code: no match for any of 3 sampled hashes]**.

## 1. Command transport: the "serial" path

Builder: `0x0c01df88(vc_addr, src, len)` **[code]**. Every command builder calls it with
vc_addr = **0x000FF020** (same mailbox as the original). Sequence:

1. Save IME/IE; mask IE bit 13 (GamePak) for the whole transfer; take the busy lock (0x0c01db18(1)).
2. `0xB000006 = 0x28`; `0xB000004 = vc_addr & 0xFFFF`; `0xB000004 = vc_addr >> 16`; `0xB000002 = 0`.
3. For each of `(len+1)/2` halfwords of the command struct, in memory order (struct is
   little-endian 32-bit words, so each word goes low half first): poll **0x9000006** (WS0 view of the
   same status byte) until bit 7 = 1, then store the halfword to **0xB000000**.
4. Release the lock (replays a GamePak IRQ that arrived meanwhile), doorbell
   (control |= 4, write; control &= ~4, write), restore IE.

So the command struct is byte-for-byte the original's (same IDs, same lengths, 32-bit fields);
only the transport changes: no DMA, no 0x200 chunking, no re-addressing for long commands, a
bit-7 wait **before every halfword**, and IRQs masked. The original's DMA path (0x68 -> 0xB000100)
still exists in Micro but is used only by the RAM test (0x0c01df00) and firmware upload (0x0c01df44).

## 2. Command builders (all to VC 0xFF020)

`len` = bytes sent. All fields are u32 LE unless noted. "str" = 260-byte buffer filled by strcpy
(0x0c0233f0).

| ID | Micro fn | len | layout after the ID word | vs orig |
|---|---|---|---|---|
| 0x200 | 0c01f33c | 8 | u8 type (rest of word uninitialised stack) | same |
| 0x201 | 0c01f36c | 0x108 | str dirname | same |
| 0x300 | 0c01f398 | 8 | u32 | same |
| 0x400 | 0c01f484 | 0xC | u32 1, u32 p2; word 1 = speaker-stream gate (mi_fw1 0x31636 copies it to gp+0xE5E4; the block producer 0x31352 runs only if it is non-zero) **[lle]** | same |
| 0x500 | 0c01f3b8 | 0x118 | p1, p2, p3, p4, str name | same |
| 0x600 | 0c01f45c | 0x108 | str name | same |
| 0x700 | 0c01f3ec | 0x110 | p2, p3, str name; clears pos (0x3001248/4c), arms start timeout 0x3002730 = 60 | same |
| 0x701 | 0c01f430 | 4 | stop timeout 0x3002734 = 60 | same |
| 0x800 | 0c01f4a8 | 0x108 | str name; start timeout = 60 | same |
| 0x801 | 0c01f4e4 | 4 | stop timeout = 60 | same |
| 0x902 / 0x904 | 0c01f510 / 0c01f530 | 4 | - | same |
| 0x903 | 0c01f598 | 8 | u32 | same |
| 0x905 | 0c01f550 | 8 | **u32** (signed) | same width as orig |
| 0x906 | 0c01f574 | 8 | u32 | same |
| 0xB00 | 0c01f5bc | 8 | u32 | same |
| 0xD00 | 0c01f600 | 0x10 | p3, p1, p2 (argument order rotated, as orig) | same |
| 0xD01 | 0c01f624 | 8 | u32 | same |
| 0x2000 | 0c01f5dc | 0xC | p1, p2 | same |
| **0x3000** | 0c01f770 | 0x108 | str filename (caller truncates its string at 0x103 first); only caller passes `"play_yanmicro.ini"` at ROM 0x0c023a58 | new |
| **0x3001** | 0c01f7a4 | 8 | u32 max bytes (= 0x100); also stores the destination pointer (ctx+8) in 0x30029cc for the IRQ handler | new |
| **0x3002** | 0c01f7d0 | 0xC | p1, p2 | new; **no caller** in the ROM |
| **0x3003** | 0c01f7f4 | 4 | - | new |
| **0x4000** | 0c01f64c | 0x1A8 | +4 p1 (= AudioPlayMode, 0x30029b4), +8 n1, +0xC n2, +0x10 p4, +0x14 u16[100] (n1 entries filled from bytes at 0x3000c3a+i), +0xDC p5, +0xE0 u16[100] (n2 entries from 0x3000b50) | new |
| **0x4001** | 0c01f6bc | 4 | - | new |
| 0x8000 | 0c01f6dc | 4 | - | same |
| 0x8001 | 0c01f6fc | 8 | u8 part (1 or 2) | same |
| 0x10000 | 0c01f730 | 4 | - | same |
| 0x800000 | 0c01f750 | 4 | - | same |

0x905 values sent **[code]**: music = trackbar target clamped to 0x6400 (0x0c0150b6); video =
signed step from 0x0c01c654 (magnitude from video length / 120 with a lookup table at 0x0c0e752c,
scaled by a speed factor clamped 4..60 and >>2, negated unless rewinding... sign set by player
state 7) and 0 at 0x0c01d0da. All are full 32-bit words.

0x4000/0x4001 meaning **[inferred]**: "continue playing this queue on the cartridge while the GBA
sleeps". The 0x40004000 callback (0x0c016110) sets DISPCNT bit 7 (forced blank); the 0x40004001
callback (0x0c015fd8) first sends 0x906, then restores the display and uses the packed value
`(rec+4)&0xFF | (rec+0xC &0xFF)<<8 | (rec+0x10 &0xFF)<<16 | (rec+8 &1)<<24`: bits 8..15 are
compared with the current track index; bit 24 selects "still playing" vs "stopped" UI. The first
array is a per-track byte list, the second the play order (0x3000b50, 99 x u16).

Real firmware **[lle]**: auto-play is armed like the original's music-player sleep (player.md 8;
Micro 0x0c0164xx): L+R sets the armed flag only while no 8-bit audio records arrive, so only with
headphones plugged in. Then 0x4000 follows 4.2 s later, 0x40004000 answers, and the GBA enters Stop
with **KEYCNT = 0x0008** (START selected, interrupt bit 14 clear; the original uses 0x40FF). mi_fw1
sends 0x40004001 only in answer to 0x4001 (0x1f89a); it initiated no record in 60 s of auto-play,
including the end of the queue. So the cartridge never wakes the GBA. If START ends the Stop, the
GBA sends 0x4001, gets 0x40004001, sends 0x906 and goes on to the next track (0x600, 0x800).
Whether a KEYCNT-selected key ends Stop without bit 14 is a hardware question (player.md 12).

## 3. IRQ responses (dispatcher 0x0c01ec34, ring pop 0x0c01e294)

Callback ID table at ROM **0x0c0e7848, 20 entries** (orig 0x0c0f27dc, 14):
0x80000100, 0x40000200, 0x40000201, 0x40000500, 0x40000700, 0x40000701, 0x40000600, 0x40000800,
0x40000801, 0x80001000, **0x40004000, 0x40004001**, 0x40008000, 0x40008001, 0x80010001, 0x40800000,
**0x40003000, 0x40003001, 0x40003002, 0x40003003**. Register: 0x0c01eb84(id, fn, user) (fn non-zero
enables; fn table 0x3001288, user table 0x30012d8); invoke 0x0c01ebf0 calls fn(user, value).

Record words below are rec+0, +4, ... of the 32-byte record.

| ID | Micro handling **[code]** |
|---|---|
| 0x40000200 | read **0x6938** B (= 8 + 99 x 272) from VC rec+4 into *0x30029e0 (orig 0x67AC = 8 + 99 x 268) |
| **0x40003001** | if rec+4 == 0: read rec+8 bytes (length, **not clamped**) from VC address **rec+0xC** into *0x30029cc via window 0x300, GamePak IE masked during the read. value = rec+4 |
| 0x40003000 / 3002 / 3003 | no data; value = rec+4 |
| 0x40004000 | no data; value = rec+4 |
| **0x40004001** | value = packed (see above); read **0xC6** B (99 x u16) from VC **rec+0x14** into 0x3000b50; if rec+8 == 0 (playback ended): stop-timeout 0x3002734 = 0, nesting counter -1, rate 8192, clear audio rings, state 0 |
| 0x40008000 | if rec+4 == 0: 0x3001258 = (u8)rec+8 (update-file status bits) |
| others | as the original |

While a media state is active (0x3001329 != 0) only these IDs reach callbacks: 0x80001000,
0x40000700/701/800/801, 0x80010001 (as orig) **plus 0x40000600, 0x40004000, 0x40004001** (new).

## 4. Boot sequence (scene init 0x0c0102ac, per-frame 0x0c011194, state = byte ctx+4, ctx = *0x300186c)

The Micro never touches 0x9000004/0x9000010/0x9000022 or the 0x09000100 window; the original's
WS0 boot upload (orig 0x0c023420 with 0x9000022 = 1) has **no Micro equivalent** **[code: cart
xrefs]**. All uploads use the 0xB000xxx register set, with 0x9000006 only for bit-7 polls.

Init: control 0xB000010 = 0x0A, then pulse bit 4 (0x1A, 0x0A) (orig does the same, 0x0c0101b8).
Settings defaults (0x0c010158), audio init 0x0c01d45c (8192 Hz, 0x400-B LZ77 scratch at *0x30029c4).

| state | code | action |
|---|---|---|
| 2 (after fade-in task 0x0c0101f4) | 0c011234 | register 0x80000100 -> 0x0c0109a4; upload image C (one-shot, 0x0c01e928); GamePak IE off; RAM test 0x0c01e580(0x32); IE on; state 0 |
| (cb 0x80000100) | 0c0109a4 | rec+4 == 0: register 0x40008000 -> 0x0c010928, send 0x8000. Else error screen code rec+4 (1..4, other) |
| (cb 0x40008000) | 0c010928 | status = 0x3001258. Register 0x80000100 -> 0x0c0108cc, 0x40800000 -> 0x0c0108f8. If status bit 1: register 0x40008001 -> 0x0c010910, send 0x8001(2). Else send 0x800000 |
| (cb 0x40008001) | 0c010910 | 0x0c01e7c4(1): reset handshake + start incremental upload of image B; state 4 |
| (cb 0x40800000) | 0c0108f8 | reset handshake (0x0c01e6e0); state 9 |
| 9 | 0c011414 | when 0x9000006 bit 7 set: 0x0c01e730(1) (start image B); state 4 |
| 4 | 0c011264 | 0x0c01e818: up to 8 x 0x800 B per frame; when done writes 0xA5A50032 to VC 0x10000400 and re-enables the ISR; state 0 |
| (cb 0x80000100) | 0c0108cc | ctx+5 \|= 2; rec+4 == 0 -> state 10, else error screen |
| 10 | 0c011430 | settings status != 0 -> state 11. Else register 0x40003000 -> 0x0c010644, send 0x3000("play_yanmicro.ini"), state 0 |
| 11 | 0c011470 | register 0x40000200 -> 0x0c010760, send 0x200(2), loaded-mode = 2; state 0 -> 5 via callback |
| 5..8 | 0c011270.. | logo; state 6 sends 0x400(1,0), sets audio rate **16384** (0x4000); later menus set 8192 after 0x400(1,0). The original does the same at its boot state 5 (orig 0x0c0109c6), so the 16384 Hz intro is not a Micro difference |

Reset handshake (0x0c01e6e0 / 0x0c01e7c4, same as orig 0x0c024f8c): driver reset, VC reset pulse,
FIFO-read 0x80 halfwords from VC 0x00080000 (discarded), VC reset pulse, poll 0x9000006 bit 7
(another reset pulse every 0x8000 polls, hang after 0x100000), write the poll count (u32, mode 0x70)
to VC 0x000FF080.

### Menu switch (0x0c0125cc state 3 and 0x0c0121bc)

Loaded-mode byte 0x3000e7d: 1 = video firmware, 2 = music firmware. Entering the Video menu
(ctx+0xe = 0) with bit 0 clear, or the Music menu (ctx+0xe = 1) with bit 1 clear: register
0x80000100 -> 0x0c0121bc(user 1), upload image C (no RAM test), state 0. 0x0c0121bc(1): register
0x80000100 -> 0x0c012114, 0x40800000 -> 0x0c01218c(user = mode); video: if status bit 0 send
0x8001(1) else 0x800000; music: if status bit 1 send 0x8001(2) else 0x800000. 0x0c01218c calls
0x0c01e7c4(mode) (mode 0 = image A, 1 = image B), state 5; the 0x40008001 callback 0x0c0121a4
sends 0x800000.

So dandocs' "if both bit 0 and bit 1 are set" is not what the code tests: bit 0 = video part,
bit 1 = music part, tested separately, and the 0x8001 parameter names the part **[code]; meaning
of the bits [inferred]**.

## 5. Firmware images in ROM

The Micro stores all three images as **lists of GBA-BIOS LZ77 (type 0x10) chunks**, each decoded
with SWI 0x11 (0x0c01fde4) into the 0x400-byte buffer *0x30029c4; chunk length = the 24-bit size in
its header (0x0c01d410). Each list is a NUL-terminated array of ROM pointers. (The original stores
its three images raw and copies them with DMA.)

| image | pointer list | chunks | compressed ROM range | decoded size | sha1 (decoded) | content (strings) | orig counterpart |
|---|---|---|---|---|---|---|---|
| A | 0x0c0e7898 | 294 | 0x0c023d08-0x0c05adad | 0x4975C | f7533448ece8db54718ed290110168831a9c3a3e | ASF/MP4/G.726/AAC: video | fw0 0x0c029710, 0x31B48 (by size order; parent to confirm) |
| B | 0x0c0e7d34 | 272 | 0x0c05adb0-0x0c08bc09 | 0x43D84 | 30c85e025df51bbc1e52120e788d6784967bbfdf | MP3 decoder, audio_file_list: music | fw1 0x0c05b258, 0x3F66C |
| C | 0x0c0e8178 | 96 | 0x0c08bc0c-0x0c0a1617 | 0x17FD0 | 5800cbb96a93f30067e778483f2e126e5d9536a2 | "play_yanmicro.fup", "Nintendo Playan firmware update program": update loader | fw2 0x0c09a8c4, 0x123C0 (contains "playan.fup") |

Destinations **[code]**:
- A and B (0x0c01e730 + 0x0c01e818): whole decoded image to VC 0x00000000 upward, 0x800-byte
  writes (each split in 0x200-B DMA chunks with a bit-7 wait), at most 8 per frame; then
  0xA5A50032 -> VC 0x10000400.
- C (0x0c01e928): decoded bytes [0, 0x200) -> VC 0x000; then a 6-byte record
  `7f 2f 00 02 0c 00` -> VC 0x200; then decoded bytes [0x200, end) -> VC 0x000C0200 upward in
  <=0x400-B pieces; then 0xA5A50032 -> VC 0x10000400. Identical scheme and identical 6 bytes to the
  original's fw2 loader (orig 0x0c0251c4). **[inferred]** bytes 2..5 are 0x000C0200 little-endian,
  so the record looks like a VideoCore jump to the relocated image (the image's own bytes at 0x200
  are `78 2f` + 0x000CF99C; orig `78 2f` + 0x000CF7DC). For the firmware agent to confirm.

The GBA never sends a .fup file name; "play_yanmicro.fup" exists only inside image C.

## 6. RAM test (0x0c01e3e4, wrapper 0x0c01e580)

Same algorithm as orig 0x0c024cb8 (pattern table of 0x1FF bytes at ROM 0x0c0e7648, 0xA00-byte
chunks, write with 0x68 DMA then read back with 0x78 in streaming mode, flag 0x3002738 = 2 while
running, 1 on pass, 0 on fail) but **VC range 0x00010000-0x00022BFF** (orig 0x000EC400-0x000FEFFF),
and the Micro wrapper retries once (at most 2 runs) until the flag reads 1. Argument 0x32 at boot.

## 7. Key file (play_yanmicro.ini)

Flow **[code]**:
1. Boot state 10, only if settings status (0x3002800[0]) is 0: send 0x3000 with the name.
2. cb 0x40003000 (0x0c010644): rec+4 == 0 -> register 0x40003001, send 0x3001(0x100) with
   destination ctx+8 (a 0x100-byte buffer zeroed at scene init). Non-zero -> status 0xFF (absent),
   state 11.
3. Dispatcher copies rec+8 bytes from VC rec+0xC to ctx+8. cb 0x40003001 (0x0c01068c): rec+4 == 0 ->
   register 0x40003003, send 0x3003. Non-zero -> status 0xFE, state 11.
4. cb 0x40003003 (0x0c0106c8): parse (0x0c011888), apply (0x0c011a84), state 11. If status is
   0xFE (signed < -1) the "bad key file" sprite (ctx+0x162) is shown; 0xFF shows nothing.

Parser (0x0c011888) **[code]**:
- If the buffer starts FF FE (UTF-16LE BOM) it is narrowed in place by taking every second byte
  from offset 2. Byte 0xFF of the buffer is forced to 0, so at most 255 bytes of text count.
- Status = 1, then up to **16 lines**. Per line: skip spaces, tabs and '='; match one of the 8 key
  names by exact, case-sensitive prefix (table of string pointers at ROM 0x0c0e6fb8); skip spaces,
  tabs, '='; parse an integer; record (key index, value) at ctx+0x108+4i. Then skip to CR/LF, skip
  all CR/LF; stop at NUL.
- Integer: optional '-', up to 4 digit positions; a terminator must be tab, space, CR or LF. Any
  other character, **including NUL**, is an error, so the last value needs a trailing newline.
  Unknown key or bad number -> status 0xFE.
- Apply (0x0c011a84) runs **only if status is still 1** (any lexical error discards the whole
  file). Per entry: keys 0, 1 must be 0..1; keys 3, 6 must be 0..3; out of range -> status 0xFE and
  that key skipped, the rest still applied. Later duplicates win.

| idx | key | stored at 0x3002800+ | rule | default (0x0c010158) |
|---|---|---|---|---|
| 0 | PLAY_YAN_Design | 2 (also 0x30029bc) | 0..1; inverted if byte +1 is 1 | 0 |
| 1 | PLAY_YAN_StartMode | 3 | 0..1; passed to the main-menu scene | 1 |
| 2 | PLAY_YAN_VolumeAdd | 4 | clamp(v+18, 0, 27) -> volume level | 18 |
| 3 | PLAY_YAN_AudioPlayMode | 5 (also 0x30029b4) | 0..3 | 0 |
| 4 | PLAY_YAN_AudioBass | 6 | clamp 0..1 | 0 |
| 5 | PLAY_YAN_AudioWide | 7 | clamp 0..6 | 0 |
| 6 | PLAY_YAN_VideoPlayMode | 8 (also 0x30029b8) | 0..3 | 0 |
| 7 | PLAY_YAN_VideoBright | 9 | clamp 0..5 | 0 |

Byte +1 is set only when defaults are loaded: `(held keys at 0x3001c98 & 0x300) != 0`, i.e. L or R
held at boot **[inferred: 0x3001c98 is the held-key word]**. Byte 0 values: 0 not yet read, 1 read
OK, 0xFE bad (icon), 0xFF absent. The struct is 12 bytes, zeroed at boot from ROM 0x0c0e6f14.

## 8. Directory entries

Entry stride 0x110 = **272** (inline `index*0x110 + 8` at many sites, e.g. the 0x600/0x800 callers
0x0c01391c); list header 8 bytes; 99 entries max. The name is at +0 and is passed directly as the
command string. The type byte moves from +0x108 (orig) to **+0x10C**; the 4 inserted bytes lie in
+0x100..+0x10B, not read by any code found **[code: no other field offsets seen]**.

## 9. Prior art, Micro-specific

| Claim | Source | Verdict |
|---|---|---|
| Micro uses mode 0x28, address 0xF020/0x000F, 0xB000002 = 0, writes 16-bit to 0xB000000, then PARAM 0x0C, 0x08 | dandocs | Confirmed. Extended: bit 7 of the status byte is polled before every halfword (through 0x9000006), GamePak IE is masked, and 0x0C/0x08 is a bit-2 pulse of a shadowed control word (0x0E/0x0A when bit 1 is still set) |
| Micro parameters are 16-bit, so 0x905 rewind is 0xFFFF..0xFFFD | dandocs, GBE+ (`cnt_data[4..5]` only) | Corrected: the command struct is the original's, 32-bit words sent as two halfwords low-first; a 16-bit reader sees only the low half |
| 0x3000 returns +4 = 0 if present | dandocs | Confirmed |
| 0x3001 returns +4 status, +8 size | dandocs | Confirmed; extended: +0xC is the VC address the GBA reads the text from; the GBA asks for at most 0x100 bytes but does not clamp rec+8 |
| 0x3003 closes; only if opened | dandocs | Confirmed; parsing happens on its response |
| Key semantics and ranges (VolumeAdd -18..9 etc.) | dandocs | Ranges confirmed; extended with exact clamp vs reject rules, defaults, UTF-16 BOM support, L/R Design inversion, 16-line / 255-byte limits, trailing-newline requirement |
| Bad key file -> icon, defaults | dandocs | Partly: lexical error -> nothing applied; range error on Design/StartMode/PlayModes -> that key skipped, others applied; both show the icon. Missing file -> no icon |
| 272-byte entries | dandocs | Confirmed; type field at +0x10C |
| Micro switches SFX from 16384 Hz in the intro to 8192 Hz | shonumi/dandocs | The rate is set to 16384 in the intro, but the original does the same, so it is not a Micro difference |
| play_yanmicro.fup update | dandocs | The name is only in cart firmware image C; the GBA flow is 0x8000/0x8001 as in the original, per-part bits |
| 0x8000 status: update if both bits 0 and 1 | dandocs | Corrected: bit 0 = video part, bit 1 = music part, tested separately |
| No Garage Games | dandocs | Confirmed (no hash table) |
| Not mentioned | - | New: 0x3002, 0x4000 (0x1A8 B), 0x4001 commands and 0x40004000/0x40004001 responses; LZ77-chunked firmware storage; three-image layout; no WS0 boot upload; RAM-test range 0x10000-0x22BFF |
