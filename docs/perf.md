# Shrek GBA Video -- hardware resource use during playback

Author: perf agent. Status: v1. Code and data in `perf/`.
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
(`perf/patch/mgba-perf.patch`; build tree `perf/mgba-src/`, output `mgba-src/mgba_libretro.so`).
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
(`emu/uc_decode.py`, unmodified). It prices every executed decoder instruction from GBATEK's
ARM7TDMI table:
- ALU 1S, plus 1I for a register shift. MUL 1S+mI, where m depends on Rs. LDR 1S+1N+1I. STR 2N. LDM nS+1N+1I. B 2S+1N. A failed condition costs 1S.
- Each data access is priced by region, width and sequentiality: IWRAM 1; VRAM/PAL 1 or 2; EWRAM 3 or 6;
  ROM WS0 4/2 for 16-bit (N/S) and 6/4 for 32-bit.

It uses none of mGBA's timing code. Sample: 122 frames (20 keyframes, 20 random
frames, and the heaviest frames including the maximum, 2.87 M). Seed states were made with
`mkseeds.py` -> `emu/mkstate.make`.

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
| `perf/per_frame.csv` | one row per VX frame (37,837). `frame`; `dec_start/dec_end` (global cycle stamps); `d_*` = counter deltas over that frame's decode call: `d_vdec` = decoder cycles (IRQs excluded), `d_vdec_<sub>`, `d_vdec_ins`, `d_vdec_memwait`, `d_wall` (incl. interrupting IRQs), region bytes/waits over the interval; `lead_after` = frames decoded ahead after this decode; `blit_*` = this frame's blit (cycles, wall, vcount at start/end, VRAM bytes by raster state); `slack` = blit start - decode end; `w_*` = the display window in which this frame is on screen (blit start -> next blit start): wall, busy, spin, vdec, blit, audio, irq, dma, region traffic; `w_ticks` |
| `perf/per_tick.csv` | one row per Timer3 tick (113,497) with the same breakdown |
| `perf/totals.json`, `perf/summary.json` | whole-playback raw counters (per context x bucket, per region) and the derived statistics |
| `perf/crossval.csv` | mGBA vs unicorn cost model, 122 frames |
| `perf/decode_timeline.png`, `cpu_share.png`, `decode_hist.png`, `worst_stretch.png` | plots |
| `perf/mgba_perf.py` | the whole-movie measurement (`--state`/`--frames` for a segment) |
| `perf/mgba-src/`, `perf/patch/mgba-perf.patch` | instrumented core. Build: `nix shell nixpkgs#gcc nixpkgs#gnumake nixpkgs#zlib -c make -f Makefile.libretro platform=unix ARCH=x86_64 -j8` (do `make clean` after editing `perf.h`, because the Makefile has no header dependencies) |
| `perf/classify.py` -> `buckets.json` | PC -> activity map. Hand ranges plus a capstone flood fill from the 16 partition-size mode tables, which separates MC workers from intra workers and from the dispatch stubs |
| `perf/uc_cost.py`, `mkseeds.py`, `states/`, `uc/` | cost model (b), its seed states and per-seed outputs |
| `perf/analyze.py`, `plots.py`, `queue_sim.py`, `pf.py` | statistics, plots (matplotlib via `nix shell`, see header), queue/deadline replay |

Reproduce: `cd perf && python3 classify.py && python3 mgba_perf.py --out full && cp full/*.csv full/totals.json . && python3 analyze.py`, then run the plots.
