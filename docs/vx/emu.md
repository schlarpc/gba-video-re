# Shrek GBA Video -- emulator ground-truth harness

Code in `tools/vx/emu/`. All results below are verified on this ROM,
not inferred.

## TL;DR -- regenerate ground truth

Run everything from `./emu` inside a Nix shell with mGBA libretro, unicorn and capstone (see the top-level README). Use the `python3`
the dev shell provides (re-env), not `uv run`.

```sh
cd emu
# 1. First 300 VX frames + the matching audio (about 5 s). Writes frames/ and audio/
python3 capture.py --frames 300 --out .
# 2. (once, about 6 min; already done) savestate checkpoints every 1000 frames, for seeking
python3 capture.py --checkpoints 1000 --frames 40000 --no-dump --out .
#    (the existing states are named vx_00997, vx_01997, ... because they came from an earlier numbering)
# 3. Frames deep in the movie. --skip seeks through the nearest states/vx_K.state automatically
python3 capture.py --skip 20000 --frames 30 --out deep20000
# 4. Whole-movie md5 manifest (YUV + VRAM per frame, md5 per audio block), no raw dumps
python3 capture.py --hash-only --frames 40000 --out manifest
# Other carts / streams: --stream picks the movie and walks the cart's menu to it
python3 capture.py --rom combo.gba --stream 0x1eef800 --hash-only --frames 99999 --out st
python3 mkmanifest.py --rom combo.gba --stream 0x1eef800 --name shrek-sharktale-rev5_1eef800 \
    --cart "Shrek + Shark Tale (USA) Rev 5" st          # -> verify/vx/*.frame_md5.csv.gz, verify/vx/streams.json
# 5. Unicorn oracle: seed state (main loop idle after VX frame N, N >= 4), then decode / verify / trace
python3 mkstate.py 100                       # -> states/wait_00100.state
python3 uc_decode.py states/wait_00100.state --frames 199 --verify frames --audio-blocks 3000 --verify-audio audio
python3 uc_decode.py states/wait_00100.state --frames 2 --trace bitreads.csv
```

The mGBA libretro core comes from `nix build nixpkgs#libretro.mgba`. `retro.py` finds it by itself, or
you can set `MGBA_LIBRETRO=/path/mgba_libretro.so`. The core is the 2026-07-28 snapshot. It implements the
Matrix mapper (`src/gba/cart/matrix.c`). No BIOS file is needed: mGBA's built-in HLE BIOS works.

## Files

| path | content |
|---|---|
| `retro.py` | ctypes libretro frontend: frame stepping, joypad, direct pointers into GBA memory (`SET_MEMORY_MAPS`), serialize/unserialize |
| `capture.py` | ground-truth capture (see TL;DR). Frame numbers are **VX stream frame indices** |
| `yuv2bgr.py` | bit-exact numpy port of the player's YUV->BGR555 blit (0x03000370) |
| `uc_gba.py` | unicorn GBA CPU sandbox seeded from an mGBA savestate. Includes the Matrix mapper, immediate DMA, and a few SWIs |
| `uc_decode.py` | oracle: runs the player's own video/audio decode code in unicorn, with verify and bitstream-read trace |
| `mkstate.py` | makes the oracle seed state `states/wait_NNNNN.state` |
| `frames/yuv/NNNNN.yuv` | decoder output of VX frame N, raw slot copy, 0xF000 bytes (layout below) |
| `frames/vram/NNNNN.bgr555` | mode-3 framebuffer after frame N was blitted: 240x160 u16 LE BGR555 (76800 B) |
| `frames/png/NNNNN.png` | PNG of the VRAM frame (5->8 bit as `(c<<3)\|(c>>2)`) |
| `frames/index.csv` | frame, dec_index (=frame+3), slot, slot_addr, emu frame decoded/displayed, `vid_next_map` (ctx+0xbc), `vid_rdptr` (ctx+0xc0), md5s |
| `audio/pcm.s8` / `pcm.wav` | decoded audio, s8 mono **65536 Hz** (what DMA1 feeds FIFO A), block 0 onwards |
| `audio/blocks.csv` | per 512-sample block: block (= AFrame index), emu frame, ring slot, blocks played, vx decoded/displayed, audio map ptr, md5 |
| `audio/core_mix.wav` | the libretro core's mixed stereo output. **Not bit-exact**, for listening only |
| `states/vx_NNNNN.state` | mGBA savestate taken right after VX frame N was decoded (checkpoints) |
| `manifest/frames/index.csv`, `manifest/audio/blocks.csv` | whole-movie md5 manifest. `vram_md5` hashes the picture rectangle the player centres on the screen (the whole 240x160 screen for a 240x160 movie) |
| `mkmanifest.py` | copies a hash-only capture into `verify/vx/` (frame md5s, plus an md5 digest of the played audio blocks in `streams.json`) |

