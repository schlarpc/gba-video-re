# VX family formats (VX++ and VXGB): index and per-stream parameters

The reimplementation-grade specification is split over four documents in this directory. This
file indexes them by layer and gives the coding parameters of every stream.

| layer | VX++ | VXGB (differences only) |
|---|---|---|
| container: header, chapter and seek tables, A/V mapping | [container.md](container.md) 2-5 | [vxgb.md](vxgb.md) 2, 8 (header 0x14 = QP) |
| video: bit reader, MB loop, MV prediction, partition tree, 12 modes, intra, MC, ref rotation, YUV->BGR555 | [video.md](video.md) | `vxgb.md` 3-4, 7 (no frame header; nC grid bookkeeping) |
| residual: CBP, coefficient VLC, escapes, zigzag, dequant, IDCT | [residual.md](residual.md) | `vxgb.md` 5-6 (CBP32, CAVLC tables with VXGB syntax, header QP) |
| audio: codebook, AFrame syntax, LPC/pulse synthesis, upsampler, noise shaper | [container.md](container.md) 6, 9 | unchanged (`vxgb.md` 8) |

Tables that are not standard are generated from the ROM at build time (`reference/vx/decoder/`):
- `gen_iwram_lut.py`: the clamp table of the blitter;
- `gen_fill.py`: the fill op lists, by symbolic execution of the ARM;
- `gen_vxgb_tables.py`: the CAVLC tree, CBP32, dequant and the Rev 5 blitter bytes, from any Rev 5 ROM.

The H.264 CAVLC code tables, the H.264 zigzag, the LevelScale rows and the MPEG-4 TCOEF codebook
are standard. They are cited, not copied, in the docs.

*Shrek* (Rev 5), added in the suite pass, is covered by `vxgb.md` as written: its stream exercises
no syntax that *Shark Tale* or *Shrek 2* (Rev 5) do not, and the decoder needed no change.

## Per-stream parameters

Numbers are from `tools/vx/stats/bitstats.py`, which reads per-frame bits from the C decoder and AFrame sizes
from the audio stream. Rates are over the coded stream; "1-s p99" is the 99th percentile of a
7-frame (1.000 s) sliding window.

| cart | stream | magic | size | frames | QP | seek pts | video kbit/s mean / 1-s p99 / 1-s max | AFrames | AFrame sizes 20/14/12/10 | audio kbit/s |
|---|---|---|---|---|---|---|---|---|---|---|
| Shrek Rev 6 | 0x20200 | VX++ | 240x160 | 37,837 | 30 (in bitstream) | 195 | 79.0 / 247.9 | 691,876 | 88.0/5.7/4.8/1.5 % | 19.59 |
| Shrek Rev 5 | 0x20200 | VXGB | 240x160 | 37,837 | 32 (header) | 195 | 59.1 / 183.4 | 691,877 | 89.6/4.9/4.2/1.4 % | 19.70 |
| Shrek 2 Rev 6 | 0x20200 | VX++ | 240x160 | 38,816 | 30 | 197 | 76.6 / 271.5 | 709,778 | 88.8/5.1/4.5/1.6 % | 19.64 |
| Shrek 2 Rev 5 | 0x20200 | VXGB | 240x160 | 38,816 | 32 | 197 | 54.4 / 194.9 | 709,779 | 89.4/4.8/4.3/1.5 % | 19.68 |
| Shark Tale Rev 6 | 0x20200 | VX++ | 240x160 | 37,697 | 33..37 | 191 | 80.4 / 231.3 | 689,316 | 75.6/11.9/9.4/3.0 % | 18.67 |
| Shark Tale Rev 5 | 0x20200 | VXGB | 240x160 | 37,697 | 34 | 191 | 78.8 / 222.8 | 689,317 | 87.5/5.6/5.2/1.7 % | 19.53 |
| Shrek + Shark Tale: Shrek | 0x20200 | VX++ | 240x112 | 34,874 | 34 | 180 | 36.3 / 110.0 | 637,697 | 34.2/33.5/23.7/8.6 % | 15.60 |
| Shrek + Shark Tale: Shark Tale | 0x1eef800 | VX++ | 240x112 | 34,261 | 37 | 171 | 37.5 / 111.0 | 626,486 | 32.9/32.4/25.0/9.7 % | 15.45 |
| Shrek + Shark Tale: Shrek credits | 0x3dd3600 | VX++ | 240x160 | 595 | 32 | 1 | 51.1 / 100.1 | 10,880 | 0/12.1/30.9/57.0 % | 11.37 |
| Shrek + Shark Tale: Shark Tale credits | 0x3e76a00 | VX++ | 240x160 | 707 | 32 | 1 | 63.8 / 101.6 | 12,928 | 0/11.9/31.2/56.9 % | 11.37 |

All streams run at 7.00003 fps (Timer3 reload 12,483) and 16,384 Hz mono audio, played at 65,536 Hz.

Observations:
- **The Rev 5 encodes of the same movies are different encodes, not transcodes.** The frame
  counts are equal, and the AFrame counts differ by one. VXGB at QP 32 spends 25-29 % fewer
  video bits than VX++ at QP 30 on *Shrek* and *Shrek 2*, and 2 % fewer on *Shark Tale* (QP 34
  against 33..37). Bit-rate efficiency could not be compared at equal quality: the sources are
  not available.
- **Bit rate is very peaky.** At 240x160 the median frame is 5.7-9.6 kbit. Seek-point frames
  average 21-29 kbit, the per-frame p99 is 32-43 kbit, and the largest frames are 69-115 kbit
  (scene cuts coded as intra). The 1-s p99 is therefore about 3x the mean. Constant QP gives this
  pattern. The credits streams are mostly static (median frame 151 bits = 150 skip-coded MBs)
  with occasional 80-105 kbit cards.
- **The combo cart economises.** Its 240x112 letterbox, higher QP and a lower audio rate fit
  two movies plus credits in 64 MiB. Its audio uses many more short AFrames (66 % of its AFrames are not 20 bytes,
  against 10-12 % on the single-movie carts and 24 % on Shark Tale Rev 6). This is the encoder's choice: the audio decoder is the
  same.
