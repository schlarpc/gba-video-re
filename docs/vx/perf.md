# VX family -- hardware resource use during playback

Part 1 (sections 1-7) is the deep dive on *Shrek* (Rev 6). [Part 2](#part-2-every-vx-stream-the-vxgb-build-and-the-shrek-2-rev-5-audio-underrun) measures all 10 VX
streams, compares the VXGB build and analyses the *Shrek 2* (Rev 5) audio failure.

Code and data in `tools/vx/perf/`.
Measured over the **whole movie**: all 37,837 VX frames, 113,498 Timer3 ticks, 5404.7 s of
emulated time (90,675,326,806 CPU cycles), from the first Timer3 tick to the player's exit.

Units: 1 cycle = 1/16,777,216 s. A movie frame lasts 3 Timer3 ticks = 3 x 12483 x 64 =
**2,396,736 cycles** (7.000 fps). One LCD refresh = 280,896 cycles, so a movie frame lasts 8.53 refreshes.

## TL;DR

| resource | use |
|---|---|
| CPU busy (not spinning in the main loop) | **76.2 % mean**. Per movie frame: p50 74.0 %, p99 100 %, max 100 %. 54 of 37,832 movie-frame windows have zero idle time |
| CPU split (mean of each window) | YUV->RGB blit **33.3 %**, video decode **26.3 %**, audio decode **15.9 %**, IRQ overhead 0.31 %, DMA cycle steal 0.30 %, UI/main loop 0.01 %, **idle 23.8 %** |
| video decode per frame | mean 631 k cycles (26 % of a frame period), p50 576 k, p99 1.55 M, max **2.87 M (120 % of a frame period)** |
| deadline | No frame is dropped and no blit is skipped. **41 frames are displayed one Timer3 tick (47.6 ms) late**, then the next frame is shown one tick early, so A/V sync is restored. All 41 are in long runs of heavy frames that empty the 4-frame decode-ahead queue. No single heavy frame causes one |
| headroom | Decode could be about **5-8 % slower** before late frames appear (queue replay: none at x0.94, 3 at x0.96). At x1.3, blits start to be skipped. On average, 23.8 % of the CPU is idle |
| memory stalls | 12.6 % of all CPU time (excluding the idle spin) is spent in data waitstates. In the video decoder, **28.7 % of its cycles are waitstates, 94 % of them on EWRAM** |
| cart bus | WAITCNT 0x4014: WS0 3/1, prefetch on, but no code runs from ROM. The CPU reads 12.4 KB/s from the cart (video 9.9 KB/s, audio 2.45 KB/s). That is about **0.1 % of the cart bandwidth**, and 0.11 % of CPU time is spent in ROM waitstates |
| DMA | DMA1 only: VRAM ring -> FIFO A, 65,536 B/s in 4,096 bursts/s of 16 B. It costs 0.30-0.34 % of the cycles. DMA0/2/3 are never used |
| PPU | Mode 3, single-buffered. The blit takes **802 k cycles = 2.85 refreshes**, and 57.5 % of its VRAM writes happen during active display. The result is 2-3 visible tear lines on every movie frame. No sprites are visible in normal playback (OAM is all zero, and tile 0 is not displayable in bitmap modes) |
| audio | DirectSound A only. PSG and DirectSound B are off. Timer0 = 65,536 Hz sample clock (no IRQ). Timer1 IRQ = 128 Hz, Timer3 IRQ = 21.0 Hz, 149 IRQ/s in total. Timer2 is unused |
| memory occupancy | EWRAM 99.0 % (4 YUV frame buffers = 93.8 %), IWRAM 97.6 % code/tables plus stacks, VRAM ~97 % (frame buffer 78 %; the player uses the rest of VRAM as general RAM for its state, audio tables and the PCM ring) |

## 1. Method

**(a) Instrumented mGBA (primary, whole movie).** This is the same libretro core the emu harness uses
(nixpkgs `libretro.mgba`, 2026-07-28 snapshot), rebuilt from source with a small patch
(`tools/vx/perf/patch/mgba-perf.patch`; build tree `tools/vx/perf/mgba-src/`, output `mgba-src/mgba_libretro.so`).
The patch does the following:
- After each executed instruction (`ARMStep`/`ThumbStep`), it adds the cycles mGBA charged for that
  instruction to a bucket. The cycles include fetch, execute, data waitstates, prefetch and the
  VRAM-contention stall model. The bucket key is (context, activity). Activity comes from a PC
  lookup table that `classify.py` builds (`buckets.json`). Context comes from a small shadow stack:
  the IRQ exception pushes it, the Timer1 and Timer3 ISR thunks name it, and the BIOS
  `subs pc,lr,#4` pops it.
- It wraps the `load/store{8,16,32,Multiple}` callbacks. For each access it records the region
  (`addr>>24`), the bytes, and the waitstate cycles (charged cycles minus the zero-wait cost).
  Accesses made by DMA (no cycle counter) are counted separately.
- It counts DMA units, bytes and cycles per channel, the cycles the CPU is blocked by DMA (`cpuBlocked`),
  halted cycles, IRQ-entry cycles, Matrix mapper commands, and CPU VRAM writes split by raster
  state (`DISPSTAT`: active / HBlank / VBlank).
- It takes a full snapshot of the counters at event PCs: decode start 0x03006D30, decode return
  0x03007320 / 0x02000310 (prefill), Timer3 tick 0x03007AB0, blit entry 0x03000370 and blit return 0x03007CCC.

`mgba_perf.py` boots the ROM, presses Play, and turns the snapshots into the CSVs. The
whole movie takes 8 min. The accounting is consistent: the sum of the attributed cycles plus the
DMA-blocked cycles equals the elapsed time to within 87 cycles out of 9.07e10. No snapshot was lost.

**(b) Independent cost model in unicorn (cross-validation).** `uc_cost.py` imports the unicorn oracle
(`tools/vx/emu/uc_decode.py`, unmodified). It prices every executed decoder instruction from GBATEK's
ARM7TDMI table:
- ALU 1S, plus 1I for a register shift. MUL 1S+mI, where m depends on Rs. LDR 1S+1N+1I. STR 2N. LDM nS+1N+1I. B 2S+1N. A failed condition costs 1S.
- Each data access is priced by region, width and sequentiality: IWRAM 1; VRAM/PAL 1 or 2; EWRAM 3 or 6;
  ROM WS0 4/2 for 16-bit (N/S) and 6/4 for 32-bit.

It uses none of mGBA's timing code. Sample: 122 frames (20 keyframes, 20 random
frames, and the heaviest frames including the maximum, 2.87 M). Seed states were made with
`mkseeds.py` -> `tools/vx/emu/mkstate.make`.

| check | result |
|---|---|
| instruction count per frame, (a) vs (b) | **identical on 122/122 frames** |
| decode cycles, (a) - (b) | +0.80 % mean (min +0.32 %, max +1.34 %); mGBA is always slightly higher |
| decode data-waitstate cycles | +0.61 % mean |

Most of the residual comes from two places. First, mGBA's PPU/CPU VRAM-contention stall
(`GBAMemoryStallVRAM`), which the GBATEK model does not have: about 1 k cycles per frame on the
decoder's context struct, which is in VRAM. Second, about 2 k cycles per frame in the bit readers.
`crossval.csv` lists every sample frame.