## How the movie is started

The title menu is up by emu frame ~800. `capture.py` presses A at emu frame 880 for 5 frames. "Play" is
preselected. The player context becomes valid at emu frame 886. VX frame 0 is decoded at emu frame 887 and
blitted at emu frame 899. Emulation is deterministic, so these numbers are the same on every run.
Speed: about 2000 emulated frames/s. The whole movie is about 324k emu frames, so a full pass takes about 7 min.

Other carts: Shark Tale and Shrek 2 (Rev 6) start the same way, with A at emu frame 880. The
Shrek + Shark Tale title lists Shrek and Shark Tale (Down selects the second). Each has a
submenu with Play, Chapters, Controls and Credits. `capture.py` holds the key sequence for each
stream (`MENUS`) and checks that the player's ctx+0x20 names the requested stream.
On the combo cart, the 240x112 movies leave the screen rows outside 24..135 black for the
whole movie.

## Decoder output (the bit-exact target)

- 4 frame slots in EWRAM: slot i at `0x020032C8 + i*0xF000` (ctx+0x00..0x0c = Y pointers, ctx+0x10..0x1c = UV pointers).
- Slot layout: **Y plane 256x160** (stride 256, 240 columns used), then at +0xA000 **chroma 256x80**, U,V
  **interleaved bytewise** (U0 V0 U1 V1 ..., 120 pairs used per row). 4:2:0.
- Frame index: ctx = `0x06013E80` (player state, in VRAM). ctx+0x28 = decode count. When it advances to
  c, the frame just decoded has decoder index n = c-1 and is in slot `n & 3`. `player_run` pre-sets
  the counters to 3. Slots 0..2 start black (Y=0, UV=0x80) and are never decoded, so **VX frame = n - 3**.
  The capture cross-checks this against ctx+0x30 (VX frames decoded so far).
- The slot is final when the counter advances: `yuv2bgr(slot)` equals the VRAM frame that is displayed later,
  bit for bit, for all 300 frames checked.
- A slot is overwritten only by frame n+4. The decoder waits while the 4-slot queue is full, so one read per
  emulated frame cannot miss a frame. In 300 frames there was 1 emu frame with 2 decodes, at start-up only.

## Display

- Mode 3 (DISPCNT 0x1F43), **single buffer** at 0x06000000. There is no page flip: the Timer3 IRQ blits
  straight into the visible framebuffer, and the blit spans about 3 emulated frames, so it tears on hardware.
  ctx+0x2C (display count) increments right after the blit finishes, so the capture dumps VRAM on that change.
- Display rate is **7.000 fps** (every 8 or 9 emulated frames), driven by Timer3. The header frame count is
  0x93CD = 37837 frames, which is 90.1 min.
- The YUV->RGB blit (`yuv2bgr.py`) has these exact semantics (table T at IWRAM 0x030001F0, T[k] = clamp(k>>3, 0, 31)):
  `Yd = Y - 4*((x+y)&1)` (2x2 checkerboard dither), `R = T[Yd + 2(V-128)]`, `G = T[Yd - ((V-128) + ((U-128)>>1))]`,
  `B = T[Yd + 2(U-128)]`, pixel = R | G<<5 | B<<10. The port reproduces VRAM for 297/297 frames.
- Brightness (A/B buttons in the player) patches the blit. The capture never presses A or B after Play, so the
  brightness stays at 0.

## Audio

- DMA1 streams the ring `0x06015F40` (16 slots x 512 bytes, s8) into FIFO A. Timer0 reload 0xFF00 gives
  **65536 Hz**, mono (DMA A goes to both L and R).
- ctx+0xA4 = number of 512-sample blocks decoded, written by the Timer3 IRQ (0x03007AB0, which calls
  0x03006A14 per packet). Block j goes to ring slot `j & 15` and stays there until block j+16 is decoded.
  ctx+0xA0 = blocks played (Timer1, 128 Hz). The decoder works just in time: a block is decoded once
  `a4 < a0`, then plays one ring lap (16 blocks = 125 ms) later.
- The capture reads block j at the first emulated frame where ctx+0xA4 > j. It was checked to be stable:
  76,899 re-reads over the next 15 blocks, 0 changes. Do **not** sync on ctx+0xA0: the slot being played is
  overwritten while it plays.
- The block index equals the container notes' "AFrame" index (from movie start). The PCM has a strong
  sample-to-sample alternation. That is expected: per player.md §5, the codec upsamples 16384 -> 65536 Hz 4x
  with error-feedback noise shaping.

## Unicorn oracle (finer-grained)

- `mkstate.py N` runs mGBA from the nearest checkpoint. It saves a state when VX frame N has just been
  decoded **and** the main loop is in its "queue full" wait (PC 0x030072F0..0x03007314, SP 0x03007E98).
  That state does not exist for N < 4, because the queue is not full yet.
