# ADS playback: CPU and resource use

Status: v1, 2026-11-07. Measured on **every stream of every cart** (96 streams, 621,366 frame
windows, whole streams from the first to the last blit), with the instrumented mGBA core.

## TL;DR

| item | result |
|---|---|
| CPU busy (all attributed instruction cycles / elapsed) | mean **49.9 %** over all streams; p99 97.2 %, max 98.7 % of a frame window |
| split (share of all CPU time, episodes) | LZMA **19-27 %**, ADPCM **14.7-15.4 %**, VQ blit **10-12 %**, codebook unpredict+convert 0.2-0.4 %, Thumb player code 0.4-0.7 %, IRQ/other < 0.5 % |
| blit | 165,600-165,900 cycles per frame for 4x3/3x3 (0.118 of a 5-refresh window), 130,800 for 4x4 (Nicktoons 2 / SpongeBob 3) |
| LZMA per frame | mean 0.26-0.38 M cycles, p99 0.80-0.98 M (one frame window = 1,404,480 cycles) |
| deadline | the LZMA core stops on an IRQ flag; B-layout builds cap the window at ~97 %, A-layout builds (CN USA, Disney 1, DBGT, Nicktoons 2 / SpongeBob 3, Jimmy, Noah) at ~84-86 % |
| late frames | **16 of 621,366** windows: 14 frames shown one slot (5 VBlanks, 84 ms) late, 1 shown 1 VBlank late with the next 1 early (DBGT ep. 1); all in A-layout builds; none in B-layout builds |
| dropped frames | none: every frame of every stream is blitted exactly once |
| cross-check | 119 LZMA calls replayed in unicorn with a GBATEK cost model: instruction counts equal (to within the call-boundary instructions), cycles differ by **+0.04 % mean (max +0.08 %)** |

## 1. Method

**(a) Instrumented mGBA, whole streams.** `tools/ads/emu/mgba-hooks.patch` (MPL-2.0; on the nixpkgs mGBA
libretro core) adds, after every executed instruction, the cycles mGBA charged for it (fetch,
execute, data waitstates, VRAM stall model) to one of 16 buckets picked by a PC lookup table
(2-byte granularity for IWRAM/EWRAM, 16 for ROM). `tools/ads/emu/perf.py` fills the table from the
build's codec routines (`tmp/ads/builddiff.json`, a local run output of `tools/ads/builddiff.py`: LZMA init/decode/core, codebook unpredict and
convert, VQ blit, ADPCM sample loop; other IWRAM; Thumb player code in EWRAM/ROM; BIOS) and plays
the stream from the menu exactly like the captures, snapshotting the counters at every blit
entry. A **window** is the time between two blits: normally 5 refreshes = 5 x 280,896 = 1,404,480
cycles. Busy = attributed cycles / window cycles (halted time is not attributed). DMA cycles are
not attributed (DMA1 moves 16384 B/s to FIFO A; DMA3 copies one 6 KiB codebook per segment).
`tools/ads/emu/perf_all.py` ran all 96 streams; `tools/ads/perf_summary.py` makes the tables.

**(b) Cross-check.** `tools/ads/emu/crossval.py` records, in mGBA, cycles and instructions charged to
the LZMA bucket for every index-plane decode call of SRMTHFG episode 1 (first 120 frames, 8
segments, including the deadline-stopped calls) and replays the same calls on the same blobs in
unicorn, priced by `tools/ads/emu/uc_cost.py` (GBATEK ARM7TDMI timings, WAITCNT 0x4014, EWRAM 3/6,
ROM WS0 4/2 and 6/4; copied from the VX cost model, `tools/vx/perf/uc_cost.py`, see [../vx/perf.md](../vx/perf.md)). Result: instruction counts equal
except at the call boundary (+1 for the first call of a segment, -3/-4 when the call returns
through the deadline exit), cycles +0.04 % mean (min +0.003 %, max +0.08 %); LZMA runs at 1.68
cycles per instruction (code and data in IWRAM, input bytes from ROM). `tmp/ads/perf/crossval.csv` (local run output).

