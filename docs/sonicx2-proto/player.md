# Player: Sonic X Vol 2 prototype (Hydrogen engine, 17 March 2004)

Source of every claim: the cart's own code (Ghidra projects, capstone diffs with `fdiff.py`,
decompile diffs with `cdiff.py`), unicorn runs of the blit (`blitcmp.py`, `blit2.py`) and headless
mGBA sessions (`shots_*.py`). These were working scripts of the analysis (`work/diff/`); they, the
Ghidra projects and the screenshots are not distributed in this repository. The headless mGBA
drivers that are distributed are in `tools/sonicx2-proto/emu/`.

## 1. What the player is

The proto runs the same engine as every shipped Hydrogen cart. The engine is two C++ code bases,
named by the assert file paths left in the ROM:

- **Hydrogen Library** (`C:\dev\Libraries\Hydrogen Library\Sources\...`): `hyDisplay`, `hyInterrupt`,
  `hyKeys`, `hyMemoryManager`, `hyReferential`, `hyReferentialManager`, `hySpriteStreamer`,
  `hyDefines`, and `Managers\Compression\UncompressInflate.cxx`.
- **"MoviePlayer Example"** (`C:\dev\Tutorials And Examples\MoviePlayer Example\Sources\...`):
  `MoviePlayer.cpp`, `MoviePlayer_StreamPlayer.cpp`, `StreamBase.cpp`, `StreamBase_Extra.cxx`,
  `SoundPlayer_ADPCM.cpp`, `MainMenu.cpp`, `ChaptersMenu.cpp`, `SplashMenu.cpp`, `StateMachine.cpp`,
  `FontRenderer.cxx`, `GBP\GBPDetect.cpp`.

So the retail GBA Video player is a library tutorial ("MoviePlayer Example") that shipped. The same
22 source paths appear in every build checked. Only the drive-letter case differs per build machine:
`C:\dev` in the proto, Yu-Gi-Oh and TMNT; `C:\Dev` in Sonic X 1, SpongeBob 1 and Dora.

## 2. Boot and screens (observed in mGBA)

Boot order, proto (from headless mGBA screenshots):

1. "NOT COMPATIBLE WITH GAME BOY PLAYER" with "Nintendo" (GBPDetect). The proto builds this
   screen at run time with its FontRenderer from the strings `NOT COMPATIBLE WITH` and `Nintendo`.
   The shipped builds draw a prebuilt bitmap, so the two strings are absent there.
2. "Copyright (c) Sonic Project / Licensed by Nintendo" text (SplashMenu).
3. Majesco logo with the animated "DIGITAL VIDEO" bar. The bar is `IntroMovie()` playing the
   59-frame intro stream (container ROM 0x1e47104, the same 0xc78c-byte file as in Vol 1).
4. 4Kids logo (still image).
5. Title "SONIC X  PRESS START".

Vol 1 inserts the Sonic X opening-theme video (a third video file of 0x1916f8 bytes) between the
4Kids logo and the title. The proto has no opening video.

Main menu (`shots/proto/01..03`): "MAIN MENU", two entries "Episode 103" and "Episode 104", each
with a "Chapters" tab. A: Select, D-pad: Move, SELECT: Credits.

- The thumbnail box of the focused entry is **not a placeholder**. It plays a silent live preview of
  the episode. The preview starts about 1 s after focus and shows black until then
  (`shots/proto_end2_montage.png` shows it running).
- The preview uses the 1x1 "downsampled codebook" path: IWRAM 0x03001a8c averages every codebook
  entry to one pixel, and 0x03001470 is then called with a 1x1 geometry. Each VQ block therefore
  becomes one thumbnail pixel.
- Chapter thumbnails in the Chapters menu use the same mechanism.
- After an episode, the thumbnail resumes from the last position played.

