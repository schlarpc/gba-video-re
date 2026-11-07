# Sonic X Vol 2 prototype: CPU and resource use during playback

Code and data: the analysis' working directory `work/perf/` (local; not distributed in this repository, see section 7). Units: 1 cycle = 1/16,777,216 s; one LCD refresh
(VBlank period) = 280,896 cycles; one video frame = 5 refreshes = 1,404,480 cycles (11.9455 fps).

Measured over **every frame of every stream**: Episode 103 (13,828 frames, 69,140 VBlanks, 1157.6 s),
Episode 104 (14,286 frames, 71,430 VBlanks, 1195.9 s) and the intro movie (59 frames, 4.9 s), each from
its first codebook load to its last blit.

## TL;DR

| | intro | Episode 103 | Episode 104 |
|---|---|---|---|
| blit geometry (scene mode) | 4x2 (7) | 3x3 (5) | 4x2 (7) |
| CPU busy, mean (rest is `Halt` in VBlankIntrWait) | 30.7 % | **36.3 %** | **32.3 %** |
| busy per video frame (5 VBlanks): p50 / p99 / max | 29.7 / 52.4 / 52.8 % | 35.2 / 57.1 / 59.1 % | 30.9 / 54.9 / 57.1 % |
| busy per single VBlank: p99 / max | 77.1 / 77.7 % | 77.6 / **88.4 %** | 77.6 / **93.7 %** |
| share of busy: audio decode | 43.5 % | 40.4 % | 45.5 % |
| share of busy: VQ blit | 30.1 % | 38.5 % | 28.7 % |
| share of busy: video decode (inflate + codebook + glue) | 25.0 % | 19.9 % | 24.5 % |
| . of which inflate window slide (BIOS CpuSet 16 KiB) | 2.2 % | 2.8 % | 3.7 % |
| . of which codebook build (unpredict + YUV->RGB) | 2.2 % | 0.5 % | 0.8 % |
| IRQ overhead / DMA steal / UI+other | 0.9 / 0.3 / 0.2 % | 0.8 / 0.2 / 0.2 % | 0.9 / 0.2 / 0.3 % |
| late or dropped frames | **0** | **0** | **0** |
| video bitrate mean / p99 over 1 s | 55.8 / 87.6 kbit/s (scene-level) | 80.8 / 242.0 kbit/s | 75.6 / 278.8 kbit/s |

- **Deadline.** All work runs inside the VBlank IRQ handler, one state per VBlank, so the deadline is
  the next VBlank. No handler ever overran: all 13,827 / 14,285 frame windows are exactly 5 VBlanks, and
  no VBlank-to-VBlank handler period differs from 280,896 cycles by more than 233 cycles (IRQ entry jitter). The worst
  handler took **263,156 cycles = 93.7 %** of a refresh (Episode 104 frame 9114: a 3,741-byte frame
  plus an inflate window slide), leaving 6.3 % headroom.
- **No tearing**, although the frame buffer is single (mode 3) and the blit runs during active
  display: the blit starts at line 160 and walks the frame top-down at about one row per 1,234 cycles
  (3x3) or 811 cycles (4x2), so the beam, which starts 68 lines (83,776 cycles) behind it and moves at
  1,232 cycles per line, never catches up.
- Compared with shipped Sonic X Vol 1 measured the same way: same load (Vol 1 33.9 % busy), same
  split. The proto's audio loop is 2.7 % cheaper (no output clamp); Episode 103 costs more because
  every scene uses the 3x3 blit (196 k cycles per frame against 130 k for 4x2).

## 1. Method

**(a) Instrumented mGBA (primary, whole streams).** The core is the Shrek perf core
(`tools/vx/perf/patch/mgba-perf.patch`, see [../vx/perf.md](../vx/perf.md) sec. 1). The copy adds one
thing: a PC lookup table for ROM code `0x08000000-0x0801FFFF`. The Shrek player runs only from
IWRAM, but this player runs Thumb code from ROM (state machine, stream parser, sound player, IRQ
handlers). After every instruction the core charges mGBA's cycles to (context, activity).
- Activities come from `classify.py`, which writes `buckets.json` from hand ranges based on the
  IWRAM/ROM function map. Buckets: inflate, blit, codebook luma/chroma/YUV->RGB/thumbnail, audio
  ARM loop, audio Thumb driver, Timer1 IRQ, IRQ dispatch, BIOS, MoviePlayer::Process, StreamBase,
  IRQ manager and state machine, keys, veneers, libc, font, memory manager and other.
