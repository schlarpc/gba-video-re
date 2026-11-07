# Shrek GBA Video -- player executable (static RE)

Author: player-RE agent. Status: v1. Addresses are run addresses unless marked "ROM" (= file offset).
Decompiler output, raw disassembly and the Ghidra project were working files and are not
included in this repository (they reproduce the cartridge's code); regenerate them from your own
ROM dump with `tools/extract_images.py` and Ghidra.

## 0. Decoder function map (for bitstream RE) -- IWRAM, hand-written ARM

Conversion: IWRAM addr A <-> ROM file offset `A - 0x03000000 + 0xbcc8`.

Register convention inside the decoder (asm, not APCS):
- `r0` = ctx (= state+0xec, see section 6), `r1` = 16-bit read pointer into the ROM window,
  `r2` = bit count left, `r3` = bit buffer, MSB-first, left-aligned. `r6` = return value of the VLC readers.
- `fp`/`ip` = MV predictor x / y, or block w / h, depending on the routine. `r4` = block size or pixel pointer.

| addr | role |
|---|---|
| 0x03006d30 | **decode_frame wrapper** (C). Sets up the ref/output plane pointers and calls 0x03000520(state+0xbc). Then advances the frame counters |
| 0x03000520 | **decode_frame** (asm). First bit 1 = keep the QP. First bit 0 = read ue(v) = QP and load the dequant scales into 0x03005770..78. Then loops over the MBs in raster order: median MV prediction from left/top/top-right (MV array stride 0x24), then calls 0x03001dac per 16x16 MB |
| 0x0300076c | **ue(v)** Exp-Golomb reader -> r6 (copy at 0x0300081c is se(v)) |
| 0x0300081c | **se(v)** signed Exp-Golomb -> r6 |
| 0x030007a8 | bit refill (+16 bits). When r1 crosses a 0x400 boundary, re-maps the next 1 KiB of the stream into the ROM window (Matrix mapper, section 1) |
| 0x030007fc | wait for the mapper to go idle (poll 0x08000180 bit 12) |
| 0x03001dac | **MB 16x16 mode dispatch**: ue(v) -> 24-entry table 0x03001d4c |
| 0x03002184 | 16x8 block dispatch (table 0x03002124, 24 modes) |
| 0x030030f4 | 8x16 block dispatch (table 0x03003094, 24) |
| 0x03003584 | 8x8 block dispatch (table 0x03003524, 24) |
| 0x030024ac | 16x4 dispatch (table 0x0300247c, 12 modes, no residual variants) |
| 0x03003818 | 8x4 dispatch (table 0x030037e8, 12) |
| 0x030041cc | 4x16 dispatch (table 0x0300419c, 12) |
| 0x03004468 | 4x8 dispatch (table 0x03004438, 12) |
| 0x03004750 | 4x4 dispatch (table 0x03004720, 12) |
| 0x030028ec 16x2, 0x03003adc 8x2, 0x03004950 4x2, 0x03004fd0 2x16, 0x03005294 2x8, 0x03005494 2x4, 0x030055fc 2x2 | smallest partitions, 12-entry tables at addr-0x30 |
| 0x03001578 | MC copy, MV = predictor (fp,ip), integer-pel. Stores the MV to the MV array. 4-way alignment jump table 0x03001568 (0x030015e0/1650/16e8/17a0 = src byte alignment 0..3) |
| 0x030015a4 | MC with MV = predictor + (se dx, se dy) |
| 0x03002910 / 0x0300293c / 0x03002b9c | the same MC routines for 8-wide blocks; 0x03003b00/0x03003b2c/0x03003d58 for 4-wide |
| 0x03001854 | MC with absolute MV (se x, se y) from ref0, plus per-plane DC offsets (se dY, dU, dV, each x2, clamped 0..255). Brightness-compensated copy |
| 0x03001ac0 (-> 0x03001a68, 0x03001a3c, 0x030019e0, 0x03003108, 0x03003238, ...) | "bilinear/recursive" intra: reads the corner pixel as se + neighbour average, then recursively bisects (midpoint averaging). Same shape as Mobiclip's mode 9 |
| 0x03000884 | Intra 16x16: ue = luma mode -> table 0x03000864 (V/H/DC/plane = 0x030009b8, 0x03000a04, 0x03000a54, 0x03000ba4); ue = chroma mode -> table 0x03000874 (0x03000c08, 0x03000d90, 0x03000dd4, 0x03000e44) |
| 0x030008fc | Intra 4x4: H.264-style mode prediction (pred = min(left, top), 9 -> 2). 1 bit "use pred", else 3 bits rem (+1 if >= pred). 9 modes table 0x030008d8 (0x03000ecc ... 0x030014b0). Then ue chroma mode -> 0x03000874 |
| 0x03005650 | **residual** for a WxH block (fp=W, ip=H). Per 8x8: ue -> CBP via byte table 0x03005610 (6 bits: 4 luma 4x4 + U + V), calls 0x03005794 per coded 4x4 |
| 0x03005794 | **4x4 coefficient decode + dequant + IDCT + add**. 12-bit peek into a u16 VLC LUT at [ctx+0x20] = **0x020010e4** (4096 entries in EWRAM). Entry bits 0-3 = length, 4-8 = abs level, then a sign bit, 9-14 = run, 15 = last. Top 7 bits == 0000011 -> escape at 0x030059b4. Coefs go to 0x030056f0[16]. Dequant uses 0x03005770..78 (H.264 table 0x03000680 = {10,13,16},{11,14,18},{13,16,20},{14,18,23},{16,20,25},{18,23,29}; QP%6 offsets 0x030006c8, QP/6 shifts 0x030006f8). H.264 4x4 integer IDCT. 6-entry jump table 0x0300577c |
| 0x03000370 | **YUV420 -> RGB555 blitter** (self-modified, section 4), called from the Timer3 IRQ |

16x16 mode table (0x03001d4c, index = ue(v)), as read from the asm:
0 = copy ref0 with predicted MV, 8 = ref1, 10 = ref2 (0x03001578); 3/9/11 = MV delta + copy from ref0/1/2 (0x030015a4);
4 = bilinear intra (0x03001ac0); 5 = abs MV + DC offsets (0x03001854); 6 = intra16x16 (0x03000884); 7 = intra4x4 (0x030008fc);
1 = split into two 16x8 (0x03002184 twice, +8 rows); 2 = split into two 8x16 (0x030030f4 twice, +8 cols);
12..23 = the same 12 modes, each followed by residual 0x03005650(16,16). The 24-entry tables for
16x8/8x16/8x8 have the same layout. The 12-entry tables for smaller blocks have no residual variants.
Refs: ref0 = previous frame, ref1 = 2 back, ref2 = 3 back.

Partition tree (verified from the split entries; mode 1 = horizontal split into top/bottom halves, mode 2 = vertical split into left/right halves):
16x16 -> 16x8 | 8x16;  16x8 -> 16x4 | 8x8;  8x16 -> 8x8 | 4x16;  16x4 -> 16x2 | 8x4;  8x8 -> 8x4 | 4x8;
8x4 -> 8x2 | 4x4;  4x16 -> 4x8 | 2x16;  4x8 -> 4x4 | 2x8;  4x4 -> 4x2 | 2x4;  (2-wide/2-high -> 2x2 0x030055fc).
A split adds rows*256 to the Y offset and (rows/2)*256 to the UV offset, or cols to both, because UV is interleaved.
This is the Mobiclip-style binary partition tree. A residual (0x03005650) can follow only at the 16x16, 16x8, 8x16
and 8x8 levels (modes 12..23).

## 1. Cartridge: 64 MiB "Matrix" mapper, multi-program boot

The cart is a Matrix Memory "3D Memory" 64 MiB GBA Video cart. mGBA implements it (`src/gba/cart/matrix.c`).
Bus 0x08000000-0x08001FFF is an 8 KiB window, remappable in 512-byte blocks. Everything above
0x08002000 reads the ROM linearly (first 32 MiB). The upper 32 MiB is reachable **only** through the window.
- Regs: 0x08800184 = ROM byte offset (masked 0x03FFFFFF; the code ORs in 0x10000000), 0x08800188 = window
  address (0x08000000-based, multiple of 0x200), 0x0880018C = number of 512-byte blocks, 0x08800180 = cmd (1 or 0x11 = map).
  Busy flag: 0x08000180 bit 12.
- Power-on mapping: window 0x0000-0x0FFF = ROM 0x0000, window 0x1000-0x1FFF = **ROM 0x0200**.
- Player helper: `0x020003a8 matrix_map(rom_off, win_addr, nblocks, ?, ?)`. The decoder inlines its own copy (0x030007a8).

Boot chain:
- ROM header entry `b 0xd0`: set IRQ/SYS stacks (0x03007fa0 / 0x03007f00), then `bx 0x08001000`. This runs the
  **loader at ROM 0x200** (through the window).
- Loader (ROM 0x200, run at 0x08001000): reads program index `[0x0203FFFC]`. It sums the size table at ROM 0x8e4
  `{0x4000, 0x4000, 0x10000}` from ROM 0xa00 to find the program image. It reads a 0x44-byte load header, zeroes
  one range, copies 4 sections to their run addresses, copies the EWRAM section to 0x02000000 and calls the entry.
- Programs (index -> ROM image):
  - 0: ROM 0x00a00, 0x4000 bytes (EWRAM 0x2da8 + VRAM sections; not analyzed, presumably the intro/logo screens. Its crt0 always chains to index 1)
  - 1: ROM 0x04a00, 0x4000 bytes (EWRAM 0x12fc): **menu**. It writes 0x0203FFF4 = start frame and 0x0203FFF8 = VX file offset
    from its menu entry table, then sets index 2 (player). Or it sets 0 (code at 0x02000db8/0x020005b0 in that image)
  - 2: ROM 0x08a00, 0x10000 bytes -- **the movie player** (this document)
- Each program's crt0 ends by restoring the window (ROM 0x200 -> 0x08001000), writing the next index to
  0x0203FFFC and jumping to 0x08000000 (cart reboot into the loader).

Player load header (ROM 0x8a00), sections:
| run addr | size | ROM src | content |
|---|---|---|---|
| 0x02000000 | 0x32c8 | 0x08a00 | EWRAM: header, crt0, C code (UI/control), VLC LUT, strings |
| 0x03000000 | 0x7cf4 | 0x0bcc8 | IWRAM: IRQ vectors, YUV blitter, VX decoder, audio decoder, player main loop |
| 0x06015300 | 0x2c40 | 0x139bc | zero-filled (VRAM work area; audio tables later loaded here) |
| 0x06013600 | 0x09a4 | 0x165fc | zero-filled (VRAM work area: player state 0x06013e80, audio state) |
Entry 0x02000058 (crt0). IRQ vector `[0x03007FFC] = 0x03000040`. Stacks: [0x02000050] IRQ, [0x02000054] SYS.

## 2. Memory map at run time

| addr | size | use |
|---|---|---|
| 0x02000000-0x020032c8 | | code/rodata (EWRAM C code 0x02000058-0x020010b0; OBJ palette 0x020010c4; **coef VLC LUT 0x020010e4-0x020030e4**; strings 0x020031e4+) |
| 0x020032c8 + i*0xF000 (i=0..3) | 0xA000 | Y plane of frame buffer i, stride 256, 240x160 used |
| 0x020032c8 + i*0xF000 + 0xA000 | 0x5000 | chroma plane i: **interleaved U,V bytes**, stride 256 (128 chroma px * 2), 80 rows, init 0x80 |
| 0x0203F2C8 | 0x318 | MV array (u16 per MB, packed x \| y<<8, stride 0x24 = 18 entries; ctx ptr starts at 0x0203F314) |
| 0x0203FFF4/F8/FC | | inter-program mailbox: start frame, VX file ROM offset (0x20200), next program index |
| 0x03000000-0x03000040 | | IRQ handler table (index = IRQ bit*4): +0x10 Timer1, +0x18 Timer3 |
| 0x03000040 | | IRQ dispatcher (asm) |
| 0x030001F0 | | 5-bit clamp/LUT for RGB conversion (T[k] = k>>3, saturating) |
| 0x03007cf4-0x03008000 | | stacks |
| 0x06000000 | 0x12C00 | Mode-3 framebuffer (240x160x16) |
| 0x06013600.. | | audio decoder state/buffers (0x06013600-0x06013a80+, synthesis history 0x06013680/0x06013880) |
| 0x06013E80 | ~0x110 | **player state struct** (section 6) |
| 0x06013F90 | 0x14 | audio packet bounce buffer (used when a packet straddles the window end) |
| 0x06014000 | | OBJ tiles: OSD font/icons (LZ77 from ROM 0x3a00/0x4200) |
| 0x06015300 / 0x06015B00 | 0x800 / 0x434 | audio decoder tables, copied from the start of the audio stream |
| 0x06015F00 / 0x06015F10 / 0x06015F30 | | audio scale table, synthesis table, initial step (inside the 0x6015b00 table block) |
| 0x06015F40 | 0x2000 | **audio PCM ring** (16 x 512 bytes, s8), DMA1 -> FIFO A |
| ROM window 0x08001000-0x080017FF | | video bitstream ring (2 x 1 KiB, refilled by the decoder) |
| ROM window 0x08001800-0x08001FFF | | audio stream ring (2 x 1 KiB, refilled from the main loop) |

## 3. Boot / main / main loop

- crt0 0x02000058 -> `main` 0x02000158:
  IE = Timer1|Timer3 (0x50), WAITCNT 0x4014, SOUNDBIAS amplitude-resolution = 1 (7-bit / 65.536 kHz PWM), DISPCNT = 0x1F43 (**mode 3**,
  BG2+OBJ, OBJ 1-D), BG2 affine identity. IRQ table: [0x03000010] = 0x02000100 (Timer1), [0x03000018] = 0x0200012c (Timer3).
  `0x02000b08 osd_gfx_init`: OBJ palette, maps ROM 0x3a00/0x4200, BIOS LZ77UnCompVram -> 0x06014000.
  Then calls `player_run(state=0x06013E80, file=[0x0203FFF8], startframe=[0x0203FFF4])` = **0x03006df4**.
  Return 0 (end/quit) -> next program 1; return 1 (A+B+Select+Start) -> program 0. Then clears VRAM and reboots.
- **0x03006df4 player_run** (C, in IWRAM):
  1. Clears the audio ring, allocates 4 YUV buffers, clears the MV array.
  2. Maps the file header into the window. Checks 'VX++' ("File Format Error").
  3. Reads width/height and **patches the YUV blitter** ("Unable to init YUV conversion code", section 4).
  4. Reads the header fields into the state. Loads the audio tables (audio_base+0 -> 0x06015300 0x800 bytes;
     +0x800 -> 0x06015B00 0x434 bytes). Audio packets start at audio_base + 0xC34.
  5. If startframe != 0: seek through the index (0x02000400 + 0x02000584).
  6. Prefill: 0x020002c4 decodes up to 4 frames. Starts sound/timers.
  - Loop: wait while the 4-slot ring is full (`[0x2c] < [0x28]` and same slot), **decode_frame 0x03006d30**,
    refill the audio window half when Timer3 flagged it (`[0xb4]`), read keys (0x02000874), act on input.
    Ends when `[0x30] == [0x34]` (frame count) -> 0x02000a28 (stop sound/timers, clear OAM), return 0.

## 4. Display / YUV->RGB ("YUV conversion code")

- Mode 3, a single 240x160 RGB555 framebuffer at 0x06000000. No double buffering. The picture is centered:
  dst = 0x06000000 + ((160-h)/2*240 + (240-w)/2)*2. OBJ layer for the OSD.
- **0x03000370 yuv2rgb_blit(&{Y, UV, dst})**: processes 2x2 luma per chroma pair. Y stride 256, UV interleaved.
  `R = T[Y + 2V']`, `G = T[Y - (V' + U'/2)]`, `B = T[Y + 2U']`, with U' = U-128, V' = V-128 (byte 0 = U, byte 1 = V).
  T = clamp/>>3 LUT at `r3 = [0x03000510] = 0x030001F0`. Odd pixels use index-4: a 2x2 **ordered dither**.
  It writes 2 pixels per 32-bit store. The store word is built with **adds**:
  `R0 + G0<<5 + B0<<10 + R1<<16 + G1<<21 + B1<<26`. T covers indices -260..509, but only
  part of that range is a real clamp table. Above it, T reads the blitter's own code bytes,
  including the patched `#height`/`#width` immediates, and these values can be larger than 31.
  A field can then carry into the next field, and the even pixel's B can carry into the odd
  pixel's R. Shrek never reaches this. Shark Tale and the combo cart do (see README).
- Self-modifying init in player_run (this is the "YUV conversion code" init):
  - 0x0300038C <- `mov sl,#height`, 0x03000394 <- `mov sl,#width`
  - 0x030004E8 <- `add r0,r0,#(0x200-w)`, 0x030004EC <- `add r1,r1,#(0x100-w)`, 0x030004F0 <- `add r2,r2,#(0x1E0-w)*2`
  - 0x02000280 patch_words(0x03000370, 0x68 words): placeholder 0x12345678 -> `str r8,[r2],#0x1E0`,
    0x87654321 -> `str r8,[r2],#-0x1DC`.
  - It fails with the error if any constant is not encodable as an ARM rotated immediate.
- **Brightness** (0x020008d8, A = +1, B = -1, range -2..+2): patches the instruction at **0x03000388** (normally
  `mov r0,r0`) to `add/sub r3,r3,#0x18/#0x30`. This shifts the clamp LUT base by ±3/±6 output levels.
  OSD text "Brightness +n".
- OSD: sprites only. 0x02000a74 set_obj(x, y, tile, oam_index, size, shape). 0x02000ffc draw_text(x, y, str), one
  8x8 OBJ per char (tile = char+0x18). 0x02001068 clear_oam(n). Icons: 0x02000bb4 (FF), 0x02000c1c (REW), 0x02000c84 (pause),
  0x02000cbc (Down-button icon at 200,120), 0x02000cf4 (hide it). 0x02000e90 draws "h:mm:ss/h:mm:ss" (cur/total).
  0x02000d3c frames->h:m:s = `frames*hdr[0x10]*3 + 0x20000 >> 18` seconds.

## 5. Timing, audio, A/V sync

- **Timer0**: reload 0xFF00, prescaler 1 -> **65536 Hz** sample clock. SOUNDCNT_H = 0x0B04: DMA A 100% to L+R,
  Timer0, FIFO reset. SOUNDCNT_X = 0x80. Mono.
- **DMA1**: src = PCM ring 0x06015F40 -> FIFO_A 0x040000A0, ctrl 0xB640 (enable, FIFO timing, 32-bit, repeat).
- **Timer1**: reload 0xF800, /64, IRQ -> 128 Hz = one 512-byte block of the ring. ISR 0x02000100 -> **0x03007974**:
  `[0xa0]++` (blocks played; frozen while paused `[0xb8]`). Every 16 blocks it re-arms DMA1 at the ring start.
  It also runs the OSD timers (message timeout `[0x5c]`, Down-icon timeout `[0x84]`).
- **Timer3**: reload `-hdr[0x10]` (12483), /64, IRQ -> 21.0 Hz. ISR 0x0200012c -> **0x03007ab0** av_tick:
  - Video: shows frame `[0x2c]` through 0x03000370 when `[0xac] < 1` and a frame is ready. Then `[0xac] += 2`.
    Otherwise `[0xac]--`. This gives one frame every 3 ticks = 3*12483*64/2^24 s = **7.000 fps**
    (37837 frames = 90.1 min). It drops alternate frames when it lags by more than 0x21 ticks.
  - Audio: while `[0xa4] < [0xa0]`, decode one packet into ring slot `[0x9c]` (+0x200, & 0x1FFF).
    Audio is therefore paced by the hardware playback rate. Both clocks come from the same crystal, so
    there is no other A/V sync logic. Seek and pause reset both counters together.
  - The audio window ring (0x08001800-0x08001FFF) is read sequentially. When it crosses a half, `[0xb4]` tells the
    main loop to map the next 0x400 bytes (`[0x90]`).
- **Audio codec** (0x03006a14 audio_decode_packet(src, dst) -> always 20): **fixed 20-byte packets**, each gives
  128 samples at **16384 Hz** (= hdr[0x18]). The packets run back to back from audio_base+0xC34.
  - First u16 >> 9 == 0x7F -> reset/key packet (0x03006c60 -> state init 0x030069c0); == 0x7E -> no-residual
    variant (0x03006260); otherwise 0x03006288 (prediction/overlap).
  - 0x03006ba0 = coefficient unpack: header u16 gives 6-bit shift fields (self-patched into 0x03006b8a..8d), 2- or
    3-bit quantized values * step, where step = `[0x06013660]` scaled by table 0x06015F00 >> 13.
  - 0x0300645c / 0x0300662c / 0x030068e0 / 0x03006948 = synthesis (transform/filterbank, ping-pong history 0x06013680/0x06013880).
  - Output stage in 0x03006a14: **4x linear-interpolation upsample** 16384 -> 65536 Hz with first-order
    error-feedback noise shaping to s8 (state 0x06013668/6c/70). 128 -> 512 bytes.

## 6. Player state struct (0x06013E80) -- offsets

| off | meaning |
|---|---|
| 0x00-0x0c / 0x10-0x1c | Y / UV pointers of the 4 frame buffers |
| 0x20 | VX file base (ROM byte offset, 0x20200) |
| 0x24 / 0x28 / 0x2c | ref index / decode(write) index / display index (slot = &3) |
| 0x30 / 0x34 | current frame / frame count (hdr+4) |
| 0x38 / 0x3c | chapter table abs offset (hdr+0x20) / last chapter start (hdr+0x24), gates "next chapter" |
| 0x40 | seek index abs offset (hdr+0x28) |
| 0x44 / 0x48 | hdr+0x30 (last keyframe frame) / hdr+0x34: upper limits for seeking forward |
| 0x4c | hdr+0x10 Timer3 ticks |
| 0x50 / 0x54 / 0x58 | last keys / buttons-unlocked flag / Up-hold counter |
| 0x5c / 0x60 / 0x64.. | OSD message timer / redraw flag / message text buffer |
| 0x84 / 0x88 | Down-icon timer / flag |
| 0x8c | PCM ring (0x06015F40) |
| 0x90 / 0x94 | next audio ROM offset to map / audio base (file + hdr+0x1c) |
| 0x98 / 0x9c | audio read ptr in the window / ring write offset |
| 0xa0 / 0xa4 / 0xa8 | blocks played / blocks decoded / DMA re-arm counter |
| 0xac / 0xb0 | display pacing accumulator / toggle |
| 0xb4 / 0xb8 | audio window half to refill / paused |
| **0xbc** | **decoder ctx** -- 0xbc next video ROM offset to map, 0xc0 read ptr (starts 0x0800103A), 0xc4 bit count, 0xc8 bit buffer (initialized from hdr u16 @0x38) |
| 0xcc..0xe8 | ref0 Y/UV, ref1 Y/UV, ref2 Y/UV, out Y/UV (decode_frame sets them per frame) |
| 0xec / 0xf0 / 0xf4 | current MB Y offset / UV offset / MV array ptr (`r0` in the asm points at 0xec) |
| 0x104 / 0x108 | width / height |
| 0x10c | coef VLC LUT ptr (0x020010e4) |

## 7. Container (header field use, cross-ref docs/container.md)

The header is at file+0. Video bits start at +0x38. Index entry = {frame, video bit offset from +0x38, audio byte
offset from the audio base, 0}.
- +0x04 frame count; +0x08/+0x0c w/h (patched into the blitter); **+0x10 = Timer3 reload, 64-cycle units. Video
  frame = 3 ticks**, so 12483 gives 7 fps. It is also used for the time display.
- +0x14 not read. +0x18 not read by the player (audio rate is fixed by the timers).
- +0x1c audio base; +0x20 chapter table; **+0x24 = last chapter start**: R (next chapter) only works if cur < it.
- +0x28 seek index; +0x2c count (not read by the player, which stops at the sentinel); **+0x30 / +0x34 = max current
  frame for Right / R seeks when there is no chapter table**. +0x38 the first u16 of the bitstream.
- Seek (0x02000584): it maps (file + ((bit+0x1C0)>>12)*0x200), sets r1/bit-buffer from the bit remainder, and maps the
  audio at the entry's byte offset (512-aligned + remainder in the window).

## 8. Input (0x02000874 read_keys; acted on in player_run)

Keys are read once per decoded frame. Edge-triggered: A, B, Select, Start, Down. Level: Right, Left, Up, R, L.
If the buttons are locked, every key is ignored except the A+B+Select+Start reset and Up-hold.
| key | action |
|---|---|
| A / B | brightness +1 / -1 (range ±2) |
| Select | pause audio, "Quit?  Are you sure?" -- A = yes (jump to end, return 0), B = no |
| Start | pause (pause icon + time display), Start again resumes |
| Right / Left | seek to the next / previous seek-index keyframe (~190 frames ≈ 27 s); shows time |
| R / L (hold) | FF / REW icon. After 5 polls, or repeated within 20, jump to the next / previous **chapter** (0x020006c0 mode 1) |
| Down | shows an OSD icon for 64 Timer1 ticks (0.5 s) |
| Up held 10 polls | toggle "Buttons locked" / "Buttons unlocked" |
| A+B+Select+Start | exit to program 0 (soft reset) |
Seek = 0x020006c0(state, forward, chapter_mode). It mutes audio, finds the index entry (0x02000400), repositions
(0x02000584), shows the OSD and prefills (0x020002c4).

## 9. EWRAM C function list (0x02000xxx)

0x02000058 crt0 · 0x02000100 Timer1 ISR thunk · 0x0200012c Timer3 ISR thunk · 0x02000158 main ·
0x02000280 patch_words · 0x020002c4 prefill_frames · 0x02000334 reset_audio_counters · 0x02000358 start_dma1 ·
0x020003a8 matrix_map · 0x02000400 index_find · 0x02000584 seek_to_index_entry · 0x0200067c start_timer3 ·
0x020006ac stop_timer3 · 0x020006c0 seek · 0x02000874 read_keys · 0x020008d8 set_brightness · 0x020009b4 pause_av ·
0x020009f4 resume_av · 0x02000a28 stop_all · 0x02000a74 set_obj · 0x02000b08 osd_gfx_init · 0x02000bb4/0c1c/0c84/0cbc icons ·
0x02000cf4 hide_icon · 0x02000d08 clear_icons · 0x02000d3c frames_to_hms · 0x02000d9c draw_number ·
0x02000e3c draw_char · 0x02000e90 draw_time · 0x02000ffc draw_text · 0x02001068 clear_oam · 0x020010a4 BIOS CpuSet (swi 0x0B) ·
0x020010bc BIOS LZ77UnCompVram (swi 0x12).

## 10. Open items / caveats

- The decoder is hand-written asm with a private register ABI, so Ghidra's C for 0x03000xxx-0x03005xxx is unreliable.
  Read `iwram_r2.txt` for those. The C for player_run / timer ISRs / EWRAM code is good.
- The 9 intra-4x4 and 4 intra-16x16 / chroma predictor bodies are identified by their tables only. Their order is
  assumed to be H.264 order and is not yet verified.
- Escape coding in residual_4x4 (0x030059b4) and the CBP map table at 0x03005610 are not decoded yet.
- The internals of the audio synthesis functions (0x0300645c/0x0300662c/0x030068e0) are not analyzed.
  The packet size (20), output rate and upsampler are certain.
- The decode is fully integer-pel (no sub-pel interpolation found in mc16_*). Worth confirming in the 8/4-wide MC.