**Error bars and assumptions.**
- The cycle numbers carry about ±1-2 %: the two models differ by 0.8 %, and mGBA's ARM7 timing is
  close to hardware on the published timing test suites. The CPU split between activities is exact
  within each model, because instruction counts match.
- WAITCNT = 0x4014 (written by `main`). Nothing in programs 0, 1 or 2 writes the internal memory
  control register 0x4000800. EWRAM therefore runs at the power-on 2 waitstates (3 cycles per 16-bit
  access, 6 per 32-bit access), and both models assume this.
- **Matrix mapper latency is not modelled.** mGBA copies a page instantly, and the decoder's busy-poll
  (0x030007FC) never loops: about 2.9 instructions per command. There are 65,083 map commands in the
  movie (12/s). Even 1 ms per command would cost only 1.2 % of the CPU, so this cannot change the conclusions.
- The HLE BIOS IRQ path has the same 6 instructions as the real BIOS IRQ entry and exit.
- Each movie-frame window runs from one blit start to the next. Windows are counted as 3 ticks;
  41 of them are 4 ticks long and 41 are 2 ticks long (the late frames).

## 2. CPU

### 2.1 Where the cycles go (whole movie)

| activity (bucket) | % of all cycles | cycles / movie frame | instructions | CPI | data-waitstate share |
|---|---|---|---|---|---|
| **YUV->RGB blit** (0x03000370, in the Timer3 IRQ) | **33.27** | 797.5 k (constant, ±250) | 15.10 G | 2.00 | 12.0 % |
| **idle**: main-loop spin on "queue full" (0x030072F0) | **23.84** | 571.5 k mean | 8.28 G | 2.61 | 23.4 % (it polls the ctx in VRAM) |
| **video decode**, total | **26.31** | 630.6 k mean | 10.39 G | 2.30 | 28.7 % |
| . MC copies (integer-pel, incl. abs-MV + DC) | 12.75 | 48.5 % of decode | 4.01 G | 2.89 | 42.8 % |
| . dequant + IDCT + add/clamp | 4.22 | 16.0 % | 2.22 G | 1.73 | 12.1 % |
| . intra (bilinear, 16x16, 4x4, chroma) | 3.71 | 14.1 % | 1.24 G | 2.71 | 31.0 % |
| . coefficient VLC + CBP + escapes | 1.77 | 6.7 % | 0.97 G | 1.65 | 5.5 % |
| . Exp-Golomb ue/se readers | 1.74 | 6.6 % | 1.13 G | 1.39 | 0 |
| . partition-tree dispatch / mode stubs | 1.01 | 3.8 % | 0.31 G | 2.97 | 13.2 % |
| . frame loop + MV prediction | 0.61 | 2.3 % | 0.33 G | 1.71 | 16.9 % |
| . bit refill incl. Matrix remap writes | 0.50 | 1.9 % | 0.19 G | 2.43 | 17.7 % |
| . mapper busy-wait | 0.0006 | ~0 | | | |
| **audio decode** total (Timer3 IRQ) | **15.94** | 382 k mean | 8.20 G | 1.76 | 6.7 % |
| . packet + 4x upsample/noise-shape output | 7.56 | | 5.70 G | 1.20 | |
| . synthesis | 7.16 | | 2.08 G | 3.12 | |
| . coefficient unpack / prediction | 1.22 | | 0.42 G | 2.63 | |
| IRQ overhead: entry, dispatcher, EWRAM thunks, BIOS, Timer1 tick, av_tick bookkeeping | 0.31 | 7.5 k | | | |
| DMA1 cycle steal (CPU blocked) | 0.30 | 7.3 k | | | |
| UI (read_keys once per frame, OSD), main loop, audio-window remap, other | 0.01 | ~0.4 k | | | |

Busy time is 76.2 %, at an average of 6.25 MIPS (33.8 G instructions in 5404.7 s).
The CPU never halts: `Halt`/`IntrWait` are never used, and the idle time is a busy-wait.
On hardware that spin also burns power and VRAM bandwidth, about 2.6 MB/s of reads of ctx+0x28/0x2C.

Unit costs:
- Blit: **20.8 cycles per output pixel**, 10.4 instructions per pixel, 76,800 bytes per frame.
- Audio: about **20.9 k cycles per 20-byte packet**. That is 163 cycles per 16,384 Hz sample, or 41 cycles per 65,536 Hz output byte. 128 packets/s.
- Timer1 IRQ: about 200 cycles each.
- Timer3 tick (excluding blit and audio): about 574 cycles.

### 2.2 Per movie-frame distributions (37,832 display windows of 2,396,736 cycles)

| per window | mean | p50 | p99 | max | min |
|---|---|---|---|---|---|
| busy (wall - idle spin) | 1,825 k (76.2 %) | 1,774 k (74.0 %) | 2,397 k (100 %) | 100 % | 1,407 k (58.7 %) |
| video decode | 631 k (26.3 %) | 580 k | 1,209 k (50.5 %) | 1,896 k (79.1 %) | 236 k |
| blit | 797.5 k (33.3 %) | 797.4 k | 797.8 k | 797.8 k | 797.3 k |
| audio | 382 k (15.9 %) | 377 k | 400 k | 527 k (22.0 %) | 232 k |
| IRQ + DMA | 14.8 k (0.6 %) | | | 20.5 k | |
| idle | 572 k (23.8 %) | 622 k | 968 k | 990 k (41.3 %) | 0 |

The budget is fixed. Blit + audio + IRQ + DMA take **about 1.19 M cycles = 50 % of every movie frame**
whatever the content. That leaves **about 1.20 M cycles per frame for video decode** on average.

### 2.3 Per-frame decode cost (`d_vdec`, decoder cycles only, IRQs excluded)

| | mean | p50 | p99 | p99.9 | max | min |
|---|---|---|---|---|---|---|
| all 37,837 frames | 630.6 k | 576.1 k | 1,552 k | 1,949 k | **2,875 k** | 236 k |
| the 195 keyframes (seek points) | 1,228 k | 1,248 k | 1,949 k | | 2,875 k | 236 k |
| inter frames | 627.5 k | 574.6 k | 1,529 k | | 2,856 k | 238 k |
| wall time from decode start to end (IRQs included) | 765 k | 665 k | 2,783 k | | 5,273 k (2.2 movie frames) | |

Worst frames, all from the action scenes around minute 26:

| frame | key? | decode cycles | MC | intra | coef VLC | IDCT | data waits | buffered ahead after |
|---|---|---|---|---|---|---|---|---|
| 11054 | yes | 2,874,974 | 0 | 568 k | 747 k | 1,346 k | 405 k | 2 |
| 11310 | no | 2,855,550 | 96 k | 519 k | 679 k | 1,325 k | 407 k | 2 |
| 11145 | no | 2,243,730 | 68 k | 533 k | 456 k | 970 k | 349 k | 3 |
| 9204 | no | 2,210,050 | 84 k | 554 k | 371 k | 918 k | 350 k | 3 |
| 28737 | no | 2,200,241 | 57 k | 538 k | 410 k | 986 k | 346 k | 3 |