- Contexts: 0 main, 1 IRQ dispatch, 2 VBlank handler (0x08003B30), 3 Timer1 handler (0x08006996).
  The BIOS `subs pc,lr,#4` (0x198) pops the context.
- The core takes full counter snapshots at four events: VBlank handler entry 0x08003B30, handler
  exit 0x08003B5C, blit entry 0x0300202C and codebook load 0x08005A90.
- `sx2perf.py` writes one row per VBlank (handler entry to next entry): wall, halted, DMA-blocked,
  cycles per bucket, handler duration, vcount at exit, and the blit and codebook flags.
- Navigation: Episode 103 and 104 start from the title-screen state with START, then [DOWN], then
  START, then A. The intro is measured from power-on. Runtime: 22 / 28 min per episode on a loaded
  host.
- Accounting check: in every row, halted plus the attributed cycles plus DMA-blocked cycles equals
  the elapsed cycles. No snapshot was lost.

**(b) Independent cost model in unicorn (cross-check on a sample).** `uc_cost.py` prices each executed
ARM and Thumb instruction with GBATEK's ARM7TDMI table. It runs the cart's own VBlank handler
(0x08003B30, i.e. one `MoviePlayer::Process` step) in unicorn, starting from an mGBA savestate
taken just before that handler (`sample.py`).
- Instruction costs: ALU 1S (+1I for a register shift), MUL 1S+mI, LDR 1S+1D+1I, STR 1N+1D,
  LDM/STM n accesses, B 2S+1N, Thumb BL 3S+1N, plus a pipeline refill when PC is written.
- Data access costs by region, width and sequentiality: IWRAM 1, VRAM 1 or 2, EWRAM 3 or 6, ROM
  WS0 from WAITCNT 0x4014 (4/2).
- The model has no prefetch buffer and no VRAM contention.
- BIOS CpuSet is executed in Python, so it is priced as the BIOS copy loop.
- Sample: 40 VBlanks spread over all 5 states (every 37th handler from VBlank 600), plus 16
  scene-start VBlanks (codebook inflate and codebook convert).

| VBlank kind | n | mGBA minus model (handler cycles) | per-bucket instruction counts |
|---|---|---|---|
| blit (3x3) | 8 | +4.6 % (blit bucket +4.8 %) | identical (82,916 per blit) |
| frame/codebook inflate | 16 | +1.4 % (-0.1 to +2.3 %); inflate bucket +2.2 %, CpuSet model +0.1 % | identical |
| audio | 8 | +1.8 % (ARM loop +2.1 %) | identical (142,860 in the ARM loop) |
| codebook convert | 8 | -0.4 % (YUV->RGB bucket 0.0 %) | identical |
| idle states | 16 | -25 % of about 1.3 k cycles (ROM Thumb code; the prefetch buffer is not modelled) | identical |

The handler totals differ by 7-14 instructions only because the exit event fires a few instructions
before the handler returns. In every bucket the instruction count matches exactly, so the activity
split is exact; the cycle numbers carry about +-2-5 %. mGBA charges the blit 4.8 % more than GBATEK
does. That is its PPU/CPU VRAM contention model: the blit reads the codebook from VRAM and writes
the frame buffer while the PPU scans out. Files: `xs/crossval.csv`, `xc/crossval.csv` (local run outputs).

## 2. CPU

### 2.1 The 5-VBlank schedule

`MoviePlayer::Process` (0x08004B28) is a state machine stepped once per VBlank from the VBlank IRQ
handler. During each step the handler masks its own IRQ (IE bit 0). Timer1 and keypad IRQs stay enabled
and can nest. The main loop sleeps in `VBlankIntrWait`, so idle time is real halt time, not a spin
(Shrek spins).

Measured cost of each VBlank, by what ran in it (Episode 103 / Episode 104, cycles of busy time):

