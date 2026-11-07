# gbav-fvmv port notes

Rust port of the FVMV lane's reference C decoder (`fvmvdec.c`, `fvmvaudio.c`): the
Actimagine FVMV video codec, the GSM 06.10-style audio and the 4x interpolation + noise-shaped
8-bit output stage of the *Pokemon* GBA Video carts (Vol 1-4, game codes MPAE/MPBE/MPCE/MPDE).

## Crate layout

| Module | Contents |
|---|---|
| `container` | `Header`, `Record`, `walk`, `seek_points`, `find_streams` |
| `video` | `Decoder`: the whole-EWRAM model, `decode_frame` / `decode_frame_at`, `Stats` (same counters as the C `fvmv_dec`) |
| `audio` | `Synth` (GSM-style RPE-LPC), `AudioState` (synth + output stage); tables computed from the GSM formulas |
| `util` | `OutputStage`: 4x interpolation + noise-shaped 8-bit quantiser (generic, see below) |
| crate root | `Stream`, `Video`, `Audio`, `FvmvFrame`, `Fvmv` (implements `gbav_common::Codec`) |

Stream ids are `"0"` and `"1"`: the index among the ROM's `FVMV` headers in ROM order, the
same as the C CLI's `--stream` and the manifest names `<code>_<stream>`.

No ROM bytes and no generated tables: the MV table, the `/3` table and the two GSM tables
are all formulas (the lane checked them equal to the ROM). This family needs no
ROM-derived tables at all.

## Hoisting candidate for gbav-common: `util::OutputStage`

Actimagine's output stage is the same algorithm in FVMV (IWRAM 0x03001d18) and VX audio.

```rust
pub struct OutputStage { pub err: i32, pub fb: i32, pub prev: i32 }  // zero at power-on
impl OutputStage {
    pub const fn new() -> Self;
    pub fn quantise(&mut self, v: i32) -> i8;              // one sample, updates err/fb
    pub fn process(&mut self, input: &[i32], out: &mut [i8]); // out.len() >= 4 * input.len()
}
```

Input is int32 in 16-bit scale (one output step = 256); arithmetic wraps at 32 bits, as on
the ARM. Each input `x` produces `(3p+x)>>2, (p+x)>>1, (3x+p)>>2, x`, each quantised as
`q = clamp((v + 0x80 - fb) >> 8, -128, 127)`, `err = (q << 8) - v`,
`fb = f - (f >> 4)` with `f = err_old + 2*err`. It is checked against the C
`fvmv_audio_output` on 2,000 runs of full-range random int32 input. The VX lane should check
whether its input samples are int32 with the same scale before switching to it.

## Notes on the gbav-common traits

- **Owned decoders.** `Codec::video(rom: &[u8], ..) -> Self::Video` gives no lifetime to tie
  the decoder to the ROM, so `Stream` copies the stream (about 15 MB) into an `Arc<[u8]>`.
  `Video` and `Audio` share it. A lifetime on the associated types (a GAT
  `type Video<'a>`) would avoid the copy; not needed for correctness.
- **Two audio hashes.** The audio manifest has two hashes per block: the 256 int32
  synthesis samples and the 1024 s8 PCM bytes. `AudioDecoder::next_block` returns the PCM
  bytes (what the cart plays). The synthesis bytes come from the inherent
  `Audio::synth_bytes()`. The block's (frame, block) key comes from `Audio::decode_next()` /
  `last_position()`. The trait might want a `manifest_parts()` or a position accessor.
- **`last_pcm`.** The FVMV synthesis never saturates, so its int32 output can exceed 16
  bits. `last_pcm()` returns it saturated to i16. The exact values are in `Audio::synth()`.
- **Seeking.** `VideoDecoder::seek` (cold start) zeroes the EWRAM model and sets the frame
  counter to the seek point, which keeps the player's buffer parity. All seek points are
  true keyframes, so the result is exact. The audio state is never reset: the player keeps
  it across seeks and even across episodes. `Audio::seek(frame)` repositions without
  touching the state, as the player does. A fresh `Audio` starts at power-on state.
  `Audio::with_state` lets a caller carry state from one episode into the next.