The heaviest frames are intra plus residual: IDCT about 47 %, coefficient VLC about 25 %, intra about 20 %.
They are scene-cut frames. Only one of them is a seek keyframe; the others are inter frames that are
mostly intra-coded. Typical inter frames are MC-dominated, at 48 % of decode overall.
The end credits (from about 83 min) are the lightest stretch, at about 260-500 k cycles per frame.

### 2.4 Deadline, queue and late frames

The decoder runs ahead of the display by up to 4 frames: there are 4 YUV slots, and the main loop spins while
`decode - display == 4`. After each decode, the number of frames decoded ahead was:

| frames ahead | 4 | 3 | 2 | 1 |
|---|---|---|---|---|
| count | 35,248 | 1,837 | 291 | 461 |

The queue never runs empty when a frame is due, except for **41 frames**. Each of these is due
at a tick where the frame is not ready yet. `timer3_av_tick` then decrements `[0xac]` and shows the frame on the next tick,
which is **47.6 ms late**. The frame after it is shown after only 2 ticks. Audio is not affected,
because it is clocked by hardware. No blit is skipped: the skip path needs a lag of more than 0x21 ticks (1.57 s).

The 41 late frames fall in 8 bursts (frames 2865-2873, 11164-11166, 11317-11337, 11883-11892,
18424-18477, 23552-23564, 28767-28806, 33152-33170). Every burst follows 7-20 consecutive frames of
1.3-1.7 M cycles each, which is above the 1.20 M per frame left for decode. The two 2.87 M frames are
absorbed by the queue on their own. See `worst_stretch.png`.

The smallest slack (decode end to blit start) is 775 cycles. The mean slack is 7.6 M cycles, about 3.2 frames ahead.

**Headroom.** `queue_sim.py` replays the queue and display logic with the measured costs, with decode cost scaled
by a factor. The model is approximate: it predicts 27 late ticks at x1.00, against the 41 measured.

| decode cost scale | x0.94 | x0.96 | x0.98 | x1.00 | x1.05 | x1.10 | x1.20 | x1.30 | x1.50 |
|---|---|---|---|---|---|---|---|---|---|
| late display ticks | 0 | 3 | 15 | 27 | 39 | 107 | 261 | 1,252 | 3,429 |
| skipped blits | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 19 | 367 |

So the player runs at the edge of its budget. A decoder about 6 % faster, or a cheaper blit, would remove
all lateness. At 1.3x the cost, frames would really be dropped. The idle 23.8 % is not spread evenly:
it is concentrated in easy scenes, where the queue is full.

## 3. Memory buses

### 3.1 Configuration

- WAITCNT 0x4014: SRAM 4; **WS0 N=3, S=1 waitstates**; WS1/WS2 defaults; PHI off; **prefetch buffer on**.
- 0x4000800 is not written, so EWRAM has 2 waitstates. EWRAM is a 16-bit bus: 3 cycles per 8/16-bit access and 6 per 32-bit access.
- IWRAM: 32-bit, 1 cycle. VRAM/palette: 16-bit bus, 1 cycle, 2 cycles per 32-bit access, plus PPU contention. OAM: 32-bit, 1 cycle.
- All playback code runs from IWRAM: 168.2 GB fetched = 31.1 MB/s of instruction fetch, with 0 waitstates.
  EWRAM code (ISR thunks, read_keys, matrix_map) accounts for only 37 MB of fetch over the movie, and its fetch waits are 0.05 % of time.
  The prefetch buffer therefore does nothing during playback.

### 3.2 CPU data traffic per region (whole movie; DMA listed separately)

| region | reads | writes | B/s read / write | waitstate cycles | % of all time | what |
|---|---|---|---|---|---|---|
| EWRAM | 4.63 GB (1.36 G accesses) | 2.39 GB (0.48 G) | 856.5 k / 443.0 k | 9.19 G | **10.1 %** | decoder: MC reference reads and plane writes 6.46 G waits; blit: Y/UV reads 2.72 G waits |
| IWRAM | 10.86 GB | 3.61 GB | 2.01 M / 0.67 M | 0 | 0 | stacks, coefficient buffers, clamp LUT, dequant tables |
| VRAM | 15.67 GB | 4.75 GB | 2.90 M / 0.88 M | 7.22 G | 8.0 % (5.6 % is the idle spin) | idle spin polling the ctx (14.0 GB of the reads); blit 537.6 KB/s of writes; audio decoder state/tables/PCM ring |
| ROM (window) | 66.8 MB | 0.73 MB (mapper regs) | 12.36 k / 0.13 k | 0.10 G | 0.11 % | video bitstream 53.5 MB = 9.9 KB/s (79 kbit/s); audio 13.2 MB = 2.45 KB/s (128 x 20 B/s) |
| I/O | 4.9 MB | 8.8 MB | | 0 | | IRQ regs, timers, keys |
| palette / OAM | 0 / 0 | 1 KB / 2.6 KB | | | | OSD writes only (at start/end) |
| DMA1 | VRAM 354 MB read | FIFO A 354 MB write | 65.5 k | | 0.30 % blocked | |

Data waitstates take 12.6 % of all CPU time, or 18.2 % if the spin is included. Of the busy time, about 16.6 % is stall.
- Video decoder: 28.7 % of its cycles are data stalls. 6.46 G of its 6.84 G stall cycles are on EWRAM (MC reads the
  reference frame and writes the output plane, intra reads its neighbours, IDCT adds into EWRAM), about 0.3 G are on VRAM (the ctx),
  and 0.08 G are on ROM (the bitstream).
- Blit: 12 % stall. That is about 72 k cycles of EWRAM Y/UV reads and about 24 k cycles of VRAM stores per frame.
- If EWRAM had 1 waitstate (0x0E000020, which works on most GBAs but not the GBA Micro), the decoder's EWRAM stalls would
  fall by about half (2->1 waits per 16-bit access, 5->3 per 32-bit access): about 2.6-3.2 G cycles, or 11-14 % of decode.
  That is more than the 6 % needed to remove the late frames. This is an estimate, not a measurement.

Bus utilisation, as access cycles plus waits over wall time:
- EWRAM about 12 % (9.19 G waits + 1.84 G accesses).
- VRAM CPU side about 13 %, of which 9 % is the idle poll. The PPU also reads 76.8 KB per refresh = 4.59 MB/s for scan-out.

### 3.3 Occupancy

| region | size | used | by |
|---|---|---|---|
| EWRAM | 256 KiB | **~99.0 %** (0x02000000-0x0203F5E0, plus the mailbox at 0x0203FFF4) | code/rodata/VLC LUT 12.7 KiB (0x32C8); **4 YUV420 frame slots 4 x 60 KiB = 240 KiB (93.8 %)** (Y 256x160, chroma interleaved 256x80; 16 padding columns per row are wasted = 3.75 KiB/slot); MV array 0x318 |
| IWRAM | 32 KiB | 31,988 B code+tables (**97.6 %**); stacks in the last 780 B | IRQ dispatcher, clamp LUT, blitter, whole VX decoder, audio decoder, player_run. The lowest stack byte seen written is 0x03007D20, 44 B above the code |
| VRAM | 96 KiB | **~97 %** | mode-3 frame buffer 0x12C00 (75 KiB, 78.1 %); player state + audio state 0x06013600-0x06013FA4 (2.4 KiB); OSD font/icon tiles 0x06014000-0x06015300 (4.75 KiB); audio decoder tables 0x06015300-0x06015F40 (3.1 KiB); PCM ring 0x06015F40-0x06017F40 (8 KiB). Unused: 0x06012C00-0x06013600 (2.5 KiB), 0x06017F40-0x06018000 (192 B) |
| palette | 1 KiB | 32 B | 16-colour OBJ palette for the OSD; BG palette unused (mode 3 is direct colour) |
| OAM | 1 KiB | 0 during normal playback | all 128 entries zero. That is 8x8 tile 0 at (0,0), but tiles < 512 are not displayable in bitmap modes, so nothing shows. The OSD (time, icons, "Brightness") writes one 8x8 OBJ per character only when shown |

