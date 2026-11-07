# AM3 SmartMedia card format

Layers, from the medium up. Each section says how it was established:
- **[fw]**: from the firmware, verified by differential tests against the firmware run in
  unicorn and in mGBA;
- **[spec]**: from a public standard (SSFDC / FAT);
- **[prior]**: from shonumi or GBE+, not yet checked against a real card;
- **[card]**: needs a card dump. These parts are open.

## 1. Physical medium: SmartMedia NAND [spec]

| | 32 MB card | 64 MB card |
|---|---|---|
| Page | 512 data + 16 spare | same |
| Block (erase unit) | 32 pages = 16 KiB data | same |
| Physical blocks | 2048 (2 zones x 1024) | 4096 (4 zones x 1024) |
| Logical blocks | 2 x 1000 | 4 x 1000 |
| Logical capacity | 32,768,000 bytes | 65,536,000 bytes |
| Raw size with spare | 34,603,008 bytes | 69,206,016 bytes |

Spare area per page (16 bytes): bytes 0-3 reserved (0xFF); byte 4 data status; byte 5 block
status (not 0xFF = bad block); bytes 6-7 block address; bytes 8-10 ECC of data bytes 256-511;
bytes 11-12 block address (copy); bytes 13-15 ECC of data bytes 0-255.

Block address field, 16 bits big-endian: `0001 0aaa aaaa aaaP`. Bits 10..1 are the logical
block number within the zone (0..999), and P makes the total parity even. Logical block L of
zone z is at logical byte offset `(z*1000 + L) * 16384`. Free blocks have 0xFFFF. The first good
block of zone 0 holds the CIS and has no address.

ECC: the SmartMedia 22-bit Hamming code per 256 bytes, 3 bytes stored inverted. Byte 0 holds
line parities LP07..LP00 (pairs for byte-address bits 0..3), byte 1 holds LP15..LP08
(address bits 4..7), byte 2 holds CP5..CP0 in bits 7..2, with bits 1..0 = 1. A local
implementation (not distributed) matches Linux `ecc_sw_hamming_calculate(..., sm_order)` on
302 test vectors and corrects single-bit errors.

The adapter's own controller does this mapping. The GBA sees only files (section 3), so a
logical image is enough for emulation, and the spare area only matters for raw dumps.

## 2. Logical image [spec + prior]

An MBR at sector 0. The partition entry at 0x1BE gives the start LBA (u32 at 0x1C6). One FAT12
volume (FAT16 should be accepted too) and a flat root directory. shonumi reports these
files: `01.AM3` .. `11.AM3`, `INFO.AM3`, `00.AM3`, about a dozen files, sometimes fragmented
[prior]. A reader must follow cluster chains, because files can be fragmented.

## 3. File indexing by the adapter [prior, consistent with fw]

The GBA selects files by index (register AM_SMC_FILE), not by name. The order comes from
`INFO.AM3`: a table of 32-byte entries from offset 0x200, with the 8.3 name (8+3 bytes, space
padded) at entry+8. The first listed name is index 0. Other bytes of `INFO.AM3` (the first
0x200 bytes, entry bytes 0-7 and 19-31) are not described **[card]**. An index past the end
selects the last file; 0xFFFF selects index 0 **[prior]**.

The loader opens **index 0** and expects the signed player there **[fw]**. shonumi found
`ASIG` at 0x1D3C400 in his 32 MB Conan dump, about 29 MB in. So the file at index 0 is stored
near the end of the card, and its name is unknown **[card]**.

## 4. File index 0: the signed player [fw]

A sequence of chunks, little endian:

```
chunk:  u32 tag   (ASCII fourcc read as LE u32: 'ASIG' = 0x47495341, 'AEXE' = 0x45584541)
        u32 size  (payload bytes)
        u8  payload[size]
```

The loader looks for the first `ASIG` and then for the next `AEXE` after it. A chunk with
another tag is skipped, and its size must satisfy `size & 0xF0000003 == 0` (a multiple of 4,
less than 256 MiB), otherwise loading fails. Requirements:
- `ASIG.size == 256`, and `ASIG` comes before `AEXE`;
- `3 < AEXE.size <= 0x40000`. `AEXE` is loaded to EWRAM 0x02000000 and entered in ARM state.

Whether real cards carry other chunks (version, title, player resources) is **[card]**.

### 4.1 Signature and scramble [fw]

```
n   : 2048-bit modulus, from the firmware (literal pool word at 0x03001A84 -> 256 bytes BE)
mu  : floor(2^4096 / n), from the firmware (0x03001A88 -> 257 bytes BE); verified to match n
x   : ASIG payload as a 2048-bit big-endian integer (not reduced mod n before use)
m   : x^3 mod n, computed as Barrett(Barrett(x*x) * x)
M   : bytes of m, big-endian, minimal length (leading zero bytes dropped), written over ASIG
      from offset 0; the remaining ASIG bytes stay
seed: u32 little-endian from M[0..3]
check: M[4..23] == SHA-1(AEXE payload exactly as stored)        else: blue screen
descramble, for i in 0 .. floor(size/4)-1:
      word_i (u32 LE at AEXE+4i) ^= s;  s = (s >> 1) ^ (s & 1 ? 0xA3000000 : 0)   (s0 = seed)
      the last size%4 bytes are not touched
```

M[24..255] are ignored. Section 4 of [player.md](player.md) lists the inputs on which the firmware's
Barrett code differs from `x^3 mod n`; real signatures never hit them, and a reimplementation
should report them if they occur.

The LFSR is a 32-bit Galois LFSR, shifting right, with feedback mask 0xA3000000.

## 5. Copy-protection file `00.AM3` [prior, card]

shonumi: 136 bytes, `"SMID"`, u32 length (128), then 128 encrypted bytes. The only file that
differs between two copies of the same title. The player decrypts it ("128-bit DES") and
compares 16 bytes with the card's SmartMedia ID (adapter register +0x10 after command 0x03).
The algorithm, the key source and the layout of the 128 bytes are in `AEXE` **[card]**.

## 6. Movie files: container, video, audio [card]

Nothing is known from the firmware. Prior art says only that the numbered files hold the
audio and video, that they are not encrypted, and that playback works with 1 KiB adapter
reads paced by GamePak IRQs. Cards hold one or two TV episodes (32 MB) or a feature film
(64 MB). shonumi reports good quality at 240x160 and clear speech.

Planned work once a dump arrives (section numbers reserved):
- 6.1 container / demux (file roles, headers, index/seek tables);
- 6.2 video bitstream: fingerprint against VX, VXGB, FVMV, ADS, Hydrogen and the Majesco VQ
  family with the cart classifier's code-window method over the descrambled AEXE, plus
  quatric's notes on the Actimagine MOC containers;
- 6.3 audio bitstream;
- 6.4 output stage (display mode, colour conversion, audio mixing rate).

## 7. Tables

The only firmware tables are the RSA modulus and the Barrett constant. Both are public keys,
so nothing secret is involved, but a reimplementation should still take them from the user's
firmware at run time (literal-pool words at 0x03001A84 and 0x03001A88) rather than store
them. The SHA-1 constants
and the LFSR mask are standard or derivable and are given as literals above.
