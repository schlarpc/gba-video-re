# The Alphamosaic VC01 instruction set

The scalar instruction set of the VC01 (VideoCore 1) as used by the Play-Yan and Play-Yan Micro
firmware, decoded from the eight firmware images in [firmware.md](firmware.md). No documentation of
it is public. It is a relative of VideoCore IV (the Raspberry Pi's VPU, reverse-engineered by
Herman Hermitage and others), but not encoding-compatible with it:

- **Shared with VideoCore IV:** the register roles, the condition-code numbering, the 32-bit
  compare-add-branch layout (VC4 top nibble 0x8 becomes 0x0), the branch and call layout (0x9
  becomes 0x1), the push/pop register-list byte, and the 3-register multiply-high and divide group
  (VC4 operations 32 and up).
- **VC01's own:** the ALU opcode numbering, 16-bit indexed loads and stores (only r0-r6 can be the
  index), post-increment loads and stores, stack- and pc-relative 16-bit forms, a 16-bit-offset
  load/store whose low offset bits select width and direction, and 48-bit byte accesses with a lane
  field.

## Status labels and evidence

- **[C] confirmed:** executed in an emulator, inside a firmware routine whose results match an
  independent reference (list below).
- **[P] probable:** consistent static evidence from many sites.
- **[G] guess.**

Routines run in the emulator, all passing:

| routine (loader address unless noted) | reference | cases |
|---|---|---|
| 64-bit multiply, divide, modulo, signed and unsigned (0xC0456, 0xC04C2, 0xC059A, 0xC0476, 0xC05BE, 0xC0360, 0xC0608) | Python integers | 300 each |
| soft-float double compare, to-int, multiply, add, subtract, divide (0xC0614, 0xC0940, 0xC06B6, 0xC0A0C, 0xC0A04, 0xC0D8C) | Python floats, bit-exact | 300-400 each |
| memchr, memcmp (0xC8D60, 0xC8D78) | Python | 100 each |
| FAT long-file-name checksum (0xC62C0); FAT 8.3 name to `NAME.EXT` (0xC9706) | Python | 100; 60 |
| `.fup` block check (0xC6266) | every block of the real `playan.fup`, plus corrupted copies | 1,291 |
| sprintf (0xCE7CC with the printf core 0xC4DF2) | Python formatting | 59 formats |
| Nucleus wait-list unlink (0xC14AE); list search on a signed 16-bit key (0xCC94A) | Python | 30; 40 (fails 12 of 40 if signed and unsigned halfword loads are swapped) |
| Park-Miller random number generator (0xCCD46) | Python | 200 |
| FIRMWARE_LIST and FIRMWARE_DOWNLOAD workers (0xC84F8, 0xC6D0C) on the real `playan.fup` | the two images extracted independently | RAM equals both sections byte for byte at the jump to 0x200 |
| G.726 encoders and decoders at 16, 24, 32 and 40 kbit/s, predictor, quantiser, update (video image 0x3E6-0xEBE; scalar and vector code) | Sun's g72x reference C | 6,000, bit-exact, including the whole codec state per sample |
| MPEG-4 kernels: IDCT, dequantisation, AC prediction, motion compensation, output, bitstream unpacker | ffmpeg / ISO 14496-2, IEEE-1180 | 2,500 + 24 |
| MP3 kernels: dct32, IMDCT, synthesis window, requantiser | fixed-point models; PCM within 1 LSB of ffmpeg | 98 |
| AAC kernels: inverse quantisation, window, FFT, IMDCT (N = 2048 and 256), filter bank, PCM out | a fixed-point model following FAAD2 | 4,491 |
| pixel pipeline: YUV to RGB565, crop and scale, brightness | the Garage Game thumbnail hashes | all |

Flipping a decoded operation to a plausible alternative (signed vs unsigned halfword load, max vs
min, the scaled add, abs) makes the relevant tests fail, so those operations are pinned down
individually. The loader, the video image and the music image also boot from reset in the emulator,
start the Nucleus scheduler and post their startup record, running 6-8 million instructions without a
decode fault. With the peripherals of [vc01-hardware.md](vc01-hardware.md) modelled, all five
images (loader, video, music and both `playan.fup` images) boot to status 0, and the real firmware
serves the real GBA ROM (README, "Real firmware"). A C port of the emulator runs at 120-160 million
instructions per second and matches the Python model instruction by instruction on all the tests
above. The four behaviours marked "real firmware" below were found that way: the routines in the
table do not exercise them.

