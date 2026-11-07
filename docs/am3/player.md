# AM3 Advance Movie Adapter: firmware and adapter interface

Scope: the 12 KiB adapter firmware (`am3_firmware.bin`, sha1
`f6dbcc3a4c6ed9623934416947f045aab7053f83`, archive.org item `am3_firmware`, dumped by shonumi)
and the cartridge interface it drives. All addresses are GBA addresses unless marked "fw+",
which is a byte offset into the firmware file.

**Main result.** The firmware is not a movie player. It is a small signed-code loader that
identifies itself as `Actimagine Loader 1.0.620 ->actimagine.com`. It loads an ARM program
(`AEXE`) from the SmartMedia card, checks an RSA signature over it, removes a scramble, and
jumps to it. The movie player, the SmartMedia-ID copy-protection check, the DES code that
shonumi describes, the video decoder and the audio decoder all ship on every card inside
`AEXE`. So no codec fact can come from the firmware. Section 7 lists what needs a card dump.

Status labels used below:
- **[fw]**: read from the firmware code and confirmed by running it (in a unicorn oracle and in an
  mGBA build with an AM3 adapter model; neither tool is part of this repository).
- **[prior]**: taken from shonumi (Dan Docs / GBE+) and consistent with the firmware, but not
  observable from the firmware alone.
- **[assumed]**: our modelling choice, needs hardware or card data to confirm.

## 1. Firmware image layout

| fw+ range | Size | Content |
|---|---|---|
| 0x0000-0x03FF | 1 KiB | Block 0, mapped at 0x08000000 at power-on. Standard GBA header: Nintendo logo, empty title and game code, maker `01`, fixed byte 0x96, valid complement 0xF0. Boot code at 0x080000C0. **[fw]** |
| 0x0400-0x23EF | 8176 B | The loader. Copied to IWRAM 0x03000000. 45 functions, ARM code with one Thumb trampoline. **[fw]** |
| 0x23F0-0x27EF | 1 KiB | A byte-identical copy of block 0. |
| 0x27F0-0x27FF | 16 B | `98 ca 21 02 "Ym450209" 04 23 09 99`. Looks like an ID or date stamp; not referenced. |
| 0x2800-0x2BFF, 0x2C00-0x2FFF | 2 x 1 KiB | Two identical blocks of non-ARM data that contain the strings `FAT16` and `FAT12`. These are probably the adapter controller's own code or tables, exposed by the dump read. The GBA loader copies them to IWRAM 0x03002000-0x03002BFF but never references them (no literal or computed address points above 0x03001FF0). |

So the useful firmware is 0x23F0 bytes. The loader stops copying when the adapter raises the
end-of-file flag after 11 blocks (fw+0x400..0x2FFF), which is why the dump is 12 KiB.

## 2. Cartridge interface

The adapter decodes only 2 KiB of the cartridge address space and mirrors it over
0x08000000-0x09FFFFFF **[prior]**. The low 1 KiB is a data window. The high 1 KiB holds
registers, mirrored every 64 bytes. The firmware uses the window at 0x08000000 and the
register mirror at 0x08010400 **[fw]**. The loader reads the window base address from
register +0x00 instead of hard-coding it.

| Reg | Width | Name (shonumi) | Firmware use **[fw]** |
|---|---|---|---|
| +0x00 | u32 | AM_BLK_ADDR | read: base of the data window (0x08000000) |
| +0x04 | s32 | AM_SMC_OFFS | written before each card command: 0 = read the next block, +k*blk = skip, -total = rewind |
| +0x08 | u16 | AM_BLK_SIZE | read: block size (0x400). Used for both firmware and card blocks |
| +0x0A | u16 | AM_SMC_SIZE | written: card read size (0x400), or 0 for a seek-only command |
| +0x0C | u16 | AM_SMC_FILE | written: file index (0) |
| +0x0E | u16 | AM_SMC_EOF | not read by the loader. **[prior]**: valid bytes in the last card read |
| +0x10 | 16 B | AM_SMID / AM_FILE_SIZE | not read by the loader (the player reads them, **[prior]**) |
| +0x20 | u16 | AM_BLK_STAT | command/status, see below |

