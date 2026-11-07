# Port notes: Majesco lane (gbav-majesco, gbav-hydrogen, gbav-ads)

Rust port of the Majesco reference decoders (`reference/{majesco,hydrogen,ads,sonicx2-proto}`).
Pure Rust, no dependencies outside the workspace, no `unsafe` outside the test harness.

## Crates

| crate | contents |
|---|---|
| `gbav-majesco` | `vq` (segment word, unpredict, convert with dither and gain, blit for all 8 modes, line 159 as `Row159::{Palette, Untouched}`), `adpcm` (both context-table codings, `Output::{Clip, Wrap}`), `audio` (type-2 resource, `TrackDecoder`), `VqFrame`; re-exports `gbav_common::bytes::{rd16, rd32, ArcSlice, deobfuscate}` |
| `gbav-hydrogen` | `container` (container scan, video header, segments), `engine` (player tables and variant detection), `inflate` (cart-exact Inflate), `HyVideo`, `Hydrogen: Codec` |
| `gbav-ads` | `archive` (SFCD, `.mmstr`, Settings, Hydrogen packaging), `lzma` (fixed lc/lp/pb, ring output), `AdsVideo`, `Ads: Codec` |

## Codec semantics

- Stream ids: Hydrogen = container index in ROM order (`"0"` is the logo). ADS = SFCD member
  name with spaces as underscores (`movie_1`, `logo_majesco`, `jingle`), or `res<N>` on the two
  Hydrogen-packaged LZMA carts. These are the ADS manifest names; Hydrogen manifests are
  `<short>_<index>`, the Sonic X Vol 2 proto's too (`sx2p_0` = Episode 103, `sx2p_1` = Episode 104, `sx2p_2` = intro).
- Decoders keep the shared ROM (`Rom` = `Arc<[u8]>`, via `ArcSlice`); no stream or track bytes are
  copied. `StreamInfo::chapters` comes from the chapter records (segment / chunk start + name).
- `Frame::manifest_bytes` = the framebuffer lines the blit wrote: all 160 lines (row 159 filled
  from the zero BG palette), except 159 lines in modes 2..5 on the Sonic X Vol 2 proto, whose blit
  never writes row 159.
- Audio (`TrackDecoder`): `next_block` = one coded block (16458 s8 samples; a track's last block
  can end inside the padding and is then returned short, after which the track ends);
  `decode_samples(n)` = one player call (the Hydrogen manifests hash the player's calls: 4096,
  then 1371/1372 split at the 4096-byte ring wrap); `manifest_part(0)` = the s8 output of the last
  block or call as bytes; `pcm()` = the same samples as `s8 << 8` (i32; the native output is
  8-bit); `key().index` = block number for `next_block`, call number for `decode_samples`;
  `seek(frame)` = cold start at the block holding video frame `frame` (12 frames per block,
  every block is a restart block), then decode and discard up to floor(frame * 1371.5625).
- `VideoDecoder::seek` accepts segment starts only; `seek_any` decodes forward from the segment.
- fps = 2^24 / (5 * 280896) (`FPS_NUM`/`FPS_DEN`); audio 16384 Hz.

## Values read from the ROM at run time

- dither table (16 bytes before the block-geometry table) and brightness gains;
- ADPCM output stage: the Sonic X Vol 2 proto's loop has no clamp after `asr #6` (searched in
  the IWRAM image; every other Majesco cart has `cmp sl, #0x7f`). This is also used to select
  the proto's row-159 behaviour (the proto build is the only one that differs in both);
- Jimmy Neutron's +51 gain bias: a Thumb `ldr; movs #K; str` to the IWRAM global in the
  brightness builder's literal pool (found only in Jimmy, applies after the logo);
- `BaseBrightness` from the ADS Settings text (CN Edition Platinum (France): +51).

## Deliberate deviations from the C references

- Undefined input is rejected instead of read: more than 256 codebook entries (C overruns
  `out[256][16]`); a codebook shorter than the converter reads (C converts stale buffer bytes);
  index bytes a request did not produce (C blits stale heap memory). All `Error::Undefined`.
