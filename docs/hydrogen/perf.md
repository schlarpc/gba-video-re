# Hydrogen GBA Video: hardware resource use during playback

Measured over **every frame of all 62 streams** of the 13 carts (402,649 frames, 9.4 hours of
emulated playback), from the first blit of a stream to its last. Code and data:
`tools/hydrogen/perf/` (driver `hyperf.py`, batch `run_all.py`, tables `summarize.py`, cost model `uc_cost.py`).
The raw per-frame and per-VBlank CSVs are local run outputs in `tmp/hydrogen/perf/<CODE>_<stream>/` (not distributed).

Units: 1 cycle = 1/16,777,216 s. One VBlank period (LCD refresh) = 280,896 cycles. One video frame
= 5 VBlanks = 1,404,480 cycles (11.9455 fps).

## TL;DR

| resource | use |
|---|---|
| CPU busy (not halted) | **31.7 % mean** over all streams (per stream 29.4..37.1 %). Per 5-VBlank frame window: p99 41..67 %, max 54..70 % |
| CPU split (share of all CPU time) | ADPCM audio **15.0 %**, VQ blit **9.8 %**, Inflate **5.0 %**, codebook unpredict + convert 0.3 %, BIOS (IRQ entry, VBlankIntrWait) 0.8 %, ROM Thumb glue 0.6 %, IRQ dispatcher 0.03 %, **halted 68.3 %** |
| deadline | the player does one state per VBlank; the heaviest VBlank of any stream uses **93.1 %** of its period (an Inflate request), typical maxima are ADPCM 80.7 %, codebook convert 87.9 % (4x4), blit 65.8 %. **No VBlank overran** in 2,013,485 steady-state VBlanks, and every one of the 402,649 blits came exactly 5 VBlanks after the previous one (captures): no late or dropped frames |
| headroom | ~7 % on the worst Inflate VBlank, ~19 % on the ADPCM VBlank; as a whole the CPU is idle two thirds of the time, but the one-state-per-VBlank schedule cannot use idle VBlanks for a heavy one |
| blit | 120 k (4x4), 130 k (4x3, 4x2), 162 k (3x3) cycles, started at VCOUNT 160; it stays ahead of the beam, so the single-buffered mode-3 picture never tears (32 % of its VRAM writes land during active display, all on lines the beam has not reached) |
| memory stalls | data waitstates are 4.8 % of all cycles (15 % of busy time) |
| cart bus | WAITCNT 0x4014 (WS0 3/1, prefetch); no code runs from ROM in the hot paths. CPU data reads from the cart: 12.3 KB/s (compressed video + audio), Thumb code fetches 53.9 KB/s |
| DMA | DMA1 only during playback: PCM ring -> FIFO A, 16,384 B/s, 0.07 % of cycles. DMA3 copies the codebook (0x1800 B) once per segment |
| interrupts | 63.7 IRQ/s: VBlank 59.7/s (all decoding runs in it) + Timer1 4/s (DMA1 restart every 4096 samples) |
| audio | DirectSound A, 16384 Hz, 8-bit; 1371.5625 samples decoded per frame into a 4096-byte ring |


## 1. Method

**(a) Instrumented mGBA (primary, whole streams).** The libretro mGBA core rebuilt with the Shrek
perf patch (`tools/vx/perf/patch/mgba-perf.patch`, unchanged; built into `tmp/mgba/perf/`). The
patch charges every emulated cycle (fetch + execute + data waitstates, as mGBA charges them) to a
(context, bucket) pair from a PC lookup table, counts data traffic per memory region, DMA, halted
and DMA-blocked cycles, CPU VRAM writes by raster state, and snapshots all counters at event PCs.
For Hydrogen the table is set per build by `hyperf.py`:

| bucket | IWRAM routines (All Grown Up addresses; translated per build) |
|---|---|
| inflate | 0x03000000..0x03000b1c, 0x030024a0..0x03002a40, 0x03002d44.. (Inflate) |
| vq_convert / vq_unpredict | 0x03001430..0x03001ba8 / 0x03001ba8..0x03001de0 |
| vq_blit | 0x03001ff4..0x030024a0 |
| adpcm | 0x03002ad0..0x03002d44 |
| irq_dispatch | 0x03001e7c..0x03001ff4 |
| osd_font | 0x03000c68..0x03001430, 0x03001de0..0x03001e7c |
| rom_code | all Thumb code in ROM (StreamBase/SoundPlayer glue, state machine, main thread) |
| bios | BIOS (mGBA's HLE BIOS: IRQ entry, VBlankIntrWait) |

Events: the blit entry (frame boundary: `per_frame.csv` has one row per displayed frame, the window
from its blit to the next = 5 VBlanks), and the IRQ dispatcher entry at VCOUNT 160 (VBlank:
`per_vblank.csv` has one row per VBlank). Every stream is started exactly as in the verification
captures (state injection, then `PlayMovie()`), from a savestate made with `tools/hydrogen/emu/mkstate.py`, so the
cart plays it from frame 0 with its own scheduler. Busy = elapsed - halted cycles (the player
halts in `VBlankIntrWait` between interrupts).

**(b) Independent cost model in unicorn (cross-check).** `uc_cost.py` runs the cartridge's IWRAM
routines in the unicorn oracle on the same data (the index stream read straight from the ROM, the
window in EWRAM, the codebook in VRAM) and prices each executed ARM instruction from GBATEK's
ARM7TDMI table and each data access by region, width and sequentiality (WAITCNT 0x4014). It uses
no mGBA timing. The instruction-pricing class is the Shrek tooling's, unchanged.

The cost model was run on 325 frames in three geometries and on ADPCM calls, against the mGBA
numbers for the same frames (local outputs `tmp/hydrogen/uc_cost_*.csv`):

| routine | sample | unicorn model / instrumented mGBA |
|---|---|---|
| VQ blit, 3x3 (All Grown Up ep 1, segments 1-3) | 216 frames | 0.989 |
| VQ blit, 4x4 (Yu-Gi-Oh! ep 1, segments 25-26) | 35 frames | 0.982 |
| VQ blit, 4x2 (Dora ep 1, segments 0 and 5) | 74 frames | 0.989 |
| Inflate, one frame's request | the same 325 frames | 0.985..0.994 mean (0.977..0.999 per frame) |
| ADPCM, 1371 samples | 6 calls | 208.5..211.2 k vs mGBA median 211.4 k per frame (0.99..1.00) |

The model is 1-2 % below mGBA throughout, consistent with mGBA charging VRAM access contention and
prefetch effects the GBATEK table does not model.

## 2. CPU

Over all 62 streams (402,649 frames, 9.36 hours of emulated playback):

| bucket | share of all CPU cycles |
|---|---|
| ADPCM (IWRAM 0x03002ad0) | 15.04 % |
| VQ blit | 9.84 % |
| Inflate (index stream + codebooks) | 5.02 % |
| codebook unpredict + convert | 0.31 % |
| BIOS (HLE: IRQ entry, VBlankIntrWait) | 0.84 % |
| ROM Thumb (StreamBase/SoundPlayer glue, state machine) | 0.55 % |
| IRQ dispatcher | 0.03 % |
| DMA cycle steal | 0.07 % |
| **halted** | **68.3 %** |

Audio decoding costs more than video: about 154 cycles per sample, 211 k cycles per frame, a
constant. Video cost depends on the block geometry (blit) and on how well the index stream
compresses (Inflate); the 4x4 segments are cheapest to blit but most expensive to convert.

### Per stream

| stream | build | frames | CPU busy mean | p99 | max | inflate | convert | blit | ADPCM | other | heaviest VBlank | ROM read B/s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| agu1_0 | `04cbcc07fe42` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| agu1_1 | `04cbcc07fe42` | 15851 | 32.7 % | 55.8 % | 63.3 % | 5.1 % | 0.2 % | 10.6 % | 15.0 % | 1.6 % | 82.3 % | 17499 |
| agu1_2 | `04cbcc07fe42` | 15865 | 32.6 % | 59.2 % | 63.6 % | 5.1 % | 0.3 % | 10.6 % | 15.0 % | 1.6 % | 84.0 % | 5952 |
| agu1_3 | `04cbcc07fe42` | 542 | 36.9 % | 56.6 % | 57.6 % | 8.1 % | 1.2 % | 11.5 % | 15.0 % | 1.2 % | 80.3 % | 5761 |
| sonicx1_0 | `04cbcc07fe42` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| sonicx1_1 | `04cbcc07fe42` | 14217 | 33.1 % | 56.5 % | 63.4 % | 5.6 % | 0.3 % | 10.7 % | 15.1 % | 1.6 % | 89.8 % | 19285 |
| sonicx1_2 | `04cbcc07fe42` | 14248 | 33.0 % | 55.4 % | 58.6 % | 6.0 % | 0.2 % | 9.8 % | 15.1 % | 1.9 % | 90.0 % | 5689 |
| sonicx1_3 | `04cbcc07fe42` | 723 | 37.1 % | 56.6 % | 57.4 % | 8.6 % | 1.0 % | 11.5 % | 15.0 % | 1.0 % | 85.6 % | 5551 |
| yugioh_0 | `f73d177a8155` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| yugioh_1 | `f73d177a8155` | 13638 | 30.2 % | 65.0 % | 67.7 % | 4.6 % | 0.4 % | 8.9 % | 15.0 % | 1.3 % | 89.4 % | 20074 |
| yugioh_2 | `f73d177a8155` | 13551 | 30.4 % | 61.3 % | 67.9 % | 4.7 % | 0.4 % | 9.0 % | 15.0 % | 1.2 % | 90.8 % | 7228 |
| yugioh_3 | `f73d177a8155` | 716 | 33.2 % | 61.1 % | 61.7 % | 6.9 % | 1.0 % | 9.2 % | 15.0 % | 1.0 % | 80.3 % | 8860 |
| sponge1_0 | `92e428ed134d` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24011 |
| sponge1_1 | `92e428ed134d` | 7963 | 29.8 % | 61.7 % | 67.6 % | 4.2 % | 0.3 % | 8.9 % | 15.0 % | 1.3 % | 90.1 % | 17720 |
| sponge1_2 | `92e428ed134d` | 7951 | 29.9 % | 65.8 % | 68.4 % | 4.3 % | 0.3 % | 8.9 % | 15.0 % | 1.3 % | 89.9 % | 18843 |
| sponge1_3 | `92e428ed134d` | 7950 | 30.2 % | 61.4 % | 68.1 % | 4.4 % | 0.3 % | 9.0 % | 15.0 % | 1.4 % | 89.9 % | 6559 |
| sponge1_4 | `92e428ed134d` | 7951 | 29.9 % | 61.7 % | 67.8 % | 4.3 % | 0.3 % | 9.0 % | 15.0 % | 1.3 % | 90.0 % | 6060 |
| sponge1_5 | `92e428ed134d` | 517 | 32.8 % | 55.0 % | 63.2 % | 5.8 % | 0.2 % | 10.1 % | 15.0 % | 1.7 % | 80.4 % | 6108 |
| sponge2_0 | `92e428ed134d` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24011 |
| sponge2_1 | `92e428ed134d` | 7933 | 30.0 % | 61.7 % | 68.2 % | 4.4 % | 0.4 % | 8.9 % | 15.0 % | 1.3 % | 90.2 % | 18779 |
| sponge2_2 | `92e428ed134d` | 7941 | 30.1 % | 61.5 % | 67.7 % | 4.4 % | 0.3 % | 9.0 % | 15.0 % | 1.4 % | 89.9 % | 18185 |
| sponge2_3 | `92e428ed134d` | 7951 | 29.4 % | 66.4 % | 68.7 % | 4.0 % | 0.3 % | 8.8 % | 15.1 % | 1.3 % | 89.5 % | 6462 |
| sponge2_4 | `92e428ed134d` | 7944 | 29.6 % | 66.8 % | 68.0 % | 4.1 % | 0.6 % | 8.6 % | 15.0 % | 1.1 % | 89.5 % | 7035 |
| sponge2_5 | `92e428ed134d` | 517 | 32.8 % | 55.0 % | 63.2 % | 5.8 % | 0.2 % | 10.1 % | 15.0 % | 1.7 % | 80.4 % | 6108 |
| fop1_0 | `1c25494320ba` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24011 |
| fop1_1 | `1c25494320ba` | 8100 | 30.4 % | 60.8 % | 62.1 % | 4.4 % | 0.5 % | 9.2 % | 15.0 % | 1.3 % | 80.5 % | 17490 |
| fop1_2 | `1c25494320ba` | 8091 | 30.4 % | 60.4 % | 62.3 % | 4.4 % | 0.5 % | 9.2 % | 15.0 % | 1.2 % | 80.7 % | 17607 |
| fop1_3 | `1c25494320ba` | 16160 | 34.1 % | 54.6 % | 57.5 % | 5.7 % | 0.2 % | 11.5 % | 15.0 % | 1.7 % | 86.1 % | 6260 |
| fop1_4 | `1c25494320ba` | 554 | 34.0 % | 54.3 % | 55.7 % | 6.2 % | 0.3 % | 11.0 % | 14.8 % | 1.7 % | 80.4 % | 5728 |
| fop2_0 | `92e428ed134d` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24011 |
| fop2_1 | `92e428ed134d` | 7928 | 31.5 % | 60.1 % | 63.3 % | 4.9 % | 0.3 % | 9.8 % | 15.0 % | 1.4 % | 80.5 % | 18687 |
| fop2_2 | `92e428ed134d` | 7934 | 31.7 % | 60.1 % | 63.0 % | 5.0 % | 0.3 % | 9.9 % | 15.0 % | 1.5 % | 80.6 % | 18240 |
| fop2_3 | `92e428ed134d` | 8119 | 33.8 % | 54.8 % | 57.3 % | 5.4 % | 0.2 % | 11.5 % | 15.1 % | 1.6 % | 80.6 % | 4946 |
| fop2_4 | `92e428ed134d` | 8013 | 33.9 % | 54.6 % | 56.7 % | 5.4 % | 0.2 % | 11.5 % | 15.1 % | 1.7 % | 81.6 % | 4945 |
| fop2_5 | `92e428ed134d` | 554 | 34.0 % | 54.3 % | 55.7 % | 6.2 % | 0.3 % | 11.0 % | 14.8 % | 1.7 % | 80.4 % | 5728 |
| tmnt_0 | `93fe5e2e96b3` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| tmnt_1 | `93fe5e2e96b3` | 14206 | 31.2 % | 60.6 % | 63.8 % | 4.9 % | 0.4 % | 9.4 % | 15.1 % | 1.3 % | 80.6 % | 19729 |
| tmnt_2 | `93fe5e2e96b3` | 14207 | 30.7 % | 60.9 % | 67.7 % | 4.8 % | 0.4 % | 9.2 % | 15.1 % | 1.3 % | 91.2 % | 6775 |
| tmnt_3 | `93fe5e2e96b3` | 733 | 32.2 % | 60.4 % | 63.2 % | 5.8 % | 0.5 % | 9.6 % | 15.0 % | 1.3 % | 87.2 % | 6966 |
| nick1_0 | `c1313a3736b4` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| nick1_1 | `c1313a3736b4` | 16373 | 29.7 % | 66.2 % | 68.2 % | 4.1 % | 0.5 % | 8.8 % | 15.0 % | 1.2 % | 90.5 % | 18065 |
| nick1_2 | `c1313a3736b4` | 8478 | 30.4 % | 60.0 % | 62.0 % | 4.4 % | 0.4 % | 9.2 % | 15.0 % | 1.3 % | 80.5 % | 7820 |
| nick1_3 | `c1313a3736b4` | 8396 | 31.0 % | 60.9 % | 62.5 % | 5.0 % | 0.3 % | 9.2 % | 15.0 % | 1.4 % | 80.6 % | 5922 |
| strawb1_0 | `c3b335b0b286` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| strawb1_1 | `c3b335b0b286` | 14158 | 30.0 % | 66.4 % | 67.9 % | 4.4 % | 0.4 % | 8.9 % | 15.0 % | 1.3 % | 90.6 % | 19857 |
| strawb1_2 | `c3b335b0b286` | 14131 | 30.9 % | 60.6 % | 69.5 % | 4.8 % | 0.3 % | 9.3 % | 15.0 % | 1.4 % | 90.2 % | 6421 |
| strawb1_3 | `c3b335b0b286` | 729 | 34.4 % | 60.5 % | 62.9 % | 7.5 % | 0.6 % | 9.9 % | 15.0 % | 1.3 % | 86.1 % | 7053 |
| knd1_0 | `dff48b69f680` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| knd1_1 | `dff48b69f680` | 7643 | 30.6 % | 60.9 % | 63.6 % | 4.5 % | 0.4 % | 9.3 % | 15.1 % | 1.4 % | 80.7 % | 17394 |
| knd1_2 | `dff48b69f680` | 7729 | 34.3 % | 55.4 % | 58.0 % | 5.8 % | 0.2 % | 11.5 % | 15.1 % | 1.8 % | 86.9 % | 18563 |
| knd1_3 | `dff48b69f680` | 7641 | 34.0 % | 56.0 % | 57.6 % | 5.5 % | 0.2 % | 11.5 % | 15.1 % | 1.8 % | 83.8 % | 6084 |
| knd1_4 | `dff48b69f680` | 7789 | 31.3 % | 60.9 % | 64.1 % | 5.1 % | 0.4 % | 9.4 % | 15.1 % | 1.3 % | 80.5 % | 6464 |
| knd1_5 | `dff48b69f680` | 359 | 32.7 % | 53.5 % | 55.1 % | 6.2 % | 0.8 % | 9.3 % | 14.9 % | 1.4 % | 80.3 % | 8882 |
| cn1_0 | `f5f0564f89e3` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24012 |
| cn1_1 | `f5f0564f89e3` | 5763 | 34.1 % | 56.3 % | 58.4 % | 5.7 % | 0.2 % | 11.5 % | 15.0 % | 1.7 % | 86.0 % | 17581 |
| cn1_2 | `f5f0564f89e3` | 5270 | 34.9 % | 55.7 % | 57.4 % | 6.5 % | 0.2 % | 11.5 % | 15.0 % | 1.7 % | 86.1 % | 21478 |
| cn1_3 | `f5f0564f89e3` | 8175 | 32.6 % | 60.3 % | 63.8 % | 6.0 % | 0.2 % | 9.7 % | 15.0 % | 1.6 % | 92.6 % | 10563 |
| cn1_4 | `f5f0564f89e3` | 8152 | 33.8 % | 56.5 % | 58.3 % | 5.4 % | 0.2 % | 11.5 % | 15.0 % | 1.8 % | 84.3 % | 4940 |
| dora1_0 | `ff6e0e4279e0` | 59 | 32.2 % | 53.5 % | 53.7 % | 6.0 % | 0.5 % | 9.2 % | 14.5 % | 2.0 % | 80.1 % | 24011 |
| dora1_1 | `ff6e0e4279e0` | 15964 | 32.4 % | 41.1 % | 58.0 % | 6.0 % | 0.1 % | 9.2 % | 15.0 % | 2.2 % | 93.1 % | 17105 |
| dora1_2 | `ff6e0e4279e0` | 16062 | 34.0 % | 42.0 % | 58.1 % | 5.6 % | 0.1 % | 11.4 % | 15.0 % | 1.9 % | 80.6 % | 5486 |
| dora1_3 | `ff6e0e4279e0` | 502 | 34.5 % | 42.7 % | 58.4 % | 6.1 % | 0.0 % | 11.3 % | 15.0 % | 2.1 % | 80.4 % | 5797 |



## 3. Deadlines: one state per VBlank

The player does one piece of work per VBlank (player.md section 5), so the deadline that
matters is per VBlank, not per frame: a state that ran past the next VBlank would make that VBlank's
interrupt a no-op and push the whole schedule back by one VBlank.

Each frame's five VBlanks do: (1) next segment's codebook inflate when a segment ends, (2) its
unpredict + convert, (3) the frame's index request, (4) the blit, (5) the ADPCM samples and input.
Steady-state VBlanks of all 62 streams, classified by the routine that dominated them:

### VBlank classes (all streams)

| VBlank doing | count | busy p50 | p99 | max (of 280,896 cycles) |
|---|---|---|---|---|
| adpcm | 402420 | 213744 | 225329 | 226795 (80.7 %) |
| vq_convert | 9251 | 187214 | 246827 | 246870 (87.9 %) |
| vq_blit | 402639 | 131457 | 163565 | 184969 (65.8 %) |
| inflate | 411874 | 68732 | 196178 | 261465 (93.1 %) |
| idle | 787301 | 1669 | 1909 | 62784 (22.4 %) |

The tightest work is the Inflate request of a frame that compresses badly (scene cuts, noise):
up to 261 k cycles (93.1 %, Dora ep 1). Codebook conversion of a 4x4 segment takes 247 k (87.9 %).
ADPCM is constant at 211-227 k (75-81 %). No VBlank exceeded its period, so the schedule never
slipped (confirmed independently by the captures: every blit exactly 5 VBlanks after the previous).


## 4. Memory, bus, DMA, display, audio

Averages over all streams:

| traffic | bytes/s |
|---|---|
| CPU reads from ROM (compressed data) | 12,282 |
| Thumb code fetched from ROM | 53,931 |
| EWRAM reads / writes (index window, heap) | 108,399 / 69,874 |
| IWRAM reads / writes | 590,534 / 479,511 |
| VRAM reads / writes (codebook reads, framebuffer writes) | 965,324 / 920,295 |
| DMA1 (PCM -> FIFO A) | 16,384 |

- **Cart bus**: about 66 KB/s in total, well under 1 % of what WS0 can deliver; the compressed
  video averages 7.0..23.1 KB/s (56..185 kbit/s) per stream (`stats.json` video_kbps_mean).
- **Display**: mode 3, BG2 + OBJ (the OSD), single-buffered. The blit starts at VCOUNT 160 and
  writes 76.8 KB; 32 % of those writes fall in active display but always on lines below the beam
  (blit rate about 1000 cycles per line vs the beam's 1232), so no tearing.
- **Audio**: DirectSound A only, PSG off; Timer0 sample clock, Timer1 IRQ every 4096 samples.
- **Memory occupancy during playback** (player.md section 4): IWRAM image 12 KB, bss 7 KB
  (incl. the 6 KB YUV codebook), PCM ring 4 KB, `StreamBase` 5.6 KB on the stack; EWRAM heap with
  the ~75 KB index window and 24 KB of codebook copies; VRAM framebuffer 75 KB + codebook 8 KB.


## 5. Reproducing

```sh
python3 tools/hydrogen/perf/hyperf.py MGUE 1 tmp/hydrogen/perf/MGUE_1   # one stream (makes its own start state)
JOBS=4 python3 tools/hydrogen/perf/run_all.py                           # all streams
python3 tools/hydrogen/mkstats.py && python3 tools/hydrogen/perf/summarize.py
python3 tools/hydrogen/perf/uc_cost.py MGUE 1 --segments 1,2,3 --state tmp/hydrogen/states/MGUE_1_b40 --out uc.csv
```
