# Majesco ADPCM audio (ADS and Hydrogen players)

Code: `reference/majesco/adpcm.c`, `reference/majesco/adpcm.h`.
Status: v1, 2026-11-07. Bit-exact: every audio sample of every stream of the 19 ADS carts and of
the two Hydrogen-engine LZMA carts (Jimmy Neutron 1, Noah proto) matches the emulator (whole
tracks, `tools/ads/verify.py`); 600 random bit streams and 9 real tracks also match the cartridges'
own routines run in unicorn (`tools/ads/emu/uc_adpcm.py`, two builds). `tools/hydrogen/verify.py --audio` verifies
the Hydrogen-inflate carts with the same code (`MJ_CTAB_FLAT`).

## 1. Where it sits

A type-2 resource ([../ads/format.md](../ads/format.md) 6) holds a small header, a table of block sizes in
32-bit words, and the payload. The payload is one bit stream; it is cut into blocks of 16458
(0x404A) samples. Output: signed 8-bit PCM at 16384 Hz, played through DirectSound A.

## 2. Bit reader

Bits are read most-significant first from little-endian 32-bit words: bit `p` of the stream is
bit `31 - (p & 31)` of word `p >> 5`. (The cartridge keeps a left-aligned 32-bit accumulator
plus one pending word and refills a word at a time; the effect is exactly this.)
`get(n)` returns the next n bits as an unsigned number, `peek(n)` the same without consuming.

## 3. State

```
step    s32   log-domain step size (top bits pick the mantissa/exponent of the magnitude)
prev    s32   previous magnitude (signed)
h0, h1  s32   last two reconstructed values (before the output shift)
f0, f1  s32   adaptive pole coefficients for h0 / h1
f2      s32   adaptive zero coefficient for prev
s1, s2  u1    sign of the last two "error" values
ctx     u6    last three symbols (2 bits each)
tab[64] u8    context table: 4 symbols x 2 bits per entry
left          samples left in the block
```

## 4. Block header

At the start of every block (`left == 0`):

```
if peek(1) == 1:                       continuation: keep step and the predictor
    get(1)
else:                                  restart
    get((32 - (pos & 31)) & 31)        skip to the next 32-bit word boundary
    get(1)
    step = sign_extend(get(24), 24)
    f2 = 0; prev = 1; f0 = f1 = 0; h0 = h1 = 1; s1 = s2 = 0
read 64 context-table entries (4.1 or 4.2)
left = 16458; ctx = 0
```

