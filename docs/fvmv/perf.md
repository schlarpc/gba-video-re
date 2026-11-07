# FVMV player: hardware resource use during playback

All eight episodes were measured **whole-stream**, in both player builds: 80,972 frames and
about 605,000 video refreshes of emulated time. The tools are `tools/fvmv/perf/prof_fvmv.py`, `tools/fvmv/perf/summarize.py` and
`tools/fvmv/perf/uc_cost_fvmv.py`. They write their per-run data to `tmp/fvmv/perf/`; these are
local run outputs and are not part of the repository.

Units: 1 cycle = 1/16,777,216 s. One movie frame lasts 1704 scanlines =
**2,099,328 cycles** (7.99171 fps). One video refresh is 280,896 cycles, so a movie frame
lasts 7.47 refreshes.

## TL;DR

| Resource | Use |
|---|---|
| CPU busy (not in the frame-wait spin) | **51.2-52.1 % mean** per stream. Per frame window: p99 63-65 %, max 70-77 % |
| CPU split (whole stream, all streams within ±0.5 %) | audio synthesis **17.2 %**, video decode **16.3 %**, blit to VRAM **8.8 %**, audio 4x interpolation + quantiser **8.6 %**, DMA cycle steal 0.65 %, IRQ + UI + BIOS 0.14 %, **idle 48 %** |
| Video decode per frame | mean 342 k cycles (16 % of a frame period), p99 about 550 k, max **764 k (36 %)** (Vol 2 ep 1, frame 9617) |
| Audio per frame | 540-545 k cycles (26 %) for 8 blocks, at most 621 k for 9. Audio costs more CPU than video |
| Deadline | **No frame is late.** The worst frame window still has 457 k idle cycles. All work could be about 1.3x slower before a frame was late. The player never drops frames |
| Audio sync | 9-15 ring resyncs per episode (clusters of 2-3, 3-5 clusters per episode). Each one skips or repeats up to 1/8 s of audio |
| Cart bus | reads 8.4-11.3 KB/s of video + 2.56 KB/s of audio from ROM. No code runs from ROM during decoding (the ROM wait loop is the only ROM code that is hot) |
| DMA | DMA1 only: PCM ring to FIFO A, 65,536 B/s, about 1,815 cycles per refresh |
| Memory | EWRAM: 256,000-byte heap block (97.7 % of EWRAM) = 2 frame buffers + menu display buffer + PCM buffers. IWRAM: 28,260-byte image (86 %) + stacks |
| Interrupts | VBlank (59.7 Hz) + Timer 1 (32 Hz) = about 92 IRQs/s, all trivial (0.03 % of the CPU) |

## 1. Method

**(a) Instrumented mGBA, whole stream (primary).**

- The core is mGBA's libretro core with `tools/mgba/mgba-prof.patch` applied (MPL-2.0). It adds
  the cycles mGBA charges for every executed instruction (fetch, execute, data waitstates,
  VRAM stall) to a histogram indexed by PC.
- `tools/fvmv/perf/prof_fvmv.py` boots the cartridge, selects the episode and plays it to the end.
- After every video refresh it folds the histogram delta into activity buckets by PC range
  (table below).
- The cycles in a refresh that no instruction accounts for are DMA cycle steal, because the
  CPU never halts during playback.
- A PC breakpoint at the frame decoder entry (0x03005fe8) records the refresh in which each
  frame starts decoding. Consecutive starts define the frame windows.
- The decode-only PC ranges give the exact video decode cycles of every frame.

