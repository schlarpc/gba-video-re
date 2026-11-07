# gbav-vx port notes

Rust port of the VX family: VX++ and VXGB video, VX audio, the YUV -> BGR555 blit (clamp
table overrun, field carries, 2x2 luma dither; brightness 0). Reference: `reference/vx/decoder/vxdec.c`,
`residual.c`, `vxgb_residual.c`, `bits.h`, `fill_tables.h`; `tools/vx/container/vxcart.py`,
`vxaudio.py`. Pure Rust, no `unsafe`, no dependencies besides `gbav-common`.

## Crate layout

| module | ports | notes |
|---|---|---|
| `cart` | `tools/vx/container/vxcart.py`, vxdec_main.c stream scan | program table -> player layout, build id (md5 of the player program), stream headers, seek table |
| `tables` | `gen_iwram_lut.py`, `gen_vxgb_tables.py`, residual.c literal tables | all read from the player images at runtime |
| `bits` | `bits.h` | register-exact reader (refill phase, power-on vs seek init) |
| `video` | `vxdec.c` | EWRAM-image decoder core (`Core`), VX++ and VXGB |
| `residual` | `residual.c` | VX++ MPEG-4-style VLC residual |
| `cavlc` | `vxgb_residual.c` | VXGB CAVLC residual + nonzero-count grids |
| `fill`, `fill_data` | `fill_tables.h` | bilinear / plane fill op lists (see below) |
| `blit` | `vx_yuv_to_bgr555` | per-stream patched IWRAM clamp table |
| `audio` | `vxaudio.py` | AFrame decoder; output stage = `gbav_common::output::OutputStage` |

## API

- `Vx: gbav_common::Codec` (`FAMILY = "vx"`, stream ids = header offset in hex: `20200`,
  `1eef800`, ...; an index `0`, `1`, ... is accepted too). `Codec::video/audio` take the shared
  `Rom` (`Arc<[u8]>`) and return `VxVideo<Rom>` / `VxAudio<Rom>`, which read straight from it:
  no stream bytes are copied. `StreamInfo::chapters` = the chapter table (frames only; every
  chapter is a seek point).
- `VxVideo<S: AsRef<[u8]>>`: `open(rom, id)` decodes from any borrowed / shared ROM.
  Trait `seek` = cold start at a seek point; `seek_to(frame)` = cold start at the last seek
  point <= frame and decode forward. In-place API without per-frame allocation:
  `decode_next()`, `slot()`, `bgr555_into()`, `frame_bit()`, `stats()`, `core()`.
  `set_strict(false)` keeps decoding through frames that raise decoder error flags (like the
  C reference) instead of returning `Err(Undefined)`.
- `VxFrame: Frame`: `manifest_bytes()` = the 0xF000-byte slot, `size()`, `bgr555_into()` =
  the player's mode-3 picture (`width * height`), plus `luma`, `chroma`.