| VBlank work | count | mean | p99 | max | % of a refresh (max) |
|---|---|---|---|---|---|
| inflate one frame of indices (4,240 B for 3x3, 4,800 B for 4x2) | 13,828 / 14,286 | 94.9 k / 103.6 k | 190.8 k / 212.8 k | 248.3 k / 263.4 k | 88.4 / 93.8 % |
| VQ blit | 13,828 / 14,286 | 198.1 k / 131.6 k | 203.8 k / 136.9 k | 219.1 k / 152.2 k | 78.0 / 54.2 % |
| audio: decode ~1,371 samples (one video frame of 16,384 Hz) | 13,826 / 14,284 | 208.0 k / 208.0 k | 218.7 k / 218.6 k | 219.7 k / 219.2 k | 78.2 / 78.0 % |
| scene start: inflate codebook + DMA copy (state 1) | 321 / 344 | 149.4 k / 137.8 k | | 153.3 k / 141.9 k | 54.6 / 50.5 % |
| scene start: unpredict + YUV->RGB555 codebook (state 2) | 321 / 344 | 100.0 k / 127.8 k | | 100.2 k / 128.0 k | 35.7 / 45.6 % |
| idle (key polling, state bookkeeping) | 27,016 / 27,886 | 1.7 k | 2.0 k | 2.4 k | 0.8 % |

On a scene change, the codebook load replaces one idle VBlank and the codebook convert takes the
next, so scene starts cost nothing extra in deadline terms. Both are well under one refresh.
Per-scene spikes are therefore invisible in the frame windows. The busiest frame windows are frames
with large compressed data plus a window slide.

### 2.2 Where the cycles go

Whole streams, share of busy cycles. Each bucket is also given as cycles per video frame.

| activity | intro | Episode 103 | Episode 104 | per frame, ep103 / ep104 |
|---|---|---|---|---|
| audio decode: ARM loop 0x03002A4C + Thumb block driver + Timer1 IRQ | 43.5 % | 40.4 % | 45.5 % | 206.2 k / 206.2 k |
| VQ blit 0x0300202C | 30.1 % | 38.5 % | 28.7 % | 196.2 k / 129.8 k (constant +-90) |
| inflate, IWRAM 0x03000094-0x03000B1C, 0x0300241C-0x030029BC | 19.9 % | 16.1 % | 19.4 % | 79.1 k / 85.2 k (mean) |
| inflate window slide (BIOS CpuSet, 4,097 words EWRAM->EWRAM, 78.0 k cycles each) | 2.2 % | 2.8 % | 3.7 % | 1 per 5.8 / 4.9 inflate calls |
| codebook unpredict + YUV->RGB555 | 2.2 % | 0.5 % | 0.8 % | |
| MoviePlayer / StreamBase / veneers | 0.6 % | 0.5 % | 0.6 % | |
| IRQ entry, dispatch, handlers | 0.9 % | 0.8 % | 0.9 % | |
| DMA cycle steal (DMA1 audio FIFO, DMA3 codebook copy) | 0.3 % | 0.2 % | 0.2 % | |
| UI, keys, libc, other | 0.2 % | 0.2 % | 0.3 % | |

Unit costs:
- Blit, 3x3 (mode 5): 196,210 cycles for 240x159 pixels = 5.14 cycles per pixel, 82,916
  instructions. The `ldm` codebook reads from VRAM and the `strh` stores to VRAM dominate.
- Blit, 4x2 (mode 7): 129,766 cycles for 240x160 = 3.40 cycles per pixel.
- Inflate: about **55-63 k cycles + 30 cycles per compressed input byte** per frame (least squares over
  all frames, R^2 = 0.95/0.96). Mean 845 / 791 compressed bytes per frame.
- Every 16 KiB of output, `UncompressBlock` slides the output window with a 16 KiB BIOS CpuSet,
  which costs 78.0 k cycles. This happens on 2,503 / 3,066 inflate calls and is the largest single
  item in the worst VBlanks.
- Audio: about 150 cycles per 16,384 Hz sample, decoded in one burst per video frame.

### 2.3 Worst frames and headroom

| stream | frame | handler cycles | compressed bytes | window slide |
|---|---|---|---|---|
| ep104 | 9114 | 263,156 (93.7 %) | 3,741 | yes |
| ep104 | 11395 | 255,922 | 3,007 | yes |
| ep104 | 9065 | 254,031 | 3,618 | yes |
| ep103 | 3386 | 247,992 (88.3 %) | 2,815 | yes |
| ep103 | 12376 | 238,765 | 2,958 | yes |

Without a window slide, the worst inflate VBlank is 174.6 k / 195.2 k cycles. The margin to the
deadline is therefore set by the slide coinciding with a large frame. With the measured cost model
(sec. 2.2), a frame with a slide would overrun at about 4,600 (ep104) to 4,900 (ep103) compressed
bytes. The largest slide frames in these streams are 3,741 / 2,958 bytes.
- The largest index payloads overall are 4,664 bytes (ep104) and 3,676 bytes (ep103).
- All 4 frames over 4,000 bytes are the first frame of a scene. The output window restarts at
  each scene, so no slide happens on a first frame (0 of 665).

