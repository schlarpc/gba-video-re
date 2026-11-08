# The Nintendo MP3 Player's DSP board

The Nintendo MP3 Player's media chip is a **TI TMS320VC5416** DSP. Its firmware, a TI C runtime
with DSP/BIOS, is uploaded by the GBA as a standard C54x boot table ([firmware.md](firmware.md) 6).
This page is the board as the firmware uses it, inferred from the firmware and proven by running
it: a C54x core written from TI's manuals (SPRU172C, SPRU131G; data sheet SPRS095P; bootloader
SPRA602F), its decoding cross-checked against binutils on all 44,828 instructions of the image and
against a community Ghidra C54x module, runs the real firmware under the real GBA ROM in a patched
mGBA.

## Result

- The whole job runs on the real firmware: upload, boot, SD card mount, menu, folder walk, ID3,
  play, seek, pause, stop, and the `meteor.fup` update path (with a synthetic update file).
- **The decoded MP3 matches ffmpeg to within 1 LSB** on the headphone output: Nintendo's
  `sample_music.mp3` through the full GBA and DSP stack gives SNR 96.3 dB, maximum error 1 LSB,
  98.6 % of samples identical, with no drift or dropouts over 30 s.
- Speaker output: the DSP sends 0x480-byte blocks of 8-bit samples (its 16-bit output >> 8) at
  16,384 Hz, double-buffered at DSP 0x500/0x740. The first channel of a block is the right speaker
  and the odd-address byte is the earlier sample, confirming the GBA side ([protocol.md](protocol.md)
  11.5). That conversion has no bit-exact reference yet.
- The tick at 0x010F advances every 16 ms, 1.05 per GBA frame, against the GBA watchdog's 0.87.

Not exercised: background play (0x0400/0x0401) and sleep/wake (0x0500/0x0501).

## Board

Tags: **[C]** the real firmware runs correctly through it; **[P]** read from the code and
consistent with the run; **[G]** a guess. Addresses are data space unless written `p:`; code
addresses are XPC:PC.

