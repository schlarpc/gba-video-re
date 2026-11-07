# GBA Video: the players and codecs of every Game Boy Advance Video cartridge

Game Boy Advance Video cartridges (2004-2006) carried TV episodes and feature films on a GBA
cartridge. Four different video systems from two companies are behind the 47 cartridges in the
No-Intro database. This repository documents all of them from the cartridges' own code, and has
a reference decoder for each that is **bit-exact on every frame and every audio block of every
stream we could check** against the cartridge running in an emulator.

**The cart list is in [docs/carts.md](docs/carts.md)**: every No-Intro entry with its family,
player build, streams, measurements and verification status.

**No ROM data or video content is included.** You bring your own dumps; see
[Bring your own ROM](#bring-your-own-rom).

## The GBA Video ecosystem

| family | maker | cartridges | codec in one line |
|---|---|---|---|
| [**VX** (VX++ and VXGB)](docs/vx/README.md) | Actimagine (the predecessor of Mobiclip) | *Shrek*, *Shark Tale*, *Shrek 2* (Rev 5 and Rev 6), *Shrek + Shark Tale*: the 64 MiB movie carts | 16x16 macroblocks with a partition tree, integer-pel motion compensation from 3 references, H.264-style 4x4 transform; VLC (VX++) or CAVLC (VXGB) residuals; LPC/pulse audio |
| [**FVMV**](docs/fvmv/README.md) | Actimagine codec and player, published by Nintendo | *Pokemon* Volumes 1-4 | pixel-domain block codec on RGB555: 8x8 cells split down to 1x2, copy / MV / colour-offset / fill / pattern modes, no transform; stripped GSM 06.10 audio |
| [**Hydrogen**](docs/hydrogen/README.md) | Majesco (the "Hydrogen" library) | 13 retail carts (Nickelodeon, Cartoon Network, Sonic X, Yu-Gi-Oh!, ...) and the *Sonic X Vol 2* prototype | vector quantisation (1 byte per 4x4/4x3/3x3/4x2 block, 256-entry YCbCr codebook per segment) under a DEFLATE variant; Majesco ADPCM audio |
| [**ADS**](docs/ads/README.md) | Majesco (the "ADS" engine) | 19 carts (Cartoon Network, Disney, Nicktoons, Dragon Ball GT, ...) plus 2 Hydrogen-engine carts that use the ADS codec | the same VQ layer under LZMA; Majesco ADPCM audio |
| [**AM3**](docs/am3/README.md) (partial) | Actimagine | the Japanese Advance Movie Adapter with SmartMedia movie cards; not a cartridge, not in the DAT | only the adapter's firmware loader is documented; the player and codec are on the cards, and no card has been dumped |

The Hydrogen and ADS families share the VQ frame format and the ADPCM audio decoder; they differ
in the container and the compression layer ([docs/majesco](docs/majesco/README.md)). The VX and
FVMV players share Actimagine's GBA audio output stage (4x interpolation and noise shaping to
65,536 Hz), but their codecs are unrelated. A comparison of all families is
[Table 3 of docs/carts.md](docs/carts.md#table-3-families-compared).

### Verification

<!-- BEGIN gen_tables:summary -->
| family | carts (No-Intro) | streams | hours | video frames verified | audio blocks verified | docs |
|---|---|---|---|---|---|---|
| [VX](docs/vx/README.md) | 7 of 7 | 10 | 11.9 | 299,137 / 299,137 | 5,055,621 / 5,469,139 | [vx](docs/vx/README.md) |
| [FVMV](docs/fvmv/README.md) | 4 of 4 | 8 | 2.8 | 80,972 / 80,972 | 648,427 / 648,427 | [fvmv](docs/fvmv/README.md) |
| [Hydrogen](docs/hydrogen/README.md) | 14 of 14 | 65 | 10.0 | 430,822 / 430,822 | 572,820 / 572,820 | [hydrogen](docs/hydrogen/README.md) |
| [ADS](docs/ads/README.md) | 21 of 22 | 96 | 14.5 | 621,421 / 621,462 | 51,791 / 51,793 | [ads](docs/ads/README.md) |
| **all** | 46 of 47 | 179 | 39.2 | 1,432,352 / 1,432,393 | 6,328,659 / 6,742,179 | [carts.md](docs/carts.md) |
| [AM3](docs/am3/README.md) (adapter + SmartMedia cards) | not in the DAT | - | - | firmware loader only | - | [am3](docs/am3/README.md) |
<!-- END gen_tables:summary -->

For every stream, the ground truth is a whole-stream capture of the cartridge in a headless
mGBA: the decoder's output buffer or the framebuffer the blit writes, hashed per frame, and the
audio as the cartridge decodes it, hashed per block. Each family's manifests in
[verify/](verify/) hold only those hashes. In addition, each family's decoder routines run in
unicorn from a savestate, as an oracle for differential fuzzing of the reference decoders.
The few counts that are not all-equal are explained in Table 1 of [docs/carts.md](docs/carts.md);
none of them is a decoder error.

The Rust decoders reproduce all of it. `gbav verify` over the 46 available carts checks every
frame and every audio unit of the 179 streams against `verify/` in about 50 seconds on 8 cores:

| family | carts | streams | frames | audio units (per-block manifests) | audio segments only |
|---|---|---|---|---|---|
| VX | 7 | 10 | 299,137 / 299,137 | 5,055,621 / 5,055,621 + 413,518 known cart-side (Shrek 2 Rev 5) | 1,323 / 1,323 |
| FVMV | 4 | 8 | 80,972 / 80,972 | 648,427 / 648,427 | 342 / 342 |
| Hydrogen | 14 | 65 | 430,822 / 430,822 | 572,820 / 572,820 calls | 1,242 / 1,242 |
| ADS | 21 | 96 | 621,421 / 621,421 + 41 known cart-side (Noah proto) | 51,791 / 51,791 | 1,773 / 1,773 |

`cargo test --release` runs the same checks per crate, plus cold starts at every seek point, and
with `GBAV_CDIFF=1` differential fuzzing against the C reference decoders in `reference/`.

## Repository layout

| path | contents |
|---|---|
| [docs/](docs/) | Per family (`vx`, `fvmv`, `hydrogen`, `sonicx2-proto`, `ads`, `majesco`, `am3`): `README.md` (overview, results, open issues), `player.md`, `format.md` (reimplementation grade), `perf.md`, `prior-art.md`, `features.md`, `stats.json`. [docs/carts.md](docs/carts.md) is the generated cart table |
| `reference/<family>/` | The C99 reference decoders: `vx/decoder` (vxdec), `fvmv` (fvmvdec), `hydrogen` (hydec, hystat), `sonicx2-proto` (sx2dec), `ads` (adsdec), and `majesco` (VQ + ADPCM shared by Hydrogen and ADS). Tables that are not standard are generated from your ROM at build time |
| `crates/` | The Rust workspace: `gbav-common` (decoder traits, md5, shared output stages), `gbav-rom` (cart identification, family classifier, player build id, embedded No-Intro table), one crate per codec (`gbav-vx`, `gbav-fvmv`, `gbav-majesco`, `gbav-hydrogen`, `gbav-ads`) the `gbav` command line (`gbav-cli`), and `gbav-testkit` (test support: ROM lookup, env gates, the C reference decoders loaded for differential tests) |
| `tools/<family>/` | Python and emulator tooling: mGBA capture harnesses, unicorn oracles, fuzzers, profilers, verify and stats scripts. `tools/common/roms.py` finds your ROMs; `tools/gen_tables.py` regenerates the tables; `tools/mgba/` and the family `patch` files are mGBA patches |
| `verify/<family>/` | Hash manifests, hashes only: `<cart>_<stream>.frame_md5.csv.gz` (every frame), `.audio_seg_md5.csv.gz` (the audio in about 30-second segments) and, where present, `.audio_md5.csv.gz` (every audio block; large, see [Manifests](#manifests)). [verify/carts.csv](verify/carts.csv) maps each cart's short id to its sha1 and No-Intro name; [verify/known.csv](verify/known.csv) lists the frames and blocks where the cart itself shows something else than the stream (an emulated audio failure, a stale codebook) |

## Build and verify

The Rust workspace has no dependencies outside the standard library:

```sh
cargo build --release
export GBAV_ROMS=/path/to/your/roms
cargo test --release            # unit tests, plus every cart in $GBAV_ROMS against verify/
target/release/gbav list ROM                       # family, player build, No-Intro name, streams
target/release/gbav decode ROM --stream S --out DIR [--start-frame N --count M] [--png|--raw] [--wav]
target/release/gbav verify $GBAV_ROMS              # whole-suite verification against verify/
target/release/gbav stats ROM
```

Without `GBAV_ROMS` the ROM-based tests skip themselves. The C reference decoders build with
`make -C reference/<family>` (gcc, Python 3); each family README has its verify command, e.g.
`python3 tools/fvmv/verify.py`. The unicorn oracles and fuzzers need the `unicorn` and
`capstone` Python packages; the capture tools need the mGBA libretro core
(`nix build nixpkgs#libretro.mgba`), and the profilers a core built with one of the patches.

### Manifests

Every frame manifest has one md5 per frame. Audio comes in two forms. The per-block manifests
(`*.audio_md5.csv.gz`) hash every audio block (VX: every 512-byte AFrame output) or every
player call (Hydrogen); for the VX movies alone they are 120 MB of incompressible hashes, so
they may be left out of a published copy. The segment manifests (`*.audio_seg_md5.csv.gz`) are
always present: each row hashes about 30 seconds of the same output and names the blocks it
covers. They were made with `gbav make-segments` from the per-block manifests and contain only
blocks that matched them. Verification (`cargo test`, `gbav verify`) uses the per-block manifests
when they are there and the segments otherwise; a mismatch is then located to a 30-second
segment instead of a block.

To regenerate [docs/carts.md](docs/carts.md) and the summary above after changing a
`stats.json` or the No-Intro table: `python3 tools/gen_tables.py` (`--check` in CI).

## Bring your own ROM

Nothing derived from a cartridge's bytes is in this repository except hashes: no ROM data, no
decoded frames or audio, no decompiler output. Where a decoder needs a table that is not a
published standard, it is read from your ROM at build time (C) or at run time (Rust).

Put your dumps in one directory and set `GBAV_ROMS` to it. Name the files as No-Intro does
(`Game Boy Advance Video - Shrek (USA) (Rev 6).gba`) or by the short ids in
[verify/carts.csv](verify/carts.csv) (`shrek-rev6.gba`). `gbav list` tells you whether a dump
matches a No-Intro entry. It also recognises an overdump whose first 32 MiB match an entry,
such as the 64 MiB dumps of *Disney Channel Collection Vol 2 (Rev 5)*.

## Prior art and credits

These formats were studied before this repository, above all by **quatric**:

- ["The Mobiclip Formats"](https://gist.github.com/quatric/3e2f0caa5c22a9a8b24d9cf1ffdfe860),
  section 8: VX/VX++/VXGB, FVMV, ADS and Hydrogen;
- [quatric/mobipeg](https://github.com/quatric/mobipeg): notes (`doc/gba_video_vxpp.md`,
  `gba_video_vxgb.md`, `gba_video_fvmv.md`, `gba_video_ads.md`) and FFmpeg-based demuxers and
  decoders for all four cartridge families (`libavcodec/vx.c`, `vx_audio.c`, `fvmvdec.c`,
  `majesco.c`, `adsgbavideo.c`, `adsgbaaudio.c`, `adslzma.c`, `libavformat/gbavx.c`,
  `gbavideo.c`). For FVMV, mobipeg runs the cartridge's own ARM routines under unicorn instead
  of reimplementing them.

Their work covered the VX container, bitstream grammar, prediction modes, residual coding,
transform and audio codec; the ADS and Hydrogen containers, LZMA parameters, VQ layout and
audio; and the FVMV container. This repository's first VX grammar walker took its mode semantics
from mobipeg; everything else was used as hints and checked against the cartridges' ARM code.
mobipeg's VX decoder gives pictures identical to ours on four of the nine VX streams it was tested
on; two bugs in its frame wrapper break the other five ([docs/vx/prior-art.md](docs/vx/prior-art.md)).
What each family adds or corrects is listed element by element in its `prior-art.md`.

Other prior art:

- VX audio is the DS VX codec, implemented earlier by
  [CharlesVanEeckhout/actimagine](https://github.com/CharlesVanEeckhout/actimagine) and in
  mobipeg's `vx_audio.c`; the DS VX video codec, whose residual coding VXGB shares, goes back to
  Gericom's FFmpeg decoder patch (2021). The VX++ coefficient code is the table in FFmpeg's
  `mobiclip.c` (Florian Nouwt, Adib Surani, Paul B Mahol).
- The [MultimediaWiki page](https://wiki.multimedia.cx/index.php/Actimagine_Video_Codec) on the
  DS codec, and Kostya Shishkov's
  [2025 note](https://codecs.multimedia.cx/2025/08/a-quick-glance-at-another-bunch-of-codecs/)
  on the `VXGB` magic.
- AM3: shonumi's ["Edge of Emulation: Advance Movie Adapter"](https://shonumi.github.io/articles/art28.html),
  his Advance Movie Adapter technical documentation and card dumping guide, and the AM3 support
  in GBE+.
- The emulator ground truth relies on [mGBA](https://mgba.io), including its emulation of the
  Matrix mapper of the 64 MiB carts ("Dumping the Undumped", mgba.io, 2015), and on
  [unicorn](https://www.unicorn-engine.org/) and capstone for the oracles.
- The cart list follows the [No-Intro](https://www.no-intro.org) *Game Boy Advance (Video)* DAT.

What this repository adds, across the families: the player programs (boot, memory maps, timers
and interrupts, A/V pacing, blitters and output stages), reimplementation-grade specifications
including the corner cases that make decoders bit-exact, whole-stream verification of 179
streams against an emulator, differential fuzzing against the cartridges' own code, per-stream
CPU and resource measurements, and an analysis of every player build.

## Licence

The code and documentation are under the MIT licence; see [LICENSE](LICENSE). The exceptions are
the mGBA patches (`tools/mgba/*.patch`, `tools/vx/perf/patch/*.patch`,
`tools/hydrogen/emu/mgba-cb-on-triage-prof.patch`, `tools/ads/emu/mgba-hooks.patch`): they
modify mGBA and are under mGBA's licence, the Mozilla Public License 2.0. Each carries an SPDX
header. The cartridges, their programs and their video content belong to their owners and are
not included.