## Coverage

Code found by following branches and calls from the entry point, the interrupt vectors and the call
targets:

| image | code bytes | executed in passing tests | decoded, not executed | vector (length only) | unknown |
|---|---|---|---|---|---|
| Play-Yan loader | 59,836 | 95.95 % | 4.05 % | 0 % | 0.01 % |
| Play-Yan video | 130,216 | 88.60 % | 3.33 % | 8.06 % | 0.01 % |
| Play-Yan music | 160,396 | 54.36 % | 2.26 % | 43.38 % | 0 % |
| Micro loader | 60,272 | 95.93 % | 4.06 % | 0 % | 0.01 % |
| Micro video | 181,696 | 88.14 % | 3.71 % | 8.14 % | 0.01 % |
| Micro music | 157,590 | 53.55 % | 2.29 % | 44.15 % | 0 % |
| `playan.fup` video | 182,700 | 88.20 % | 3.69 % | 8.10 % | 0.01 % |
| `playan.fup` music | 156,714 | 53.33 % | 2.26 % | 44.40 % | 0 % |

The music images are about 44 % vector code (MP3 synthesis and IMDCT), the video images 8 %. The
table counts vector bytes as "length only"; the vector coverage is in the next section.

## Machine model

```
* 32 x 32-bit registers.  r24 = gp (data base; set at entry 0x200 by `mov gp,#imm32`), r25 = sp,
  r26 = lr (bl writes it, rts reads it), r31 = pc.  Same roles as VideoCore IV.  [C]
* ABI: args r0-r5 (more args / va_list on the stack directly above them), result r0 (r1:r0 for
  64-bit, low word in r0), r6-r23 callee-saved (push/pop of r6-rN), r0-r5 caller-saved.  [C]
* Writes to pc (r31) as a destination of mov/add/ld are jumps; the GBA's record at VC 0x200 is
  `mov pc, #0xC0200`.  [C, real firmware]
* Word and halfword accesses ignore the low address bits (the bus aligns them).  [C, real firmware]
* Interrupt entry: sp -= 8, [sp] = sr, [sp+4] = pc, interrupts off, pc = [4*vector]; rti undoes
  it.  sr bit 30 = interrupt enable.  [C, real firmware]
* Flags N Z C V, set ONLY by cmp / cmn / tst / btst(Z).  C after cmp = borrow (a <u b), i.e.
  'cs' = unsigned lower (VC4 convention, inverted vs ARM).  addcmpb does not change flags.  [C]
* Instruction stream = little-endian 16-bit words; the first word gives the length:
    hb = word0 >> 8:  0x00-0x1f -> 32 bit ; 0x20,0x21 -> 80 bit (vector) ; 0x22,0x23 -> 48 bit
    (vector) ; 0x28-0x2e -> 32 bit ; 0x2f -> 48 bit ; everything else 16 bit.
  The 0x20-0x23 lengths are [P]: with them, recursive-descent decoding of fup_sec0/fup_sec1/py_fw0
  has 67 instruction-overlap conflicts (all in zero-filled data) against 14,312 with 16-bit, and
  vector loops close (e.g. fup_sec0 0x9c1c: vec48; add r1,r5; vec48; addcmpbcs ... back to 0x9c1c).
* Condition codes cccc (VC4 numbering):  0 eq 1 ne 2 cs/lo 3 cc/hs 4 mi 5 pl 6 vs 7 vc 8 hi 9 ls
  10 ge 11 lt 12 gt 13 le 14 always 15 never.  [C] (eq ne cs cc hi ls ge lt gt le all executed)
