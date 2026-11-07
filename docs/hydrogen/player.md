# Hydrogen GBA Video: the player program

Scope: the player on the 13 Majesco "Hydrogen" cartridges (10 builds, section 9). Addresses are
run addresses in *All Grown Up! Vol 1* (build `04cbcc07fe42`) unless marked ROM (file offset).
Decompiler output and disassembly were working files and are not included; regenerate them
from your own dump (`tools/hydrogen/ghidra/gh_build.py`: ROM 0..0x20000 at 0x08000000 as Thumb, the IWRAM image at
0x03000000 as ARM).

## 1. What the program is

The player is a C++ program built on Majesco's **Hydrogen Library** (`hy*` classes) with a
"**MoviePlayer Example**" application on top. Both names come from the debug strings the
release build kept: assert messages with `Class::Method` names, and source paths
`C:\dev\Libraries\Hydrogen Library\Sources\...` and `C:\dev\Tutorials And Examples\MoviePlayer
Example\Sources\...`.

| layer | classes / files | where |
|---|---|---|
| Hydrogen Library: low level | `hyDisplay`, `hyBlend`, `hyFade`, `hyKeys`, `hyMemoryManager`, `hyInterruptManager`, `hyDumpMemory` | ROM Thumb 0x0800019c..0x080025c8 |
| Hydrogen Library: sprites | `hyReferential`, `hyReferentialManager`, `hySpriteStreamer` (OAM streaming, affine slots, animation) | ROM Thumb 0x08006c40..0x0800b6e0 |
| Hydrogen Library: compression | `hyCompressionManager::Inflate` (`UncompressInflate.cxx`) | IWRAM ARM 0x03000000..0x03000b90, 0x030024a0..0x03002a40, 0x03002d44 |
| application: menus | `SplashMenu`, `MainMenu`, `ChaptersMenu`, `FontRenderer`, `ExternalStateMachine` | ROM Thumb 0x08002a7a..0x080055ec, IWRAM 0x03000c68..0x03001430 (font) |
| application: playback | `MoviePlayer`, `MoviePlayer_StreamPlayer` (`StreamBase`), `SoundPlayer` | ROM Thumb 0x08004678..0x08006c40; IWRAM: VQ (0x03001430..0x03001de0, 0x03001ff4..0x030024a0), ADPCM 0x03002ad0 |
| runtime | ARM SDT C/C++ runtime (division helpers, `__rt_` signal strings, memcpy/memset) | ROM 0x0800b900..0x0800d600 |

All code that runs per pixel or per sample is hand-written-looking ARM in IWRAM (unrolled loops,
`ldm`/`stm` blits); everything else is Thumb in ROM, compiled from C++ (vtables, `operator
new`/`delete` through `hyMemoryManager`).

## 2. Boot

1. ROM 0x000 `b 0xc0`: ARM, IRQ stack 0x03007fa0, SYS stack 0x03007f00 (ROM 0xc0).
2. ROM 0xf4 crt0: walks the init table (ROM-relative pointers at ROM 0x18c): one copy entry (the
   IWRAM image, 0x302c bytes from ROM end to 0x03000000) and one zero-fill (IWRAM bss
   0x0300302c..0x03004c04). Then the C++ runtime init (static constructors, ROM 0x0800ca68).
3. `main` (ROM 0x08002608): `hyMemoryManager` init, interrupt manager, `hyDisplay` (mode 3),
   key manager, then the top-level **state dispatcher** (ROM 0x0800483c), which never returns.

Boot screens in order (All Grown Up): "Not compatible with Game Boy Player" (the GBP is detected
in the VBlank handler, `GBPVBlankFunction()`), the licence/copyright page, the **Majesco Digital
Video logo** (container 0, 59 frames), the publisher logo (a still), the **show intro**
(the last container, 359..733 frames), and the title "Press Start".

## 3. Top-level state machine and menus

The dispatcher reads a state byte (IWRAM 0x03002ff0; group-B builds 0x03002f7c) in a loop and
calls one screen function per state. The screen function returns after setting the next state.