An overrun would not drop a frame. It would delay the next VBlank step by one refresh, a 16.7 ms
slip of the video, and audio would continue unaffected (DMA-fed). It never happens on this cart.

### 2.4 Comparison with shipped Hydrogen (Sonic X Vol 1)

Vol 1 was measured with the same core and method. `vol1perf.py` maps the IWRAM bucket ranges from the
proto via an instruction alignment (`addrmap.py`): blit 0x03001FF4, inflate 0x030024A0, audio
0x03002AD0. Sample: 3,600 VBlanks of episode playback from the triage savestate `nav/Sonic_d`.

| | Vol 1 (this method) | Vol 1 (triage PC histogram) | proto ep103 | proto ep104 |
|---|---|---|---|---|
| CPU busy | 33.9 % | 32.8-36.8 % | 36.3 % | 32.3 % |
| audio | 44.4 % = 211.7 k cycles per frame | 45 % | 40.4 % = 206.2 k | 45.5 % = 206.2 k |
| blit | 31.8 % = 151.5 k per frame (modes 3 and 5 mixed) | 34 % | 38.5 % = 196.2 k (all 3x3) | 28.7 % = 129.8 k (all 4x2) |
| inflate incl. CpuSet slide | 20.6 % = 98.2 k per frame | 16 % (CpuSet not counted) | 18.9 % = 93.3 k | 23.2 % = 102.1 k |
| codebook | 1.2 % | | 0.5 % | 0.8 % |

The differences come from content and one code change, not from the player architecture:
- **Blit.** Episode 103 is entirely 3x3 and Episode 104 entirely 4x2. Vol 1's episode mixes 4x3
  and 3x3. The proto's 3x3 blit body is coded differently from Vol 1's (the diff fork documents
  how), so the per-mode cost may differ.
- **Audio.** The proto's ARM loop stores `s>>6` without Vol 1's 6-instruction +-127 clamp. That
  saves about 5.5 k cycles per frame (2.7 %), the expected 4 cycles per sample.
- **Inflate.** The two players run the same code. The per-frame cost follows the compressed size:
  Vol 1's episode averages more compressed bytes per frame than ep103 (triage: about 1,070 against 846).
- The triage figure undercounts inflate because it leaves out the BIOS CpuSet window slide (1.8 %
  BIOS time in the triage histogram).

## 3. Memory buses (Episode 103, whole stream)

- WAITCNT 0x4014: WS0 3/1 waitstates, prefetch on. EWRAM keeps 2 waitstates (0x4000800 is not changed).
- Code fetch: 14.44 GB from IWRAM, 65 MB from ROM (the Thumb engine), 182 MB from the BIOS
  (CpuSet loop, IRQ path).

| region | CPU reads | CPU writes | waitstate cycles (% of time) | what |
|---|---|---|---|---|
| VRAM | 1.20 GB | 1.06 GB | 2.17 % | blit: codebook `ldm` from 0x06012C00 (89.9 KB per frame) and frame-buffer stores (76.3 KB per frame) |
| EWRAM | 154 MB | 100 MB | 2.29 % | inflate output window and LZ back-references, codebook backup |
| ROM | 22.5 MB | 0 | 0.16 % | compressed video (11.7 MB) and audio (4.3 MB), read with `ldrh` |
| IWRAM | 683 MB | 552 MB | 0 | YUV codebook, PCM ring, contexts, stacks |
| DMA1 | IWRAM -> FIFO A | 19.04 MB = 16,384 B/s | 0.07 % blocked | audio |
| DMA3 | IWRAM/VRAM | 1.39 MB | | per-scene codebook backup copy (3,328 B per scene to EWRAM), OSD |

The cart bus carries 19.4 KB/s of data (video 10.1 KB/s, audio 3.7 KB/s, plus re-reads), about 0.1 %
of WS0 bandwidth. The 64 MiB Matrix mapper is not used: the ROM is linear and under 32 MiB.

## 4. Memory occupancy

These are the bytes that changed at least once during a whole episode, sampled every 7 VBlanks, with
the owners identified from the player's pointers.