- `uc_gba.GBA(state)` loads GPRs/CPSR, IO, palette, VRAM, OAM, IWRAM and EWRAM from the mGBA state (layout
  in `mgba/internal/gba/serialize.h`). It maps the ROM file's first 32 MB at 0x08000000. Then it rebuilds
  the 8 KB Matrix window from the state's mapping table (state offset 0x328, 16 x u32 ROM offsets).
- Video step: `run_until(0x03007318 -> 0x030072F0)` runs one main-loop iteration without the wait:
  decode_frame 0x03006D30 plus key handling 0x02000874. The video window refills (Matrix writes) happen inside
  the decoder and are emulated.
- Audio step: call 0x03007AB0(ctx) with ctx+0xAC = 1 and ctx+0xA0 = a4+1, so it decodes exactly one block.
  Then do the main loop's audio window refill (ctx+0xB4 -> map 2 pages from ctx+0x90).
- **Verified:** from `wait_00004`, VX frames 5..299 are 295/295 bit-identical to mGBA, and 5300/5300 audio
  blocks are bit-identical. From `wait_00150`, frames 151..299 are 149/149. A video frame takes about
  2.6 ms in unicorn. No SWIs or DMA are used in the decode path. ARMv5 (ARM926) emulation of this ARMv4T code
  gives identical results.
- Trace: `--trace f.csv` logs every read of the window 0x08000000-0x08001FFF as
  `tag (v<frame>|a<block>), pc, addr, size, value, rom_file_offset`. The video bit reader (0x030007A8) fetches
  LE halfwords. For example VX frame 101 is read from ROM 0x2CBE0 onwards, 226 halfword reads.
  `Oracle` in `uc_decode.py` is importable: add your own `uc.hook_add(...)` (code hooks on 0x0300076C ue(v),
  0x0300081C se(v), MB dispatch 0x03001DAC, ...) to trace syntax elements.

## Captures on disk

- `tools/vx/emu/frames`, `tools/vx/emu/audio`: VX frames 0..299 plus audio blocks 0..5471.
- `tools/vx/emu/deep20000`: frames 20000..20029 plus 600 audio blocks. Unicorn reproduces all 29 frames and 400 blocks from `wait_20000`.
- `tools/vx/emu/deep37800`: frames 37800..37836. The movie ends at VX frame 37836, so it has 37837 frames, which
  matches the header. The last few frames have no `vram_md5`, because the player exits before it blits them.
  When the movie ends the player clears the ctx, and `capture.py` stops.
- `tools/vx/emu/states/vx_KKKKK.state` for K = 997, 1997, ..., 36997: seek checkpoints. `wait_00004`, `wait_00150`
  and `wait_20000` are oracle seeds.
- `tools/vx/emu/manifest/`: whole-movie md5s (see the validation note below).

## Whole-movie validation

- `manifest/frames/index.csv` has 37837 rows (VX frames 0..37836), each with `yuv_md5`. `vram_md5` is present
  for every frame except the last few, which are never blitted. `manifest/audio/blocks.csv` has md5s for
  blocks 0..691795. That is 691,796 blocks. The container notes count 691,876 AFrames in the stream; the
  player stops before it decodes the last 80.
- The 300-frame and both deep captures match the manifest exactly, YUV and VRAM.
- **Unicorn vs mGBA, whole movie:** started from `wait_00004` and run in one pass of 218 s, the oracle
  decodes VX frames 5..37835. All 37,831 are bit-identical to the manifest md5s. The pass includes every
  seek/keyframe boundary and the Matrix window refills. The very last frame (37836) ends the player's main
  loop, so the oracle's step does not return cleanly there; take that frame from mGBA.
- To check your decoder: compare `md5(slot bytes)` (0xF000 bytes, Y 256x160 then interleaved UV 256x80, with
  columns 240..255 of each row exactly as the player leaves them) against `yuv_md5`. If you only produce the
  240x160 pixels, compare against `frames/yuv` cropped, or render through `yuv2bgr.py` and compare with
  `vram_md5`.
  In all 300 dumped frames the padding is constant: Y columns 240..255 = 0x00 and UV bytes 240..255 = 0x80,
  the init values the decoder never touches. So a full-slot md5 works if you fill the padding with those values.

## Gotchas

- The libretro core publishes its memory map only on the first `retro_run`. Call `run(1)` before reading memory.
- Savestates restore the Matrix window: mGBA re-reads the 16 mapped pages from the ROM file on load.
  Resuming from a state and dumping again matched the continuous run exactly.
- The libretro serialize blob is mGBA state version 0x01000009, 0x81040 bytes: base state 0x61000 plus
  extdata (128 KB savedata).
- In the re-env Python, never name a script after a stdlib module (`dis.py` breaks capstone).
