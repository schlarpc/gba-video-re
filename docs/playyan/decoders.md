# Play-Yan decoders

Standalone decoders, in Rust (`crates/gbav-playyan`) and C99 (`reference/playyan/<codec>`), for
everything the three cartridges decode: the VC01 firmware's MPEG-4 Part 2, G.726, MP3 and AAC-LC
with their containers and output stages, and the Nintendo MP3 Player's DSP MP3 decoder. Each one
reproduces what the cartridge's own firmware does, quirks included, and is checked against that
firmware rather than against the standards. Rust and C give identical results on every input they
were both run on, corrupted inputs included.

Like the other families, nothing derived from the firmware is in the source except md5s and
addresses: tables that are not published standards are read from your files at run time (Rust)
or generated into gitignored headers at build time (C). The firmware sources are the Play-Yan ROM
(video and music images stored raw), the Play-Yan Micro ROM (LZ77 chunk lists), `playan.fup`
(v1.11, sections with their check bytes stripped) or a raw image; every image is identified by
its md5 ([firmware.md](firmware.md)). The image names used below:

| name | image | name | image |
|---|---|---|---|
| `py_fw0` | Play-Yan video | `py_fw1` | Play-Yan music |
| `mi_fw0` | Micro video | `mi_fw1` | Micro music |
| `fup_sec0` | `playan.fup` (v1.11) video | `fup_sec1` | `playan.fup` music |

## Ground truth

Two kinds of oracle, both running the cartridges' real firmware in our emulators (local, not in
this repository; [README.md](README.md) "Real firmware"):

- **Full-system manifests** ([verify/playyan](../../verify/playyan/README.md)): the real GBA ROM
  drives the real firmware image from power-on and plays one file from the SD card to its end
  record. Every frame the GBA reads through the pixel window, every thumbnail, every headphone
  buffer the AC'97 DMA played and every 8-bit block sent to the GBA is hashed. 61 sets: Nintendo's
  `sample_movie.asf`, the 13 Garage Game ASFs and both MP4 samples on each video image that plays
  them, and `sample_music.mp3` plus four test tones on each music image.