| region | used | by |
|---|---|---|
| IWRAM (32 KiB) | code+data image 12,152 B (0x03000000-0x03002F78), plus about 13.8 KB of dynamic state | YUV codebook buffer 0x0300320C (3,328 B for 3x3, 3,072 B for 4x2; max 0x1800); audio ctx 0x03004A14; audio output buffers 0x030052E4-0x03006868 (DMA1 reads the 4 KiB PCM ring, e.g. at 0x03005774); MoviePlayer 0x03006868 (about 0x1640 B); inflate object 0x0300789C; stacks 0x03007B46-0x03007FFF |
| EWRAM (256 KiB) | about 70 KB touched (27 %) | inflate output window 0x02000018-0x02010A19 (about 66.5 KB: index raster plus LZ history, slid by 16 KiB CpuSet); codebook backup 0x020126A8 (re-converted when brightness changes); BIOS mailbox |
| VRAM (96 KiB) | 0x14BF2 (3x3) / 0x13C00 (4x2) + 0x71C | mode-3 frame buffer 0x12C00; RGB555 codebook 0x06012C00 (8 KiB at 32 B/entry for 3x3, 4 KiB at 16 B/entry for 4x2); OSD sprite tiles from 0x06017140 |
| palette / OAM | small | OSD ("Play", fast-forward/rewind, brightness) |

## 5. Display

- DISPCNT 0x1403: mode 3, BG2 and OBJ on. Single buffered, with no page flip.
- The blit writes the visible frame buffer directly. 45 % of its VRAM bytes land during active
  display, 11 % in HBlank and 44 % in VBlank, but no frame tears: it starts at line 160 and stays
  ahead of the beam (sec. TL;DR). Shrek tears on about a third of refreshes; this player does not.
- The 3x3 geometry covers 159 rows. The blit never writes row 159 in Episode 103.
- The intro and Episode 104 (4x2) cover all 160 rows.

## 6. Audio, timers and IRQs

| item | setting |
|---|---|
| Sound | DirectSound A only, 100 % volume, L+R (SOUNDCNT_H 0x0304), master on, PSG off; SOUNDBIAS 0x0200 |
| Timer0 | reload 0xFC00, /1: **16,384 Hz** sample clock, no IRQ |
| DMA1 | 0xB640 (FIFO, 32-bit, repeat) from the IWRAM PCM ring to FIFO A: 1,024 bursts/s of 16 B |
| Timer1 | cascaded on Timer0, IRQ every 4,096 samples (4 Hz): re-arms DMA1 at the ring start |
| IRQs | IE = VBlank, Timer1, Keypad. 63.7 IRQ/s (59.7 VBlank + 4 Timer1) |

The audio is decoded in one VBlank per video frame: about 1,371 samples, 208 k cycles, inside the
VBlank handler. The A/V clock is the VBlank counter; the audio hardware is free-running at 16,384 Hz.

## 7. Files (`work/perf/`)

These were working files of the analysis and are not distributed in this repository; the table
records what produced the numbers above. The ROM lookup-table addition to the perf core is small
(a PC LUT for 0x08000000-0x0801FFFF on top of `tools/vx/perf/patch/mgba-perf.patch`).

| path | content |
|---|---|
| `mgba-src/` | instrumented core (Shrek perf patch plus the ROM LUT in `perf.h`/`perf.c`). Build: `nix shell nixpkgs#gcc nixpkgs#gnumake nixpkgs#zlib -c make -f Makefile.libretro platform=unix ARCH=x86_64 -j16` |
| `classify.py` -> `buckets.json` | PC -> activity map |
| `sx2perf.py` | measurement: `--stream intro\|ep103\|ep104 --out DIR` -> `DIR/per_vbl.csv`, `DIR/totals.json` |
| `analyze.py` | `DIR/summary.json` (shares, per-state table, frame-window distribution, unit costs) |
| `streams.py`, `fbytes.py`, `bitrate.py` -> `bitrate.json` | exact stream/scene sizes from the container; per-frame compressed bytes from the inflate input pointer (plain core); working-set masks `fb10x/touched.json` |
| `sample.py`, `uc_cost.py`, `uc_gba_sx2.py`, `xs/`, `xc/` | cross-check: savestates, unicorn GBATEK model, `crossval.csv` |
| `vol1perf.py`, `addrmap.py`, `vol1_totals.json` | the same measurement on shipped Sonic X Vol 1 |
| `make_stats.py` -> `perf_stats.json` | per-stream figures for stats.json |