## 4. Cart bus and mapper

- CPU reads from the cart: 33.4 M accesses, 66.8 MB = **12.4 KB/s**. Most are 16-bit LDRH from the
  window at random (N) cost of 4 cycles, about 0.15 % of CPU time including waits.
  - Theoretical WS0 bandwidth is 16.8 MB/s sequential (2 cycles per halfword) or 8.4 MB/s non-sequential.
  - The movie uses about **0.07-0.15 %** of it. The 128 KiB/s that an uncompressed audio-only stream would need is not approached either.
- **Matrix mapper:** 65,083 remap commands during playback, all from the main context.
  - About 52 k are video: the decoder refill at 0x030007A8 maps 1 KiB each time the read pointer crosses 0x400.
  - About 13 k are audio: the main loop at 0x0300732C maps 2 x 512 B when Timer3 flags a half.
  - 66.65 MB remapped in total (12.3 KB/s, 12 commands/s). Each command is 4 register writes; 260 k writes in all.
  - CPU cost: refill bucket 0.5 %, busy-wait about 0.
  - The mapper copies inside the cart, so it adds no GBA-bus traffic beyond the register writes. Its real copy latency is unknown (see section 1).
- **DMA:** only DMA1 (control 0xB640: FIFO timing, 32-bit, repeat), VRAM 0x06015F40 ring -> FIFO A.
  - 4,096 requests/s x 4 words = 65,536 B/s, 88.5 M word transfers in all.
  - mGBA charges 14 cycles per burst (2N+6S plus internal cycles) = 57 k cycles/s = 0.34 %. The measured CPU-blocked time is 0.30 % (275 M cycles).
  - Timer1 re-arms DMA1 at the ring start every 16 blocks. DMA0/2/3 are never used, and the code uses no CpuSet/CpuFastSet during playback.

## 5. PPU / display

- DISPCNT 0x1F43: **mode 3** (240x160 direct colour, single buffer, no page flip), BG2 on (the BG0/1/3 bits are set but have no effect in mode 3),
  OBJ on, 1-D OBJ mapping. BG2 uses the identity affine matrix. Blending, windows and mosaic are off.
  The PPU therefore reads the whole 75 KiB frame buffer every refresh (4.59 MB/s).
- **The blit writes into the visible frame buffer from the Timer3 IRQ, with no VBlank sync.**
  - Each movie frame: 76,800 B of 32-bit stores (2 pixels each) over **801.8 k cycles = 2.85 LCD refreshes**.
  - It starts at a random raster line: p50 vcount 113, range 0-227. Timer3's 798,912-cycle tick is not locked to the 280,896-cycle refresh.
  - Of the blit's VRAM writes, 57.5 % happen during active display (outside HBlank), 13.0 % in HBlank and 29.6 % in VBlank.
    The VBlank share matches the time fraction, so nothing aligns the blit to blanking.
  - The scan-out passes the write position 2-3 times per movie frame. About 2.85 of every 8.53 refreshes (33 %) show a
    **torn image**: new picture above the tear and previous picture below it. The tear moves down about 56 lines per refresh.
  - Fixing this would need a second 75 KiB frame buffer, which does not exist: mode 5 is too small, and VRAM is 97 % used.
    The only alternative is a blit about 3x faster, so that it fits in VBlank plus racing the beam.
- VRAM bandwidth: the blit writes 537.6 KB/s, against about 33.5 MB/s of CPU write capacity (1.6 %).
  **The blit is compute-bound** at 10.4 instructions per pixel, with 12 % stalls. It is not limited by VRAM bandwidth.
- Sprites: none are visible during normal playback (see OAM above). The OSD is drawn only on demand, one 8x8 OBJ per character.

## 6. Audio, timers and IRQs

| item | setting | rate | CPU |
|---|---|---|---|
| Sound | **DirectSound A only**, 100 % volume to L+R, Timer0. DirectSound B off. PSG: SOUNDCNT_L = 0 (no channel enabled); SOUNDCNT_X = 0x80 (master on only). SOUNDBIAS 0x4200 = 7-bit / 65.536 kHz PWM | 65,536 samples/s s8 mono | |
| Timer0 | reload 0xFF00, /1, no IRQ | 65,536 Hz FIFO clock -> 4,096 DMA1 requests/s | DMA 0.30 % |
| Timer1 | reload 0xF800, /64, IRQ | 128 Hz (one 512-B ring block) | ~200 cycles/IRQ, 0.15 % (incl. dispatch) |
| Timer2 | unused | | |
| Timer3 | reload -12483, /64, IRQ | 21.000 Hz; every 3rd tick shows a frame (7 fps). Decodes audio just in time (2.4 packets/tick avg) | blit 33.3 % + audio 15.9 % + 0.07 % bookkeeping |
| IRQs | IE = Timer1 \| Timer3. The dispatcher re-enables IRQs, so Timer1 can nest inside the long Timer3 handler | **149 IRQ/s** (805,290 total) | entry + dispatch + thunks + BIOS ~0.17 % |

Audio decode runs inside the Timer3 IRQ, as does the blit. As a result the Timer3 handler occupies
**49.3 % of all CPU time**, and on a display tick it holds the CPU for about 0.93 M cycles (about 55 ms).
Only Timer1 can interrupt it.

## 7. Files

| path | content |
|---|---|
| `tools/vx/perf/per_frame.csv` | one row per VX frame (37,837). `frame`; `dec_start/dec_end` (global cycle stamps); `d_*` = counter deltas over that frame's decode call: `d_vdec` = decoder cycles (IRQs excluded), `d_vdec_<sub>`, `d_vdec_ins`, `d_vdec_memwait`, `d_wall` (incl. interrupting IRQs), region bytes/waits over the interval; `lead_after` = frames decoded ahead after this decode; `blit_*` = this frame's blit (cycles, wall, vcount at start/end, VRAM bytes by raster state); `slack` = blit start - decode end; `w_*` = the display window in which this frame is on screen (blit start -> next blit start): wall, busy, spin, vdec, blit, audio, irq, dma, region traffic; `w_ticks` |
| `tools/vx/perf/per_tick.csv` | one row per Timer3 tick (113,497) with the same breakdown |
| `tools/vx/perf/totals.json`, `tools/vx/perf/summary.json` | whole-playback raw counters (per context x bucket, per region) and the derived statistics |
| `tools/vx/perf/crossval.csv` | mGBA vs unicorn cost model, 122 frames |
| `plots/decode_timeline.png`, `cpu_share.png`, `decode_hist.png`, `worst_stretch.png` | plots |
| `tools/vx/perf/mgba_perf.py` | the whole-movie measurement (`--state`/`--frames` for a segment) |
| `tools/vx/perf/mgba-src/`, `tools/vx/perf/patch/mgba-perf.patch` | instrumented core. Build: `nix shell nixpkgs#gcc nixpkgs#gnumake nixpkgs#zlib -c make -f Makefile.libretro platform=unix ARCH=x86_64 -j8` (do `make clean` after editing `perf.h`, because the Makefile has no header dependencies) |
| `tools/vx/perf/classify.py` -> `buckets.json` | PC -> activity map. Hand ranges plus a capstone flood fill from the 16 partition-size mode tables, which separates MC workers from intra workers and from the dispatch stubs |
| `tools/vx/perf/uc_cost.py`, `mkseeds.py`, `states/`, `uc/` | cost model (b), its seed states and per-seed outputs |
| `tools/vx/perf/analyze.py`, `plots.py`, `queue_sim.py`, `pf.py` | statistics, plots (matplotlib via `nix shell`, see header), queue/deadline replay |

