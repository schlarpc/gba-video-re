# The FVMV player (Pokemon GBA Video Vol 1-4)

This document describes the program on the four *Pokemon* GBA Video cartridges (USA): boot,
memory map, menus, episode playback, interrupts, timing, input and display. The codec itself
is specified in [format.md](format.md).

Addresses are for Vol 1 unless noted. Evidence tags: **(code)** read from the ARM code
(Ghidra decompile plus capstone disassembly), **(emu)** observed in mGBA or the unicorn oracle.

## 1. Cartridges and builds

| Cart | Header title, code | sha1 | Player build |
|---|---|---|---|
| Vol 1 | POKEGBAVID01, MPAE | ad0164df397860b0967590ee46ca1f41d6cecdcd | A |
| Vol 2 | POKEGBAVID02, MPBE | e7b2e00d6a60e8c149cd07b0ee8d98bf2b4efd15 | A |
| Vol 3 | POKEGBAVID03, MPCE | 0f0020ef4278e1777baaf74b6787355a5cb57d50 | B |
| Vol 4 | POKEGBAVID04, MPDE | fd79b821ff601e0091f19ab9c86b08f1c39ea2f3 | B |

The cart classifier (`crates/gbav-rom`) gives all four carts the same build id, `a7e6307ff34a`. A function-by-
function diff (capstone, with branch targets and literal-pool offsets normalised) shows two
sub-builds **(code)**:

- **Vol 1 vs Vol 2** and **Vol 3 vs Vol 4**: identical code. Only the literal pool words differ,
  because the data (graphics, streams, the IWRAM image) sits at different ROM addresses.
- **A (Vol 1/2) vs B (Vol 3/4)**: one function differs, the episode playback loop
  (0x0800276c, 2376 bytes in A, 2408 in B). Every later ROM function moves by 0x20 bytes. B
  changes only UI behaviour, mainly in the SELECT "Quit?" dialog: B first waits until SELECT,
  A and B are all released, while A accepts an A press that is still held from before. A
  also re-reads the keys in a slightly different order around pause and brightness.
- The IWRAM image (decoder, audio, IRQ, timing; 0x6e64 bytes) is byte-identical in all four
  volumes apart from 18 relocated literal words.

**No difference affects the codec.** The same video and audio code decodes all eight
streams, and the timing constants are the same.

## 2. Boot **(code)(emu)**

1. The cartridge header jumps to ARM code at 0x080000e4. It sets the IRQ and system stacks
   (SP_irq from 0x03007fa0, SP_sys 0x03007f00) and switches to Thumb.
2. The Thumb crt0 (ARM SDT scatter loader, 0x0800010c) does three things:
   - It copies the IWRAM image from the end of the ROM to 0x03000000 (Vol 1: ROM 0x1ef3008,
     0x610 + 0x6854 bytes; Vol 2 0x1f229a8, Vol 3 0x1fd31d0, Vol 4 0x1fd09c8).
   - It zeroes 0x03006e80-0x03006ee3.
   - It sets the IRQ vector 0x03007ffc to 0x03000610.
3. It runs the C++ static constructors (0x080051a0 -> 0x08003a84, 0x08003af8), then `main`
   at 0x08000284. The program is ARM-mode APCS C, compiled with ARM SDT, with the soft-float
   library (used only by the time display).
4. `main` does the following:
   - It sets IME = 1, IE = VBlank|Timer1 (0x0011), DISPSTAT = 8 and WAITCNT = 0x4014 (WS0 3/1,
     prefetch on).
   - It clears palette, OAM and VRAM.
   - It shows the boot screens (0x080003f0 and two calls of 0x080037ac): "Not compatible with
     Game Boy Player", the **"powered by actimagine"** logo and the Pokemon logo with the
     copyright text. They are tile graphics DMA'd from ROM and faded with BLDCNT/BLDY.
   - It sets mode 3 (DISPCNT 0x1f43) and calls 0x03006d78.
5. 0x03006d78 allocates **256,000 bytes** (0x3e800) from the C heap. The heap starts at
   0x02000000, so the block is at 0x02000008. It then calls the menu engine 0x08002064 with
   the menu tree (Vol 1 ROM 0x1ea9c1c). That call never returns. If the allocation fails, the
   screen is filled with colour 0x1f plus a row of white dots (error 10) and the program hangs.

## 3. Memory map during playback

