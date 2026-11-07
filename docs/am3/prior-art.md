# AM3: prior art and how this work compares

Sources:
- **[A]** shonumi, "Edge of Emulation: Advance Movie Adapter",
  https://shonumi.github.io/articles/art28.html (2022)
- **[D]** shonumi, "Advance Movie Adapter Technical Documentation 0.4" (Dan Docs, 2022-01-02),
  in the GBE+ tree at `src/docs/technical/Advance_Movie_Adapter.txt`
- **[G]** GBE+ emulator source, `src/gba/am3.cpp` + `mmu.cpp` (GPLv2; read for comparison only,
  no code taken), commit 8b748c00 (2026-10-01)
- **[S]** shonumi, "AM3 SmartMedia Card Dumping Guide",
  https://shonumi.github.io/articles/am3_dumping_guide.html
- **[N]** No-Intro wiki, "Nintendo - Game Boy Advance SmartMedia undumped"
- **[Q]** quatric, "The Mobiclip Formats" gist (no AM3 coverage; section 4.5 covers
  Actimagine's pre-Nintendo `.mo` MOC1-3 containers)
- **[L]** Linux `drivers/mtd/nand/ecc-sw-hamming.c` (SmartMedia ECC order; used only as a test
  oracle)

## Emulator support

| Emulator | AM3 support | Input |
|---|---|---|
| GBE+ | Yes, since 2022 [A][G] | firmware + `dd` image (or a folder of files) + 16-byte `SMID.KEY`, or `--auto-gen-key`, which captures the expected ID from RAM |
| mGBA (0.11-dev) | No | Not present upstream. An AM3 adapter patch was written for this work and used locally; it is not part of this repository |
| VBA-M, NanoBoyAdvance, others | No (searched) | |

## Element by element

| Element | Covered by prior art? | Agrees? | Corrected / new here |
|---|---|---|---|
| Firmware dump (12 KiB, 1 KiB banking through STAT 0x09, EOF bit 8) | [A][D] | Yes | New: the layout of the dump. Only 0x23F0 bytes are loader. fw+0x23F0 repeats block 0, then 16 ID bytes and 2 KiB of non-ARM data that the loader copies but never uses |
| Register map (+0x00..+0x20, commands 0x01/03/05/09/0B) | [D][G] | Yes, for every register the loader touches | Corrected: one 0x0B handshake can cover more than one register write (the loader writes OFFS and SIZE together). [D] says the firmware does one handshake per write |
| Read-pointer semantics (OFFS, EOF wrap, negative clamp) | [D][G] | Consistent | GBE+ applies OFFS at register write and only when the value changes. We apply it at command time. The loader cannot tell the two apart; it needs the player to decide |
| PHI output 16.78 MHz needed | [D] | Yes | New: the boot block sets it (WAITCNT = 0x1800) |
| Boot: "loads the rest of the firmware into RAM" | [A][D] | Yes | New detail: block 0 -> EWRAM stub -> IWRAM loader. The RAM-clear routine has its operands swapped and clears nothing |
| "The 12 KiB firmware is responsible for loading, decrypting, decoding, and playing" [D] | [D] | **No** | Corrected: the firmware only loads and verifies. Decoding, playing, the SMID check and DES are in `AEXE`, which is loaded from card file 0 |
| `ASIG` | [A] (a 4-byte magic the firmware checks; meaning unknown) | Partly | New: `ASIG` is a 256-byte RSA-2048 signature, e = 3, Barrett reduction; the result holds an LFSR seed + SHA-1 |
| `AEXE` | Not mentioned | | New: the player program, max 256 KiB, loaded to 0x02000000, scrambled with a 32-bit Galois LFSR (mask 0xA3000000) |
| Blue error screen | [A] (from a wrong `ASIG` read) | Yes | New: palette[0] = 0x7C00 is the loader's only failure path. Red and green screens must come from the player. Also new: the loader's 7-dot progress bar |
| "128-bit DES", `00.AM3` "SMID" lock file, SMID compare | [A][D] | Not checkable from the firmware | The firmware contains no DES, no SMID read and no `00.AM3` access. All of that is in `AEXE` |
| GamePak IRQ per transfer | [A][D][G] | Not used by the loader | Modelled in the local mGBA patch both ways (IRQ at command completion, or at issue as GBE+ does). Which one is right needs the player |
| Card filesystem: MBR -> FAT12 -> root dir; INFO.AM3 order table at 0x200, name at +8 | [D][G] | Implemented the same way | New: FAT16 support, cluster chains, raw-NAND (SSFDC) input with block remapping and 1-bit ECC correction |
| Dump format, sizes 32,768,000 / 65,536,000 | [S] | Yes | Added: the raw NAND+OOB alternative and its mapping ([format.md](format.md) 1, [README.md](README.md)) |
| Public card dumps | [N]: all undumped | Yes, none found | |
| Signature security | | | New: e = 3 without a padding check is weak; synthetic test cards that the firmware accepts were built locally for emulator testing and are not distributed |
| Codec lineage | [A]: Actimagine developed the codec | | New: the loader shares ARM C library routines (divide, memcpy) byte for byte with the FVMV (Pokemon) carts, and nothing with the VX carts' loader. The codec itself needs a card |
| Video/audio formats | None | | Open, needs a card |