Reproduce: `cd perf && python3 classify.py && python3 mgba_perf.py --out full && cp full/*.csv full/totals.json . && python3 analyze.py`, then run the plots.

## Part 2: every VX stream, the VXGB build and the Shrek 2 (Rev 5) audio underrun

Scope: all 10 VX streams (6 single-movie carts, 4 streams on *Shrek + Shark Tale*). Every stream
was measured whole, in the instrumented mGBA, from Play to the player's exit. Two player builds
are covered: VX++ (Rev 6 + combo) and VXGB (Rev 5). The method and the full single-stream deep
dive (memory buses, DMA, PPU, occupancy) for *Shrek* (Rev 6) are part 1 above and are not
repeated. This part gives the family-level numbers, quantifies the VXGB build, and analyses the
audio-window failure.

Code: `tools/vx/perf/`. A run writes `tools/vx/perf/runs/<cart>_<stream>/` (per_frame.csv.gz,
per_tick.csv.gz, lev.csv.gz, totals.json, summary.json); runs are local outputs, not in the repository.

Units: 1 cycle = 1/16,777,216 s. A movie frame = 3 Timer3 ticks = 2,396,736 cycles (7.000 fps).

### P2.1 Method

**(a) Instrumented mGBA, whole stream (primary).** We use the same patch as part 1
(`tools/vx/perf/patch/mgba-perf.patch`). It attributes every executed instruction's charged cycles to
(context, activity bucket), and snapshots the counters at event PCs. We added one thing: a
*light event* log (`tools/vx/perf/patch/mgba-perf-light-events.patch`, LUT flag 0x2000). At chosen PCs it records the cycle time
stamp and 16 words of the player state struct (VRAM 0x06013E80). That gives the audio read
pointer, the refill request ctx+0xB4, the counters and the av_tick lag at every audio packet,
refill, Timer3 tick, blit and decode boundary.

The driver `vx_perf.py` replaces `mgba_perf.py`. It has one profile per player build (event PCs,
context PCs, bucket map; [player.md](player.md) 11.2) and walks each cart's menu with capture.py's key tables.
It numbers displayed frames from the player's own display counter, so it handles skipped blits
correctly.

- **Check that the generalisation changed nothing.** On *Shrek* (Rev 6) the new core and driver
  reproduce the part 1 data exactly: d_vdec, w_busy and blit_cyc are identical for all
  37,837 frames. The light events therefore do not perturb timing.
- **VXGB bucket map (`align_vxgb.py`).** The Rev 5 decoder aligns with the Rev 6 one instruction
  for instruction (difflib on normalised mnemonics, 5,831 of 7,988 IWRAM instructions). The Rev 6
  labels carry over to the aligned code. The VXGB-only code is labelled by hand: decode_frame,
  the bit readers, the CAVLC residual (0x03005e84..0x03006524), dequant + IDCT
  (0x03006524..0x03006920) and the 4 grid-zeroing routines (new bucket `vdec_grid`). The EWRAM C
  code (main loop, Timer ISRs, inline refill) is also labelled by hand.

**(b) Second method: GBATEK cost model in unicorn (cross-check on a sample).** We ran
`uc_cost.py` (from part 1) on the VXGB player's own decode_frame in the standalone Rev 5 oracle
(`uc_cost_vxgb.py`). It starts cold at seek points. The sample is 122 frames on the three VXGB
carts: the heaviest frames within 12 frames of a seek point, plus random seek points. It
includes the 3.58 M-cycle maximum of *Shrek* (Rev 5) and the 3.23 M maximum of *Shark Tale*.

| cart | frames | instruction count equal | mGBA - model, decode cycles |
|---|---|---|---|
| Shrek Rev 5 | 40 | 25 (the other 15 differ by exactly +-14) | +1.03 % mean (+0.60 .. +1.75) |
| Shrek 2 Rev 5 | 40 | 29 (+-14) | +0.89 % (+0.47 .. +1.57) |
| Shark Tale Rev 5 | 42 | 27 (+-14, one +7) | +1.00 % (+0.54 .. +1.36) |
| (Shrek Rev 6, perf.md) | 122 | 122 | +0.80 % (+0.32 .. +1.34) |

The +-14 instruction differences come in +14/-14 pairs on neighbouring frames. The cold-start
oracle maps the first video pages differently from the running player, so one bit-reader refill
(14 instructions) lands in the next frame. As with Rev 6, mGBA charges about 1 % more than the
GBATEK model: its VRAM-contention stall on the ctx struct, plus a little in the bit readers. VX++
was cross-checked in part 1 (122 frames). The VX++ streams here run the same player code.

### P2.2 Suite summary

| stream | build | frames | busy mean | busy p99 / max (per frame window) | video | blit | audio | IRQ+DMA+UI | idle | saturated windows |
|---|---|---|---|---|---|---|---|---|---|---|
| shrek-rev6_20200 | vxpp | 37,837 | 76.2 % | 100.0 / 100.0 % | 26.3 % | 33.3 % | 15.9 % | 0.63 % | 23.8 % | 54 |
| shrek-rev5_20200 | vxgb | 37,837 | 76.9 % | 100.0 / 100.0 % | 26.7 % | 33.3 % | 15.9 % | 0.96 % | 23.1 % | 84 |
| shrek2-rev6_20200 | vxpp | 38,816 | 76.0 % | 100.0 / 100.0 % | 26.2 % | 33.3 % | 15.9 % | 0.63 % | 24.0 % | 57 |
| shrek2-rev5_20200 | vxgb | 38,816 | 76.1 % | 100.0 / 100.0 % | 25.9 % | 33.3 % | 15.9 % | 0.96 % | 23.9 % | 68 |
| sharktale-rev6_20200 | vxpp | 37,697 | 77.2 % | 100.0 / 100.0 % | 27.3 % | 33.3 % | 15.9 % | 0.63 % | 22.8 % | 39 |
| sharktale-rev5_20200 | vxgb | 37,697 | 81.7 % | 100.0 / 100.0 % | 31.5 % | 33.3 % | 15.9 % | 0.97 % | 18.3 % | 203 |
| shrek-sharktale-rev5_20200 | vxpp | 34,874 | 55.7 % | 75.8 / 100.0 % | 15.9 % | 23.3 % | 15.9 % | 0.63 % | 44.3 % | 0 |
| shrek-sharktale-rev5_1eef800 | vxpp | 34,261 | 56.0 % | 75.2 / 98.3 % | 16.2 % | 23.3 % | 15.9 % | 0.63 % | 44.0 % | 0 |
| shrek-sharktale-rev5_3dd3600 | vxpp | 595 | 64.2 % | 100.0 / 100.0 % | 15.6 % | 33.3 % | 14.7 % | 0.65 % | 35.8 % | 0 |
| shrek-sharktale-rev5_3e76a00 | vxpp | 707 | 65.8 % | 100.0 / 100.0 % | 17.2 % | 33.3 % | 14.7 % | 0.65 % | 34.2 % | 0 |