- **Routine oracles** (`tools/playyan/<codec>`): a firmware routine (the MP3 frame decoder 0x222f2,
  the AAC decoder 0xcee8, the G.726 payload decoder 0x1234e, the motion-compensation wrapper
  0x38a8, the frame fit 0x2749a, the NMP DSP's synthesis output) runs in the emulator on our
  inputs: generated test streams, crafted corner cases and randomly corrupted frames. Its outputs
  are compared directly or kept as hash manifests in `verify/playyan/oracle/`.

## Exactness

| codec / stage | firmware | result |
|---|---|---|
| ASF container + MPEG-4 SP video (I/P, 4MV, resync markers, dquant, H.263 and MPEG quantisation, custom matrices, f_code up to 3, not-coded VOPs, intra DC through the TCOEF table) | py_fw0, mi_fw0, fup_sec0 | full-system manifests 29,258 / 29,258 frames; real-firmware frame dumps of 12 crafted streams (176x144, 320x240, 240x192, 96x64, q 1..31) bit-exact; Rust == C on 4,340 frames and 420 corrupted windows |
| 240x160 frame stage: fit to the screen, brightness, RGB565 to the window's BGR555 | all video images | manifests; brightness levels 546 / 552 captured frames (the other 6 are timing: frames sent before the new level is picked up); fit oracle 0 differences on 18 source sizes |
| 60x40 thumbnails | all video images | 92 / 92 manifest rows; the 13 Garage Game hashes in the GBA ROM, on all three images |
| G.726 at 16 / 24 / 32 / 40 kbit/s, including the firmware's payload feed | py_fw0 (and the others through ASF) | routine oracle 136 / 136 payloads (139,344 samples, saturating bursts included); all headphone and 8-bit blocks of the 45 ASF manifest sets; captures at 40 / 24 / 16 kbit/s: headphone 95,200 / 95,200 samples (40k, 24k), 8-bit 287 / 287 blocks at each rate |
| MP3 (MPEG-1 Layer III), headphone PCM | py_fw1, mi_fw1, fup_sec1 | routine oracle on `sample_music.mp3` (5,530 / 5,530 frames) and a LAME corpus of 9 files (1,337 frames; mono, joint, forced M/S, 32-320 kbit/s, VBR, CRC); 11,986 fuzzed frames, 0 differences; all 15 music manifest sets |
| 8-bit GBA speaker stream (music and video) | all images | every block of every manifest set, with the per-image start rule below |
| MP4 container + AAC-LC | mi_fw0, fup_sec0 | manifests: headphone 3,201 / 3,201 buffers, 8-bit 1,057 / 1,057 blocks, frames 2,228 / 2,228 per set; routine oracle on 16 ffmpeg-encoded files (8-48 kHz, mono/stereo, M/S, intensity, PNS, TNS, short windows) on every frame; fuzz: 2,506 corrupted frames equal, 0 different (493 more outside the model in both) |
| Nintendo MP3 Player DSP MP3 (MPEG-1, -2 and -2.5 Layer III) | NMP ROM | DSP oracle: `sample_music.mp3` 22,120 / 22,120 synthesis calls; 16 LAME streams plus 13 crafted ones (intensity stereo, mixed blocks at every LSF rate class): every call; 8-bit speaker stream 56 / 56 blocks on 11 streams at all nine rates; ID3 and length 32 / 32 probe files |

Totals over the 61 manifest sets: 29,258 frames, 92 thumbnails, 41,180 headphone buffers and
30,133 8-bit blocks, all bit-exact.

## What the decoders reproduce

Addresses are in `py_fw0` (video) and `py_fw1` (music) unless noted. The source files document
each point at its site.

### Video (`asf.rs`, `m4v.rs`, `idct.rs`, `pixel.rs`; `reference/playyan/video`)

Departures from ISO 14496-2 that the firmware makes and the decoders copy:

1. **MPEG quantisation weights intra blocks with the non-intra matrix**, default and custom
   matrices alike (proven by a stream with custom matrices). The dequantisation kernel itself reads
   the intra rows correctly in isolation; the decoder's vector register file holds the inter matrix
   there when it runs.
2. **Motion vectors far outside the picture wrap in a 32-pixel window.** The wrapper 0x38a8
   predicts a block whose half-pel position is outside [0, 2W-32] x [0, 2H-32] from a 32x32 window
   (16x16 for chroma): [-16, 16) or [W-16, W+16) with edge replication, otherwise raw memory from
   the position rounded down to 16, read at (mv & 31) / 2. Up to 16 pixels outside this equals
   ISO edge extension; beyond, it wraps. A half-pel read at the last column takes the next row.
3. **4MV**: each 8x8 block is a quadrant of a 16x16 prediction at the macroblock origin with that
   block's vector, clamped (0x3e4c) to origin + mv in [-30, 2W-2] x [-30, 2H-2] half-pels. The
   chroma vector is the sum of the unclamped vectors, rounded with the sign-magnitude ISO table 7-9
   (0x2df00).
4. **AC prediction** scales (p x QPp) / QPc through the 64-entry reciprocal table 0x2c61c: a
   truncating multiply-high, corrected by ±1 when |remainder| >= ceil(QPc / 2), never from 0. DC
   prediction divides with 0x26f0e and a one-step fix. The stored DC predictor is not clipped to 2047.
5. **IDCT**: the firmware's AP-922 variant with the prescale table at 0xf240; the input is
   s16(min(c, 2047) << 4), which wraps.
6. **Dequantisation is 16-bit**: H.263 s16(2LQ ± qadd) with intra levels clipped to ±2048 first;
   MPEG intra trunc(sat16(sat16(L x Q) x W) / 8); MPEG inter per ISO with 16-bit saturation steps.
7. **Intra DC through the TCOEF table** (QP >= intra_dc_vlc_thr, using the QP after the
   macroblock's dquant): the firmware reads no DC at all but still starts the block at scan index
   1, so every event lands one index later; the DC is whatever the block buffer holds.
8. **Coefficients past index 63**: the loop 0x20484 checks the index after storing, so an event at
   index >= 64 is stored at the raster position read from the 64 bytes after the scan table (read
   from the image), possibly in a later block of the macroblock. A non-last event at >= 63 or an
   escape past 63 sets the error flag and the VOP is abandoned after that macroblock; the rest of
   the picture keeps its old content.
9. A not-coded VOP makes the firmware send the previous picture again.

The inter MCBPC table is indexed in FFmpeg's order. Short video header, data partitioning and
interlace are rejected.

**Output.** Frame = brightness(convert(crop or fit to 240x160)), then the window's RGB565 to
BGR555 swap. 240x176 sources are cropped by (H-160)/2 rows. Other sizes go through the fit
0x2749a mode 2, which scales to fill: dh = W x sh / sw; letterbox if dh < H, else crop the
source to H x sh / dh rows, centred. The fit's plane scalers (0x276de and its kernels: 2x2 box,
vertical and horizontal box and bilinear passes, 16/8/4-row kernels, a flooring single-row kernel)
are reproduced exactly, including the byte each row kernel writes past its row when the
destination is not 16-aligned, which lands in the next row or a guard. The brightness b is the
VIDEO_MODE_SET value (0x101..0x2C0, 256 = neutral at start), stored at gp+0x65C by the 0x300
handler and applied to the whole frame by 0x273aa; 0x101 is the identity on RGB565. Thumbnail =
frame 0, crop, 4:1 box filter (reciprocal table 0x2765c), conversion, no brightness.

### G.726 (`g726.rs`; `reference/playyan/g726`)

All four rates, ITU-T tables, with Sun's reference structure. The firmware keeps Sun's 16-bit
`short` intermediates (sezi, sei, se, y, dq, sr, dqsr wrap at 16 bits), which only matters on
saturating input, and clamps the output (sr << 2) instead of wrapping. Codes are read MSB first.

**The feed** is part of the result. Per ASF *payload* (Nintendo's objects are four 400-byte
payloads), n = ((len & ~1) x 8) / bits codes are decoded into buffer k mod 3 of a ring of three
1,152-word buffers, and the block the output stage plays is 2 x ceil(n x bits / 8) words. At 40
kbit/s the last fifth of each block is what the buffer held before; at 24 and 16 kbit/s the block
is cut short.

### MP3, VC01 music image (`mp3/`, `mp3file.rs`, `gbapcm.rs`; `reference/playyan/mp3`)

MPEG-1 Layer III only (the player rejects MPEG-2 files). Firmware behaviour reproduced:
big_values clamped to 288; window-switching region boundaries from min(r0c, 21); sf[21] and the
last short band's scalefactors are 0, so intensity stereo uses position 0 there; a granule with
part3 > 7,968 bits keeps the previous values; Huffman decoding from a bit-expanded heap buffer
without a bit budget in the big-values region, with the count1 end compare signed; the requantiser's
16-value blocks, 8,192-entry |x|^(4/3) table and unbounded negative index for long blocks; the
intensity-stereo rules (positions 7, 15, 23 and negatives skipped, M/S on an illegal band only for
position 7); M/S and alias reduction as high-word products. The IMDCT, window and DCT-32 kernels
are written from their algorithms; their 203 multiply constants are read from the firmware's
vector instruction immediates, so the source holds only their addresses. The file layer
reproduces the ID3v2 / ID3v1 walk, the frame search and the duration.

### AAC-LC (`aac/`, `mp4.rs`; `reference/playyan/aac`)

The video images of the Micro and v1.11 carry a FAAD2-derived AAC-LC decoder on Alphamosaic
fixed-point helpers. This one is written from that firmware code, not from FAAD2. It reproduces:
AudioSpecificConfig parsing; the element rules (at most two SCE/LFE, one CPE, CCE an error, DSE
and FIL skipped, DRC parsed but never applied); the firmware's error codes and the state it keeps
after an error; the first two frames reporting no samples; inverse quantisation q x cbrt[|q|]
(Q12, 1,280 entries) with the 16-bit large-value path; the 9-bit escape prefix; scale factors as
mulhd with a shift; M/S *before* PNS; PNS from the firmware's global LCG (seeded once at boot, so
it carries across files); intensity stereo with an s16 mantissa gain; TNS with a normalised s16
LPC; the filter bank and PCM rounding clip16((x + 512) >> 10). The quantised spectrum lives in
channel 0's time buffer, as in the firmware. The ISO Huffman codebooks give the same symbols as
the firmware's own table scheme (`hcb.h` / `hcb.rs`, standard tables).