| state (A1, B builds) | state (A2 builds) | function |
|---|---|---|
| 0x13 | 1 | `PlayMovie()`: play the movie pointed to by `[base+4]`, from chapter `[state+4]` (-1 = start). Coming from the main menu it first shows the "Instructions" page (ROM 0x080042cc) |
| 0x14 | 2 | `DisplayChapters()`: chapter list of the selected episode |
| 0x15 | 3 | `DisplayMainMenu()`: episode list |
| 0x16 | 4 | info/credits screen (SELECT on the main menu; scrolling text from the ROM, ROM 0x08003bce) |
| 0x17 | 5 | boot sequence (start state at power-on, ROM 0x080065b0): copyright page, logos, `IntroMovie()`, title "Press Start" |
| 0x18 | 6 | credits (the same screen, reached after playback) |

`base` is the first literal of the dispatcher (IWRAM 0x0300314c in All Grown Up); `[base+4]` is
the movie container pointer, `[state+8]` the brightness level (0..10, default 5).

The **main menu** lists the episodes from an IWRAM table of 4 `{container, title, u16 preview
segment}` records (0x03002fc0); the containers are filled in at run time, the title pointers and
the segment the thumbnail preview starts from (`SeekToScene`, 0 to 8) are static. While an episode is highlighted,
the menu plays it in a thumbnail window: the same `StreamBase` runs, but each segment's codebook is
reduced to one pixel per entry (IWRAM 0x03001a54 averages the luma and chroma of each entry) and a
separate small blit (0x03000c68) draws the index frame at one pixel per block. A selects
(episode or its "Chapters" tab), the D-pad moves, SELECT shows the info/credits screen.

During playback (`MoviePlayer::Process`, ROM 0x08004b0e), as the "Instructions" page lists them:
START pause, SELECT back to the menu, A/B brightness up/down (rebuilds the clamp table and
re-converts the current codebook, from the next frame), L/R previous/next chapter (seek),
D-pad right fast forward (decodes up to 3 extra frames per period without displaying them),
D-pad left rewind (re-seeks to earlier segments), and a button lock held for 3 seconds (180
VBlanks). An on-screen message ("Fast forward", "Buttons locked", ...) is drawn into the
framebuffer for 30 frames by IWRAM 0x0300134c.

## 4. Memory map during playback