```

## ALU operation numbering (VC01-specific; NOT VC4's)

```
op5 (used by 16-bit 110o oooo and by the 32-bit 3-register form):
   0 btst [P]   1 cmn [C]   2 bmask [C]  3 bic [P]    4 asr [P]    5 eor [C]   6 shl [C]   7 and [C]
   8 lsr [C]    9 ?         10 mul [C]   11 rsub [C]  12 add [C]   13 sub [C]  14 mov [C]  15 or [C]
  16 cmp [C]   17 ?         18 max [C]   19 min [C]   20 adds2 [C] 21 adds4 [C] 22 adds8 [P] 23 adds16 [P]
  24 neg [C]   25 msb [C]   26 exts8 [P] 27 exts16 [P] 28 brev [G] 29 ?       30 abs [C]  31 not [C]
  semantics: 2-operand  rd = rd OP rs ; unary ops (neg msb exts* brev abs not mov) rd = OP(rs)
  bmask rd,n: rd &= (1<<n)-1.  btst rd,n: Z = !(rd>>n & 1).  addsK: rd += rs << k (k=1..4).
  max/min signed.  msb = index of highest set bit (-1 for 0).  cmn: flags of rd+rs.
op4 (16-bit immediate form, hb 0x60-0x77): op5 = 2*op4 for op4 0..7, op4 8 = cmp:
   0 btst [C LFN checksum]  1 bmask [C]  2 asr [P]  3 shl [C]  4 lsr [C]  5 mul (signed imm) [C MP3]  6 add [C]
   7 mov [C]  8-11 (hb 0x70-0x77) never occur in reachable code (cmp? by analogy) [G].  Immediate: signed 5-bit for add/mov/cmp/mul,
   unsigned 0..31 otherwise.
```

## 16-bit encodings

```
0011 cccc 0ooo oooo   b<cc> pc+2*sx7(o)                                        [C]
0011 cccc 1111 dddd   set<cc> rd   (rd = cc ? 1 : 0)                             [C] memcmp setge,
                                                                                  64-bit carry setcs
0011 1111 00oo oooo   add sp, #4*sx6(o)      (3f3d = sub sp,#12)                 [C]
0011 1111 01xx xxxx   only 0x3f73, seen as padding between functions             [P]
010o oooo 0001 dddd   ld rd, (sp + 4*o)                                         [C]
010o oooo 0101 dddd   st rd, (sp + 4*o)                                         [C]
010o oooo 1001 dddd   lea rd, sp + 4*o                                          [C]
010o oooo iii0 dddd   lea rd, (pc & ~3) + 4*(o<<3 | iii)   (forward literal/string pointer)
                                                         [C] loader 0xc6d10 'playan.fup', 0xc6d46 header
010o oooo 0011 dddd   misc, selected by o:                                      (rd field = d)
    o=2  4230   nop (padding after functions)                                 [P]
    o=3  4330   wfi: wait for interrupt (Nucleus idle loop, SLEEP handler)       [C real firmware, LLE run]
    o=4  4430   rts  (pc = lr)                                                 [C]
    o=6  46 3d  b rd                                                           [C] (executed)
    o=7  47 3d  bl rd   (call through function pointer)                        [C]
    o=10 4a30   ei      o=11 4b30 di  (bracket Nucleus TCT_Protect critical sections)  [P]
    o=12 4c3d   switch.b rd: pc = next + 2*sx8(byte table[next + rd])           [C] sprintf
    o=13 4d3d   switch.h rd: pc = next + 2*sx16(half table[next + 2*rd])         [C]
    o=16 50 3d  tst rd  (flags of rd - 0)                                       [C]
    o=18 5230   bkpt: breakpoint, executes as a no-op (entry, fatal-error paths) [C LLE run]
    o=19 53 3d  rdcfg? rd: reads a status/version word (loader tests bit 25)   [G]
    o=20..23 5430 5530 5630 5730: vcbase (vector unit: VRF column base := 0 / += 16/32/48)  [C gap_test]
    o=24 5830   rti  sr=*sp++, pc=*sp++ (IRQ exit; Nucleus context restore)       [P] boot emulation
011o ooo i iiii dddd  op4 rd, #imm5  (op4 0..11; see table)                    [C]/[P]
0111 1wwL ssss dddd   ld/st<w> rd, (rs)++   post-increment by access size      [C] memcmp, memcpy
                      wwL: 000 ld 001 st 010 ldh 011 sth 100 ldb 101 stb 110 ldsh 111 ldsb