- **`StreamInfo::title`** is `None`. The menu titles are images in the ROM. The episode
  names are in `docs/format-fvmv.md` section 1, if the CLI wants a table keyed by game code
  and stream.
- **Errors.** `Video::next_frame` returns `Error::Undefined` if a reader runs past the
  payload, which never happens in the carts. The low-level `Decoder::decode_frame_at`
  instead follows the C decoder exactly: reads past the data return 0, are counted in
  `Stats::err`, and decoding continues.

## Differences from the C reference

- `container::walk` also rejects a record whose audio payload runs past the record area.
  The C walker does not check this. No difference on the carts.
- The C CLI's `--start-frame N` starts at the previous segment's start (`rec[N].back`).
  `Video::seek` instead requires an exact seek point.
- Doc fix for the lead: there are **994** seek points (93+96+97+95+146+162+153+152, the same
  as `stats.json`), not 992 as `docs/format-fvmv.md`, `REPORT.md` and `prior-art-fvmv.md`
  say.

## Verification

All gated on env vars; each test prints a skip notice and passes when they are unset.

| Test | Needs | Checks |
|---|---|---|
| `tests/manifests.rs` | `GBAV_ROMS`, `GBAV_FVMV_MANIFESTS` (or `crates/gbav-fvmv/manifests/`) | every frame (16-bit md5 and bit-15-cleared md5) and every audio block (synth md5 and PCM md5) of all 8 episodes from power-on, through the `Codec` traits. Then a cold start at every seek point, decoding to the next one, which covers every frame again |
| `tests/differential.rs` | `GBAV_FVMV_CREF` (dir with `fvmvdec.c`, `fvmvaudio.c`, headers), `cc`; `GBAV_ROMS` for real frames | the C reference is built with `tests/cref/harness.c` and driven over a pipe; the generators are a port of the lane's `fuzz.py` |

The manifests are not committed (about 28 MB of gzip, mostly audio). They live in the FVMV
lane at `src/manifests/`. The tests read them with `gzip -dc` (no crates.io deps).

```sh
GBAV_ROMS=.../roms GBAV_FVMV_MANIFESTS=.../fvmv/src/manifests GBAV_FVMV_CREF=.../fvmv/src \
  cargo test --release -p gbav-fvmv -- --nocapture
```

Results (2026-11-07):

- Manifests: **80,972 / 80,972 frames** (all 16 bits, and with bit 15 cleared) and
  **648,427 / 648,427 audio blocks** (synth and PCM) bit-exact. **994 / 994 seek points**
  cold-started, covering 80,972 / 80,972 frames again. 26 s on 8 threads.
- Differential fuzz against C: 3,000 video cases (1,000 random, 1,000 bit-flipped real,
  1,000 real), with the whole EWRAM, the return code and all 143 statistics counters
  compared. They hit all 88 (size, mode) pairs and include random buffer placement and the
  real layout with accesses outside EWRAM. Also 50,000 random and 32,033 real audio blocks,
  with synth, PCM and state compared after every block, and 2,000 output-stage runs. **0
  mismatches.** A planted bug (per-pixel instead of pixel-pair carry in copy + offset)
  gave 248 mismatches in 300 cases, so the harness does catch this kind of error.
- Wall time, all 8 episodes, decode only. The host was loaded (load average about 17 on 8
  cores), so CPU time is the steadier figure:

  | | Rust | C (`-O2`) |
  |---|---|---|
  | video + audio, user CPU | 17.5-19.9 s | 30.6 s (24.2 video + 6.4 audio) |
  | video + audio, best wall | 26.8 s | 55.6 s |
  | with md5 of every frame/block, wall | 73.6 s | 138.2 s |

  The md5 run is dominated by `gbav_common::md5`, which allocates per call and per chunk.
  It is worth optimising before the CLI's `verify` uses it.
  `examples/fvmv_bench.rs` reproduces the Rust side.