`AM_BLK_STAT` protocol, exactly as the loader drives it **[fw]**:
- **Data command** (`0x09` firmware block, `0x01` card block): write the command, read it back,
  and write it again until the read-back equals the command. Then poll until bit 0 clears.
  Then copy the window. For firmware blocks, bit 8 set afterwards means end of image.
- **Register write** (`0x0B`): write 0x0B until it reads back 0x0B. Wait until bit 14 is set.
  Write the register(s). Wait until bit 14 clears, then until bit 0 clears. The card-read helper
  writes **two** registers (OFFS and SIZE) inside one handshake, then issues command 0x01.
  shonumi's note that every register write gets its own handshake is therefore not true for
  this firmware.
- Command codes 0x03 (SMID mode) and 0x05 (file-size mode) are not used by the loader
  **[prior]**.
- Each non-zero STAT write raises the GamePak IRQ (IF bit 13) **[prior]**. The loader leaves
  IE/IME at 0 and polls instead. shonumi reports that the player relies on it.

**Card read pointer semantics [assumed, consistent with the firmware and GBE+]:** the command
0x01 does `ptr += OFFS; window = file[ptr : ptr+SIZE] (zero padded); ptr += SIZE`. When the
pointer reaches the end of the file, bit 8 is set and the pointer wraps to 0. A negative result
clamps to 0 and sets SMC_EOF to 0. GBE+ applies OFFS when the register is written and only if
the value changed. That variant gives the same result for every access pattern the loader uses.

## 3. Boot sequence

1. **Block 0 (cart, ARM) [fw].** Sets the IRQ stack (0x03007FA0) and the SYS stack (0x03007F00).
   It writes 0x1800 to WAITCNT (0x04000204). That sets the PHI terminal output to 16.78 MHz
   (bits 11-12), which the adapter needs (shonumi), and leaves WS0 at the default 4/2. It then
   calls a "fill" routine for IWRAM, EWRAM, palette, OAM and VRAM. That routine has its operands
   swapped (`str r0,[r1],#4` with r0 = the region base, r1 = 0), so it writes into the BIOS area
   and **clears nothing**. Real hardware ignores the writes. Next it copies its own block-loader
   stub (fw+0x170..0x23F) to EWRAM 0x02000000 and jumps there.
2. **Stub (EWRAM) [fw].** Reads AM_BLK_SIZE and AM_BLK_ADDR. It issues command 0x09 repeatedly
   and copies each 1 KiB window to IWRAM 0x03000000+, until STAT bit 8 is set or 32 KiB has been
   copied. The first 0x09 returns fw+0x400, because block 0 counts as already read. It then
   jumps to 0x03000000.
3. **Loader entry (IWRAM) [fw].** Sets the stacks again and switches to Thumb for a trampoline
   that calls `main` at 0x03001A94 (ARM). `main` sets DISPCNT = 0x0404 (mode 4, BG2), backdrop
   palette[0] = black, palette[1] = white, and calls the loader (0x03001970). If the loader
   returns (any failure), `main` sets palette[0] = 0x7C00 and loops forever at 0x03001AD0.
   **This blue backdrop is the loader's only error indication.** It matches the "blue
   screen" shonumi saw before his emulation returned the right `ASIG` data. His red and green
   error screens therefore come from the player (`AEXE`), not from this firmware.
