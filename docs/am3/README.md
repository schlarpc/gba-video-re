# AM3 Advance Movie Adapter (partial)

The Advance Movie Adapter (AM3) is a Japanese GBA cartridge adapter that plays movies from
SmartMedia cards. Its 12 KiB firmware (`am3_firmware.bin`, sha1
`f6dbcc3a4c6ed9623934416947f045aab7053f83`, dumped by shonumi, archive.org item
`am3_firmware`) identifies itself as "Actimagine Loader 1.0.620".

**This lane is partial.** Only the firmware is available, and the firmware is a signed-code
loader, not a movie player. The player, the copy protection, the video codec and the audio codec
all ship on each card as an ARM program (`AEXE`). No AM3 card has been dumped publicly, so this
repository documents the loader, the adapter interface and the card format as far as they are
known, and has no decoder, no manifests and no stream statistics for AM3.

## Findings

- The firmware loads card file index 0, finds an `ASIG` chunk (256-byte RSA-2048 signature,
  e = 3, modulus in the firmware) and an `AEXE` chunk (the player, up to 256 KiB, loaded to
  EWRAM 0x02000000). It checks the SHA-1 of `AEXE` against the signature result, removes a
  32-bit LFSR XOR scramble seeded from the same result, and jumps to the player.
- Corrections to the prior art (shonumi): the firmware does no decoding, has no DES code and no
  SmartMedia-ID check; `ASIG` is a real signature, not a magic; one register handshake can cover
  two register writes; the boot RAM-clear routine has swapped operands and clears nothing. The
  blue screen is the loader's only error path, so the red and green screens come from the player.
- The signature check is weak: e = 3 with no padding check, and only 20 bytes of the result are
  compared. Synthetic test cards were built locally to test the loader in emulators without card
  data. The test cards and the tooling that builds them are not part of this repository.
- Lineage: the loader shares only ARM C library routines (divide, memcpy) with the FVMV
  (Pokemon) carts, and no code with the VX or Majesco players. The codec lineage needs a card.
- Verified locally: firmware SHA-1, LFSR and RSA routines against reference implementations and
  a C reimplementation (the only mismatches are degenerate inputs that real signatures cannot
  reach); the whole boot of 24 synthetic cards agrees between the firmware in unicorn and the
  reimplementation.
- Emulation: GBE+ supports AM3. An mGBA AM3 adapter patch was written and boots the real
  firmware, but it is not included here (no savestates; not cross-checked against GBE+).

## Loader timing

Measured locally in the patched mGBA with synthetic cards, from power-on until the `AEXE` runs:
about 0.29 s fixed (mostly two 2048-bit Barrett reductions) plus about 6.4 ms per KiB of `AEXE`
(window copy, SHA-1, LFSR): 0.30 s for 1 KiB, 0.72 s for 64 KiB, 1.98 s for the 256 KiB maximum.
The adapter read latency was assumed (2048 cycles per KiB) and is the main uncertainty. The
loader heap uses 2,884 bytes of IWRAM for one RSA operation. Playback cannot be measured without
a card.

## Documents

| file | contents |
|---|---|
| [player.md](player.md) | The firmware: image layout, the cartridge register interface, boot sequence, loader routines, signature scheme, timing, fingerprint against the GBA Video families, what the player does per prior art |
| [format.md](format.md) | The card: SmartMedia NAND layout and ECC, logical image, file indexing through `INFO.AM3`, the `ASIG`/`AEXE` chunk format and the descramble, what is known of `00.AM3` and the movie files |
| [prior-art.md](prior-art.md) | shonumi's article, technical documentation and GBE+, compared element by element |
| [features.md](features.md) | Feature matrix (loader known; player builds need cards) |

## Open issues

Everything beyond the loader is blocked on a card dump: the player (`AEXE`) and its builds, the
copy-protection check (`00.AM3`, the SmartMedia ID, the "DES" shonumi reports), the movie
container, the video and audio codecs, the output stage, and every stream statistic. Also open:
the name and contents of card file index 0 apart from its two chunks, other chunk types, the
`INFO.AM3` header fields, and which read-pointer semantics and GamePak IRQ timing the player
needs.

What would unblock it, in order of preference (one movie card is enough to start; cards from
different release dates allow player builds to be compared):

1. A logical disk image of one AM3 movie card, made with `dd` through a USB SmartMedia reader
   following shonumi's guide (https://shonumi.github.io/articles/am3_dumping_guide.html):
   exactly 32,768,000 bytes (32 MB card) or 65,536,000 bytes (64 MB card), MBR + one FAT12
   partition with `INFO.AM3`, `00.AM3`, `01.AM3` and so on. Do not mount the card read-write
   before dumping.
2. Optionally, the card's 16-byte SmartMedia ID (`SMID.KEY`), to check the copy-protection path.
3. Alternatively, the extracted files with their original 8.3 names, or a raw NAND dump
   including the spare area (528-byte pages; layout in [format.md](format.md) 1).

Useful first cards: am3-00001 / am3-00002 (Detective Conan ep. 1/2, the first release), and a
2005-2007 64 MB Pokemon movie (a late player build, with the Game Boy Player lock shonumi
reports). No-Intro lists the whole AM3 SmartMedia set as undumped, and no card images were
found on archive.org.
