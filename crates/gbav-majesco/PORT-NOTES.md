# Port notes: Majesco lane (gbav-majesco, gbav-hydrogen, gbav-ads)

Rust port of the Majesco reference decoders (`tmp/suite/{majesco-shared,hydrogen,ads,sonicx2-proto}`).
Pure Rust, no dependencies outside the workspace, no `unsafe` outside the test harness.

## Crates

| crate | contents |
|---|---|
| `gbav-majesco` | `vq` (segment word, unpredict, convert with dither and gain, blit for all 8 modes, line 159 as `Row159::{Palette, Untouched}`), `adpcm` (both context-table codings, `Output::{Clip, Wrap}`), `audio` (type-2 resource, `TrackDecoder`), `VqFrame` |
| `gbav-hydrogen` | `container` (container scan, video header, segments), `engine` (player tables and variant detection), `inflate` (cart-exact Inflate), `HyVideo`, `Hydrogen: Codec` |
| `gbav-ads` | `archive` (SFCD, `.mmstr`, Settings, Hydrogen packaging), `lzma` (fixed lc/lp/pb, ring output), `AdsVideo`, `Ads: Codec` |

## Codec semantics

- Stream ids: Hydrogen = container index in ROM order (`"0"` is the logo). ADS = SFCD member
  name with spaces as underscores (`movie_1`, `logo_majesco`, `jingle`), or `res<N>` on the two
  Hydrogen-packaged LZMA carts. These are the manifest names.
- `Frame::manifest_bytes` = the framebuffer lines the blit wrote: all 160 lines (row 159 filled
  from the zero BG palette), except 159 lines in modes 2..5 on the Sonic X Vol 2 proto, whose blit
  never writes row 159.
- `AudioDecoder::next_block` = one coded block (16458 s8 samples). The Hydrogen manifests hash the
  player's calls (4096, then 1371/1372 split at the 4096-byte ring wrap); use
  `TrackDecoder::decode(&mut [i8])` for arbitrary sizes. `last_pcm` is the s8 output `<< 8`.
  A track's last block can end inside the padding: `next_block` returns it short and then `None`.
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

## Requests for gbav-common

1. `md5::digest` allocates a `Vec` per 64-byte block and copies the input; hashing ~80 GB of
   framebuffers in verification needs a streaming version (the tests carry one in
   `gbav-majesco/tests/support/mod.rs`, cross-checked against `gbav_common::md5`).
2. Hoist candidates: `util::{rd16, rd32, ArcSlice, deobfuscate}`; the test support module
   (env gates, PRNG, manifest reader via `gzip -dc`, `par_map`, and `CLib`, which compiles the
   C reference into a shared library and loads it with `dlopen`).
3. The `Codec` trait methods take `&[u8]` without a lifetime, so decoders copy the stream bytes
   they need (a few MB). A `rom: Arc<[u8]>` parameter would avoid the copy.
4. `AudioDecoder::next_block` has no way to express the Hydrogen manifests' call sizes; a
   `decode(n)` method on the trait would let the CLI verify them generically.

`Cargo.lock` in the workspace root changes because the three crates now depend on each other.

## Verification (GBAV_ROMS, GBAV_CREF=/home/schlarpc/re-shell/tmp/suite)

```
cargo test --release -p gbav-majesco -p gbav-hydrogen -p gbav-ads -- --nocapture
```

| test | result |
|---|---|
| `gbav-hydrogen --test verify` (13 carts + Sonic X 2 proto, 65 streams) | 430,822 / 430,822 frames, 572,820 / 572,820 audio calls/blocks (590.7 M samples), 9,982 segment cold starts, 35,916 audio-block cold starts |
| `gbav-ads --test verify` (19 ADS carts + Jimmy + Noah, 96 streams) | 621,421 / 621,462 frames + the 41 Noah stale-codebook frames (all equal to adsdec and all reproduced with the codebook 8 chunks earlier), 51,791 / 51,791 audio blocks, 15,536 chunk cold starts, 51,793 audio-block cold starts |
| `gbav-ads --test classify` (all 46 ROMs) | 14 Hydrogen, 21 ADS, no overlap, VX/FVMV carts claimed by neither |
| `gbav-majesco --test diff_c` | vq.c (1,200 random codebooks/frames, all 8 modes, gains and biases), adpcm.c (300 random streams in random call sizes, both codings, overruns at the same sample), sx2_adpcm.c (300 resources, clip and wrap) |
| `gbav-hydrogen --test diff_inflate` | hyinflate.c: 1,800 encoded (stored/fixed/dynamic) and corrupted streams in random request sizes, same offsets, bytes, error class (ok 1107 / err1 471 / err2 84 / err3 138) and end-marker check; stored-block quirks 1 and 2 hit in 499 / 103 runs; 600 real and corrupted All Grown Up blobs |
| `gbav-ads --test diff_lzma` | lzma_dec.c: 600 random streams, 600 real and corrupted blobs (3 carts), random slice sizes, same error class and ring after every call |