| Region | Use |
|---|---|
| ROM 0x08000000-0x080051f4 | ARM code (header, crt0, main, menus, playback loop, soft-float, heap) |
| ROM 0x080051d4 ... | menu graphics, sprite tiles, the two FVMV streams |
| ROM (Vol 1) 0x1ea9c1c | menu tree; 0x1ea9de4 four 64 KiB brightness LUTs; 0x1ef2fb4 "Quit?" text |
| ROM tail | IWRAM image (load source) |
| IWRAM 0x03000000-0x03006e63 | IRQ handler table (0x00-0x34), IRQ dispatcher 0x610, memcpy/brightness blit 0x6dc-0x818, GSM pulse table 0x81c, LAR tables 0x101c-0x12db, audio work buffer 0x12dc, audio decoder 0x146c-0x1e48, video decoder 0x1ee0-0x5be8, MV table 0x5be8, frame driver 0x5fe8, player/AV helpers 0x6068-0x6e60 |
| IWRAM 0x03006e80 | scanline clock (u32); 0x03006e84 sound context |
| IWRAM 0x03007c00-0x03007fff | stacks; BIOS IRQ vector 0x03007ffc, BIOS IF flags 0x03007ff8 |
| EWRAM 0x02000000-0x0203e807 | heap block: 2 frame buffers, display buffer, PCM buffers ([format.md](format.md) section 2.4) |
| VRAM 0x06000000-0x06012bff | mode 3 frame buffer (240 x 160 BGR555) |
| VRAM 0x06014000- | OBJ tiles for the on-screen display |

## 4. Menus **(code)(emu)**

The menu engine (0x08002064) runs a table of pages. A page holds:

- a list of items;
- a background (a static image, or an FVMV stream: the engine supports both, but these carts
  use only static images);
- a default selection.

An item has a highlight sprite, a position, a target page (or -1 = play), a pointer to a
stream pointer, and a key code. Vol 1 has two pages:

| Page | Items |
|---|---|
| 0 Main | CONTROLS (to page 1), episode 1 (play stream 0), episode 2 (play stream 1). Episode 1 is selected first |
| 1 Controls | the controls help screen; B (item key 2) returns to page 0 |

The D-pad moves the selection (0x08005080) and A chooses. Each page frame draws the
background into the display buffer (EWRAM 0x02025808), draws the item sprites, copies it to VRAM
and waits about 150 scanlines on the scanline clock.

The episode titles are part of the menu bitmaps; no title text exists in the ROM:

| Cart | Episode 1 | Episode 2 |
|---|---|---|
| Vol 1 | A Hot Water Battle | For Ho-Oh the Bells Toll! |
| Vol 2 | Playing with Fire! | Johto Photo Finish |
| Vol 3 | Pokemon - I Choose You! | Here Comes the Squirtle Squad |
| Vol 4 | Beach Blank-out Blastoise | Go West Young Meowth |

## 5. Episode playback (0x0800276c) **(code)(emu)**

### 5.1 Opening a stream (0x03006068)

- It checks the `FVMV` magic and reads the header ([format.md](format.md) section 2.1).
- It sets up the A/V context (0x080031f8):
  - frame buffers at heap+0 and heap+0x12c00, display buffer at heap+0x25800;
  - decoded-PCM buffer at heap+0x38400. IWRAM would be used if 2 x header[0x1c] bytes fitted
    below 0x03007c00, which they do not;
  - sound init (0x03006964) with one channel: a 16 KiB ring at heap+0x3a800, FIFO A.
- It sets the time base to `clock - period`, so that the first frame is due at once.
- It sets the brightness level to 1 (normal) and the loop flag to 0.
- It starts the sound (0x030069fc). This sets up:
  - SOUNDCNT_H 0x0b04 (DirectSound A, 100 %, left and right, Timer 0, FIFO reset);
  - DMA1 from the ring to FIFO A (0xb640: repeat, 32-bit, FIFO timing);
  - Timer 0 reload 0xff00 at F/1, which overflows at **65,536 Hz** (the sample clock);
  - Timer 1 reload 0xe000 at F/64 with IRQ. It is not cascaded, so it fires every 8192 x 64
    cycles = **32 Hz**.

### 5.2 The loop

```
loop:
    decode_next()                 # 0x03006248: one record = video + all its audio blocks
    if end of stream: stop sound, clear sprites, return to the menu
    blit()                        # 0x030063c4 -> 0x080035d4: copy the new frame to VRAM
    while clock - base < period - 150: pass      # busy-wait (no Halt / IntrWait)
    keys = KEYINPUT; handle UI (section 6)
```

`decode_next` (0x03006248 -> 0x08003474) does the following:

1. It decodes the video payload with the IWRAM decoder (0x03005fe8) into buffer `sel`, with
   buffer `sel ^ 1` as the reference, and toggles `sel`.
2. It decodes every 40-byte audio block with 0x03001b88 into the PCM buffer (256 x int32
   each).
3. It passes them all at once to the ring writer (0x080032fc -> 0x03001d18, the 4x
   interpolator and quantiser). The writer writes 1024 bytes per block into the ring at the
   write position.
4. It advances `base` by one period (1704 scanlines).

### 5.3 Timing

- **The clock** is a scanline counter. The VBlank IRQ (0x030068e8) adds 228 to the u32 at
  0x03006e80. `clock()` (0x03006910) returns that value plus VCOUNT, or plus VCOUNT - 228
  during VBlank, with IME cleared around the read. One unit is one scanline (1232 cycles).
- **Video**: each frame is due `period` = 1704 scanlines after the previous one, which is
  7.99171 fps. A frame is decoded and blitted as soon as the clock is within 150 scanlines of
  its due time, so it is shown up to 150 lines (11 ms) early. If a decode runs late, the next
  wait is shorter; `base` advances by exactly one period per frame, so the schedule never
  drifts. Frames are never dropped.