10ii iwwL bbbb dddd   ld/st<w> rd, (rb + ri<<w)  for i=0..6 (only r0-r6 can index);
                      i=7: ld/st<w> rd, (rb)                                   [C] (ld/ldb/st/stb)
                      Nucleus TCC_Register_LISR: 'ldb r1,(r0+r8)', 'ld r2,(r1+r3<<2)'; ldh/sth [P]
110o oooo ssss dddd   op5 rd, rs                                               [C]/[P] per op
1110 0000 Lbbm mmmm   pop rb..r(b+m) [,pc]    bb: 0 r0, 1 r6, 2 r16, 3 r24; L = also pc;
1111 0000 Lbbm mmmm   push rb..r(b+m) [,lr]   f0ef/e0ef = lr/pc only.  Low byte identical to VC4
                                              stm/ldm (0x03ef = "push lr").             [C]
                      Order: highest register at the LOWEST address, lr/pc above them (VC4).  [C LLE]
1110 uuuu ssss dddd   ld rd, (rs + 4u)   u = 1..15                              [C] Nucleus list
1111 uuuu ssss dddd   st rd, (rs + 4u)   u = 1..15                              [C]
(0x80-0xbf, 0xe0/0xf0 and 0x78-0x7f together make up all 16-bit loads/stores; there is no
 16-bit unscaled-offset form.)
```

## 32-bit encodings

```
0000 cccc aaaa dddd | 00ss ssoo oooo oooo   addcmpb<cc> rd, ra, rs, pc+2*sx10   rd += ra;
                    | 01ss ssoo oooo oooo   addcmpb<cc> rd, #sx4(a), rs, ...      if (rd <cc> rs/u)
                    | 10uu uuuu oooo oooo   addcmpb<cc> rd, ra, #u6, pc+2*sx8         branch
                    | 11uu uuuu oooo oooo   addcmpb<cc> rd, #sx4(a), #u6, pc+2*sx8
   Same layout as VC4 1000 cccc ....; VC01 top nibble 0000.  [C] (ii, ir, rr forms executed; ri [P])
   Common idioms: 'addcmpbeq rX,#0,#0,L' = cbz; 'addcmpbgt rX,#-1,#0,L' = decrement-and-loop.
0001 cccc 0ooo oooo | o16    b<cc> pc + 2*sx23(o16:o7)                          [C]
0001 oooo 1ooo oooo | o16    bl pc + 2*sx27(o4:o16:o7)                          [C]
0010 10ss bbbb dddd | i16    16-bit-offset load/store, s = hb-0x28 selects:
     (hb 0x28-0x2b)          s=0: i&3=00 ld rd,(rb+i) ; i&3=10 st ; i&1=1 ldh (unsigned) at i&~1
                             s=1: i&1=1 ldsh at i&~1 ; i&1=0 sth at i
                             s=2: ldb rd,(rb+sx16 i) ; s=3: stb
     [C] 28 ld/st (loader fup path), 2a ldb, 29 ldsh/sth (halfword_signedness test: flipping
     ldh/ldsh makes it fail 12/40); [P] the rest: 28/odd = ldh from 0xc8b98 (value
     compared with 0xffff), 'ldh;exts16' and 'ldsh;bmask #16' idioms, struct cross-refs
     (a field written with sth is read back with 28/odd at the same offset).
0010 1100 ssss dddd | i16    add rd, rs, #sx16                                  [C]
0010 1101 mmmm dddd | i16    m 0-3: s-selector as above, base gp; m 4-7: base sp;
                             m 12: lea rd, gp+i ; m 13: lea rd, sp+i ; m 14: lea rd, pc+sx16 [C G.726 tables]
                             [C] for gp ld/st/stb (loader fup path, Nucleus code); others [P]
0010 1110 ooo ddddd | i16    o 0 cmp rd,#u16 [C] ; 1 mul rd,#sx16 [C minstd] ; 2 add rd,#sx16 [C]
                             (2e59 = add sp) ; 3 mov rd,#sx16 [C] ; 6,7 rare/unknown