| Bucket | PCs |
|---|---|
| video decode | IWRAM 0x03001ee0-0x03005be8, 0x03005fe8-0x03006068; ROM A/V wrapper 0x08003474-0x08003560 (+0x20 in build B) |
| audio synthesis | IWRAM 0x0300146c-0x03001d18 (unpack, RPE, lattice) |
| audio output | IWRAM 0x03001d18-0x03001e48 (4x interpolation, quantiser) + ring bookkeeping |
| blit | IWRAM 0x030006dc-0x03000818 (copy, brightness LUT) + ROM 0x080035a4-0x080036f4 |
| idle | the frame-wait loop (ROM 0x080028a4-0x080028b8 in A, 0x080028a8-0x080028bc in B) and its callees 0x03006424 and 0x03006910 |
| IRQ | 0x03000610-0x030006dc, 0x030068ac-0x03006910, 0x03006bd4-0x03006cb0 |
| UI / main | everything else |

**(b) Independent cost model (cross-check).**

- `tools/fvmv/perf/uc_cost_fvmv.py` runs the cartridge's video decoder in the unicorn oracle.
- It prices every instruction and data access from GBATEK's ARM7TDMI timing table, using the
  VX cost model (`tools/fvmv/perf/uc_cost_model.py`, a copy of `tools/vx/perf/uc_cost.py`; see [../vx/perf.md](../vx/perf.md)) with WAITCNT 0x4014 and
  EWRAM at 2 waitstates.
- Sample: 120 frames per build (frames 1-40, 60 random frames, and the 20 heaviest).

| Check | Build A (Vol 1 ep 1) | Build B (Vol 3 ep 1) |
|---|---|---|
| decode cycles, mGBA - model | **+0.16 %** mean (+0.09 to +0.21 %) | **+0.16 %** mean (+0.09 to +0.21 %) |

The residual is the ROM wrapper around the decoder call: method (a) counts it as decode,
method (b) does not run it. The two cycle models agree to
well under 1 %.

**Caveats.**

- Window statistics (p99 and max busy) are at refresh granularity. Frame windows are 7 or 8
  refreshes long (the period is 7.47 refreshes), so a 7-refresh window shows about 7 % less
  idle time than the frame really had. The figures are therefore slightly pessimistic.
- Means are exact sums.
- mGBA does not model the cartridge prefetch buffer exactly, but no hot code runs from ROM.

## 2. CPU, per stream

| stream | frames | busy mean | busy p99 | busy max | video | audio synth | audio out | blit | DMA | other | decode/frame mean | p99 | max (% of period) | audio/frame mean | min idle/frame |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Vol 1 ep 1 | 9936 | 51.6 % | 63.0 % | 70.1 % | 16.3 % | 17.1 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 342 k | 528 k | 656 k (31 %) | 540 k | 518 k |
| Vol 1 ep 2 | 9963 | 51.7 % | 63.9 % | 71.2 % | 16.4 % | 17.2 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 343 k | 549 k | 662 k (32 %) | 541 k | 566 k |
| Vol 2 ep 1 | 9977 | 51.9 % | 64.4 % | 76.7 % | 16.5 % | 17.2 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 346 k | 559 k | 764 k (36 %) | 542 k | 457 k |
| Vol 2 ep 2 | 9937 | 51.6 % | 63.6 % | 72.9 % | 16.3 % | 17.1 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 342 k | 550 k | 717 k (34 %) | 541 k | 532 k |
| Vol 3 ep 1 | 10213 | 51.6 % | 64.1 % | 73.1 % | 16.2 % | 17.3 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 339 k | 544 k | 677 k (32 %) | 543 k | 529 k |
| Vol 3 ep 2 | 10215 | 51.9 % | 64.9 % | 71.6 % | 16.3 % | 17.3 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 343 k | 561 k | 677 k (32 %) | 544 k | 559 k |
| Vol 4 ep 1 | 10598 | 52.1 % | 64.7 % | 71.6 % | 16.6 % | 17.3 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 349 k | 562 k | 659 k (31 %) | 545 k | 518 k |
| Vol 4 ep 2 | 10133 | 51.2 % | 63.5 % | 72.3 % | 15.7 % | 17.3 % | 8.6 % | 8.8 % | 0.65 % | 0.14 % | 330 k | 529 k | 659 k (31 %) | 543 k | 545 k |

