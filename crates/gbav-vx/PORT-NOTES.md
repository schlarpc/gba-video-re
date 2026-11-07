# gbav-vx port notes

Rust port of the VX family: VX++ and VXGB video, VX audio, the YUV -> BGR555 blit (clamp
table overrun, field carries, 2x2 luma dither; brightness 0). Reference: `decoder/vxdec.c`,
`residual.c`, `vxgb_residual.c`, `bits.h`, `fill_tables.h`; `container/vxcart.py`,
`vxaudio.py`. Pure Rust, no `unsafe`, no dependencies besides `gbav-common`.

## Crate layout

| module | ports | notes |
|---|---|---|
| `cart` | `container/vxcart.py`, vxdec_main.c stream scan | program table -> player layout, build id (md5 of the player program), stream headers, seek table |
| `tables` | `gen_iwram_lut.py`, `gen_vxgb_tables.py`, residual.c literal tables | all read from the player images at runtime |
| `bits` | `bits.h` | register-exact reader (refill phase, power-on vs seek init) |
| `video` | `vxdec.c` | EWRAM-image decoder core (`Core`), VX++ and VXGB |
| `residual` | `residual.c` | VX++ MPEG-4-style VLC residual |
| `cavlc` | `vxgb_residual.c` | VXGB CAVLC residual + nonzero-count grids |
| `fill`, `fill_data` | `fill_tables.h` | bilinear / plane fill op lists (see below) |
| `blit` | `vx_yuv_to_bgr555` | per-stream patched IWRAM clamp table |
| `audio` | `vxaudio.py` | AFrame decoder; output stage in `util::Interp4Shaper` |
| `util` | -- | generic helpers (hoist candidates) |

## API

- `Vx: gbav_common::Codec` (`FAMILY = "vx"`, stream ids = header offset in hex: `20200`,
  `1eef800`, ...; an index `0`, `1`, ... is accepted too).
- `VxVideo<S: AsRef<[u8]>>`: `open(rom, id)` decodes straight from a borrowed / shared ROM;
  `Codec::video` copies the video elementary stream into a `Vec`. Trait `seek` = cold start at
  a seek point; `seek_to(frame)` = cold start at the last seek point <= frame and decode
  forward. In-place API without per-frame allocation: `decode_next()`, `slot()`,
  `bgr555_into()`, `frame_bit()`, `stats()`, `core()`. `set_strict(false)` keeps decoding
  through frames that raise decoder error flags (like the C reference) instead of returning
  `Err(Undefined)`.
- `VxFrame: Frame`: `manifest_bytes()` = the 0xF000-byte slot, `bgr555()` = the player's
  mode-3 picture (`width * height`), plus `bgr555_into`, `luma`, `chroma`.
- `VxAudio<S>: AudioDecoder`: one block = one AFrame = 512 bytes of signed 8-bit 65536 Hz
  output; `last_pcm()` = its 128 samples at 16384 Hz saturated to i16, `last_samples()` the
  exact i32 values; `next_into(&mut [u8; 512])` without allocation; `seek_frame(f)` = cold
  start (fresh state) at a seek point's AFrame. Ends at the zero padding after the last AFrame.
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

`fill_tables.h` is the output of `decoder/gen_fill.py`, which symbolically executes the
player's straight-line ARM fill routines. The lists record code structure (which
neighbours each store averages, in what order, and whether an operand is the truncated stored
byte or the untruncated register), not ROM data. I kept them as data
(`src/fill_data.rs`, 2312 ops, converted 1:1 by `tools/fill_to_rs.py`) rather than
re-deriving them at runtime: a runtime derivation needs a small ARM decoder + symbolic
executor and the per-build addresses of 16 + 9 + 9 routines, all to reproduce a fixed list
that is identical for both player builds (the VXGB player runs the same routines; the C
reference uses one header for both and matches every manifest). Regenerate with
`python3 decoder/gen_fill.py && python3 crates/gbav-vx/tools/fill_to_rs.py decoder/fill_tables.h > crates/gbav-vx/src/fill_data.rs`.

## Verification (all passed, 2026-11-07)