4. **Loader [fw].** A progress bar of 7 dots: halfword writes of 1 to VRAM 0x06009510 +4*i,
   which is row 159 (the bottom line) in mode 4. One dot per completed step:
   1. open card file 0: register write AM_SMC_FILE = 0;
   2. find the first `ASIG` chunk and read it into a 256-byte buffer. It must be exactly
      0x100 bytes;
   3. set the heap to 0x03004000 and compute `ASIG^3 mod n` in place (section 4);
   4. find the next `AEXE` chunk, read it to EWRAM 0x02000000 (at most 0x40000 bytes, and more
      than 3);
   5. rewind the card file (OFFS = -bytes_read, SIZE = 0, command 0x01). Then
      SHA-1(AEXE as stored) and compare it with bytes 4..23 of the RSA result;
   6. descramble AEXE with the LFSR seeded from bytes 0..3;
   7. call 0x02000000 (ARM, `bl`). A return from AEXE falls into the blue-screen loop.

No interrupt is ever taken. The boot code sets CPSR with I = F = 0, but IE and IME keep their
reset value 0, and the loader never writes them.

## 4. Loader routines

| Address | Role | Notes |
|---|---|---|
| 0x03001700 | stream init | {regs, total, blk, win_base, cur, end}; cur = end, so the first read loads a block |
| 0x03001608 | select file | 0x0B handshake + AM_SMC_FILE |
| 0x0300165C | card command | 0x0B handshake, OFFS and SIZE, then command 0x01 |
| 0x03001790 | stream read | copies from the window and loads the next block (OFFS 0, SIZE blk) when it is empty; total += blk |
| 0x03001838 | stream skip | inside the window: move cur. Otherwise one seek command (OFFS = blk*floor(rest/blk), SIZE 0) then one read. **total is not increased by the seek**, so a later rewind would be short. This is a latent bug; the loader never seeks before its rewind if ASIG is the first chunk |
| 0x030018CC | chunk search | read {u32 tag, u32 size}. If tag matches: fail if size > max, otherwise read the payload and return size. If not: fail if `size & 0xF0000003`, otherwise skip. No end-of-file check: a missing chunk makes the loader spin over the wrapped file forever, without a blue screen |
| 0x03000D8C | RSA public op | in-place, big-endian input of `len` bytes. Output is big-endian **without leading zero bytes**, written from the start of the buffer, and the tail of the old buffer stays |
| 0x03000490 | x^3 mod n | Barrett reduction, twice: x² mod n, then (x² mod n)·x mod n |
| 0x030012A0 | SHA-1 | standard FIPS 180-1 (fuzzed against hashlib: 200/200) |
| 0x03001564 | LFSR descramble | for each 32-bit LE word: `w ^= s; s = (s >> 1) ^ (s & 1 ? 0xA3000000 : 0)`. Uses floor(len/4) words; a 1-3 byte tail stays as stored |
| 0x03001954 | malloc | bump allocator; the pointer lives at 0x03000000 (over the entry branch); free is a no-op |
| 0x03001AD4, 0x03001C0C, 0x03001B8C | divide, memcpy, memcmp | ARM C library routines. The divide and memcpy bodies are byte-identical to the ones in the FVMV (Pokemon) carts, see section 6 |

**Bignum format [fw].** {u32 *limbs (LE), int len, int cap, int sign}. The heap
(0x03004000..) grows without bounds. One RSA operation uses about 2.8 KiB.

**Barrett preconditions [fw, found by fuzzing].** The code assumes full-length operands. It
does something other than `x^3 mod n` when:
- the square of the input is shorter than 63 limbs, or an intermediate quotient is shorter
  than 65 limbs. A word-count underflow then runs a copy loop for about 2^32 iterations
  (hang);
- `floor(x / b^63)` or the Barrett quotient q3 is 0. It then multiplies by a zero-length
  number and reads one stale heap limb;
- `(x mod b^65) < (q3*n mod b^65)`. The magnitude of a negative difference is kept. The
  probability is about 2^-30 per reduction for random operands.

Genuine signatures do not reach these cases in practice. A reimplementation of the loader must therefore either reproduce these cases or
detect and report them. The local C and Python reimplementations used for this work (not
distributed) compute the true value and flag all three conditions; a differential test against
the firmware in unicorn (500 inputs in 5 classes) gave **0 mismatches on unflagged inputs**. 88
inputs were flagged; on 63 of those the
firmware result really differs, which confirms that the flags are needed.

