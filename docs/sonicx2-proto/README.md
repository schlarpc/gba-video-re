# Sonic X - Volume 2 (USA) (Proto): a prototype Hydrogen cart

*Game Boy Advance Video - Sonic X - Volume 2 (USA) (Proto)*, dev file name
`Test6_Sonic_2_17March2004.gba`, 32,022,872 bytes, sha1 `00257021d9c4444e96d96331fb6eb07e756f53cc`.

It is an ordinary **Majesco Hydrogen** cart (player build `96a25377cb75`, 17 March 2004), an early
common ancestor of the shipped Hydrogen builds; see [../hydrogen/README.md](../hydrogen/README.md)
for the family. The static classifier reported it as UNKNOWN; that was a false negative caused by a
rewritten blit (the 3x3 path draws one block at a time and there is no row-159 fill). The dump is
complete: it ends at the end of the IWRAM image.

## Streams and results

| stream | frames | geometry | video | audio |
|---|---|---|---|---|
| Episode 103 | 13,828 | 3x3 (mode 5) | 13,828/13,828 bit-exact | 1,153/1,153 blocks |
| Episode 104 | 14,286 | 4x2 (mode 7) | 14,286/14,286 | 1,191/1,191 blocks |
| intro (Majesco DIGITAL VIDEO logo) | 59 | mode 7 | 59/59 | 5/5 blocks |

Video is bit-exact on every frame with both `reference/sonicx2-proto/sx2dec` and the Hydrogen
family decoder `reference/hydrogen/hydec` (their outputs are byte-identical). Audio is bit-exact on
every block with `reference/sonicx2-proto/audio/sx2adec`. The manifests are
`verify/hydrogen/sx2p_{0,1,2}.*` (stream 0 = Episode 103, 1 = Episode 104, 2 = the intro; the indices are the container order that `gbav` uses).

## Documents

| file | contents |
|---|---|
| [player.md](player.md) | the program, boot and screens, engine code bases, function-by-function diff against five shipped builds, dev leftovers and toolchain |
| [format.md](format.md) | ROM layout and file slots, video stream, Hydrogen Inflate (with the stored-block streaming quirks), audio stream, verification |
| [vs-shipped-hydrogen.md](vs-shipped-hydrogen.md) | every decoder-relevant difference from the shipped Hydrogen builds, in one place |
| [perf.md](perf.md) | CPU and resource use on every frame of every stream |
| [prior-art.md](prior-art.md) | element-by-element comparison with quatric's gist and mobipeg |
| [features.md](features.md) | codec feature matrix (proto against shipped Sonic X Vol 1) |
| [stats.json](stats.json) | one record per stream |

## Findings in brief

- Differences from the shipped builds: no audio output clamp (samples wrap: 134 clicks in ep103,
  260 in ep104); 8 brightness levels instead of 11; the 3x3 blit and row 159 as above.
- New format facts: Inflate block type 3 is an end marker; the exact stored-block semantics, plus
  three stored-block streaming bugs in the cart (never triggered by its streams); `{ptr,size}` file
  slots; the stream header word carries fps and VBlanks per frame; the dither is a halved 4x4 Bayer
  matrix; 0xCC filler bytes in resource headers point to an MSVC debug-built encoder.
- Perf: CPU busy 30-36 %; of the busy time, audio is 40-46 %, the blit 29-38 % and video decoding
  20-25 %; no late frames.
- Leftovers: `C:\dev` paths (Hydrogen Library and "MoviePlayer Example"), the ARM ADS toolchain,
  AGBPrint asserts, dead debug code.

## Build and verify

```sh
make -C reference/sonicx2-proto            # sx2dec, libsx2.so and audio/sx2adec
reference/sonicx2-proto/sx2dec ROM --list
reference/sonicx2-proto/sx2dec ROM --stream 1 --count 120 --out frames.bin
python3 tools/sonicx2-proto/verify.py "$GBAV_ROMS/Game Boy Advance Video - Sonic X - Volume 2 (USA) (Proto).gba"
python3 tools/sonicx2-proto/verify.py ROM --hydec reference/hydrogen/hydec   # the family decoder instead
```

sx2dec options: `--list`, `--stream N`, `--start-frame F`, `--count C`, `--out FILE`, `--md5`,
`--brightness K`. The headless mGBA drivers in `tools/sonicx2-proto/emu/` use the triage profiling
core (`tools/mgba/mgba-prof.patch`, MPL-2.0), built into `tmp/mgba/prof/` or given by
`GBAV_MGBA_PROF_CORE`.

## Open issues

1. **Integration (Rust port):** the shared Majesco ADPCM decoder needs a no-clip (wrap) option so
   that this cart can use it instead of its own `sx2_adpcm.c`. Until then the proto's audio is
   bit-exact only through `reference/sonicx2-proto/audio/sx2adec`; the shared decoder differs in
   exactly the 394 wrapped samples.
2. 0xa400 bytes of unidentified data at ROM 0x0e000-0x16000.
3. The main-menu thumbnail path is not reimplemented.
4. Brightness levels other than the default are not verified by capture.
5. The last ~4,100 audio samples of each stream have no ground truth: the cart stops decoding when
   the video ends.