"busy" = everything except the main loop's idle spin (the CPU never halts). Per-frame-window
figures are over the blit-to-blit windows. A *saturated* window has no idle time at all. blit,
audio, IRQ, DMA and UI are content-independent fixed costs:

- the blit is 33.3 % at 240x160 and about 23 % at 240x112 (20.8 cycles per output pixel);
- audio is 15.9 % (14.7 % on the combo credits streams, whose AFrames are mostly 10-12 bytes);
- the rest is under 1 %.

So about 50 % of the CPU is spent before any video decoding.

| stream | decode mean / p50 / p99 / max (k cycles) | max as % of a frame period | seek-point mean | max wall (decode + IRQs) | CPI | data stalls | late frames (late ticks, max) | skipped blits | min audio-refill margin |
|---|---|---|---|---|---|---|---|---|---|
| shrek-rev6_20200 | 631 / 576 / 1552 / 2875 | 120 % | 1228 k | 5273 k | 2.30 | 28.7 % | 262 (1017, 12) | 0 | 49.7 ms |
| shrek-rev5_20200 | 639 / 575 / 1680 / 3579 | 149 % | 1364 k | 6083 k | 2.39 | 29.2 % | 484 (2211, 16) | 0 | 18.8 ms |
| shrek2-rev6_20200 | 627 / 566 / 1519 / 2561 | 107 % | 1148 k | 5689 k | 2.29 | 28.7 % | 729 (8176, 32) | 0 | 26.6 ms |
| shrek2-rev5_20200 | 622 / 550 / 1582 / 3019 | 126 % | 1240 k | 5512 k | 2.38 | 29.7 % | 717 (8455, 32) | 8 | 29.9 ms; **1 lost request** |
| sharktale-rev6_20200 | 655 / 602 / 1470 / 2504 | 104 % | 1088 k | 4881 k | 2.25 | 27.9 % | 368 (1801, 23) | 0 | 131.9 ms |
| sharktale-rev5_20200 | 756 / 687 / 1806 / 3234 | 135 % | 1346 k | 5717 k | 2.27 | 26.2 % | 2899 (31129, 36) | 26 | 29.7 ms |
| shrek-sharktale-rev5_20200 | 380 / 351 / 860 / 1700 | 71 % | 715 k | 2690 k | 2.40 | 31.6 % | 0 (0, 0) | 0 | 258.2 ms |
| shrek-sharktale-rev5_1eef800 | 388 / 361 / 847 / 1408 | 59 % | 687 k | 1584 k | 2.37 | 31.0 % | 0 (0, 0) | 0 | 277.2 ms |
| shrek-sharktale-rev5_3dd3600 | 374 / 238 / 1637 / 1938 | 81 % | 1080 k | 3200 k | 3.13 | 42.6 % | 0 (0, 0) | 0 | 463.6 ms |
| shrek-sharktale-rev5_3e76a00 | 412 / 238 / 1949 / 2096 | 87 % | 1100 k | 3356 k | 2.91 | 39.5 % | 0 (0, 0) | 0 | 433.0 ms |

- **late frames** = frames whose blit starts after its scheduled Timer3 tick (frame k due at tick
  T0 + 3k). The parentheses give the sum of late ticks over all late frames and the largest lag.
- **skipped blits** = frames the player never drew (lag > 0x20 ticks; [player.md](player.md) 11.4).
- **min audio-refill margin** = the smallest time, over the whole stream, between a refill of an
  audio-window half and the start of the Timer3 handler that next reads that half (P2.4).

Decode cycles by activity (% of decode):

| stream | mc | intra | idct | resid_vlc | bits | dispatch | grid | frame | refill |
|---|---|---|---|---|---|---|---|---|---|
| shrek-rev6_20200 | 48.5 | 14.1 | 16.0 | 6.7 | 6.6 | 3.8 | 0.0 | 2.3 | 1.9 |
| shrek-rev5_20200 | 47.2 | 12.8 | 14.6 | 10.7 | 5.3 | 3.4 | 2.0 | 2.5 | 1.4 |
| shrek2-rev6_20200 | 49.7 | 14.2 | 15.1 | 6.5 | 6.5 | 3.8 | 0.0 | 2.3 | 1.9 |
| shrek2-rev5_20200 | 49.7 | 13.0 | 13.1 | 9.8 | 5.1 | 3.2 | 2.1 | 2.6 | 1.3 |
| sharktale-rev6_20200 | 47.2 | 16.5 | 15.8 | 6.4 | 6.4 | 3.6 | 0.0 | 2.2 | 1.9 |
| sharktale-rev5_20200 | 41.0 | 14.3 | 17.7 | 12.5 | 5.6 | 3.5 | 1.7 | 2.1 | 1.6 |
| shrek-sharktale-rev5_20200 | 56.9 | 15.0 | 10.1 | 4.0 | 6.2 | 3.6 | 0.0 | 2.7 | 1.5 |
| shrek-sharktale-rev5_1eef800 | 54.7 | 17.2 | 10.5 | 4.1 | 5.9 | 3.4 | 0.0 | 2.7 | 1.5 |
| shrek-sharktale-rev5_3dd3600 | 60.5 | 6.1 | 11.6 | 9.7 | 3.6 | 2.6 | 0.0 | 3.9 | 2.1 |
| shrek-sharktale-rev5_3e76a00 | 56.0 | 6.3 | 13.8 | 10.7 | 4.3 | 2.9 | 0.0 | 3.6 | 2.4 |

### P2.3 VXGB (Rev 5) against VX++ (Rev 6), same movie

The three movies exist in both builds, encoded separately (different QP, so different bits;
[format.md](format.md)). Only the residual entropy layer and the player's C-code placement differ.

| movie | build | video kbit/s | decode mean / seek-pt mean / max (k cyc) | residual entropy (VLC/CAVLC + CBP + grid) cycles per video bit | dequant+IDCT share of decode | entropy share of decode |
|---|---|---|---|---|---|---|
| Shrek | VX++ | 79.0 | 631 / 1228 / 2875 | 3.76 | 16.0 % | 6.7 % |
| Shrek | VXGB | 59.1 | 639 / 1364 / 3579 | 9.60 | 14.6 % | 12.7 % |
| Shrek 2 | VX++ | 76.6 | 627 / 1148 / 2561 | 3.71 | 15.1 % | 6.5 % |
| Shrek 2 | VXGB | 54.4 | 622 / 1240 / 3019 | 9.58 | 13.1 % | 12.0 % |
| Shark Tale | VX++ | 80.4 | 655 / 1088 / 2504 | 3.65 | 15.8 % | 6.4 % |
| Shark Tale | VXGB | 78.8 | 756 / 1346 / 3234 | 9.51 | 17.7 % | 14.2 % |