**Error bars.** About +-1 % on absolute cycles (the two models agree to 0.1 %; mGBA's ARM7 timing
is close to hardware). Shares within a window are exact up to the bucket boundaries (a few
instructions at function edges). Busy excludes DMA (< 0.4 %).

## 2. Per build (episodes only)

| build | episodes | frames | busy mean | p99 | max | LZMA | codebook | blit | ADPCM | Thumb | LZMA cycles/frame mean | p99 | late windows |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `17d6ab8a82ba` | 3 | 32321 | 0.494 | 0.972 | 0.983 | 0.211 | 0.0028 | 0.117 | 0.154 | 0.0069 | 295968 | 970841 | 0 |
| `22db83f74bdf` | 4 | 31667 | 0.472 | 0.842 | 0.851 | 0.206 | 0.0021 | 0.107 | 0.148 | 0.0048 | 289365 | 797959 | 0 |
| `69261e8948ae` | 8 | 66537 | 0.458 | 0.843 | 0.863 | 0.197 | 0.0035 | 0.100 | 0.148 | 0.0063 | 276631 | 797686 | 2 |
| `73b2d7da03af` | 2 | 28582 | 0.513 | 0.972 | 0.975 | 0.229 | 0.0024 | 0.118 | 0.154 | 0.0063 | 322026 | 970907 | 0 |
| `882a917e99c5` | 2 | 31861 | 0.492 | 0.973 | 0.975 | 0.216 | 0.0024 | 0.117 | 0.147 | 0.0061 | 303783 | 981470 | 0 |
| `8831b3ba8f06` | 2 | 27195 | 0.517 | 0.842 | 0.853 | 0.241 | 0.0017 | 0.118 | 0.147 | 0.0038 | 339127 | 799138 | 2 |
| `bde72f6aba53` | 4 | 59030 | 0.466 | 0.972 | 0.983 | 0.188 | 0.0028 | 0.112 | 0.154 | 0.0066 | 263635 | 970690 | 0 |
| `bfa01253c3c5` | 8 | 84806 | 0.502 | 0.972 | 0.987 | 0.222 | 0.0026 | 0.114 | 0.154 | 0.0063 | 311204 | 971646 | 0 |
| `e0080e16dac3` | 20 | 137866 | 0.515 | 0.844 | 0.863 | 0.236 | 0.0020 | 0.118 | 0.148 | 0.0072 | 331680 | 797240 | 8 |
| `e19d3efb314e` | 2 | 29501 | 0.496 | 0.843 | 0.853 | 0.235 | 0.0032 | 0.099 | 0.148 | 0.0070 | 329742 | 797157 | 4 |
| `e3117652326c` | 8 | 50521 | 0.553 | 0.972 | 0.985 | 0.269 | 0.0022 | 0.118 | 0.154 | 0.0062 | 378084 | 971860 | 0 |
| `f364f46e4bef` | 3 | 34392 | 0.485 | 0.972 | 0.975 | 0.202 | 0.0027 | 0.118 | 0.153 | 0.0062 | 283454 | 972256 | 0 |

LZMA cycles/frame = LZMA bucket cycles per window. The busy ceiling (p99/max) differs by layout:
the B-layout players let the LZMA core run until ~97 % of the window, the A-layout players stop it
at ~84 %, which is why only A-layout carts show late frames.

## 3. Per stream