The continuation branch exists in every player but no ADS or Hydrogen-engine stream uses it:
all 51,793 blocks of the 96 streams are restarts (the oracle's random streams exercise it).

### 4.1 ADS coding (`MJ_CTAB_VLC`)

Seven palette bytes (each a permutation of {0,1,2,3}, 2 bits per symbol, low bits = symbol for
selector 0): `PAL = {0xD8, 0x72, 0xE4, 0x4E, 0x78, 0xD2, 0xE1}`.

```
t = peek(4)
if t < 12:                     prefix code
    code: 00 -> PAL[0] (2 bits)  010 -> PAL[1]  011 -> PAL[2]   (3 bits)
          1000 -> PAL[3]  1001 -> PAL[4]  1010 -> PAL[5]  1011 -> PAL[6]  (4 bits)
else:                          "11" escape
    get(2); entry = escape()
```

### 4.2 Hydrogen coding (`MJ_CTAB_FLAT`)

Every entry is `escape()` (5 bits, no prefix). Used by all Hydrogen players, including the two
Hydrogen-engine carts with LZMA video.

### 4.3 escape()

```
a = get(2); b = get(2); sel = get(1)
m = (1 << a) | (1 << b)                        one-hot pair
lo = ESC[m & 3]; hi = ESC[m >> 2]              ESC = {0x0C, 0x0D, 0x08, 0x06}
if sel: p = hi >> 2; q = lo & 3  else: p = lo & 3; q = hi >> 2
entry = a | b << 2 | p << 4 | q << 6
```

(For a != b this completes the permutation with the two symbols not yet used.)

## 5. Sample

```
e   = VLC[peek(3)]            VLC = {0x01,0x01,0x01,0x01,0x12,0x12,0x23,0x33}
get(e & 15)                   code length 1, 2 or 3
sym = (tab[ctx] >> (e >> 3)) & 3        selector shift 0, 2, 4 or 6
ctx = ((ctx << 2) & 0x3F) | sym

r   = (step >> 8) + 0x74 + (sym & 1 ? 0xF9 : 0)
mag = (((r & 0x7F) | 0x80) << 7) >> (14 - ((r >> 7) & 15))      arithmetic shift
if sym & 2: mag = -mag

l = step >> 6
if sym & 1: fp = l > 0x12E0 ? 0x1400 : l + ((0x36E0 - l) >> 5)
else:       fp = l < 0x248  ? 0x220  : l + ((-(l + 0x2C0)) >> 5)
step = step + fp + ((-step) >> 6)

pf  = prev * f2                                   32-bit wrap-around products
acc = ((h1 * (f1 >> 2)) >> 11) + ((h0 * (f0 >> 2)) >> 11) + (pf >> 11)
h   = mag + (acc >> 1)
h1 = h0; h0 = (h != 0) ? h : 1
out = clamp(trunc(h / 64), -128, 127)             division toward zero

err = mag + (pf >> 12)                            pole/zero adaptation
g0 = f0 - (f0 >> 8); f1 = f1 - (f1 >> 7)
sg = err < 0
if err != 0:
    if sg == s1: t = -g0; g0 += 0xC0  else: t = g0; g0 -= 0xC0
    if (unsigned)(t + 0x1FFF) <= 0x3FFF: f1 += t >> 5
    elif t < 0: f1 -= 0x100
    else: f1 += 0xFF
    f1 += (sg != s2) ? -0x80 : 0x80
if g0 > 0x3C00 - f1: g0 = 0x3C00 - f1
f0 = g0
f2 = f2 - (f2 >> 8) + ((mag ^ prev) >= 0 ? 0x20 : -0x20)
s2 = s1; s1 = sg; prev = mag
left -= 1
```

All `>>` are arithmetic shifts on signed 32-bit values; additions and products wrap at 32 bits
(the ARM MUL); `(unsigned)` is a 32-bit unsigned comparison. Initial state before the first
block: everything zero, so the first block must be a restart (it always is).

## 6. Output stage (the only difference between player builds)

The decode loop is the same instruction sequence in every ADS and Hydrogen build. It differs
only in how a sample is stored:

| builds | store | buffer |
|---|---|---|
| ADS `bde72f6aba53` (SRMTHFG, Proud), `17d6ab8a82ba` (Disney 2), `f364f46e4bef` (Nicktoons 3), `bfa01253c3c5`, `e3117652326c` (French), `73b2d7da03af` (TMNT proto) | two samples per `strh` (VRAM cannot take byte writes) | ring in VRAM, DMA1 to FIFO A |
| ADS `e0080e16dac3` (CN USA), `882a917e99c5` (Disney 1), `e19d3efb314e` (DBGT), `69261e8948ae` (Nicktoons 2, SpongeBob 3); Hydrogen-engine `22db83f74bdf` (Jimmy), `8831b3ba8f06` (Noah); all Hydrogen | one `strb` per sample | ring in IWRAM/EWRAM, DMA1 to FIFO A |

The paired variant needs an even sample count per call; the players always ask for even
counts. The samples themselves are identical; this is the "Disney-style" vs "Hydrogen-identical"
split seen by the static classifier.

## 7. API (`reference/majesco/adpcm.h`)

```c
mj_adpcm a;
mj_adpcm_init(&a, payload, payload_len, MJ_CTAB_VLC);   /* or MJ_CTAB_FLAT for Hydrogen */
mj_adpcm_seek(&a, byte_offset_of_block, block_index);   /* optional; block must be a restart */
size_t got = mj_adpcm_decode(&a, out_s8, nsamples);     /* reads block headers as needed */
```

`payload` is the first byte after the block-size table (`entry + ((16 + 2*n + 3) & ~3)` where
`n` is the u16 at entry offset 14). Reading past the end of the buffer stops the decode
(`a.overrun`). The cartridge's word reader does not stop at the end of the entry: on some
Hydrogen streams its last call reads a word past the payload (found on
the SpongeBob 2 logo), so pass the payload plus the following ROM bytes (both decoders pass 64
more bytes). The last block of a track is never played to its end ([../ads/format.md](../ads/format.md) 6.3).

## 8. Prior art

quatric's "The Mobiclip Formats" (section 8.1) and mobipeg (`libavcodec/adsgbaaudio.c`,
`doc/gba_video_ads.md`) describe the same codec; this spec agrees with them on the VLC, the
palette and escape coding, the 25-bit restart header and the flat Hydrogen coding. Differences
found here: the restart resets **all** of f0, f1, h0, h1 (the block-header routine, e.g.
0x020086B4 in SRMTHFG, stores 0 to +0x10/+0x14/+0x18 and 1 to +0x04/+0x08/+0x0C; mobipeg's
notes say f1 and h1 survive), the restart first skips to a 32-bit word boundary and then reads
one more bit before the 24-bit step, and the output stage differs between builds (section 6).
