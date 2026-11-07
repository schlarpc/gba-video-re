# gbav-fvmv port notes

Rust port of the FVMV lane's reference C decoder (`reference/fvmv/fvmvdec.c`, `fvmvaudio.c`): the
Actimagine FVMV video codec, the GSM 06.10-style audio and the 4x interpolation + noise-shaped
8-bit output stage of the *Pokemon* GBA Video carts (Vol 1-4, game codes MPAE/MPBE/MPCE/MPDE).

## Crate layout

| Module | Contents |
|---|---|
| `container` | `Header`, `Record`, `walk`, `seek_points`, `find_streams` |
| `video` | `Decoder`: the whole-EWRAM model, `decode_frame` / `decode_frame_at`, `Stats` (same counters as the C `fvmv_dec`) |
| `audio` | `Synth` (GSM-style RPE-LPC), `AudioState` (synth + `gbav_common::output::OutputStage`); tables computed from the GSM formulas |
| crate root | `Stream`, `Video`, `Audio`, `FvmvFrame`, `Fvmv` (implements `gbav_common::Codec`) |

Stream ids are `"0"` and `"1"`: the index among the ROM's `FVMV` headers in ROM order, the
same as the C CLI's `--stream` and the manifest names `<short id>_<stream>` in `verify/fvmv/`
(`pokemon1_0` .. `pokemon4_1`).

No ROM bytes and no generated tables: the MV table, the `/3` table and the two GSM tables
are all formulas (checked equal to the ROM). This family needs no ROM-derived tables at all.

## Interface

- `Stream` holds an `ArcSlice` of the shared ROM (`gbav_common::Rom`); `Video` and `Audio`
  share the `Stream`, so no stream bytes are copied.
- `FvmvFrame`: `manifest_bytes()` = the 240x160 u16 LE buffer with bit 15 kept;
  `bgr555_into()` clears bit 15.
- `Audio: AudioDecoder`: one block = one 40-byte GSM block. `key()` = (frame, block within
  that frame), the manifest's `frame,block` columns. `parts() = 2`: part 0 = the 256 int32
  synthesis samples as LE bytes (`md5_synth`), part 1 = the 1024 s8 PCM bytes (`md5_pcm`).
  `pcm()` = the exact int32 synthesis (it can exceed 16 bits; the cart never saturates).
  Byte buffers live in the decoder; no per-block allocation.
- `Video::seek` / `Audio::seek` are cold starts at a seek point (all 994 are true keyframes,
  so video is exact). The player itself keeps its audio state across seeks and episodes:
  `Audio::reposition(frame)` moves without touching the state, `Audio::with_state` carries a
  state from one episode into the next.
- `StreamInfo::title` is `None` (the menu titles are images; episode names are in
  `docs/fvmv/format.md` section 1). `chapters` is empty: the container has seek points but
  no chapter table.
- Errors: `Video::next_frame` returns `Error::Undefined` if a reader runs past the payload,
  which never happens in the carts. The low-level `Decoder::decode_frame_at` follows the C
  decoder exactly: reads past the data return 0, are counted in `Stats::err`, and decoding
  continues.

## Differences from the C reference

- `container::walk` also rejects a record whose audio payload runs past the record area.
  The C walker does not check this. No difference on the carts.
- The C CLI's `--start-frame N` starts at the previous segment's start (`rec[N].back`).
  `Video::seek` instead requires an exact seek point.

## Verification

All gated on env vars; each test prints a skip notice and passes when they are unset.

| Test | Needs | Checks |
|---|---|---|
| `tests/manifests.rs` | `GBAV_ROMS` (manifests from `verify/fvmv/`, or `GBAV_VERIFY`) | every frame (16-bit md5 and bit-15-cleared md5) of all 8 episodes from power-on through the `Codec` traits; every audio block (synth and PCM md5, frame/block key) against the per-block audio manifest when it is present, else the ~30 s segment manifest; then a cold start at every seek point, decoding to the next one, which covers every frame again |
| `tests/differential.rs` | `GBAV_CDIFF=1` (C sources in `reference/fvmv/`, or `GBAV_CREF`), `cc`; `GBAV_ROMS` for real frames | the C reference is built with `tests/cref/harness.c` and driven over a pipe; the generators are a port of `tools/fvmv/fuzz.py` |

```sh
GBAV_ROMS=/path/to/roms GBAV_CDIFF=1 cargo test --release -p gbav-fvmv -- --nocapture
```

Results (2026-11-07, after the move to the shared gbav-common interface):

- Manifests: **80,972 / 80,972 frames** (all 16 bits, and with bit 15 cleared) and
  **648,427 / 648,427 audio blocks** (synth and PCM, per-block manifest) bit-exact.
  **994 / 994 seek points** cold-started, covering 80,972 / 80,972 frames again. 12.6 s on
  8 threads.
- Differential fuzz against C: 3,000 video cases (1,000 random, 1,000 bit-flipped real,
  1,000 real), with the whole EWRAM, the return code and all 143 statistics counters
  compared. They hit all 88 (size, mode) pairs and include random buffer placement and the
  real layout with accesses outside EWRAM. Also 50,000 random and 32,033 real audio blocks,
  with synth, PCM and state compared after every block, and 2,000 output-stage runs (now
  `gbav_common::output::OutputStage`). **0 mismatches.** A planted bug (per-pixel instead of
  pixel-pair carry in copy + offset) gave 248 mismatches in 300 cases, so the harness does
  catch this kind of error.
- Earlier timing (before the shared streaming md5), all 8 episodes, decode only, host
  loaded: Rust 17.5-19.9 s user CPU vs C (`-O2`) 30.6 s. `examples/fvmv_bench.rs`
  reproduces the Rust side.
