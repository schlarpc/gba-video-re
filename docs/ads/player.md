# ADS player program (Majesco GBA Video, LZMA lineage)

Status: v1, 2026-11-07. Addresses are run addresses. Unless marked otherwise they are from
*Super Robot Monkey Team Hyper Force Go! Vol 1* (build `bde72f6aba53`). Decompiler output and
disassembly were working files and are not included; regenerate them from your own ROM with
`tools/ads/images.py` (RAM images) and Ghidra (`tools/hydrogen/ghidra/gh_build.py` shows the loading recipe).

## 0. Builds

The classifier's build id hashes the executed player code. Inside a build, `main.bin` (EWRAM and
IWRAM images) is byte-identical apart from its GBA header.

| build | carts | IWRAM image | audio output | stream header w1 |
|---|---|---|---|---|
| `bde72f6aba53` | SRMTHFG 1, Proud Family 1 | 0x2DEC | pairs to VRAM | layout B |
| `17d6ab8a82ba` | Disney Channel Collection 2 (Rev 5), 64 MiB Matrix cart | 0x2A98 | pairs to VRAM | B |
| `882a917e99c5` | Disney Channel Collection 1 | 0x2DD4 | bytes | A |
| `e0080e16dac3` | CN Collection Limited / Platinum / Premium / Special Edition, Volume 2 (USA) | 0x2E08 | bytes | A |
| `bfa01253c3c5` | CN Edition Platinum (F), TMNT Le Demenagement (F), Yu-Gi-Oh! Yugi vs Joey (F) | 0x2AAC | pairs | B |
| `e3117652326c` | CN Edition Premium (F), CN Edition Speciale (F) | 0x2AAC | pairs | B |
| `69261e8948ae` | Nicktoons Collection 2, SpongeBob 3 | 0x2D9C | bytes | A |
| `f364f46e4bef` | Nicktoons 3 | 0x3AC0 | pairs | B |
| `e19d3efb314e` | Dragon Ball GT 1 | 0x2E04 | bytes | A |
| `73b2d7da03af` | TMNT The Shredder Strikes (Proto) | 0x2A98 | pairs | B |
| `22db83f74bdf` | Jimmy Neutron 1 (Hydrogen engine, ADS codec) | 0x3498 | bytes | A |
| `8831b3ba8f06` | Yu-Gi-Oh! Noah's Final Threat (Proto) (Hydrogen engine, ADS codec) | 0x3494 | bytes | A |

*Cartoon Network Collection - Special Edition (USA) (Rev 5)* was not available and is unverified.

**Noah's Final Threat (Proto).** The classifier scored it 0.85 ADS. It is a Hydrogen-engine
cartridge (Thumb C++ "Hydrogen Library" player running from ROM, Hydrogen resource tables, the
Hydrogen context-table coding for audio) whose video streams use the ADS codec: codec nibble 7,
LZMA blobs, the same LZMA core as the ADS builds. Its player build differs from Jimmy Neutron's
only in addresses (same LZMA, VQ and audio code). So it is the same hybrid as Jimmy Neutron, not
a separate lineage.

## 1. Boot

1. ROM header branch to 0x08000200: a bootstrap of about 3.6 KB (ARM and Thumb, before the SFCD
   archive) searches the SFCD directory for `main.bin`, copies it to EWRAM 0x02000000 and jumps
   to it.
2. `main.bin` starts with its own GBA header (title zeroed) and an ARM entry. Its crt0 walks a
   copy table inside `main.bin`: `{src, 0x03000000, len}` copies the IWRAM image (ARM codec code),
   `{0, bss, bss_len}` clears BSS. Example (SRMTHFG): IWRAM = `main.bin[0xA658:+0x2DEC]`, BSS
   0x03002DEC + 0x3F4. `tools/ads/images.py` finds the table by its shape.
3. The C++ player (Thumb, EWRAM) sets WAITCNT 0x4014, installs the IRQ handler (IWRAM, address
   written to 0x03007FFC), opens `menuresources.mmstr` (images, the `Settings` text) and
   `commonresources.mmstr`, then plays `logo majesco.mmstr` and, if present, `jingle.mmstr`, then
   shows the title screen and the main menu.

Hydrogen-engine carts (Jimmy, Noah) instead run Thumb code from ROM and copy only the IWRAM image
(descriptor right before the image near the ROM end, same format).