- **CAVLC costs 2.6x more cycles per coded bit than the VX++ run/level VLC.** The figure is
  9.5-9.6 against 3.7 cycles per video bit for the residual entropy layer: CBP + coefficient
  tokens + levels/runs + nC grid bookkeeping. The 4-bit coeff_token tree walk, the separate
  total_zeros/run_before lookups, the per-coefficient run handling and the grid reads/writes all
  cost more than one table lookup per (last, run, level) symbol. The entropy layer takes 12-14 %
  of decode time in VXGB, against 6.5-6.7 % in VX++.
- **The movies are cheaper to code but not cheaper to decode.** VXGB *Shrek* uses 25 % fewer
  bits than VX++ *Shrek*, yet its mean decode is 1 % higher, its seek-point mean 11 % higher, and
  its worst frame 24 % higher (3.58 M against 2.87 M cycles = 149 % of a frame period). The heavy
  frames are intra scene cuts dominated by residual coding, which is where CAVLC costs most. The
  same holds for *Shrek 2*: mean -1 %, seek points +8 %, maximum +18 %.
- **More lateness, and blit skips.** Lateness comes from runs of heavy frames that drain the
  4-slot queue. VXGB *Shark Tale* is the heaviest stream in the family: mean decode 31.5 % of the
  CPU, 203 saturated windows, 2,899 late frames, a lag of up to 36 ticks (1.7 s behind the audio)
  and 26 skipped blits. Mean CPU busy is still only 82 %; the problem is bursts, not the average.
- **Code placement.** The Rev 5 C code (Timer ISRs, main loop) runs from EWRAM, so the
  per-interrupt bookkeeping costs twice as much: IRQ + thunks + av_tick + Timer1 = 0.64 % of all
  cycles, against 0.31 % in VX++. This is negligible next to the decoder.
- **Not a difference.** Blit (same code), audio (same code), DMA (same FIFO set-up) and the
  Matrix mapper protocol are identical. The mapper sees fewer commands on VXGB *Shrek* (52 k vs
  65 k) only because the stream is smaller.

**A correction to part 1 (Shrek Rev 6).** Section 2.4 says that 41 frames are shown one tick late
and the next frame one tick early. Exactly 41 display windows are 4 ticks long and 41 are 2 ticks
long, so the count of lateness *onsets* is right. But inside a burst the onsets add up: av_tick's
lag counter (ctx+0xAC) reaches -12. So 262 frames are shown late, by up to 12 ticks (0.57 s),
before the queue refills and the player catches up at one frame per tick. Over the movie the
lateness totals 1,017 ticks. On *Shrek 2* (Rev 6) the lag reaches 32 ticks, one tick short of the
blit-skip threshold (lag < -0x20 and odd).

### P2.4 The Shrek 2 (Rev 5) audio underrun

#### P2.4.1 What happens

The audio window and its refill protocol are described in [player.md](player.md) 11.3. The timeline below is
from the light-event log of the whole-movie run. Times are relative to the AFrame that reads the
stale half; "dec n" is the player's decode counter, and VX frame = n - 3.

| t (ms) | event |
|---|---|
| -431.0 | Timer3 tick. The main loop leaves its idle spin and decodes VX frame 16190 (dec_s -428.5) |
| -402.0 | Decode 16190 ends. ctx+0xB4 = 0: nothing to refill. The queue is full again (4 frames ahead), so the main loop enters the idle spin |
| -399.0 | Timer3 tick. The audio reader wraps from 0x08001fde to 0x08001806 (-395.2) and **posts a refill request for half 0x08001c00**. That is 6.8 ms after the last moment the main loop checked |
| -351.4 .. -288 | Blit of frame 16187, then audio. The display counter advances and the spin ends |
| -285.6 | The main loop starts decoding **VX frame 16191**: a scene cut, all intra, 2.58 M decoder cycles, 6 video map commands. With the interrupts it takes **297.6 ms** of wall time |
| -2.5 | Timer3 tick. The reader is at 0x08001bfc |
| 0.0 | The next AFrame straddles 0x08001c00 and reads half 0x08001c00, which was never refilled and still holds the audio from 2 KiB earlier. Crossing into that half, av_tick **posts the request for 0x08001800, overwriting the pending 0x08001c00 request** |
| +11.9 | Decode 16191 ends. The main loop services ctx+0xB4 = 0x08001800: it maps the next 1 KiB of audio (the bytes that belonged in 0x08001c00) into **0x08001800** |
| after | Half 0x08001c00 is never refilled out of turn again. The two halves stay swapped, so the reader always sees 1 KiB of stale data and then 1 KiB of the right data, 1 KiB off. 1,024 is not a multiple of the AFrame sizes, so the reader parses AFrame headers at non-AFrame offsets from AFrame 296,039 on. Every later block is noise-like output of the LPC synthesis fed with misaligned bytes, until the end of the movie |

This matches the earlier observation ([vxgb.md](vxgb.md) 9) (blocks 0..296,038 are equal to vxaudio.py, then
garbage) and pins the exact mechanism. It is **not** an underrun in the usual sense of the
reader catching up with the writer. It is a **lost refill request**: the single-slot mailbox
ctx+0xB4 is overwritten. The request was posted just after the main loop's last check. It then
waited through an idle spin, which never checks requests, and through the longest decode in that
part of the movie: 407.2 ms from request to service, against 392.7 ms of audio left in the
window.

Its numbers:
- refill 14.50 ms after the start of the Timer3 handler that read the stale half;
- AFrame 296,039, VX frame 16,191, at 2,313 s into the movie;
- in VXGB *Shrek 2* only one request in 13,320 is lost, and the stale reads are 0 before it.

#### P2.4.2 Deterministic in mGBA

The player restarts all its timers at Play. The only free phase is between the LCD and the
timers, set by when Play is pressed, and it moves the PPU's VRAM-contention stalls. We repeated
the run up to frame 16,260 with Play pressed at emu frames 1001, 1009 and 1030 (the reference
run uses 1000). Every run loses the request at the same AFrame, 296,039, during the same decode,
with the refill late by 14.500 / 14.510 / 14.509 ms (reference 14.504 ms). The phase changes the
outcome by about 10 microseconds.

#### P2.4.3 Would a real GBA do the same?

The outcome depends only on the timing between two hardware-clocked events (the Timer3 tick
that posts the request, and the one that reads the stale half) and the CPU time in between.
What separates mGBA from a real GBA here:

| factor | effect on this event | size |
|---|---|---|
| ARM7 instruction timing, IWRAM/EWRAM/VRAM waitstates | All decoder and ISR code runs from IWRAM/EWRAM. The GBATEK-table model agrees with mGBA to +0.5..+1.8 % on decode cycles, with mGBA always slower. EWRAM waitstates are the power-on 2 in both (the player never writes 0x4000800) | about 1 % |
| ROM prefetch buffer (mGBA's least exact timing area) | no code runs from ROM; the only ROM accesses are the bit reader's and the audio packet's LDRH from the window, 0.1 % of time | none |
| DMA (FIFO A) | modelled; 0.3 % of time | <0.1 % |
| PPU VRAM contention | modelled; varying its phase (P2.4.2) changes the margin by 10 microseconds | none |
| **Matrix mapper copy latency** | mGBA copies instantly. On hardware a 1 KiB map takes time. The Matrix 3-D OTP memory behind the controller is specified at about 1 MB/s read (Matrix's 512 Mb 3-D PROM, ISSCC 2003), so about 1 ms = 17 k cycles per 1 KiB, if the controller is not faster. The decoder waits for the mapper only before a frame (0x03000710) and before issuing a new command, so latency mostly hides behind decoding. At the refill, the main loop first waits for the last video command of frame 16191, then the audio copy itself must finish before the stale half is read | makes the refill **later** by up to about 2 ms; it cannot make it earlier |

So the event fails if the CPU-bound span from the -351.4 ms Timer3 tick to the refill (363.4 ms,
no idle) finishes after the -2.5 ms tick. For the refill to come in time, the hardware would have
to run that span at least **4.0 % faster** than mGBA (14.5 ms of 363 ms; 4.8 % if only the
decode itself scales).

The other escape is the opposite one. If decode 16190 had ended after the -399.0 ms tick (3.0 ms
later, about 10 % slower over its 29 ms span), av_tick would have posted its request while that
decode was still running. The main loop would then have serviced it at -398 ms, and no request
would be lost.

The underrun therefore happens for any CPU timing from about 4 % faster to about 10 % slower
than mGBA. Mapper latency pushes it further in on the fast side. It could only help through the
slow side: it would have to delay decode 16190 by 3 ms or more. That decode issues one video map
command, so this needs about 3 ms per 1 KiB command, i.e. a mapper slower than about 0.35 MB/s.
That is three times slower than the Matrix part's specified read rate. The cost model puts real hardware
within about 1-2 % of mGBA, in the "hardware slightly faster" direction. **Conclusion: a real GBA
playing *Shrek 2* (Rev 5) from the start without interruption very likely loses the same refill
request at the same AFrame (VX frame 16,191, 38 min 33 s), and plays scrambled audio for the
remaining 54 minutes until the player is restarted. Confidence: high (we would put it at about
90 %).** Residual doubt: an unmodelled hardware effect larger than 4 % in the faster direction,
or a mapper that both copies faster than mGBA's zero time (impossible) and changes CPU timing.
We know of neither. We have no hardware measurement; a capture of a real cart's audio around 38:30
would settle it.

The failure depends on the history since the last seek, because the queue state and the
reader's position within the window at frame 16,191 come from that history. Starting from a
chapter, pausing, or seeking changes the phase. It can then avoid this failure, or move the risk
elsewhere. Once the halves are swapped, only a seek repairs the window: seek_to_entry (0x020004c8)
re-maps the whole 2 KiB window.

#### P2.4.4 Near misses on the other streams

Same analysis on every stream. "Slow-down to fail" = the margin divided by the CPU-bound span
before the refill, i.e. the uniform CPU slow-down relative to mGBA that would turn the closest
refill into a stale read.

| stream | refills | closest refill: margin (frame) | smallest slow-down to fail (margin, frame) | lost requests | stale reads |
|---|---|---|---|---|---|
| shrek-rev6_20200 | 12,921 | 49.7 ms (11310) | 15.7 % (49.7 ms, 11310) | 0 | 0 |
| shrek-rev5_20200 | 12,996 | 18.8 ms (28737) | 6.3 % (18.8 ms, 28737) | 0 | 0 |
| shrek2-rev6_20200 | 13,289 | 26.6 ms (5351) | 7.8 % (26.6 ms, 5351) | 0 | 0 |
| shrek2-rev5_20200 | 13,320 | 29.9 ms (5545) | 10.5 % (34.6 ms, 6143) | 1 | 52 |
| sharktale-rev6_20200 | 12,270 | 131.9 ms (16739) | 74.4 % (155.4 ms, 23645) | 0 | 0 |
| sharktale-rev5_20200 | 12,838 | 29.7 ms (31672) | 11.1 % (36.4 ms, 22553) | 0 | 0 |
| shrek-sharktale-rev5_20200 | 9,486 | 258.2 ms (2830) | 210.5 % (258.2 ms, 2830) | 0 | 0 |
| shrek-sharktale-rev5_1eef800 | 9,226 | 277.2 ms (22793) | 267.1 % (277.2 ms, 22793) | 0 | 0 |
| shrek-sharktale-rev5_3dd3600 | 116 | 463.6 ms (462) | 246.9 % (463.6 ms, 462) | 0 | 0 |
| shrek-sharktale-rev5_3e76a00 | 138 | 433.0 ms (175) | 223.7 % (450.0 ms, 553) | 0 | 0 |

(The Shrek 2 Rev 5 row covers the refills that succeeded. The lost request of P2.4.1 is not a
refill and is not in it.)

- ***Shrek* (Rev 5)** has the closest call that does not fail: 18.8 ms at frame 28,737, the
  same scene cut that is heavy on Rev 6. It would fail only if the hardware were 6.3 % slower than
  mGBA, or the mapper were 10x slower than 1 MB/s. Given the cross-check, it is safe, with
  medium-high confidence.
- ***Shark Tale* (Rev 5)**: the closest refill is 29.7 ms (frame 31,672), 15 % slow-down to fail.
  The tightest relative to its span is frame 22,553 (36.4 ms, 11 %). Safe.
- ***Shrek 2* (Rev 6)** (the VX++ encode of the same movie): frame 16,191 is harmless there.
  Its closest call is 26.6 ms at frame 5,351 (7.8 % slow-down to fail). Safe with medium-high
  confidence.
- *Shrek* (Rev 6) keeps 49.7 ms (15.7 % slow-down to fail). The other VX++ streams keep at least
  132 ms; on the combo cart the decode is light and the margin is over 250 ms.
- On every stream, no request was ever posted in the short window between the map command and
  the clear of ctx+0xB4 ([player.md](player.md) 11.3, item 3), although av_tick did run in that window.

### P2.5 Matrix mapper and cart bus per stream

The ROM is read at about 6-12 KB/s (video + audio), 0.1 % of the cartridge bus. Map commands
run at 12 per second on the 240x160 movies and 6 per second on the combo movies: about 1 per KiB of video plus 1 per KiB of audio. At an assumed 1 ms per
1 KiB command that is about 1 % of the time, mostly overlapped with decoding (P2.4.3). The
counts are in stats.json notes.

### P2.6 Files

Files in `tools/vx/perf/`:

| file | content |
|---|---|
| `vx_perf.py` | whole-stream measurement, any VX cart/stream (`--rom --stream [--menu] --out`) |
| `patch/mgba-perf.patch` + `patch/mgba-perf-light-events.patch` | instrumented core (part 1 patch + light events in `src/gba/perf.c`, `include/mgba/internal/gba/perf.h`, `src/arm/arm.c` event mask 0x3F00); build tree `tools/vx/perf/mgba-src/` (local) |
| `align_vxgb.py` -> `buckets_vxgb.json` | VXGB PC -> activity map |
| `vx_analyze.py` | per-run summary, late/skip schedule, audio-window replay |
| `nearmiss.py` | closest refills and slow-down to fail |
| `uc_cost_vxgb.py`, `crossval/*.csv` | second-method cross-check |
| `cavlc_cost.py`, `perf_tables.py` | the tables above |
| `runs/` (local) | per-stream outputs, incl. the Play-press phase runs of the determinism check |