| range | use |
|---|---|
| 0x02000018.. ~0x020126a0 | Inflate output window for the index stream (`ctx+0x102c`; > 64 KiB + a frame, the player slides the last 32 KiB back when the write pointer passes 0xffff) |
| 0x020126a8 + 0x6000 | 4 x 0x1800 copies of the YUV codebook (`ctx+0x15e4`), for brightness re-conversion |
| 0x020186b4 | chapter records (12 bytes each) |
| 0x02000000..0x0203ffff (rest) | `hyMemoryManager` heap (blocks with header/footer, `new`/`new[]`/`malloc` tags) |
| 0x03000000..0x0300302c | IWRAM image: IRQ dispatcher, Inflate, VQ convert/unpredict/blit, font, ADPCM, menu tables |
| 0x030032c4 + 0x1800 | inflated YUV codebook (DecompressCodebook target) |
| 0x0300302c..0x03004c04 | bss |
| 0x03005768 | `SoundPlayer`; its 4096-byte PCM ring at +4 (0x0300576c), DMA1 source |
| 0x03006860 | `StreamBase` (on PlayMovie's stack frame, ~0x1650 bytes) |
| ..0x03007f00 / ..0x03007fa0 | SYS / IRQ stacks |
| 0x06000000..0x06012c00 | mode-3 framebuffer, 240x160 BGR555, **single-buffered** |
| 0x06012c00..0x06014c00 | VQ codebook, 256 entries x 32 bytes (RGB555, blit-ready) |
| 0x05000000.. | BG palette, kept at 0 during playback (the blitter copies entries 0..23 into line 159) |

`StreamBase` fields used by the codec (offsets from `ctx`): +4 current segment pointer,
+8 stream header, +0xc segment header, +0x10 index bytes per frame, +0x14 segments left,
+0x18 frames left in the segment, +0x1c frame counter, +0x20/+0x24/+0x28 chapter table/count/current,
+0x2c 16 x 256 dither tables, +0x10dd clamp table (0x200 + 256 + 0x200 bytes), +0x102c index window,
+0x1030 current frame's index pointer, +0x1034 index Inflate object, +0x10d4 new-codebook size,
+0x10d8 2-byte-index flag (`ncb > 256`), +0x15e0 first segment, +0x15e4 codebook copies,
+0x160c "brightness changed" flag. Its own Inflate object for codebooks is at +0x1034 in
DecompressCodebook (the same object; codebooks are decompressed in one call).

## 5. Timing: one state per VBlank

Everything runs from the **VBlank interrupt**. The VBlank handler (ROM 0x08003b9c) disables its own
vector (re-entrance guard), calls `ExternalStateMachine::Process()` once, and re-arms itself. The
main thread just waits (`VBlankIntrWait`) in PlayMovie's loop until the state machine stops.

`ExternalStateMachine` (ROM 0x080052ba..0x08005654) holds up to 7 states; each state has a list of
objects whose `Process()` runs when the machine is in that state; after each `Process()` call the
machine advances one state. PlayMovie programs it to cycle **states 1..5**, so one cycle = 5
VBlanks = one video frame:

| VBlank | state | StreamBase | SoundPlayer | MoviePlayer |
|---|---|---|---|---|
| n | 1 | if the segment is exhausted: next segment, inflate its codebook (`DecompressCodebook`, then UnPredictLuminance) | - | - |
| n+1 | 2 | if a new codebook arrived: UnPredictChrominance + convert to the VRAM codebook (`ctx`, 0x08005d1c) | - | - |
| n+2 | 3 | inflate this frame's index bytes (`UncompressBlock(B)`) | - | - |
| n+3 | 4 | **blit** to the framebuffer; frame counter++; OSD redraw; DMA-save the codebook | - | - |
| n+4 | 5 | - | decode 1371/1372 samples into the ring | keys, seek, pause, brightness |
| (paused) | 6 | - | - | keys only |

Hence the frame rate is exactly 2^24 / (5 x 280896) = **11.9455 fps** for every stream, and the
blit always starts at the start of VBlank (VCOUNT 160). A state that needed more than one VBlank
period would make the next VBlank IRQ a no-op (re-entrance guard) and delay the schedule by one
VBlank; this never happens during playback (`perf.md`: the heaviest VBlank uses 80 %
of its period), and every capture shows every blit exactly 5 VBlanks after the previous one.

The blitter is faster than the beam: started at line 160, it writes rows at about one per 1000
cycles against the beam's 1232 per line, so the beam never overtakes it and the single-buffered
picture never tears.

## 6. Interrupts, timers, DMA, sound hardware

The IRQ vector (0x03007ffc) points to an ARM dispatcher in IWRAM (0x03001e7c, group B 0x03001e14).
It acknowledges IF, masks IE down to the higher-priority sources, switches to SYS mode with IRQs
re-enabled (**nested interrupts**) and calls the handler registered with
`hyInterruptManager::SetVector` (a 14-entry table). During playback IE = 0x1011: VBlank, Timer1
and keypad.

| unit | setting | purpose |
|---|---|---|
| DISPCNT | 0x1403 | mode 3, BG2 + OBJ (OSD sprites) |
| Timer0 | reload 0xFC00, prescaler 1 | 16384 Hz sample clock for DirectSound A |
| Timer1 | cascade, reload 0xF000, IRQ | counts 4096 samples; its IRQ (every 0.25 s) restarts DMA1 at the ring start (ROM 0x08006aa8) |
| DMA1 | 0xB640 (repeat, 32-bit, FIFO timing) | ring (0x0300576c) -> FIFO A |
| SOUNDCNT_H | 0x0304 | DirectSound A 100 %, both speakers, Timer0; PSG off |
| WAITCNT | 0x4014 | ROM WS0 3/1, prefetch on |

The sample ring is 4096 bytes. `SoundPlayer` prefills it with 4096 samples when playback (re)starts,
then in state 5 of every frame writes the next `(acc + 702240) >> 9 - acc >> 9` samples (a 21-bit
phase accumulator, 1371.5625 samples per frame on average) at its write index, splitting the call at
the ring end. Both clocks are the CPU clock, so audio and video never drift.

## 7. Playing a stream (PlayMovie, ROM 0x08004678)

1. `StreamBase::Initialize(video entry)` (0x08005998): build the 256 x 16 dither tables and the clamp
   table for the current brightness, parse the chapter list into records (0x080057c0).
2. `SeekToChapter(chapter)` (0x08005ab8) or start of stream.
3. `SoundPlayer::SetSoundFile(audio entry)` (asserts codec 3 = ADPCM, "not adpcm audio stream in
   adpcm only build"), then `Seek(first frame)` and prefill.
4. Register StreamBase on states 1-6, SoundPlayer on 1, 2 and 5, the MoviePlayer input handler on 5
   and 6; start the cycle 1..5.
5. Wait until the state machine is stopped (end of stream or SELECT), then tear down and return to
   the menu (or the credits after the last episode on some carts).

Seeking (chapters, rewind, L/R) is by segment: `SeekToScene(n)` walks the segment chain from the
first segment summing frame counts. `SoundPlayer::Seek(frame)` (ROM 0x080069a8) starts the ADPCM
decoder at block `frame / 12` (byte offset = 4 x the sum of the earlier entries of the block-size
table) and then decodes `frame % 12` frames' worth of samples (1371, 1372, 1371, ... alternately)
to advance the predictor. For that skip it **patches its own ARM code**: the sample store of the
ADPCM loop (IWRAM 0x03002c28) is overwritten with `mov r9, r9` and restored afterwards, so the
skip decodes write nothing. Then the 4096-sample prefill. Seek checks: every chapter of episode 1
on one cart per build, started cold in the emulator, gives the same frames as `hydec --start-frame`
and the same ADPCM output as the shared decoder started at the same block ([README.md](README.md)).

## 8. Display format

The stream header says 240x160, but the blitter covers 240x160 only in the 4x4 and 4x2 modes
(40 or 80 block rows). The 4x3 and 3x3 modes have 53 block rows = **159 lines**; line 159 is
written from BG palette RAM (entries 0..23 repeated), which is black during playback. Every
Hydrogen episode uses 4x3 or 3x3 segments, so the effective picture is 240x159 with a black
bottom line.

## 9. Player builds

The 13 carts have 10 different player builds (the classifier's build id hashes the executed code
windows). Function-by-function comparison (decompiled ROM Thumb with addresses, assert line numbers
and register names normalised; IWRAM ARM word-by-word with branch offsets and literal pointers
masked; `tools/hydrogen/ghidra/ghdiff.py`, `tools/hydrogen/ghidra/iwcmp.py`):

| group | builds (carts) | differences from All Grown Up |
|---|---|---|
| A1 | `04cbcc07fe42` (All Grown Up, Sonic X 1), `c1313a3736b4` (Nicktoons Coll. 1), `c3b335b0b286` (Strawberry Shortcake 1) | menu code only: MainMenu::PlayMovieSequence (episode count, title layout), sprite helpers |
| A2 | `92e428ed134d` (SpongeBob 1, SpongeBob 2, Fairly OddParents 2), `1c25494320ba` (Fairly OddParents 1), `ff6e0e4279e0` (Dora 1) | dispatcher states renumbered 1..6; MoviePlayer::Process key handling rewritten as independent tests; ChaptersMenu, PlayMovie, SetNewMessage changed; memory-manager delete paths changed |
| B | `f73d177a8155` (Yu-Gi-Oh!), `93fe5e2e96b3` (TMNT), `dff48b69f680` (Kids Next Door 1), `f5f0564f89e3` (CN Collection 1) | IWRAM image 0x2fb8/0x2fc0 bytes: Inflate's debug "Dumping symbols..." routine removed, so everything after 0x03000b28 moves by -0x68 (and -0x74 from the veneers on); IntroMovie, PlayMovie, menus changed; states 0x13..0x19 at 0x03002f7c |

**Codec-relevant code is identical in all 10 builds**: the Inflate routines, UnPredictLuminance /
UnPredictChrominance, the converter and thumbnail averager, the blit, the IRQ dispatcher and the ADPCM
routine are word-for-word the same modulo relocation, and StreamBase, SoundPlayer and the
ExternalStateMachine are identical in the decompilation. The tables they use (dither, block
geometry, brightness gains, length/distance bases) are byte-identical in all 13 carts. One decoder
therefore serves every build, which the per-stream verification confirms (stats.json).

The two Hydrogen-packaged LZMA carts (Jimmy Neutron 1, the Noah proto) share this engine
(containers, StreamBase, VQ, SoundPlayer); only the compression object differs (codec nibble 7).
Their codec is documented and verified with the ADS family ([../ads/README.md](../ads/README.md)).

## 10. Function map (All Grown Up; group B = -0x68 / -0x74 from 0x03000b28 on)

| address | role |
|---|---|
| 0x03000000 / 0x03000048 | Inflate ctor / dtor |
| 0x03000094 | Inflate::Initialize(obj, src, dst, circular) |
| 0x030005c8 | Inflate::UncompressBlock(obj, n): block-type state machine, returns the frame pointer |
| 0x03000144 | Inflate::CoreExpand_Static (fixed Huffman) |
| 0x03000338 | fixed-code table build |
| 0x03000490 | stored-block copy |
| 0x030024a0 | code-length decoder (16/17/18) |
| 0x0300265c | Huffman table build (8-bit primary + sub-tables, pool 0x740 entries) |
| 0x03002870 | dynamic-block core |
| 0x03002d44 | get(k) |
| 0x03000b1c | Inflate::SetTablePool |
| 0x03000c68 | thumbnail blit (main menu) |
| 0x03000ee4..0x03001194 | FontRenderer (text extents, glyph draw) |
| 0x0300134c | OSD message redraw |
| 0x03001430 | codebook converter YCbCr -> dithered RGB555 (ctx, src, dst, dims, ncb) |
| 0x03001a54 | codebook averager (thumbnail, 1 pixel per entry) |
| 0x03001ba8 / 0x03001cac | StreamBase::UnPredictLuminance / UnPredictChrominance |
| 0x03001de0 | text-to-framebuffer helper |
| 0x03001e7c | IRQ dispatcher |
| 0x03001ff4 | VQ blit (4 geometries) |
| 0x03002a40..0x03002ab4 | ARM->Thumb veneers |
| 0x03002ad0 | ADPCM decode (ctx, dst, n) |
| 0x0800483c | top-level dispatcher |
| 0x08004678 | PlayMovie() |
| 0x08004b0e | MoviePlayer::Process() |
| 0x080052ba..0x08005654 | ExternalStateMachine |
| 0x0800565a / 0x08005744 | StreamBase ctor / dtor |
| 0x080057c0 | chapter-record builder |
| 0x0800594c | clamp-table builder (brightness gain) |
| 0x08005998 | StreamBase::Initialize |
| 0x080059e0 / 0x08005a30 / 0x08005ab8 | GetSceneFrameID / SeekToScene / SeekToChapter |
| 0x08005b2c | StreamBase::DecompressCodebook |
| 0x08005d1c | start segment: UnPredictChrominance + convert (or average for the thumbnail) |
| 0x08005e06 | container entry lookup by type |
| 0x08005f34 / 0x08005f78 | SoundPlayer decoder reset / decode with block headers |
| 0x0800664e..0x08006b42 | SoundPlayer (ctor, stop, start, SetSoundFile, refill, Seek, Process) |
| 0x08003b9c | VBlank handler |
| 0x08006aa8 | Timer1 handler (DMA1 restart) |