The two builds cannot be told apart: the codec, blit and timing code are the same, and
build B's UI change does not run in normal playback.

Unit costs (from the means):

- **Video**: about 8.9 cycles per output pixel on average, and 20 cycles per pixel in the
  worst frame. An unsplit skip or MV copy of an 8x8 block is only 8 `ldm/stm` row copies
  (more for halfword-aligned sources).
- **Audio synthesis**: about 45 k cycles per 40-byte block, or 176 cycles per 16,384 Hz sample.
  Most of it is the 8-stage lattice: 16 `mla` and 8 `ldr`/`str` pairs per sample.
- **Audio output**: about 22.5 k cycles per block, or 22 cycles per 65,536 Hz output byte.
- **Blit**: 185 k cycles per frame, or 2.4 cycles per byte (EWRAM reads at 6 cycles per word,
  VRAM writes).
- **DMA1**: about 1,815 cycles per refresh, 0.65 %.

## 3. Deadlines and scheduling

- The main loop decodes a frame (video + 8 or 9 audio blocks), blits it, and spins on the
  scanline clock until 150 lines before the next due time. Then it decodes the next frame.
- One frame's work averages 1.08 M cycles (52 % of the period).
- The heaviest windows still leave 457-566 k cycles of spin, so **no frame in any episode is
  late or dropped**.
- The work could grow by about 30 % before the worst frame missed its slot. Even then the
  schedule would recover, because the time base advances by exactly one period per frame.
- The 150-line early start means that a frame reaches VRAM up to 11 ms before its nominal
  time. The blit is not synchronised to VBlank, so a tear line can appear wherever the
  raster is during the 185 k-cycle (0.66 refresh) copy.

## 4. Audio buffering and resyncs

- Audio is decoded once per frame, together with the video, into a 16 KiB ring. That is
  250 ms at 65,536 Hz, or two frames' worth.
- DMA1 reads the ring. The player only knows the read position in 2 KiB steps, from the
  32 Hz Timer 1 IRQ.
- Before each write the ring writer (0x080032fc) checks the distance to the read position.
  When the distance check fails it moves the write position by half the ring (0x2000 bytes).
- In mGBA this happens 9-15 times per episode:
  - The first cluster comes in the first ~16 s.
  - Later clusters come every 2,200-3,300 frames (5-7 minutes).
  - Each cluster has 2-3 resyncs within a few frames, usually next to a 9-block frame
    (frames 125n - 1).
  - Logs: `tmp/fvmv/cap3/pVeE/resync.txt` (a local capture output), as (frame, refresh) pairs.
- The cause is the slow drift between the two clocks. The 9-block correction keeps audio
  within 38 ppm of the frame clock. The 2 KiB read-position granularity and the 8-9 KiB writes
  then decide on which frame the threshold is crossed.
- Each resync skips or repeats up to 1/8 s of sound.
- Whether hardware shows exactly the same count depends on mGBA's timer and DMA timing,
  which is cycle-accurate for these simple uses.

## 5. Memory and bus

| Region | Use during playback |
|---|---|
| EWRAM 256 KiB | heap block 0x02000008-0x0203e807 (256,000 B): frame buffers 2 x 76,800, menu display buffer 76,800 (idle during episodes), decoded PCM 9,216, PCM ring 16,384 |
| IWRAM 32 KiB | 28,260 B code and tables (video decoder ~15.6 KB, audio ~2.5 KB + 2.8 KB tables, MV table 1 KB, player helpers), globals, stacks at the top |
| VRAM | 76,800 B mode-3 frame + OSD sprite tiles |
| ROM | stream data read in place (records are not copied). About 11.0-13.9 KB/s on average |

The decoder runs entirely from IWRAM. Its data accesses go to EWRAM (frame buffers, at 2
waitstates) and ROM (the three substreams, sequential). The ROM traffic is about 0.2 % of the
cartridge bandwidth.