- Hydrogen-packaged audio length: the ADS reference passes the entry length plus 4 (it measures
  from the entry word); the Rust ADS decoder keeps that so the hybrids match the ADS manifests.
- The Noah proto intro's 41 stale-codebook frames are decoded as intended (like the C decoder);
  `AdsVideo::segment_vcb` reproduces what the cartridge shows (codebook of the chunk 8 earlier).

## Open request for gbav-common

The ADS and Sonic X proto per-block audio manifests (`block,samples,md5`) hash only the first
`samples` bytes of a stream's last block (as far as the cartridge decoded it before the
stream ended), while `next_block` returns the whole coded block. `manifest::check_audio_blocks`
compares the whole part, so the generic check fails on that last row; the crate tests do the
prefix comparison themselves. `check_audio_blocks` (and `make_segments`) should hash only the
first `samples` bytes when a `samples` column is present.

## Verification (GBAV_ROMS; GBAV_CDIFF=1 for the C differential tests)

```
GBAV_ROMS=... GBAV_CDIFF=1 cargo test --release -p gbav-majesco -p gbav-hydrogen -p gbav-ads -- --nocapture
```

Manifests come from `verify/hydrogen/` and `verify/ads/` (`gbav_common::manifest`, no system
gzip); audio uses the per-block / per-call manifests when present, else the ~30 s segment
manifests. The C references are compiled from `reference/` into the test tmpdir and loaded with
`gbav_testkit::CLib`.

| test | result |
|---|---|
| `gbav-hydrogen --test verify` (13 carts + Sonic X 2 proto, 65 streams) | 430,822 / 430,822 frames, 572,820 / 572,820 audio calls/blocks, 9,982 segment cold starts, 36,375 audio cold starts (every block via `seek_block`, plus `AudioDecoder::seek` at up to 8 seek points per stream) |
| `gbav-ads --test verify` (19 ADS carts + Jimmy + Noah, 96 streams) | 621,421 / 621,462 frames + the 41 Noah stale-codebook frames (all reproduced with the codebook 8 chunks earlier, all equal to `adsdec` built from `reference/`, and exactly the `verify/known.csv` ranges, so `manifest::check_video` passes), 51,791 / 51,791 audio blocks, 15,536 chunk cold starts, 52,477 audio cold starts |
| `gbav-ads --test classify` (all 46 ROMs) | 14 Hydrogen, 21 ADS, no overlap, VX/FVMV carts claimed by neither |
| `gbav-majesco --test diff_c` | `reference/majesco/vq.c` (random codebooks/frames, all 8 modes, gains and biases), `adpcm.c` (random streams in random call sizes, both codings), `reference/sonicx2-proto/audio/sx2_adpcm.c` (clip and wrap) |
| `gbav-hydrogen --test diff_inflate` | `reference/hydrogen/hyinflate.c`: encoded (stored/fixed/dynamic) and corrupted streams in random request sizes, stored-block quirks, real and corrupted All Grown Up blobs |
| `gbav-ads --test diff_lzma` | `reference/ads/lzma_dec.c`: random streams, real and corrupted blobs (3 carts), random slice sizes |

## Rust vs C wall time (whole cart, single thread, best of 3, release; machine shared with other lanes)

| cart | Rust | C |
|---|---|---|
| All Grown Up 1 (Hydrogen), video, 4 streams / 32,317 frames | 1.97 s (`hydrogen_decode_cart --video-only`) | 2.07 s (`hydec --stream 0..3`) |
| All Grown Up 1, audio, 44.3 M samples | 0.84 s | n/a (hydec has no audio path) |
| SRMTHFG (ADS), video, 4 streams / 29,362 frames | 3.16 s (`ads_decode_cart --video-only`) | 3.24 s (`adsdec --stream 0..3`) |
| SRMTHFG, audio, 40.3 M samples | 0.85 s | 1.31 s (`adsdec --wav /dev/null`) |

The C times include process start and ROM load per stream (about 10 ms each).