0010 1110 101 ddddd | oooooo bbbbb aaaaa    op5 rd, ra, rb   (3-register; rd = ra OP rb;
                             unary ops use rb)  [C] add sub and or shl lsr mov adds4 ; rest [P]
0010 1110 100 ddddd | oooooo bbbbb aaaaa    'x' group = VC4 ops 32+n:
                             0 mulhd.ss [P magic-number division]  1 mulhd.su [G]  2 mulhd.us [G]
                             3 mulhd.uu [C muldi3]  4 div.ss [C]  5 div.su [G]  6 div.us [G]
                             7 div.uu [C udivdi3]   8.. VC4 order (adds/subs/...) [G]
```

## 48-bit encodings  (first word, then 32 bits: w1 low, w2 high)

```
0010 1111 011d dddd | imm32          mov rd, #imm32                             [C]
0010 1111 001d dddd | o27 | bbbbb    add rd, rb, #sx27  (b=31: pc-relative; b=24 gp)  [C]
                        (o27 = (w2 & 0x7ff)<<16 | w1 ; rb = w2>>11)
0010 1111 000d dddd | o27 | bbbbb    o27 bits 1..0: 00 ld, 01 st, 10 ldh, 11 sth at (rb + o27&~3)
                                     [C] ld; ldh [C MPEG-4 bit reader]; st/sth [P]
0010 1111 1kkd dddd | o27 | bbbbb    o27 bit1 = 1: ldsh/sth rd, (rb + (o27&~3) + k)  [C ldsh: MPEG-4 VLC sign
                                     bit at py_fw0 0x204bc -> 13/13 Garage hashes in the LLE run; sth P]
                                     o27 bit1 = 0: ldb/stb rd, (rb + (o27&~1) + k)   (byte lane k = 0..3;
                        o27 bit0 = store)  [C] loader reads the fup section count with
                        '2fc2 5204 c000' = ldb r2,(gp+0x5206); py_fw1 0x212a8 rebuilds a
                        big-endian word from lanes 0..3 of one offset.
0010 1111 010d dddd                  not seen in code
```

## Unknown / unresolved

```
* op5 9, 17, 29; misc o=19; 2e op 6/7; 2f op 2.  All rare (< 0.01% of code bytes).
* 4x x nibble 7/b/d/f (odd, not 1/3/5/9): 1 occurrence in reachable code.
* 3x with bit7 set but bits 6-4 != 111 (2 occurrences).
* rti, ei/di executed only in the LLE real-firmware run (tmp/playyan/emu/lle).
```

## Vector instructions

First halfwords 0x20-0x23 are vector (pixel processing unit) instructions: 0x20/0x21 start 80-bit
and 0x22/0x23 48-bit forms (bit 9 is the "compact" flag of the Alphamosaic patents US20030154361,
US7036001, US20030159023 and EP1320029, whose architecture description the model follows). The unit
is 16 lanes of 16 bits over a 64x64-byte register file addressed by rows or columns.

The model is confirmed the same way as the scalar instructions: firmware kernels run in the
emulator bit-exact against references, and each opcode or field is ablated (changed to a plausible
alternative) to show that the kernels then fail. Executed in passing tests: 99.3 % of the reachable
vector code of the Play-Yan video image, 98.5-98.7 % of the Micro and `playan.fup` video images, and
99.99-100 % of the music images; no vector encoding in reachable code is unknown. End to end, the
real firmware's own MPEG-4 decode of the Garage Game files, run in the full-system emulator,
reproduces the thumbnail hashes in the GBA ROM's table.

```
Register file  [C: MPEG-4 coefficient reorder 0x26800 builds 16-bit lanes from two 8-bit moves at x
and x+16 (0/384 errors bytes vs 177/384 with 16-bit cells); pixel conversions; all suites]
  64 x 64 BYTES P[y][x] (EP1320029).  16-bit lane (y,x) = P[y][x] | P[y][x+16] << 8 (x mod 64);
  8-bit lane = P[y][x], zero-extended read, low byte written.  32-bit data = two 16-bit registers at
  x and x+32 of one row (MP3/AAC multi-precision).
  Register spec s (8 bits) = [h1 h0][m1 m0][n3..n0]:
     H: row h*16+n, columns m*16 .. +15        V: column m*16+n, rows h*16 .. +15
  Spec 0x10 as a 16-bit H destination = discard (no write-back; SETF/SRU only) [C G.726 1285/6000 without].
  16-bit vertical REP steps by 16-bit column (byte column bits 4/5 swapped) [C AAC 0x9930].
  Column base (hidden): scalar 0x5430 = 0, 0x5530/0x5630/0x5730 += 16/32/48 (mod 64); added to the
  column of operands whose offset flag is set in 80-bit ALU and memory forms [C bilinear 0x28afa
  18/18, RGB888 brightness 0x17fcc 10/10; off 1/18, 1/10].
  Per lane: N Z C flags, 16-bit accumulator (ACC, CLRA).