**Signature scheme strength.** e = 3, and the result is not checked for padding: only bytes
4..23 of the result (the SHA-1) are compared; bytes 0..3 are the scramble seed and bytes
24..255 are ignored. The check is therefore weak, and signatures that the real firmware
accepts can be made without the private key. Synthetic test cards built this way were used
locally to test the loader in unicorn and in mGBA without card data. The test cards and the
tooling that builds them are not distributed with this repository.

## 5. Timing and resources (loader)

Measured locally in an AM3-patched mGBA build (not part of this repository) with synthetic test
cards. The adapter delay is an assumption: 2048 cycles per 1 KiB block (`AM3_DELAY_READ`), a small part of the total.

| AEXE size | Power-on to AEXE running |
|---|---|
| 1 KiB | 0.30 s (18 frames) |
| 64 KiB | 0.72 s |
| 128 KiB | 1.14 s |
| 256 KiB (max) | 1.98 s |

That is about 0.29 s fixed (mostly the two 2048-bit Barrett reductions on the ARM7) plus about
6.4 ms per KiB (block copy, SHA-1, LFSR). shonumi notes a short loading icon on DS-Lite before
the AM3 logo and none on a GBA. That is consistent: the loader draws only the 7-dot bar, so
the icon must come from the player.

Memory used by the loader: IWRAM 0x03000000-0x03001FEF code and data, heap from 0x03004000
(about 2.8 KiB), stacks at 0x03007F00 / 0x03007FA0. EWRAM 0x02000000-0x0203FFFF holds the AEXE.
After the jump, the player owns all of RAM.

## 6. Fingerprint against the GBA Video families

- No 32-byte code window of the loader occurs in any VX, Hydrogen, ADS or Majesco cart.
  (The test scans all 46 suite ROMs plus the Shrek reference, first 1 MiB, 4-byte aligned,
  windows with more than 12 distinct bytes.)
- **Pokemon Vol. 1-4 (FVMV)** share exactly 28 windows. They are the ARM C library divide
  (0x03001AF4.., at ROM 0x08004FF0 in Pokemon Vol. 1) and part of memcpy (0x03001C58..,
  ROM 0x080047F4). That is the same toolchain runtime, not codec code. FVMV carts show
  "powered by actimagine" ([../fvmv/player.md](../fvmv/player.md)), and the AM3 boot animation does too (shonumi).
- The VX carts (Shrek, Shark Tale) have a different "Actimagine loader" at ROM 0x200: a plain
  program table at ROM 0x8E4, with no signature and no scramble ([../vx/player.md](../vx/player.md) 1). It
  shares no code with this loader.
- Conclusion for the codec question: the loader cannot tell. It points to Actimagine's 2003
  ARM toolchain, the same as FVMV. Whether the AEXE video codec is VX-like, FVMV-like, or
  something older (Actimagine's pre-Nintendo `.mo`/MOC formats, quatric gist 4.5) needs the
  AEXE from a card.

## 7. What the player (AEXE) does, per prior art, and what needs a card

From shonumi's article and Dan Docs (not verifiable here): the player reads `INFO.AM3`
through the adapter (the last index is reached by writing an out-of-range file number). It
reads `00.AM3` (136 bytes: `SMID`, u32 length, 128 encrypted bytes), decrypts it ("128-bit
DES"), and compares 16 bytes with AM_SMID (command 0x03). It aborts with a red, green or blue
screen on failure. It can refuse to run on a Game Boy Player, depending on the card. It plays
audio and video from the numbered `*.AM3` files, with transfers paced by GamePak IRQs. Its
controls are play, stop, fast-forward, rewind, and episode choice on multi-episode cards. All
of this, plus the actual codecs, comes from `AEXE` and needs a dump (see [README.md](README.md), "Open issues").