### Nintendo MP3 Player (`nmp/`; `reference/playyan/nmp`)

The TI C54x DSP's decoder, from a DSP data memory rebuilt from the ROM's boot table and the TI
`cinit` records (so all tables come from your ROM). MPEG-1, -2 and -2.5 Layer III. Per stage:
Huffman, the requantiser (exponent and mantissa tables below 128, a cubic seed plus three Newton
steps above, gain applied before a two-branch normalise), count1, M/S and intensity stereo
(including the firmware's MPEG-2 slen computation with % 63 / % 15 / % 3), the mixed-block
scalefactor reader that writes past its area, band zeroing, antialias, the IMDCT as 18x18 and 6x6
matrices, windowing and overlap, frequency inversion, the DCT-32 (written by hand from its
even/odd decomposition) and the synthesis window with saturating stores. The 8-bit speaker stream
reproduces the DSP's 17-tap FIR sample-rate converter and second-order section to 44.1 kHz, then
the decimation to 16,384 Hz. ID3 and length follow the DSP's AUDIO_INFO block.

### 8-bit speaker stream (`gbapcm.rs`)

Each sample times the VOLUME_SET value / 32 (truncating, default 28), decimated by keeping a
sample when |(c << 14) / rate| changes (16,384 Hz music; << 13 and 8,192 Hz video), high bytes,
576 + 576 bytes per block. **Start rule**: the original images (py_fw0 0x1b57c, py_fw1 0x33ff6)
test the *previous* stream's rate, which is 0 after power-on, so the first song after boot loses
its first sample; the Micro and v1.11 store the new rate first. py_fw1's speaker stream is
therefore the others' shifted by one sample.

## Build and run

```sh
cargo build --release
export PY_ROMS=/path/to/dir     # Play-Yan (Japan).gba, Play-Yan Micro (Japan).gba,
                                # Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba, playan.fup
export PY_MEDIA=/path/to/media  # Nintendo's sample files, one directory per download (below)
cargo test --release -p gbav-playyan
target/release/gbav list   FILE --firmware "$PY_ROMS/Play-Yan (Japan).gba"
target/release/gbav decode FILE --firmware FW... --out DIR [--png|--ppm|--raw] [--wav] [--gba-audio]
                          [--brightness N] [--volume N] [--start-frame N] [--count M] [--image NAME]
target/release/gbav verify FILE... --firmware FW... --manifests verify/playyan [--volume N] [--prev-rate HZ]
```

`FILE` is an ASF, MP4 or MP3 file; `--firmware` may be given several times, and `--image` picks
one image when the sources hold several. `--raw` writes each frame as the GBA receives it (240x160
BGR555), `--wav` the headphone PCM, `--gba-audio` the 8-bit blocks.

Test gates (`crates/gbav-testkit`): `PY_ROMS` (default `GBAV_ROMS`); `PY_MEDIA`; `PY_VERIFY`
(default `verify/playyan`); `PY_VERIFY_MEDIA`, colon-separated directories searched for the media
of the manifests that are not Nintendo's (the test tones and chirp, from `tools/playyan/emu/make_test_audio.py`); `GBAV_CDIFF=1` for the
Rust-against-C tests; `PY_MP3_CORPUS`, `PY_AAC_CORPUS`, `NMP_TEST_DIR`, `NMP_ID3_DIR` for the
generated corpora (`tools/playyan/mp3/gen_media.py`, `tools/playyan/nmp/make_test_mp3.sh`,
`tools/playyan/nmp/make_id3_tests.py`). `PY_MEDIA` uses the names of Nintendo's downloads:
`playan_support_sample_movie/sample_movie.asf`, `playan_support_sample_music/sample_music.mp3`,
`play_yan_micro_support_mp4sample_movie/MP4sample_movie.mp4`, `playan_garage_<name>/<NAME>.ASF`.
The manifest test finds each manifest's media file by name anywhere under `PY_MEDIA` and
`PY_VERIFY_MEDIA`.

The C references, each with its own Makefile:

```sh
make -C reference/playyan/video FW="$PY_ROMS/Play-Yan (Japan).gba"   # or the Micro ROM or playan.fup
make -C reference/playyan/g726                                       # ITU tables only
make -C reference/playyan/mp3   FW="$PY_ROMS/Play-Yan (Japan).gba"
make -C reference/playyan/aac   FW_MICRO="$PY_ROMS/Play-Yan Micro (Japan).gba"   # or FUP=playan.fup
make -C reference/playyan/nmp   PY_ROMS="$PY_ROMS"                   # or ROM=<NMP ROM>
reference/playyan/video/pyvdec FILE.asf --out DIR   reference/playyan/g726/pyg726dec FILE.asf OUT.raw
reference/playyan/mp3/pymp3dec FILE.mp3 OUT.pcm --gba OUT8.raw
reference/playyan/aac/pyaacdec FILE.mp4 OUT.pcm   reference/playyan/nmp/pynmpdec FILE.mp3 OUT.pcm --gba OUT8.raw
```

The tools in `tools/playyan/` are the oracles and the manifest capture (`emu/capture.py`,
`emu/make_manifests.py`), with the emulators they drive (`vc01/`, `c54x/`, `emu/`;
[tools/playyan/README.md](../../tools/playyan/README.md)). Run `tools/playyan/extract_firmware.py`
first: it builds the firmware images from `PY_ROMS` into `PLAYYAN_WORK` (default `tmp/playyan`). The test-stream makers (`video/make_tests.sh`,
`video/dcvlc_transcode.py`, `g726/rate_splice.py`, `mp3/gen_media.py`, `nmp/make_test_mp3.sh`,
`nmp/craft.py`) need only ffmpeg or LAME.

## Open

Inputs on which the firmware's result is not a function of the file, or where it reaches data we
cannot model. The decoders report these as undefined (Rust `Error::Undefined`, C a negative
status) rather than guess.

- **G.726 payloads of more than 1,152 samples** (none of Nintendo's; 16 kbit/s with 400-byte
  payloads has 1,600): the decoder writes into the buffer the headphone DMA is playing, so the
  headphone output depends on DMA timing. The speaker stream stays exact. Writes past the ring and
  its 748 spare words reach live firmware data.
- **MPEG-4**: a dquant that leaves 1..31 (the firmware sets its error flag and indexes its tables
  out of range); an invalid VLC (the firmware stops sending frames); on py_fw0, a coefficient that
  spills past the macroblock's sixth block buffer (harmless on mi_fw0; never reached on py_fw0);
  data partitioning, interlace and the short header.
- **MP3, VC01**: mixed blocks. The short-block reorder 0x2c52a does not reset its source index on
  the mixed path and reads 540 words of the music task's stack, which depend on the file name and
  the chip's timing. MPEG-2 code paths exist but the player rejects those files.
- **NMP**: a mixed block on the MPEG-2 intensity-coded right channel (no stream reaches it);
  corrupt streams whose big_values run past part2_3_length (the DSP's Huffman decoding drifts).
- **AAC**: program_config_element, and DRC excluded-channel lists (they overwrite decoder flags).