| cart | stream | windows | busy mean | p99 | max | LZMA | codebook | blit | ADPCM | Thumb player | other | late windows |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| srmt | logo majesco | 58 | 0.335 | 0.610 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0060 | 0.0032 | 0 |
| srmt | jingle | 633 | 0.591 | 0.972 | 0.973 | 0.305 | 0.0048 | 0.118 | 0.153 | 0.0068 | 0.0036 | 0 |
| srmt | movie_1 | 14326 | 0.506 | 0.972 | 0.973 | 0.237 | 0.0041 | 0.101 | 0.154 | 0.0067 | 0.0035 | 0 |
| srmt | movie_2 | 14341 | 0.508 | 0.972 | 0.983 | 0.232 | 0.0030 | 0.109 | 0.154 | 0.0066 | 0.0035 | 0 |
| proud | logo majesco | 58 | 0.335 | 0.610 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0060 | 0.0032 | 0 |
| proud | jingle | 582 | 0.473 | 0.972 | 0.973 | 0.211 | 0.0028 | 0.097 | 0.153 | 0.0066 | 0.0035 | 0 |
| proud | movie_1 | 15262 | 0.428 | 0.972 | 0.975 | 0.144 | 0.0020 | 0.118 | 0.154 | 0.0066 | 0.0034 | 0 |
| proud | movie_2 | 15101 | 0.427 | 0.972 | 0.975 | 0.143 | 0.0022 | 0.118 | 0.154 | 0.0066 | 0.0034 | 0 |
| disney1 | logo majesco | 58 | 0.328 | 0.603 | 0.624 | 0.074 | 0.0047 | 0.097 | 0.145 | 0.0055 | 0.0033 | 0 |
| disney1 | movie_1 | 15972 | 0.459 | 0.973 | 0.975 | 0.182 | 0.0026 | 0.117 | 0.147 | 0.0061 | 0.0035 | 0 |
| disney1 | movie_2 | 15889 | 0.526 | 0.973 | 0.974 | 0.251 | 0.0022 | 0.116 | 0.147 | 0.0060 | 0.0035 | 0 |
| disney2 | logo majesco | 58 | 0.335 | 0.610 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0060 | 0.0032 | 0 |
| disney2 | movie_1 | 15889 | 0.505 | 0.972 | 0.974 | 0.222 | 0.0026 | 0.117 | 0.154 | 0.0069 | 0.0034 | 0 |
| disney2 | movie_2 | 8287 | 0.484 | 0.972 | 0.982 | 0.200 | 0.0034 | 0.116 | 0.154 | 0.0070 | 0.0034 | 0 |
| disney2 | movie_3 | 8145 | 0.483 | 0.972 | 0.983 | 0.201 | 0.0024 | 0.116 | 0.154 | 0.0069 | 0.0035 | 0 |
| cn-limited | logo majesco | 58 | 0.330 | 0.604 | 0.626 | 0.074 | 0.0047 | 0.097 | 0.145 | 0.0067 | 0.0033 | 0 |
| cn-limited | movie_1 | 7918 | 0.444 | 0.843 | 0.854 | 0.165 | 0.0020 | 0.118 | 0.148 | 0.0073 | 0.0035 | 0 |
| cn-limited | movie_2 | 8216 | 0.594 | 0.844 | 0.854 | 0.316 | 0.0023 | 0.118 | 0.148 | 0.0072 | 0.0035 | 0 |
| cn-limited | movie_3 | 5316 | 0.593 | 0.852 | 0.863 | 0.314 | 0.0026 | 0.118 | 0.148 | 0.0072 | 0.0035 | 1 |
| cn-limited | movie_4 | 8202 | 0.453 | 0.844 | 0.854 | 0.174 | 0.0022 | 0.118 | 0.148 | 0.0073 | 0.0036 | 0 |
| cn-platinum | logo majesco | 58 | 0.330 | 0.604 | 0.626 | 0.074 | 0.0047 | 0.097 | 0.145 | 0.0067 | 0.0033 | 0 |
| cn-platinum | movie_1 | 8141 | 0.479 | 0.844 | 0.854 | 0.201 | 0.0018 | 0.118 | 0.148 | 0.0072 | 0.0034 | 0 |
| cn-platinum | movie_2 | 8256 | 0.565 | 0.844 | 0.856 | 0.287 | 0.0019 | 0.118 | 0.148 | 0.0071 | 0.0035 | 1 |
| cn-platinum | movie_3 | 5476 | 0.481 | 0.844 | 0.854 | 0.203 | 0.0017 | 0.118 | 0.148 | 0.0072 | 0.0035 | 1 |
| cn-platinum | movie_4 | 8103 | 0.454 | 0.844 | 0.854 | 0.175 | 0.0021 | 0.118 | 0.148 | 0.0072 | 0.0036 | 1 |
| cn-premium | logo majesco | 58 | 0.330 | 0.604 | 0.626 | 0.074 | 0.0047 | 0.097 | 0.145 | 0.0067 | 0.0033 | 0 |
| cn-premium | movie_1 | 8161 | 0.420 | 0.843 | 0.854 | 0.142 | 0.0014 | 0.118 | 0.148 | 0.0072 | 0.0035 | 0 |
| cn-premium | movie_2 | 8168 | 0.586 | 0.844 | 0.854 | 0.307 | 0.0021 | 0.118 | 0.148 | 0.0072 | 0.0034 | 1 |
| cn-premium | movie_3 | 5256 | 0.555 | 0.844 | 0.863 | 0.277 | 0.0020 | 0.118 | 0.148 | 0.0071 | 0.0036 | 0 |
| cn-premium | movie_4 | 5267 | 0.523 | 0.844 | 0.854 | 0.244 | 0.0023 | 0.118 | 0.148 | 0.0072 | 0.0035 | 0 |
| cn-special | logo majesco | 58 | 0.330 | 0.604 | 0.626 | 0.074 | 0.0047 | 0.097 | 0.145 | 0.0067 | 0.0033 | 0 |
| cn-special | movie_1 | 5286 | 0.606 | 0.852 | 0.854 | 0.327 | 0.0027 | 0.118 | 0.148 | 0.0072 | 0.0035 | 0 |
| cn-special | movie_2 | 8104 | 0.622 | 0.845 | 0.856 | 0.344 | 0.0022 | 0.118 | 0.148 | 0.0071 | 0.0035 | 0 |
| cn-special | movie_3 | 5591 | 0.484 | 0.844 | 0.854 | 0.206 | 0.0020 | 0.118 | 0.148 | 0.0073 | 0.0035 | 2 |
| cn-special | movie_4 | 5284 | 0.586 | 0.844 | 0.853 | 0.308 | 0.0019 | 0.118 | 0.148 | 0.0071 | 0.0035 | 1 |
| cn-vol2 | logo majesco | 58 | 0.330 | 0.604 | 0.626 | 0.074 | 0.0047 | 0.097 | 0.145 | 0.0067 | 0.0033 | 0 |
| cn-vol2 | movie_1 | 8071 | 0.441 | 0.844 | 0.854 | 0.163 | 0.0020 | 0.118 | 0.148 | 0.0073 | 0.0036 | 0 |
| cn-vol2 | movie_2 | 5576 | 0.483 | 0.844 | 0.856 | 0.205 | 0.0018 | 0.118 | 0.148 | 0.0072 | 0.0035 | 0 |
| cn-vol2 | movie_3 | 5469 | 0.512 | 0.844 | 0.854 | 0.233 | 0.0021 | 0.118 | 0.148 | 0.0072 | 0.0035 | 0 |
| cn-vol2 | movie_4 | 8005 | 0.479 | 0.844 | 0.856 | 0.200 | 0.0021 | 0.118 | 0.148 | 0.0072 | 0.0036 | 0 |
| cnf-platinum | logo majesco | 58 | 0.335 | 0.609 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0057 | 0.0032 | 0 |
| cnf-platinum | movie_1 | 5434 | 0.486 | 0.972 | 0.973 | 0.203 | 0.0017 | 0.118 | 0.154 | 0.0062 | 0.0035 | 0 |
| cnf-platinum | movie_2 | 8139 | 0.474 | 0.972 | 0.973 | 0.190 | 0.0018 | 0.118 | 0.154 | 0.0062 | 0.0034 | 0 |
| cnf-platinum | movie_3 | 8254 | 0.569 | 0.972 | 0.973 | 0.286 | 0.0020 | 0.118 | 0.154 | 0.0062 | 0.0035 | 0 |
| cnf-platinum | movie_4 | 8098 | 0.453 | 0.972 | 0.975 | 0.170 | 0.0022 | 0.118 | 0.154 | 0.0063 | 0.0036 | 0 |
| cnf-premium | logo majesco | 58 | 0.335 | 0.609 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0057 | 0.0032 | 0 |
| cnf-premium | movie_1 | 8196 | 0.422 | 0.972 | 0.974 | 0.139 | 0.0015 | 0.118 | 0.154 | 0.0062 | 0.0035 | 0 |
| cnf-premium | movie_2 | 5050 | 0.554 | 0.972 | 0.975 | 0.271 | 0.0022 | 0.118 | 0.154 | 0.0062 | 0.0036 | 0 |
| cnf-premium | movie_3 | 8225 | 0.597 | 0.972 | 0.973 | 0.314 | 0.0023 | 0.118 | 0.154 | 0.0062 | 0.0034 | 0 |
| cnf-premium | movie_4 | 5053 | 0.521 | 0.972 | 0.973 | 0.237 | 0.0024 | 0.118 | 0.154 | 0.0063 | 0.0035 | 0 |
| cnf-speciale | logo majesco | 58 | 0.335 | 0.609 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0057 | 0.0032 | 0 |
| cnf-speciale | movie_1 | 5337 | 0.502 | 0.972 | 0.985 | 0.219 | 0.0024 | 0.118 | 0.154 | 0.0063 | 0.0035 | 0 |
| cnf-speciale | movie_2 | 5288 | 0.615 | 0.972 | 0.973 | 0.330 | 0.0028 | 0.118 | 0.154 | 0.0063 | 0.0035 | 0 |
| cnf-speciale | movie_3 | 8089 | 0.625 | 0.972 | 0.973 | 0.342 | 0.0022 | 0.118 | 0.154 | 0.0062 | 0.0034 | 0 |
| cnf-speciale | movie_4 | 5283 | 0.593 | 0.972 | 0.973 | 0.310 | 0.0020 | 0.118 | 0.154 | 0.0062 | 0.0036 | 0 |
| nick2 | logo majesco | 58 | 0.329 | 0.599 | 0.620 | 0.073 | 0.0047 | 0.097 | 0.145 | 0.0058 | 0.0033 | 0 |
| nick2 | movie_1 | 9207 | 0.399 | 0.818 | 0.828 | 0.143 | 0.0053 | 0.093 | 0.148 | 0.0063 | 0.0036 | 0 |
| nick2 | movie_2 | 8530 | 0.466 | 0.820 | 0.829 | 0.212 | 0.0064 | 0.090 | 0.148 | 0.0062 | 0.0036 | 0 |
| nick2 | movie_3 | 8438 | 0.491 | 0.844 | 0.854 | 0.221 | 0.0021 | 0.110 | 0.148 | 0.0062 | 0.0035 | 1 |
| nick2 | movie_4 | 7925 | 0.450 | 0.843 | 0.863 | 0.186 | 0.0025 | 0.104 | 0.148 | 0.0063 | 0.0035 | 1 |
| nick3 | logo majesco | 58 | 0.335 | 0.609 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0055 | 0.0034 | 0 |
| nick3 | movie_1 | 16644 | 0.479 | 0.972 | 0.973 | 0.195 | 0.0031 | 0.118 | 0.153 | 0.0062 | 0.0035 | 0 |
| nick3 | movie_2 | 9282 | 0.451 | 0.972 | 0.975 | 0.168 | 0.0026 | 0.118 | 0.153 | 0.0062 | 0.0036 | 0 |
| nick3 | movie_3 | 8466 | 0.535 | 0.972 | 0.973 | 0.252 | 0.0021 | 0.118 | 0.153 | 0.0061 | 0.0035 | 0 |
| sponge3 | logo majesco | 58 | 0.329 | 0.599 | 0.620 | 0.073 | 0.0047 | 0.097 | 0.145 | 0.0058 | 0.0033 | 0 |
| sponge3 | jingle | 516 | 0.541 | 0.852 | 0.853 | 0.271 | 0.0012 | 0.111 | 0.148 | 0.0062 | 0.0037 | 0 |
| sponge3 | movie_1 | 7943 | 0.461 | 0.843 | 0.862 | 0.201 | 0.0028 | 0.100 | 0.148 | 0.0062 | 0.0036 | 0 |
| sponge3 | movie_2 | 8006 | 0.462 | 0.843 | 0.863 | 0.201 | 0.0028 | 0.100 | 0.148 | 0.0063 | 0.0035 | 0 |
| sponge3 | movie_3 | 8431 | 0.478 | 0.843 | 0.863 | 0.215 | 0.0028 | 0.102 | 0.148 | 0.0063 | 0.0035 | 0 |
| sponge3 | movie_4 | 8057 | 0.460 | 0.843 | 0.863 | 0.200 | 0.0028 | 0.099 | 0.148 | 0.0063 | 0.0035 | 0 |
| dbgt | logo majesco | 58 | 0.330 | 0.604 | 0.625 | 0.074 | 0.0047 | 0.097 | 0.145 | 0.0064 | 0.0033 | 0 |
| dbgt | jingle | 792 | 0.695 | 0.852 | 0.855 | 0.412 | 0.0064 | 0.118 | 0.148 | 0.0072 | 0.0035 | 0 |
| dbgt | movie_1 | 15732 | 0.479 | 0.819 | 0.832 | 0.224 | 0.0038 | 0.093 | 0.148 | 0.0071 | 0.0035 | 3 |
| dbgt | movie_2 | 13769 | 0.515 | 0.844 | 0.853 | 0.247 | 0.0026 | 0.107 | 0.148 | 0.0070 | 0.0035 | 1 |
| tmnt-fr | logo majesco | 58 | 0.335 | 0.609 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0057 | 0.0032 | 0 |
| tmnt-fr | jingle | 730 | 0.587 | 0.972 | 0.973 | 0.300 | 0.0048 | 0.118 | 0.153 | 0.0064 | 0.0036 | 0 |
| tmnt-fr | movie_1 | 13690 | 0.508 | 0.972 | 0.975 | 0.223 | 0.0026 | 0.118 | 0.154 | 0.0063 | 0.0034 | 0 |
| tmnt-fr | movie_2 | 13683 | 0.483 | 0.972 | 0.975 | 0.199 | 0.0024 | 0.118 | 0.154 | 0.0063 | 0.0034 | 0 |
| yugioh-fr | logo majesco | 58 | 0.335 | 0.609 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0057 | 0.0032 | 0 |
| yugioh-fr | jingle | 713 | 0.788 | 0.973 | 0.983 | 0.495 | 0.0107 | 0.118 | 0.154 | 0.0070 | 0.0034 | 0 |
| yugioh-fr | movie_1 | 13801 | 0.509 | 0.972 | 0.987 | 0.237 | 0.0031 | 0.106 | 0.153 | 0.0063 | 0.0035 | 0 |
| yugioh-fr | movie_2 | 13707 | 0.517 | 0.972 | 0.974 | 0.244 | 0.0036 | 0.106 | 0.153 | 0.0063 | 0.0035 | 0 |
| tmnt-proto | logo majesco | 58 | 0.335 | 0.609 | 0.631 | 0.074 | 0.0047 | 0.097 | 0.151 | 0.0057 | 0.0032 | 0 |
| tmnt-proto | jingle | 733 | 0.614 | 0.972 | 0.973 | 0.351 | 0.0031 | 0.097 | 0.153 | 0.0063 | 0.0036 | 0 |
| tmnt-proto | movie_1 | 14294 | 0.501 | 0.972 | 0.975 | 0.218 | 0.0023 | 0.118 | 0.154 | 0.0063 | 0.0034 | 0 |
| tmnt-proto | movie_2 | 14288 | 0.525 | 0.972 | 0.975 | 0.241 | 0.0024 | 0.118 | 0.154 | 0.0062 | 0.0035 | 0 |
| jimmy | res0 | 58 | 0.328 | 0.599 | 0.619 | 0.073 | 0.0048 | 0.097 | 0.145 | 0.0047 | 0.0036 | 0 |
| jimmy | res5 | 433 | 0.626 | 0.850 | 0.850 | 0.357 | 0.0051 | 0.106 | 0.147 | 0.0048 | 0.0056 | 0 |
| jimmy | res1 | 8036 | 0.465 | 0.842 | 0.851 | 0.198 | 0.0025 | 0.107 | 0.148 | 0.0048 | 0.0046 | 0 |
| jimmy | res2 | 7802 | 0.464 | 0.842 | 0.850 | 0.198 | 0.0019 | 0.107 | 0.148 | 0.0048 | 0.0044 | 0 |
| jimmy | res3 | 7849 | 0.480 | 0.842 | 0.850 | 0.214 | 0.0023 | 0.106 | 0.148 | 0.0048 | 0.0045 | 0 |
| jimmy | res4 | 7980 | 0.479 | 0.842 | 0.850 | 0.214 | 0.0019 | 0.107 | 0.148 | 0.0048 | 0.0043 | 0 |
| noah-proto | res0 | 58 | 0.327 | 0.598 | 0.618 | 0.073 | 0.0048 | 0.097 | 0.145 | 0.0038 | 0.0036 | 0 |
| noah-proto | res3 | 737 | 0.731 | 0.828 | 0.829 | 0.469 | 0.0075 | 0.097 | 0.148 | 0.0038 | 0.0066 | 0 |
| noah-proto | res1 | 13571 | 0.522 | 0.842 | 0.853 | 0.247 | 0.0016 | 0.118 | 0.147 | 0.0038 | 0.0046 | 0 |
| noah-proto | res2 | 13624 | 0.511 | 0.842 | 0.852 | 0.236 | 0.0017 | 0.118 | 0.147 | 0.0038 | 0.0047 | 2 |