- **Audio**: the sample clock (Timer 0) runs at exactly 65,536 Hz. The Timer 1 IRQ
  (0x030068ac -> 0x03006bd4) adds 0x800 to the ring read position at 32 Hz. Every 8 calls it
  restarts DMA1 at the ring start. So the player knows the DMA read position only to 2 KiB.
- **Sync**: audio is not timed separately. Each record carries 1/8 s of audio, plus one extra
  block every 125 frames to match the 7.9917 fps video ([format.md](format.md) section 2.2). The
  ring writer checks the space between the write and read positions before each write. If
  the write position has caught up with the read position, or fallen behind it (by the
  measure at 0x0800333c), it moves the write position 0x2000 bytes (half the ring) away from
  the read position (0x08003420). In mGBA this resync fires 9 to 15 times per episode, about
  every 100 to 140 s ([perf.md](perf.md)). Each one skips or repeats up to 1/8 s of audio.

### 5.4 Display

- The screen is mode 3, single buffered. The blit copies the 240 x 160 decoded frame straight
  to VRAM row by row. The copy routine also centres smaller pictures, which never occur.
- At brightness level 1 the blit is a plain 32-byte `ldm/stm` copy (0x030006dc).
- At levels 0, 2, 3 and 4 every pixel goes through one of four 32K-entry RGB555 lookup tables
  in ROM (Vol 1 0x1ea9de4 + 0x10000 x n), by the routine at 0x03000750.
- The blit starts as soon as the decode ends, whatever the raster position, so tearing is
  possible.
- The decoded frame is never copied to the display buffer during an episode; that buffer is
  for the menus only.
- The on-screen display uses OBJ sprites (tiles at 0x06014000, palette 0x05000200). It
  covers brightness, time and the quit dialog.

## 6. Input during playback **(code)(emu)**

The controls screen lists these, and the code agrees:

| Key | Action |
|---|---|
| Right / Left (hold) | fast forward / rewind: jump to the next / previous seek point. While the key is held the jump size grows. The time display (current and total, h:mm:ss, from frame count x 1704 / 13618.0 lines per second) is shown |
| R / L (hold) | skip forward / backward 12 seek points (about 1.5 to 3 minutes). It acts once the key has been held for 5 frames; keeping it held repeats the skip |
| A / B | brightness up / down (levels 0-4, default 1). An OSD bar is shown for 8 frames |
| START | pause. Shows the time display; START again resumes. The time base is reset on resume |
| SELECT | "Quit? Are you sure? A Button: Yes, B Button: No". A returns to the menu |
| Up or Down (held) | shows a status sprite (OAM 0) |

Seeking moves the record pointer by the `back` / `fwd` links of the records
(0x03006594 / 0x03006618 forward, 0x030066e0 / 0x03006764 backward). It mutes and refills
the sound ring (0x030064d8) and resets the time base (0x03006550). Seek points are true
keyframes, so the picture is correct at once. The audio decoder state is not reset by a seek,
so the first audio block after a seek is filtered with the state left by the old position.

Holding all of A, B, SELECT and START triggers a soft reset (0x08003b58: SWI 1 and SWI 0)
from every wait loop.

## 7. Interrupts **(code)(emu)**

| Source | Rate | Handler | Work |
|---|---|---|---|
| VBlank | 59.73 Hz | 0x030068e8 | scanline clock += 228; BIOS IF flag |
| Timer 1 | 32 Hz | 0x030068ac -> 0x03006bd4 | ring read position += 0x800; restart DMA1 at the ring start every 0x4000 bytes; BIOS IF flag |
| Game Pak | -- | 0x030006c0 | spins forever (cartridge removed) |
| others | -- | 0x03006960 | `bx lr` |

The dispatcher at 0x03000610 is the BIOS user IRQ handler. It looks the handler up in the
table at IWRAM 0x03000000 by the lowest set bit of IE & IF, acknowledges IF, and jumps. The
IRQ load is about 92 interrupts per second. **No video or audio decoding runs in interrupts**:
everything is done in the main loop.

## 8. What is different from the VX (Shrek) player

The VX player is documented in [../vx/player.md](../vx/player.md).

| | FVMV player | VX++ player (Shrek) |
|---|---|---|
| Code | ARM C (ARM SDT) in ROM + hand ARM in IWRAM | ARM in IWRAM/EWRAM, 3 programs and a loader |
| ROM | 32 MiB, linear, no mapper | 64 MiB, "Matrix" bank switching |
| Decode scheduling | main loop, one frame at a time, 150-line early start | decode-ahead queue of 4 frames, Timer 3 IRQ blits |
| Frame clock | scanline counter, 1704 lines | Timer 3 at 21 Hz, 3 ticks per frame |
| Picture | decoded straight to RGB555, copied to VRAM | YUV420, self-modifying YUV->RGB blit |
| Audio decode | once per video frame (8 or 9 blocks), in the main loop | per Timer 3 tick, in the IRQ |
| Output stage | 4x interpolation, (e1 + 2e) * 15/16 noise shaper | identical |
| Branding | "powered by actimagine" boot logo | Actimagine |