Encoding
  h0 = 0010 00 C oooooo www    C = compact (48-bit) [C]; o = opcode [C]; www: bit2 D vertical,
                               bit1 16-bit, bit0 D offset flag
  h1 = DDDDDDDD rrr AAAAA       rrr = offset register r0-r6 (7 none): value v adds v&63 to x and
                               (v>>6)&63 to y (patent "+r0") [C IDCT chroma r0=32, MC r3, requant r6]
  h2 = AAAAA i bbbbbbbbbb       A10: bit9 A vertical, bit8 A offset flag, bits 7..0 spec
  48-bit data, i=1: b = F PPP iiiiii (SETF, condition, simm6); i=0: b = Bspec<<2 | Boff<<1 | Bvert
  80-bit data: h3 = [15 B scalar][14..12 B offset/scalar reg][11 B 16-bit][10 B steps]
                    [9..5 A field f][4 ?][3..1 A offset reg][0 D/A step]
               f: bit4 = byte-address mode (8-bit H, y = (A10>>6&3)*16 + f&15, x = A10&63);
                  f == 0: 8-bit A, no REP step when D is 16-bit; else bit3 = A 16-bit, f&3 added to
                  the A row/column [C IDCT TAN pairs, MP3 byte-1 multiplies]
               imm16 = (h3 & 0xfc00) | b when i=1 [C xvid TAN1/2/3, SQRT2 0xB505]
               h4 = [14 SETF][13..11 cond][10 CLRA][9 ACC][8..5 REP: 1<<n, 7 = r0 times][4..3 SRU op]
                    [2..0 SRU reg, 7 none]
  memory (op 0-3): b = RRRR bbb sss: REP 1<<R, base r[bbb], stride r[sss] (7: one row = 16/32 B)
               vld: D <- mem[base + 16*(sx10(A)<<1 | i) (+2*(h3>>13) 80-bit)], 80-bit: offset reg
               h4 bits 7..5, lane condition h4 bits 13..11; vst: A -> mem[base + 32*D + 16*i]
               (direction from A bit9; 80-bit: A-field encoding incl. byte mode); op0/op1 ignore
               address bits below the row; op2/op3 = unaligned pair (op3 = lanes past the block)
  Conditions: 0 always, 2 ifz, 3 ifnz, 4 ifn, 5 ifnn, 6 ifc, 7 ifnc (1 never seen)
  SRU: 0 unsigned lane sum [C MP3 synthesis], 1 signed sum, 2 imin, 3 imax (active lanes only).