"late windows" = windows longer or shorter than 5 refreshes. The late frames are at
cn-limited ep3 #596, cn-platinum ep2 #1898, ep3 #3684, ep4 #1012, cn-premium ep2 #5258,
cn-special ep3 #405 and #4365, ep4 #2912, nick2 ep3 #1571, ep4 #507, dbgt ep1 #8614 (+ a 6/4
pair at #15166), dbgt ep2 #2968, noah ep2 #3374 and #10165 (stream frame numbers). In each case
one frame stays on screen for 10 VBlanks and the rest of the stream keeps the 5-VBlank cadence
one slot later. Audio is not interrupted (the PCM ring is decoded ahead).

## 4. Other resources

- **Memory:** EWRAM holds `main.bin` (33-55 KB), the 128 KiB index ring and the codebook slots;
  IWRAM the codec image (10.9-15.0 KB) plus BSS (decoder objects, clamp and 4 KiB dither tables);
  VRAM the framebuffer (75 KiB), the RGB codebook (8 KiB), the LZMA probabilities and, in the
  pairs-to-VRAM builds, the PCM ring ([player.md](player.md) 2).
- **Cart bus:** WAITCNT 0x4014 (WS0 3/1, prefetch on). The LZMA core reads its input bytes from
  ROM (episode averages 36-125 kbit/s of video data, 4.5-15.6 KB/s), the ADPCM reader
  28-31 kbit/s (3.5-3.9 KB/s). Disney 2 reads both through the Matrix window ([player.md](player.md) 9).
- **Display:** mode 3, single buffer, one blit per 5 refreshes; the blit costs 0.118 of a window
  (0.59 of one refresh), so it can tear when it straddles the refresh it starts in.
- **Audio:** DirectSound A, 16384 Hz (Timer0), Timer1 sample-count IRQ, DMA1 FIFO refill.