## 2. Memory map during playback (SRMTHFG)

| range | use |
|---|---|
| 0x02000000-0x0200D444 | `main.bin`: EWRAM code (Thumb C++ player, menus, settings parser, audio block headers, ROM reader) and data |
| 0x0200E3A0 +0x20000 | 128 KiB LZMA ring for the index planes ([format.md](format.md) 5.3) |
| 0x0203xxxx | codebook slots, 0x1800 bytes each, LZMA output for the codebook blob |
| 0x03000000-0x03002DEC | IWRAM image: LZMA (0x03000404 init, 0x03000554 decode, 0x03001C58 core), codebook converter 0x03000610, unpredict 0x03000D8C / 0x03000DF4, IRQ handler 0x0300160C, VQ blit 0x03001748, ADPCM loop 0x03002B14, ARM->Thumb veneers |
| 0x03002DEC- | BSS: video player object 0x0300669C (chunk ring, positions), LZMA objects 0x0300672C (index planes) and 0x03006788 (codebook), clamp/brightness table at video object +0x348, 16x256 dither table at 0x03006DA8, ADPCM decoder state 0x03002DF4 |
| 0x06000000 +0x12C00 | mode-3 framebuffer 240x160 |
| 0x06012C00 +0x2000 | RGB555 codebook, 256 entries x 32 bytes ([`vq.md`](../majesco/vq.md)) |
| 0x0601606C | LZMA probability array (0xA36 x u16) |
| 0x060174D8.. | audio PCM ring (DMA1 source); byte-output builds keep it in IWRAM (CN: 0x030045D0) |
| 0x05000000 | BG palette; entries 0..23 are copied to line 159 by the 53-row blits, kept at 0 |

## 3. Playback object and the decode schedule

The video player object (0x0300669C in SRMTHFG; layouts differ per build) holds:

- an 11-entry ring of upcoming chunks, 12 bytes each `{chunk pointer, w0, w1}` at +0x648, read
  index byte at +0x54 (the blit takes the mode from the current entry's `w0 & 7`);
- the index-plane ring: base +0x78, display offset +0x7C (advanced by one plane per frame),
  mask +0x80 (0x1FFFF);
- the frame counter +0x64 and frames left in the chunk +0x60;
- the two LZMA decoder objects (+0x90 index planes, +0xEC codebook), C++ objects with a vtable
  of self-relative offsets (0x03002AE8: init, decode, set-probabilities, size, ...);
- the codebook state machine (+0x6D8: 0 idle, 1 LZMA-decoding the next chunk's codebook into
  slot `+0x6E0 + slot*0x1800`, 2 unpredict + DMA3 copy of the slot to the working buffer).

Per VBlank the main loop runs (function 0x02007304): every 5th VBlank it blits the current plane
(0x03001748), then decodes audio for the PCM ring, then asks the index LZMA object for about one
more plane (0x02002130 -> 0x03000554). The LZMA core stops early when the IRQ code raises the
deadline flag ([format.md](format.md) 5.3), so a heavy plane is finished on the next call. At a chunk
change the next chunk's codebook goes through states 1 and 2 ahead of time; the RGB conversion
into VRAM (0x03000610) runs at the first blit of the new chunk.

**Late codebooks.** If the codebook pipeline falls behind, the blit uses whatever the codebook
slot holds. This never happens in the 19 ADS carts (every frame matches). It happens in the Noah
proto's intro, which has a burst of 1- and 2-frame chunks (frames 193-205): 41 frames are shown
with the codebook of the chunk 8 positions earlier (the Hydrogen engine's 8-slot codebook ring),
see [README.md](README.md) and `tools/ads/explain_stale.py`.

## 4. Interrupts and timers (playback)

| register | SRMTHFG | CN USA |
|---|---|---|
| IE | 0x3031: VBlank, Timer1, Timer2, Keypad, Game Pak | 0x1031: VBlank, Timer1, Timer2, Keypad |
| Timer0 | reload 0xFC00, prescaler 1: 16384 Hz sample clock (no IRQ) | same |
| Timer1 | count-up from Timer0, IRQ: sample-count tick for the PCM ring | same |
| Timer2 | prescaler 64 with IRQ (player time base) | stopped (control 0) when sampled during playback |
| DMA1 | ring -> FIFO A (0x040000A0), 32-bit, repeat, FIFO timing (control 0xB640) | same, ring in IWRAM |
| DMA3 | codebook slot -> working buffer, 0x600 words | same |
| SOUNDCNT_H / X | 0x0304 (DirectSound A 100 %, both speakers, Timer0), 0x0080 | same |
| KEYCNT | 0xC00F: IRQ when A+B+SELECT+START are all down (soft reset) | same |

The IRQ handler (0x0300160C) reads IE & IF, acknowledges the highest-priority pending source,
masks it in IE, switches to system mode and calls the handler from a table, then restores IE
and the mode.

## 5. Timing

- Video: a frame is blitted every 5th VBlank: 59.7275 / 5 = 11.9455 fps. In 84 of the 96
  streams no blit is ever delayed; 12 streams of A-layout builds have one or two frames shown a
  slot late (16 of 621,366 windows, [perf.md](perf.md)). No frame is ever skipped.
- Audio: 16384 Hz. The PCM ring is filled from the main loop in pieces of 1371/1372 samples
  per video frame plus refills at ring wrap; DMA1 drains it at the sample rate.
- The decoders run ahead: the index-plane LZMA keeps about one plane ahead of the blit, the
  codebook of the next chunk is prepared before the chunk starts, the audio stays a ring ahead.

## 6. Display

DISPCNT 0x0443: mode 3, BG2, OBJ 1D mapping (OBJs are used by the on-screen display: brightness
bar, pause icon). The blit writes the framebuffer directly (single buffer). For the 4x3 and 3x3
geometries line 159 is filled with BG palette entries 0..23 repeated 10 times; the player keeps
them 0, so the line is black. The French builds and the TMNT proto take the codebook pointer from
the player object and add a framebuffer offset (+0x90); both are 0 for 240x160 streams.

## 7. Input

From the in-game instructions screen and the code: START pause, SELECT back to the menu, A/B
brightness up/down (11 levels, gain table {128,...,384}, default level 5 = 256), L/R previous/next
chapter, right fast-forward, left rewind, and a button lock (hold for 3 s). Holding A+B+SELECT+START
raises the keypad IRQ (soft reset).

The brightness level selects the gain of the VQ clamp table ([`vq.md`](../majesco/vq.md) 2.2). The bfa01253c3c5
build adds a gain bias from the `BaseBrightness` setting ([format.md](format.md) 3.2: +51 on CN Edition
Platinum (F)). Jimmy Neutron's boot code (0x08008032) writes 51 to its gain-bias variable
(0x03003738) right after the logo, so its intro and episodes play at gain 307.

## 8. Menus and episode selection

The main menu lists the episodes two at a time ("More" scrolls); each entry has a "Chapters"
submenu. SELECT shows info. Choosing an entry shows the instructions screen, which waits for any
key; the episode then starts from frame 0 (or a chapter's chunk). The capture tool drives this
with: `(k-1) x DOWN`, A, and A again only while nothing has been blitted yet.

The menu order is not always the `movie_N` order and the scroll page swallows a key press:
on Nicktoons Collection 2 and SpongeBob 3 the fourth entry is reached with UP (wrap) from the
first; DOWN x3 lands on the third entry again. Jimmy Neutron needs about 90 frames between
presses. All of this is navigation only; the capture tool records which stream actually played
(`tools/ads/identify.py`).

## 9. Matrix mapper use (Disney Channel Collection 2, Rev 5)

Disney 2 is a 64 MiB "Matrix" cartridge with all data in the first 32 MiB (the upper half is
0xFF; the No-Intro dump is the 32 MiB). Its player reads every stream byte through the mapper
window rather than linearly: a ROM reader object (0x02004BDA) maps a range by waiting for the
busy flag (bit 12 of 0x08000180), writing the ROM offset (0x08800184), the window address
(0x08800188), the number of 512-byte pages (0x0880018C) and command 1 (0x08800180), then
either copies the bytes out (LZMA input is staged into EWRAM buffers) or returns the window
pointer (the ADPCM bit reader reads its words straight from 0x08001000..0x080011FF). Each reader
owns a page range of the 8 KiB window (bytes +4/+5 of the object). The other builds use the
same reader interface with a linear-ROM implementation. mGBA enables its Matrix emulation only
for ROM files larger than 32 MiB, so the emulator is given the dump padded with 0xFF to 64 MiB
(`tools/ads/carts.py` `emu_rom_path`); with the plain dump the cart shows a white screen.

## 10. Codec entry points per build

| build | LZMA init / decode / core | codebook convert | unpredict Y / C | VQ blit | ADPCM loop |
|---|---|---|---|---|---|
| `bde72f6aba53` | 0x03000404 / 0x03000554 / 0x03001C58 | 0x03000610 | 0x03000D8C / 0x03000DF4 | 0x03001748 | 0x03002B14 |
| `882a917e99c5` | 0x03000404 / 0x03000528 / 0x03001C5C | 0x030005DC | 0x03000D74 / 0x03000DF0 | 0x0300174C | 0x03002B1C |
| `17d6ab8a82ba` | 0x03000548 / 0x03000698 / 0x03001904 | 0x030009E8 | 0x03001160 / 0x030011C8 | 0x030013D4 | 0x030027C0 |
| `e0080e16dac3` | 0x03000404 / 0x03000528 / 0x03001CA8 | 0x030005E4 | 0x03000D80 / 0x03000DFC | 0x03001798 | 0x03002B50 |
| `bfa01253c3c5`, `e3117652326c` | 0x03000090 / 0x030001E0 / 0x0300194C | 0x0300029C | 0x03000A14 / 0x03000A7C | 0x030013EC | 0x03002808 |
| `69261e8948ae` | 0x030011C0 / 0x030012E4 / 0x03001C64 | 0x030004FC | 0x03000C80 / 0x03000CF0 | 0x03001768 | 0x03002AE8 |
| `f364f46e4bef` | 0x03000404 / 0x03000554 / 0x030028FC | 0x03000610 | 0x03000D8C / 0x03000DF4 | 0x030023EC | 0x030037D0 |
| `e19d3efb314e` | 0x030011E0 / 0x03001304 / 0x03001CA4 | 0x030004F8 | 0x03000C90 / 0x03000D0C | 0x03001794 | 0x03002B4C |
| `73b2d7da03af` | 0x03000090 / 0x030001E0 / 0x03001904 | 0x0300029C | 0x03000A14 / 0x03000A7C | 0x030013D4 | 0x030027C0 |
| `22db83f74bdf` (Jimmy) | (Thumb, ROM) / 0x030021AC core | 0x03000928 | 0x03001214 / 0x03001178 | 0x03001CB0 | 0x03003058 |
| `8831b3ba8f06` (Noah) | (Thumb, ROM) / 0x030021A8 core | 0x03000924 | 0x03001210 / 0x03001174 | 0x03001CAC | 0x03003054 |

`tools/ads/emu/builds.py` finds the blit and the ADPCM loop by code signature; `tools/ads/builddiff.py`
locates the rest and compares them.

## 11. Build differences that touch the codec

`tools/ads/builddiff.py` compares every routine above with SRMTHFG's after normalising branch targets
and literal-pool addresses. Result (instruction differences / reference length):

| routine | differences |
|---|---|
| LZMA init, decode, core | identical in every SFCD build except `69261e8948ae` (Nicktoons 2 / SpongeBob 3): its core has no input-budget check (41 instructions differ, all in the entry/exit bookkeeping) and its init stores the input pointer differently. The Hydrogen-engine core is the same as `69261e8948ae`'s. Decoding results are identical (the core math is unchanged) |
| codebook convert | identical in the B-layout builds; the A-layout builds (`882a917e99c5`, `e0080e16dac3`, `e19d3efb314e`, `69261e8948ae`, Jimmy, Noah) are a different compilation of the same algorithm (same tables, same output) |
| unpredict | same loops; the A-layout chroma routine computes the chroma count with a table instead of a switch |
| VQ blit | B-layout French/proto builds take the codebook pointer from the object and add a framebuffer offset (28-43 instructions); the A-layout builds read the chunk word through a pointer. Output identical |
| ADPCM loop | identical except the output store (pairs vs bytes, 17 instructions) |
| ADPCM block header | identical in all SFCD builds (`MJ_CTAB_VLC`); the Hydrogen-engine builds use the flat coding (`MJ_CTAB_FLAT`) |
| brightness | `bfa01253c3c5` adds the `BaseBrightness` bias; Jimmy adds a fixed 51 after the logo; the others use the plain gain table |
| chunk drivers (Thumb) | rewritten between layouts (codebook state machine, ring sizes); behaviour is the same apart from timing |

[features.md](features.md) has the per-build matrix.