Vol 1 shows real episode titles ("Chaos Control Freaks", "Sonic to the Rescue") instead of
"Episode 103/104". Its SELECT button is labelled "Info" (screen "INFO", "GBA Video Pak Production
Credits"), where the proto has "Credits" (screen "CREDITS", which starts directly with "- Majesco -
Chief Technology Officer Kevin B. Ray ...").

Other screens:

- **Chapters menu** (`07/08`): 6 chapters per episode in a 3x2 grid (the stream header has 6
  chapter records), A: Select, B: Back.
- **Instructions** (`11`): shown before every playback. "START Pause movie, SELECT Back to previous
  menu, A/B Brightness control, L/R Previous/Next chapter, Right: Fast forward, Left: Rewind,
  Up: **Parental lock** (hold for 3 sec)". Vol 1 says "Button lock".

Playback OSD (bottom-left text, drawn by IWRAM 0x03001388 into the bitmap for 30 frames):

| Input | Action | OSD |
|---|---|---|
| START | pause / resume | "Pause" / "Play" |
| A / B | brightness up / down | "Brightness ±n" (see §4) |
| R / L | next / previous chapter | "Chapter / Chapter n" |
| Right (held) | fast forward | "Fast forward" |
| Left (held) | rewind | "Rewind" |
| Up held 180 VBlanks | toggles button lock | "Buttons locked" / "Buttons unlocked" |
| SELECT | back to main menu | |

End of an episode (`shots/proto_end_montage.png`, `proto_end2_montage.png`):

1. The video ends on its last frame.
2. A text credit roll in the menu font follows on black: "Executive Producers Alfred R. Kahn ...",
   ending with "Produced by TMS ENTERTAINMENT ... Sonic X is a trademark ...". These are 1872 bytes
   of text in the ROM (0x16ee6), rendered by FontRenderer. They are not video.
3. The player returns to the main menu.

Episode 103 (13828 frames) returned to the menu about 71500 VBlanks after playback started, which is
about 5.17 VBlanks per frame including per-scene setup and the credit roll.

## 3. Frame machine (`MoviePlayer::Process`, proto 0x08004b28, Vol 1 0x08004a56)

The state numbering and the per-state decode work are the same in both builds. One state runs per
VBlank:

| State | Work |
|---|---|
| 1 | new scene: `DecompressCodebook` and the DMA copy of the codebook |
| 2 | per-scene unpredict and YUV->RGB codebook conversion; a brightness change rebuilds the codebook here |
| 3 | inflate one frame of indices: `blocks × (ncb>256 ? 2 : 1)` bytes |
| 4 | VQ blit, frame counters, 30-frame OSD timer |
| 5/6 | input handling (6 = paused) |

The function was largely rewritten between the proto and the shipped builds. Proto 0x08004b28 is
0x744 bytes; Vol 1 0x08004a56 is 0x680 bytes. The behavioural differences:

- **Key decoding.** The proto tests every key independently, so the last key tested wins. Shipped
  builds use an else-if priority chain.
- **Pause.** Shipped builds ignore brightness, FF, RW, chapter skip and the lock while paused
  (state 6). The proto does not check.
- **Fast forward.** The proto inflates (and discards) 2 frames per tick. Shipped builds inflate up to
  3 frames per tick.
- **Rewind.** The proto seeks one scene back on every tick (`SeekToScene(cur-1/-2)`). Shipped builds
  rate-limit to one scene every 4 ticks (counter at this+0x1648) and wrap at the stream start.
- **Lock.** The lock message code is 0x13 in the proto and 0x12 in shipped builds. Shipped builds
  also add an "any key exits" mode (flag at this+0x1644).
- **End-of-stream.** The proto jumps to app state 6; shipped builds jump to 0x18. The top-level
  app state machine (proto 0x0800486e) is renumbered from 1..6 (proto) to 0x13..0x18 (shipped),
  because shipped builds added screens such as the opening video.
- **Scene start.** The proto calls `DecompressCodebook(void)`. Shipped builds call
  `DecompressCodebook(unsigned long scene)`. The argument only feeds an assert, and shipped builds
  add `StreamBase::GetSceneFrameID(unsigned long)`, which the proto does not have.

None of these differences changes the bitstream or the decoded frames.

## 4. Brightness

The brightness table builder (proto 0x08005904, Vol 1 0x08005894) builds the YUV->RGB clamp table at
ctx+0x10dd: 512 zeros, then 256 entries `min(255, (coef*i)>>8)`, then 512 copies of the last entry.

| | Proto | Shipped (all 5 builds) |
|---|---|---|
| Levels | 8 (index 0..7) | 11 (index 0..10) |
| Coefficients | `{230,256,281,307,332,358,384,409}` (ROM 0x08016d4c) | `{128,153,179,204,230,256,281,307,332,358,384}` (Vol 1 ROM 0x0800eaf8) |
| Coefficient formula | `coef = table[level]` | `coef = table[level] + bias` (bias = IWRAM global 0x030032c0 in Vol 1) |
| Default level | 1 (IWRAM .data 0x03002f48), coef 256 = identity | 5, coef 256 = identity |
| OSD label | `level - 3` | `level - 5` |

**Proto quirk:** the OSD label is centred on level 3 (coef 307), but the default is level 1 (coef 256).
The neutral default picture is therefore labelled "Brightness -2": one press of A shows
"Brightness -1", and B shows "-3".

A decoder that matches the cart at power-on must use coef 256 for both builds.

## 5. Function-by-function comparison

Method:

1. Ghidra auto-analysis plus a prologue scan of each build: ROM code 0x08000000..0x0800e000 and the
   IWRAM ARM image.
2. Functions are matched by identical normalized instruction text (addresses masked), then by
   assert/`__func__` string, then by instruction-sequence similarity.

The scripts were `gh_extract.py` and `match.py` (working files, not distributed); the Hydrogen
family's equivalent tools are in `tools/hydrogen/ghidra/`.

Proto functions with the same normalized code (= identical, ~ changed, - not found):

| Region (proto fns) | Sonic X 1 `04cbcc07fe42` | Yu-Gi-Oh `f73d177a8155` | TMNT `93fe5e2e96b3` | SpongeBob 1 `92e428ed134d` | Dora `ff6e0e4279e0` |
|---|---|---|---|---|---|
| IWRAM ARM (37) | 30 = / 7 ~ | 29 = / 7 ~ / 1 - | 29 = / 7 ~ / 1 - | 30 = / 7 ~ | 30 = / 7 ~ |
| ROM Thumb (355) | 317 = / 35 ~ / 3 - | 311 = / 40 ~ / 4 - | 311 = / 40 ~ / 4 - | 317 = / 35 ~ / 3 - | 321 = / 31 ~ / 3 - |

Every codec routine is the same across all five shipped builds, so the proto differs from all of
them in the same places. The proto is a common ancestor of the shipped Hydrogen builds, not a
sibling of one of them.

### 5.1 Codec-relevant functions

Addresses are proto / Vol 1 / Yu-Gi-Oh.

| Function | Proto | Vol 1 | Yu | Status | Difference |
|---|---|---|---|---|---|
| VQ blit dispatch (4 geometries) | 0300202c | 03001ff4 | 03001f8c | **changed** | (a) **3x3 (modes 4/5):** the proto blits one block at a time with three `strh` per row. Shipped builds blit two blocks per iteration with 32-bit `stm` of packed halfword pairs. Shipped builds build the bottom-left pixel of every odd block as `(word & 0xffff0000) + (next >> 16)`, which adds the entry's unused halfword 9. The output is identical only when halfword 9 of every RGB codebook entry is 0 (unicorn-verified: with a nonzero halfword 9, 2120 pixels differ, all at row 3k+2 of odd blocks). (b) **Row 159 filler (modes 2-5, the 53-block-row geometries):** shipped builds append 10 `stm` copying 48 bytes of BG palette RAM (0x05000000, entries 0-23) ten times across row 159. The proto never writes row 159 in modes 2-5, so it keeps stale VRAM. Modes 0/1 and 6/7 are identical. |
| ADPCM decode loop | 03002a4c | 03002ad0 | 03002a5c | **changed** | The proto stores `(acc>>6)` as a byte with no clamp. Shipped builds clamp to [-128,127] first (6 instructions). Nothing else differs. |
| UnPredictLuminance / UnPredictChrominance | 03001be0 / 03001ce4 | 03001ba8 / 03001cac | 03001b40 / 03001c44 | ~ | Assert line numbers only (0x153→0x155, 0x182→0x184). |
| YUV->RGB555 codebook | 03001470 | 03001430 | | = | |
| 1x1 downsample (thumbnails) | 03001a8c | | | = | |
| Inflate (main, code lengths, tables) | 030027ec, 0300241c, 030025d8 | | | = | |
| Brightness table | 08005904 | 08005894 | 08005904 | ~ | see §4 |
| StreamBase::Initialize, SeekToScene, DecompressCodebook | 0800594c, 08005994, 08005a90 | 080058e0, 08005978, 08005a74 | | ~ | Assert line numbers and literal-pool offsets only. DecompressCodebook gains an unused `unsigned long` argument. |
| Per-scene codebook setup | 08005b44 | 08005c64 | | ~ / = | Identical to SpongeBob 1 and Dora; literal offsets only vs Vol 1. |
| Chapter-table parse | 08005778 | | | = | |
| Audio refill, bit reader, ADPCM reset | 08005ea0, 08005d6c, 08005e5c | | | = | |
| SoundPlayer seek/resume | 0800688c | 080068f0 | | ~ | Audio buffer prefill: the proto copies 0x1000 bytes through a DMA3 descriptor, shipped builds through a copy call; the bookkeeping is reorganised. |
| IntroMovie | 0800616e | 0800618e | | ~ | Becomes `IntroMovie(void*)`: the container is passed in instead of read from the file slot. Both assert fps 12 or 15. |
| MoviePlayer::Process | 08004b28 | 08004a56 | | rewritten | see §3 |
| StreamBase::GetSceneFrameID | — | 08005928 | | shipped-only | |

### 5.2 Non-codec changes

| Function | Proto | Vol 1 | Change |
|---|---|---|---|
| FontRenderer::ConvertTextToFont (IWRAM) | 0300118c | 03001194 | Shipped builds add glyph mappings for 3 more characters (0x92, '`' → apostrophe; 0xAE → "(R)" as 3 glyphs) and drop the "Unknown character!" assert. |
| FontRenderer::GetTextExtents | | | Signature changes from `(char*, unsigned long)` to `(char*, FontRenderer::FontType)`. |
| OSD banner draw (IWRAM) | 03001388 | 0300134c | Colour and font-flag constants. |
| Sprite-row copy to VRAM (IWRAM) | 03001e1c | 03001de0 | Gains a destination-base and a colour argument. |
| GBPDetect | 0800b6cc | 0800b72c | The proto renders "NOT COMPATIBLE WITH / Nintendo" with the FontRenderer; shipped builds use a bitmap. |
| DisplayCredits | 08003e2c | none found | Proto-only function. The closest shipped match, Vol 1's INFO-screen code 08003bd2 (similarity 0.45 to proto 08003b62), was largely rewritten. |
| Brightness OSD text (MoviePlayer::SetNewMessage) | 080049f4 | 08004908 | Centre 3 → 5 (see §4). |
| PlayMovie | 080046d8 | 080045c0 | Shipped builds add an app state that plays the opening-theme video. |
| Top-level app state machine | 0800486e | 08004784 | States 1..6 renumbered to 0x13..0x18. |
| MainMenu::PlayMovieSequence, ChaptersMenu::PlayChapter, menus 08002dd4 / 08003778 / 080038ee / 08003b62 | | | Episode-title strings instead of "Episode 103/104"; extra resource draws. |
| hyReferential / hyMemoryManager / hySpriteStreamer | | | Small library revisions: ComputeAffineParameter, ProcessAnimation, CheckBoxCollision, AllocateBlock (Yu/TMNT only). |
| IWRAM 03000b28 "Dumping symbols..." | 03000b28 | 03000b28 | Present in the proto, Vol 1, SpongeBob 1 and Dora; absent from Yu-Gi-Oh and TMNT. |

## 6. ROM layout and the "file slot" convention

The build tool appends each binary file to the ROM and writes an 8-byte slot `{u32 ptr, u32 size}`
**immediately after the file**, so `ptr + size == slot address`. Code reaches a file only through its
slot address, held in a literal pool. Consequences:

- Episode containers are not referenced directly by code.
- Episode pointers are copied into the IWRAM episode table at boot (0x0800d3b4).

Files in the proto:

| ROM offset | Size | Contents |
|---|---|---|
| 0x01ab14 | 0xf3e540 | Episode 103 container |
| 0xf5905c | 0xeee098 | Episode 104 container |
| 0x1e47104 | 0xc78c | intro "DIGITAL VIDEO" movie |
| 0x1e53898, 0x1e54240 | 0x9a0, 0x268 | font metrics (no code reference to their slots) |
| 18 files 0x1e544b0..0x1e87114 | | GBA-BIOS-LZ77 menu graphics and palettes; sizes identical to Vol 1's |
| 0x1e871e0 | 0x2f78 | IWRAM image |

Vol 1 has 28 files. Its order is: intro, 0x9a0 and 0x268 font files, two extra small files (0x2814,
0x71c), episode 1, episode 2, the opening-theme video (0x1916f8), menu graphics, IWRAM image, then
0xFF padding to 32 MiB.

## 7. Dev leftovers and toolchain

Working scripts (not distributed): `strs.py` (string sets per build) and `bpwarn.py`
(breakpoints on the warning/print sinks).

### 7.1 Is the dump complete? Yes

- The file is 32022872 bytes = 0x1e8a158. Its last byte is the last byte of the IWRAM load image
  (ROM 0x1e871e0 + 0x2f78 = 0x1e8a158), which is the last section the linker or build tool places.
- Every shipped Hydrogen cart has the same layout followed by 0xFF padding to 32 MiB: Vol 1 has
  110628 bytes of 0xFF after its IWRAM image, Yu-Gi-Oh 175756.
- The proto is therefore either the unpadded build output or a dump with its trailing 0xFF trimmed.
  No data is missing either way.
- All 25 file slots point inside the file, and each satisfies `ptr + size == slot address`.
- Both episode containers end exactly at their slots: ep 103 at 0x01ab14 + 0xf3e540 = 0xf59054,
  ep 104 at 0xf5905c + 0xeee098 = 0x1e470f4. The intro ends at 0x1e47104 + 0xc78c = 0x1e53890.
- So the last scenes are complete.
- The cart boots, plays both episodes to the end-credit roll and returns to the menu in mGBA.
- The header has no title and no game code (0xa0..0xb3 zero, maker "01"). The fixed byte and
  checksum are valid enough for mGBA.

### 7.2 Toolchain

- **Compiler and runtime: ARM's tools, not GCC.**
  - The crt0 at 0x080000c0 sets the IRQ and SYS stacks, then runs a region-table copy/zero loop.
    The loop reads {load, exec, size} triples relative to the table position, with bit 0/1
    base-relative flags. This is the ARM linker's `__scatterload` scheme, not GCC's
    `__data_start`/`__bss_start` crt.
  - The C/C++ library is ARM's. It carries the C library message set: "Redirect: can't open: ",
    "User-defined signal 1/2", "Pure virtual fn called", "C++ library exception", "Illegal address",
    "Stack overflow", "Out of heap memory", "Termination request", and the FP exception names
    "Divide By Zero / Overflow / Underflow / Inexact Result / Invalid Operation".
  - The engine is C++ with virtual calls through `_call_via_rX` veneers (0x0800b938...) and
    Thumb→ARM interworking stubs at 0x0800d41c...
  - The most likely toolchain is ARM ADS 1.x (armcpp). The ARM/Thumb split is: Thumb for engine
    code in ROM, ARM for the hand-scheduled codec kernels copied to IWRAM.
- **Nintendo AGB SDK debug print.** The warning/assert sink 0x08002402 ("WARNING: %s (Function: %s
  File: %s Line %d)") formats into a buffer and calls an `AgbPrint`-style routine
  (0x0800d6f4..0x0800d8d4).
  - The routine writes the string to a ring buffer whose control block is at 0x09FE20F8 and whose
    protect register is at 0x09FE2FFE, with IME masked around each write. These are the addresses
    of Nintendo's AGBPrint protocol for the IS-AGB hardware debugger (mGBA also implements it).
  - This code is compiled unoptimised (r7 frame pointer, every local spilled), unlike the rest of
    the ROM: a separately built SDK object.
  - "ASSERTION FAILED FILE=[%s] LINE=[%d] EXP=[%s]" and "WARNING FILE=..." go to the same sink.
  - On retail hardware the writes go to unmapped ROM space and do nothing.
  - No warning fires during boot, menus, credits, chapter selection, FF/RW and 3000 frames of
    playback (breakpoints on 0x08002402, 0x0800d7ac and 0x0800d754).

### 7.3 Debug features present in the code

| Feature | Where | Reachable? | In shipped builds? |
|---|---|---|---|
| Memory-manager dump: "Dumping memory blocks...", "MEM> Block #%03d: ... at adress", new/delete[] mismatch warnings, "Heap memory corrupted" | 0x08001706, called from hyMemoryManager::AllocateBlock 0x080019a6 | Only on an allocation failure | yes, all |
| "Dumping OAM..." | 0x08009cc2 (hySpriteStreamer) | no callers | yes |
| "Dumping symbols..." + "%3d: %2d" (dumps a byte table; Inflate helper) | IWRAM 0x03000b28 | no callers, no veneer | Vol 1, SpongeBob 1, Dora: yes. Yu-Gi-Oh, TMNT: removed |
| Key-script record and playback: `hyKeys::StartRecordingScript`, `PlayScript`, `RewindScript`, "Key script buffer overflow!", "Only backwards rewind allowed for recording scripts!" | 0x08001418, 0x0800148e; hyKeys::Update 0x0800115a checks the script state on every frame | Start/Rewind have no callers, so no script is ever attached | yes, all |
| Assert/warning printf to AGBPrint | 0x08002402, 0x080023c8 | yes (asserts), but none fire in normal use | yes |

There is no debug menu or key combo. Every key-reading path was traced through MoviePlayer::Process
and the menus.

### 7.4 Unused or vestigial codec paths

- **16-bit codebook indices.** When a scene's ncb > 256, StreamBase sets this+0x10d8, and states 3
  and 0xe inflate `2 × blocks` bytes per frame. But every blit geometry reads 8-bit indices
  (`ldrb`), so a >256-entry scene would display wrongly. No scene on the cart uses it (all
  ncb = 256). Shipped builds have the same half-implemented path.
- **Blit modes 0-3 and 6.** Episode 103 uses only mode 5 (3x3); episode 104 and the intro use only
  mode 7 (4x2). The 4x4 and 4x3 bodies are present and match Vol 1 except the row-159 filler.
- **1x1 downsample path (0x03001a8c).** This is not dead: it drives the live thumbnail previews in
  the main and chapters menus.
- **IntroMovie fps check.** It accepts only 12 or 15 fps from header w1 bits 4..9 ("Invalid frame
  rate!"). DisplayMainMenu, DisplayChapters and PlayMovie carry the same check. 15 fps is never used.

### 7.5 Strings: proto-only versus Vol 1 and Yu-Gi-Oh

Proto-only:

- "Episode 103", "Episode 104" (placeholder titles).
- "- Parental lock (hold for 3 sec)" (Vol 1: "- Button lock").
- "\tCredits".
- "DisplayCredits()".
- "IntroMovie()" (Vol 1: "IntroMovie(void*)").
- "StreamBase::DecompressCodebook()" (Vol 1: "...(unsigned long)").
- "FontRenderer::ConvertTextToFont(char*)" with its "Unknown character!" assert.
- "FontRenderer::GetTextExtents(char*, unsigned long)".
- "NOT COMPATIBLE WITH", "Nintendo".

Vol 1-only:

- Episode titles "Chaos Control Freaks", "Sonic to the Rescue".
- "StreamBase::GetSceneFrameID(unsigned long)".
- The `C:\Dev` path spelling.

End-credit text is 1872 bytes at ROM 0x16ee6. Where Vol 1 has spaces in "SEGA SONICTEAM" and
"MI RA I", the proto has literal '?' characters ("SEGA?SONICTEAM", "MI?RA?I"). This is a lossy
code-page conversion of the Japanese middle dot (U+30FB), and it is visible on screen in the proto's
credit roll. Vol 1 replaced the dots with spaces.

The proto's code/rodata area runs to 0x1ab10, against Vol 1's 0x1075c. Both builds carry the
end-credit text in rodata, so that text does not explain the difference. The extra 0xa400 bytes in
the proto are compressed-looking data (byte entropy about 7 bits) in 0x0e000..0x16000. They were not
identified further.