Opcodes (lane semantics; a, b signed 16-bit; C = per-lane carry, borrow convention for subtracts)
   0 vld  1 vst  2 vld2  3 vld3 (unaligned pair)                         [C all suites]
   4 mov          5 and          6 OR [C MP3 requant 0/640 vs 483/640 as max]   7 eor
   8 add (C)      9 add, 8-bit dest clamps 0..255 / 16-bit wraps [C pixel, AAC]
  10 sub (C=borrow)  11 sub variant [P]  12 rsub (C)  13 a<b (with IMIN) [C G.726]
  14 mul low16    15 saturating mul [MP4]  16 (a*b)>>8 [C pixel]  17 (a*b)>>8 saturated to dest [C brightness]
  18 mulhd.ss     19 mulhd.su (a * u16 b)>>16 floor [C MP3 dct32/IMDCT MULfw]
  20 (u16 a * u16 b + 0x8000)>>16 [C AAC iquant, pixel]  21 (a * u16 b + 0x8000)>>16 [C IEEE-1180,
     MP3 PCM 98.6% vs 94.1%, pixel hashes]  23 (a * u16 b)/65536 toward 0 [MP4]
  24 shl, C = bit out (wrapping) [C bitstream 0x1FEA0: 12/24 if saturating; AAC]  25 saturating shl, C = sat
  26 asr, C = last bit out  27 lsr, C = last bit out [C MP3 requant]  28 signed min [C AAC 9/20 as clip]
  29 max  30 |a-b|  31 |a| >= b  [MP4 AC prediction]
  34 addc  35 subc  36 rsubc  [C MP3/AAC 32-bit arithmetic; ablation 0/6..0/12]
  37 clamp a to [0,b] [C MP4 put 0/40 as min]  40 b + sign(a) [MP4 dequant]
  42 lookup: lane = mem16[r(base) + 2*acc] (memory-op format) [C AAC iquant, MP3 requant]
  43 a<<b or a>>-b  44 bitplanes (lane L = -1 if bit L of b)  [C G.726]

Kernels matched (references)
  G.726 16-40 kbit/s (Sun g72x) 6000/6000.
  MPEG-4 (py_fw0): IDCT 0x26002 = AP-922/xvid tan butterfly with a prescale table and ROUNDED
    multiply-high (not ff_xvid_idct: 29% identical, rest +-1; IEEE-1180 passes except the overall mean
    error 0.005); H.263 and MPEG dequant (ffmpeg / ISO 14496-2 7.4.4.2), AC prediction, half-pel MC both
    rounding modes + edge padding, 4MV, put/clamp, bitstream unpacker 0x1FEA0 + bit reader.
  Pixel: YUV420->RGB565 0x284a0 (298/408/516/100/200 >>8, chroma planes swapped vs ISO), 240x176 crop to
    240x160 and 4:1 box filter to 60x40 (0x2749a/0x276de), 2x2 mean, bilinear/nearest scalers, palette
    and RGB888/565 conversions, brightness 0x27bea (factor gp+1632, 256 neutral).  END TO END: the
    firmware's own decode in the LLE run (C port) reproduces all 13 Garage thumbnail hashes.
  MP3 (py_fw1): dct32 0x49c (libmad Lee structure, constants 2cos(k pi/64) float32 *2^24), IMDCT long
    0xa96c / short 0x12446 (own 67-multiply structure), synthesis window 0x316b4 (libmad D.dat),
    requantiser 0x35058 (pow43 table 0x244a0, gain table 0x234a0) -- all bit-exact vs fixed-point reference models; firmware PCM vs ffmpeg/minimp3 max 1 LSB, 98.6% identical.  8-bit GBA PCM is
    scalar (0x3185e).
  AAC (mi_fw0/fup_sec0): FAAD2 C structure, Alphamosaic vector helpers: window mul/add, complex
    multiplies, 16-point FFT, iquant (cube-root table 0x8948), PCM out, and the whole IMDCT (0x97d6
    N=2048, 0x99d8 N=256 incl. 0xad4a) and filter bank ifilter_bank 0x21bb2 (all window sequences) --
    bit-exact vs the pure-Python fixed-point model (float deviation 2.4e-5).
    SRU instructions DO write their VRF destination [C AAC block float: 10/20 without].

Coverage of reachable vector code: executed in passing tests
  py_fw0 99.26%  mi_fw0 98.67%  fup_sec0 98.52%  py_fw1/mi_fw1/fup_sec1 99.99-100%; unknown 0%.

Open
  0x286e4 mode 0 (2:1 point sampling, 80 B): output px 8-15 wrong with the current 8-bit vertical
    store model (possibly a firmware bug).  Op 11 vs 10, op 15/23/29/31/40 only [P] from single kernels.
  Accumulator saturation (only reachable with int32-overflowing IMDCT input), op17 rounding, h3 bit 4, A-field
    bit 2, cond 1 -- no kernel discriminates.  op2 vs op3 individually.
  AAC: real-bitstream frame decode not run (scalar FAAD2 front end, scalefactor gain 0x93d4, M/S, TNS).
```