```
PART: TMS320VC5416  [C]
  * HPI memory map (SPRS095P fig. 3-9) = exactly the GBA loader's bank fold: HPI 0x00000-0x07FFF
    DARAM0-3 (data/prog overlay), 0x18000-0x1FFFF DARAM4-7 (data 0x8000-0xFFFF with DROM=1),
    0x28000-0x2FFFF / 0x38000-0x3FFFF SARAM0-7 (program pages 2 and 3).  The boot table loads code into
    pages 2 and 3 and data into 0x8000-0xFFFF.
  * 6-channel DMA with subbank registers and INTOSEL, 3 McBSPs, CLKMD PLL, 128K words RAM.
  * PMST written 0x00AC by the C runtime: IPTR=1 (vectors at 0x0080), MP/MC=0, OVLY=1, DROM=1, CLKOFF.
  * No on-chip ROM content is used except the HPI bootloader (HLE'd); 0x0800's warm boot jumps to
    p:F800 (ROM bootloader) right before the GBA resets the chip [P].
  * Software: TI C compiler runtime (c_int00 at 0x42FC, cinit tables) + DSP/BIOS (HWI dispatcher
    0x4740.., SWI/PRD/TSK/SEM, CLK ISR 0x45C4, IDL loop 0x33D9, TRC bits at 0x64).

BOOT  [C]
  GBA GPIO bit 3 = RS.  On release our HLE of the VC5416 ROM bootloader (SPRA602F 2.2.1, HPI boot):
  INTM=1, OVLY=1, SWWSR=7FFF, 007Fh cleared, then poll 007Fh until non-zero, XPC = [007Eh], PC = [007Fh].
  The GBA's upload writes 0x007E/0x007F last (entry 0x0000:42FC) and then CONTROL=0x08.

CLOCK  [C]
  CLKIN = 11.2896 MHz (256 x 44.1 kHz).  CPU = CLKIN x (PLLMUL+1) (CLKMD 0x87F7 at boot: x9 = 101.6064 MHz).
  The firmware changes the multiplier at run time (2:DF8E, A = 0..8 -> CLKMD = A<<12 | 7) and rescales
  McBSP0's CLKGDV = 8(A+1)-1 and the timer PRD with it (its own constant 0x00AC4400 = 11289600 at 2:DFAF):
    no headphones / speaker stream: x9 = 101.6 MHz;  headphones: x7 = 79.0 MHz;  idle: x1 = 11.29 MHz.
  Evidence: McBSP0 then gives exactly 44.1 kHz in all three cases, the BIOS tick stays 1 ms, and the
  decoded MP3 plays at the right speed (position records 1.00 s apart, PCM aligned with no drift).

TIMER0 (TIM 0x24, PRD 0x25, TCR 0x26)  [C]
  DSP/BIOS CLK: TDDR=1, PRD = 0xC66C at 101.6 MHz -> 1 ms.  TINT (IFR bit 3) -> vector 0x00CC -> 0x45C4.
  PRD objects: 2:BDEF every 16 ms = tick (word 0x010F +1) + headphone debounce (2:BDA1) + event timer.
  Also used once at boot as a cycle counter (0x3D1A: TSS/TRB/read TIM).

HPI-8 (HPIC 0x2C)  [C]
  Host CONTROL bit 2 (DSPINT) -> DSP HPI interrupt (IFR bit 9, vector 0x00E4 -> fcall 0x472B -> BIOS
  dispatcher -> 2:BA97: copies the mailbox payload for its command ID, clears 0x0110, posts an event).
  DSP writes HPIC = 0x0008 (2:BA08) after placing a record at 0x0100 -> HINT asserted -> GBA Game Pak IRQ.
  Host CONTROL bit 3 = clear HINT (the GBA writes 0x08 at the top of its IRQ handler; the IRQ is an edge).
  Bits 4 (XHPIA) and 0 (BOB) select the address half / byte order on the host side only.

McBSP0 (0x20-0x23, SPSA 0x38/SPSD 0x39) = headphone DAC  [C]
  SPCR1=2000 SPCR2=03C1, RCR/XCR = 0140/0044 (2 x 16-bit words per frame), SRGR1=0F47 (FWID 15,
  CLKGDV 8(A+1)-1), SRGR2=301F (internal clock, FSGM, 32-bit frame), PCR=1E02.  44.1 kHz stereo.
  Model: one word slot per CLKGDV*FPER/2 CPU cycles, each slot sends DXR1 (captured as PCM) and raises
  XEVT0.  First word of a frame = left (planar buffers; the order matches the reference decode).
DMA channel 3 = audio out  [C]
  DMSRC/DMDST 1E5C -> 0023 (DXR10), 2 elements x 64 frames, DSYN=XEVT0, SIND=index mode (DMIDX0=+64,
  DMFRI0=-63: two planar 64-sample arrays L|R), autoinit from the shared global reload registers
  (DMPREC AUTOIX=0), end-of-block interrupt DMAC3 on IFR bit 11 (DMPREC INTOSEL=01, vector 0x00EC ->
  same 0x472B dispatcher).  The ISR moves the next 128 words in (2:E198 mvdd loop).
McBSP1 (0x40-0x43, 0x48/0x49) = codec control SPI + GPIO  [C/P]
  CLKSTP SPI master, 16-bit words: codec register writes (2:D9A1: (v & 0x7FF) | 0x0600 = volume,
  2:E018 / 2:DDDF: 0x6004, 0x0007, ... at power changes).  PCR RIOEN: the DR1 pin is read as GPIO
  (PCR bit 4) every 16 ms = headphone jack (1 = plugged): debounced 6 ticks, posted as event 0xC000,
  reported to the GBA as record 0x8600 and switches DAC/speaker mode and the PLL.
  Model: SPI words accepted (nothing attached), DR1 level settable .
McBSP2 (0x30-0x33, 0x34/0x35) = SD card, SPI mode  [C]
  CLKSTP SPI master (PCR 0A02), 8-bit words.  Commands, responses and tokens: CPU writes DXR, polls
  SPCR1.RRDY, reads DRR (2:D073).  512-byte data blocks: DMA ch5 transmits 0xFF from one word (no
  increment, DSYN=XEVT2) and ch4 receives to RAM (DSYN=REVT2), both 512 elements; the transfer starts
  when the transmitter leaves reset after both channels are enabled (XEVT = XRDY rising edge; firing it
  on channel enable instead loses 2 received bytes and every read fails).  CMD59 enables CRC: the
  firmware checks data CRC-16 with the CCITT table it loads at 0x0400.  Init: CMD0, (CMD55 ACMD41),
  CMD59, CMD10, CMD9, CMD16(512), then CMD17 single-block reads only.  The card must carry an MBR
  partition table (FAT16 partition, type 06); a superfloppy image gives 0x8001 status 2.
  Model: SPI-mode SD card v1 card, image with an MBR at LBA 0 and FAT16 at LBA 63.
DMA in general  [C]
  5416 semantics: element count = DMCTR+1, frame count = DMSFC[7:0]+1, index modes 101/110 use
  DMIDXn per element and DMFRIn after the last element of a frame, AUTOINIT reloads src/dst/count from
  the global reload registers (DMGSA0.. when AUTOIX=0), DINM/IMOD interrupt at block (or frame) end.
  Sync events per SPRS095P table 3-12.
GPIO / XF / BIO / I/O space  [P]
  GPIOCR/GPIOSR, XF, BIO and PORTR/PORTW are not used by the firmware (no access in any run).
INTERRUPTS used  [C]
  IFR bit 3 TINT (BIOS CLK), bit 9 HPI (GBA doorbell), bit 11 DMAC3 (audio DMA).  IMR = 0x0A08.
  TRAP 15 (vector 0x00BC, bd 0x44B0) = BIOS context switch.
```