- `VxAudio<S>: AudioDecoder`: one block = one AFrame. `key()` = `{frame: None, index: AFrame
  index from the stream start}`; `manifest_part(0)` = its 512 bytes of signed 8-bit 65536 Hz
  output; `pcm()` = its 128 exact i32 samples at 16384 Hz (they exceed 16 bits: Shrek 2 Rev 5
  reaches -113040..119586). `seek(frame)` = `seek_frame`: cold start (fresh state) at a seek
  point's AFrame; the AFrame index continues from the stream start (the first seek walks the
  AFrame size chain once to map seek entries to indices). Since every seek point's AFrame is
  intra, the blocks after a seek equal a continuous decode's (tested). `next_into(&mut [u8;
  512])` decodes without allocation. Ends at the zero padding after the last AFrame.
  `decode_samples` is not supported (VX manifests are per AFrame).
- `Core` (pub) is the raw decoder: `Core::new(tables, w, h, qp, stream)`, `ew` (the EWRAM
  image), `decode_frame(stream)`, `bits`, `stats`. The fuzz test drives it directly.

## ROM-derived tables (read at runtime)

The player is found through the boot loader's program table (ROM 0x8e4 sizes from 0xa00,
program 2) and its 0x44-byte load header, as vxcart.py does. The build is identified by the
md5 of the program image (VX++ player `7ea5c790...`, VXGB player `9c75deb7...`; sha1
7324ebf3... / 33a2f3a7... in vxcart.py); an unknown build is refused with `Error::Format`
because the table addresses are build-specific. A VX++ stream needs the VX++ build, a VXGB
stream the VXGB build.

| table | where | C reference |
|---|---|---|
| blit clamp table + surrounding code | IWRAM 0x03000000..0x400, IRQ entries patched, size immediates per stream | iwram_lut.h / vxgb_iwram_lo |
| EWRAM image below slot 0 | EWRAM image | vxdec_main.c `ewimg` |
| VX++ coefficient LUT, LMAX, RMAX | EWRAM 0x020010e4 / 0x020030e4 / 0x02003164 | literals in residual.c |
| VX++ CBP table, LevelScale | IWRAM 0x03005610 / 0x03000680 | literals in residual.c / vxdec.c |
| VXGB CBP, coeff_token tree + lengths, total_zeros, run_before, level thresholds | IWRAM (gen_vxgb_tables.py addresses) | vxgb_tables.h |
| VXGB dequant | EWRAM 0x02002a14 | vxgb_tables.h |

Note the C residual.c also carried ROM-derived literal tables (`vx_coef_lut` etc.); those are
read from the ROM too, so the crate holds no ROM bytes.

## Fill tables: kept as checked-in Rust data

`fill_tables.h` is the output of `reference/vx/decoder/gen_fill.py`, which symbolically executes the
player's straight-line ARM fill routines. The lists record code structure (which
neighbours each store averages, in what order, and whether an operand is the truncated stored
byte or the untruncated register), not ROM data. I kept them as data
(`src/fill_data.rs`, 2312 ops, converted 1:1 by `tools/fill_to_rs.py`) rather than
re-deriving them at runtime: a runtime derivation needs a small ARM decoder + symbolic
executor and the per-build addresses of 16 + 9 + 9 routines, all to reproduce a fixed list
that is identical for both player builds (the VXGB player runs the same routines; the C
reference uses one header for both and matches every manifest). Regenerate with
`(cd reference/vx/decoder && python3 gen_fill.py) && python3 crates/gbav-vx/tools/fill_to_rs.py reference/vx/decoder/fill_tables.h > crates/gbav-vx/src/fill_data.rs`.

## Verification (all passed, 2026-11-07)

Env: `GBAV_ROMS` (your ROMs, No-Intro names or verify/carts.csv short ids). Manifests come
from `verify/vx/` (`GBAV_VERIFY` overrides): frame manifests (yuv_md5, vram_md5),
`*.frame_bits.json.gz`, `streams.json` (aggregate audio digests), and for audio the per-block
manifest `<stem>.audio_md5.csv.gz` when present, else the ~30 s segment manifest
`<stem>.audio_seg_md5.csv.gz`; known cart-side differences from `verify/known.csv` (Shrek 2
Rev 5 audio from block 296039). Optional: `GBAV_VX_PCM_REF` (vxaudio.py reference,
`tests/pcm_ref.py`), `GBAV_VX_FUZZ` (corpus from `tests/fuzz/gen_corpus.py`, which uses
`reference/vx/decoder/libvxdec.so` and the generators in `tools/vx/oracle/`).

`cargo test --release -p gbav-vx --test streams -- --nocapture` (10 streams, about 100 s on 8 threads):

| stream | variant | frames: YUV / screen | seek points cold (whole segments) | audio (played blocks) |
|---|---|---|---|---|
| shrek-rev6 0x20200 | VX++ | 37837/37837, 37833/37833 | 195, 37837/37837 | 691796/691796 |
| shrek-rev5 0x20200 | VXGB | 37837/37837, 37833/37833 | 195, 37837/37837 | 691796/691796 |
| shrek2-rev6 0x20200 | VX++ | 38816/38816, 38812/38812 | 197, 38816/38816 | 709698/709698 |
| shrek2-rev5 0x20200 | VXGB | 38816/38816, 38804/38812 + 8 skipped blits | 197, 38816/38816 | 296039 match, then the known emulator underrun |
| sharktale-rev6 0x20200 | VX++ | 37697/37697, 37693/37693 | 191, 37697/37697 | 689240/689240 |
| sharktale-rev5 0x20200 | VXGB | 37697/37697, 37667/37693 + 26 skipped blits | 191, 37697/37697 | 689240/689240 |
| shrek-sharktale-rev5 0x20200 | VX++ 240x112 | 34874/34874, 34870/34870 | 180, 34874/34874 | 637614/637614 |
| shrek-sharktale-rev5 0x1eef800 | VX++ 240x112 | 34261/34261, 34257/34257 | 171, 34261/34261 | 626405/626405 |
| shrek-sharktale-rev5 0x3dd3600 | VX++ | 595/595, 591/591 | 1, 595/595 | 10802/10802 |
| shrek-sharktale-rev5 0x3e76a00 | VX++ | 707/707, 703/703 | 1, 707/707 | 12850/12850 |

Also per stream: every seek-table `video_bit` equals the continuous decode's frame start bit,
0 decoder error flags, and where the repo ships `*.frame_bits.json.gz` (grammar walker), every
frame start bit (and the end bit, where the file has it) equals the walker's (Shrek Rev 6 / Rev 5,
Shark Tale Rev 5, Shrek 2 Rev 5). "Skipped blits" are frames the emulated
cart displayed late (the screen still shows the previous picture); the test accepts them only
when the manifest screen equals one of our previous 8 pictures (same rule as the suite's
verify_vx.py, which reported the same 26 / 8). Audio is checked against the streams.json
aggregate digest where present and against the per-block manifest; the Shrek 2 Rev 5
mismatch from block 296039 is the documented emulator-side audio underrun (docs/vx/perf.md P2.4), not a
decoder difference. With `GBAV_VX_PCM_REF`, every AFrame of every stream (not only the
played ones) is compared with vxaudio.py at the 16 kHz sample level (i32) and at the 8-bit
output level: all 10 streams match for every AFrame (see the table), including
Shrek 2 Rev 5 past the emulator underrun.

| stream | AFrames = vxaudio.py (pcm + 8-bit) |
|---|---|
| shrek-rev6 / shrek-rev5 | 691876 / 691877 |
| shrek2-rev6 / shrek2-rev5 | 709778 / 709779 |
| sharktale-rev6 / sharktale-rev5 | 689316 / 689317 |
| shrek-sharktale-rev5 20200 / 1eef800 / 3dd3600 / 3e76a00 | 637697 / 626486 / 10880 / 12928 |

Whole-stream cross-check against the C binary: `vxdec --md5` (slot md5, picture md5, frame
start bit per frame) and `examples/vx_bench md5` print identical lines for all 10 streams
(299,137 frames).

Differential fuzz against the C decoder: `tests/fuzz/gen_corpus.py` builds random valid
frames with the repo's generators (fuzz.py for VX++, extended with real residual
coefficients -- an encoder built from the ROM's VLC LUT, all three escape modes -- since
fuzz.py itself only emits CBP 0; fuzz_vxgb.py's CAVLC generator for VXGB), 1..3 frames per
case, random reference memory and nonzero-count grids, 15 % of cases with a random-bit tail
(error paths), runs them through `libvxdec.so` (ctypes) and stores the C result;
`tests/fuzz_c.rs` replays them and compares the whole 256 KiB EWRAM image, the final bit
position and the error count. A deliberately broken Rust build (nC rounding, one dequant
index) fails 34 of 40 cases, so the comparison has teeth.

Result: 4,040 cases (2,020 VX++ / 2,020 VXGB; 595 with garbage tails; 582 cases raise
decoder error flags in C), 0 mismatches. Coverage per 1,000 cases: all 24 modes at every
partition size, ~110k CAVLC chroma blocks, every coeff_token class incl. the FLC, prefixes
0..14 and the level escape, suffixLength 0..5, run_before for every zerosLeft, VX++ VLC codes
plus ~8.6k / 1.5k / 34k escape mode 1 / 2 / 3 levels per 40 cases. One mismatch class was
found and fixed (ue(v) on an all-zero buffer, below).

## Performance (Rust vs C, whole movie, single thread)

Release build vs the C reference built by reference/vx/decoder/Makefile (gcc -O2). The machine was shared
with other lanes (load average ~16 on 8 cores), so the numbers are +-10 %; best of 3.

| stream | decode only: C / Rust | decode + blit + md5 of slot and picture: C / Rust |
|---|---|---|
| Shrek Rev 6 (VX++, 37,837 frames) | 3.51 s / 2.90 s | 17.5 s / 17.4 s |
| Shrek Rev 5 (VXGB, 37,837 frames) | 3.88 s / 3.17 s | 18.4 s / 19.0 s |
| Shrek + Shark Tale 20200 (VX++ 240x112, 34,874) | 3.36 s / 2.29 s | 16.7 s / 12.6 s |
| Shrek 2 Rev 6 (VX++, 38,816) | 3.99 s / 3.18 s | -- |
| Shrek 2 Rev 5 (VXGB, 38,816) | 4.22 s / 3.13 s | -- |

Decoding alone is 15-30 % faster in Rust (about 12,000 frames/s, 1,700x real time). In the
`--md5` mode both are dominated by hashing 136 KiB per frame. The BGR555 blit adds about
2.4 s per movie (60 us/frame). Audio: a whole movie's 709,778 AFrames, output stage
included, take 1.3 s in Rust; vxaudio.py needs about 10 minutes.

## Decisions / differences from the C reference

- The C decoder only counts decoder errors; `VxVideo` returns `Err(Undefined)` for a frame
  that raised one (strict by default), `Core` keeps counting like the C.
- C undefined behaviour reached only by garbage input: ue(v) on an all-zero buffer computes
  `(1u << 32) - 1` in bits.h. The gcc -O2 reference build yields 0xffffffff (the value in
  exact arithmetic), and so does the port; the differential fuzz found this (3 of 4040
  cases before the fix, all with long zero runs in a garbage tail). What the ARM does there
  is not modelled by either decoder. A shift-by-32 in the VX++ sign test cannot happen with
  the ROM's LUT (no zero-length entries).
- Audio cold start at a seek point uses a fresh decoder state. The cart does not reset its
  sample buffers on a seek, so the first blocks after a seek on hardware depend on what
  played before; there is no manifest for that, and the trait has no audio seek anyway.
- `VxVideo::open` takes any `S: AsRef<[u8]>` so a caller can share one ROM buffer
  between video and audio decoders instead of copying.

## Remaining notes

- Hoisted into gbav-common and removed here: streaming MD5 (`gbav_common::md5`), `le16`/`le32`
  (`gbav_common::bytes`), the output stage (`gbav_common::output::OutputStage`, proved equal to
  the former `Interp4Shaper` on the VX range by a gbav-common test and by every stream's audio
  manifest). The tests read manifests with `gbav_common::manifest` (no `gzip -dc`).
- `VxFrame` still owns a copy of the 60 KiB slot per frame (`next_frame`); the in-place
  `VxVideo::decode_next` + `slot()` path avoids it.
- The C differential fuzz still needs a Python step (`gen_corpus.py`, ctypes on libvxdec.so)
  to produce the corpus; it is not a testkit `CLib` harness yet.