Env: `GBAV_ROMS=/home/schlarpc/re-shell/tmp/suite/roms`,
`GBAV_VX_MANIFESTS=/home/schlarpc/re-shell/tmp/suite/vx/src/manifests` (the suite capture: per-block
audio manifests for all 10 streams, frame manifests for the two Rev 5 VXGB carts that are not in
verify/streams.json), `GBAV_VX_PCM_REF` (vxaudio.py reference, `tests/pcm_ref.py`),
`GBAV_VX_FUZZ` (corpus from `tests/fuzz/gen_corpus.py`).

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
frame start bit and the end bit equal the walker's. "Skipped blits" are frames the emulated
cart displayed late (the screen still shows the previous picture); the test accepts them only
when the manifest screen equals one of our previous 8 pictures (same rule as the suite's
verify_vx.py, which reported the same 26 / 8). Audio is checked against the streams.json
aggregate digest where present and against the per-block manifest; the Shrek 2 Rev 5
mismatch from block 296039 is the documented emulator-side audio underrun (perf-vx), not a
decoder difference. With `GBAV_VX_PCM_REF`, every AFrame of every stream (not only the
played ones) is compared with vxaudio.py at the 16 kHz sample level (i32) and at the 8-bit
output level.

Differential fuzz against the C decoder: `tests/fuzz/gen_corpus.py` builds random valid
frames with the repo's generators (fuzz.py for VX++, extended with real residual
coefficients -- an encoder built from the ROM's VLC LUT, all three escape modes -- since
fuzz.py itself only emits CBP 0; fuzz_vxgb.py's CAVLC generator for VXGB), 1..3 frames per
case, random reference memory and nonzero-count grids, 15 % of cases with a random-bit tail
(error paths), runs them through `libvxdec.so` (ctypes) and stores the C result;
`tests/fuzz_c.rs` replays them and compares the whole 256 KiB EWRAM image, the final bit
position and the error count. A deliberately broken Rust build (nC rounding, one dequant
index) fails 34 of 40 cases, so the comparison has teeth. Results: see below.

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

## For the lead

Hoistable helpers (`src/util.rs`):
- `Md5` streaming MD5 (`new/update/finish/digest`), `md5_hex`, `md5_hex_u16`, `hex`.
  `gbav_common::md5::digest` copies its input and rebuilds the K table with `sin()` per
  call; hashing a whole movie (37,837 x (60 KiB slot + 75 KiB picture)) is noticeably slower
  with it, and the audio aggregate digest wants streaming anyway.
- `Interp4Shaper`: the VX audio output stage (4x interpolation + error-feedback 8-bit
  quantiser). If FVMV / Majesco carts use the same Direct Sound stage it belongs in
  gbav-common's "GBA output stages".
- `le16` / `le32` bounds-checked readers.
- Test-side: the manifest readers in `tests/common/mod.rs` (frame / audio manifests,
  streams.json digest) shell out to `gzip -dc`; a shared inflate in gbav-common (the
  Hydrogen lane needs DEFLATE anyway) would remove that.

gbav-common change requests:
1. `Codec::video/audio(rom: &[u8], ..)` return owned decoders, which forces a copy of the
   stream (53 MB of video for Shrek). Suggest `rom: Arc<[u8]>` (or a lifetime-generic
   `type Video<'a>`).
2. `AudioDecoder::last_pcm() -> &[i16]`: VX samples exceed i16 (Shrek 2 Rev 5 reaches
   -113040..119586; the 8-bit stage clamps them later). Exact PCM needs i32: suggest
   `&[i32]` or an extra `last_pcm_i32`.
3. `Frame::bgr555() -> Vec<u16>` allocates per frame; add `bgr555_into(&mut [u16])`.
4. `AudioDecoder` has no seek; VX (and probably others) can cold-start audio at seek points.
5. Optional: chapters in `StreamInfo` (VX has a chapter table; every chapter is a seek point).

Workspace note: adding the `gbav-common` dependency changes `Cargo.lock`; that file is
outside this crate, so it is left uncommitted on this branch (cargo regenerates it).
