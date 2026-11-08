# The VC01 cartridge hardware

The peripherals of the Play-Yan's VC01 chip, inferred from the firmware code and proven by running
the real firmware on a model of them: in an emulator, the GBA ROM drives the real loader, music and
video images, and the firmware boots, reads the SD card, answers commands and streams audio. There
is no VC01 documentation; every register below comes from the firmware.

Tags: **[C]** the real firmware runs correctly through it in the emulator; **[P]** read from the
code and consistent with the run; **[G]** a guess the firmware does not contradict. Addresses are
VC addresses; "ldr", "mus" and "vid" are the loader (base 0xC0000), music and video images of the
original Play-Yan ([firmware.md](firmware.md) 1.1).

The register layout is close to the Raspberry Pi's BCM2835 in places: the system timer has the
same layout (match flags, free-running counter, four compares), and the GPIO block uses the same
register names (GPLEV, GPEDS, GPREN, GPFEN, ...), taken here from a register dumper in the music
image. Broadcom bought Alphamosaic in 2004, so this is the same lineage of peripherals.

```
MEMORY
  0x00000000-0x000FFFFF  RAM, 1 MiB.  vid data word 0x2DFA0 = 0x100000 (heap end) [C].  Nothing above 1 MiB was touched in any run.
  vectors                32-bit pointers at 4*n from VC 0: 0-5 exceptions (5230 'bkpt' + rti stubs),
                         32-63 interrupts.  The table at 0 is used (the loader's copy at 0xC0000 is a
                         second table that only the unused 'register LISR' path writes) [C].
  bus                    halfword/word accesses ignore the low address bits [C] (vid 0x13B7E heap bounds).

CPU behaviour the firmware needs (all [C]; see vc01-isa.md)
  irq entry: sp -= 8, [sp] = sr, [sp+4] = pc, IE off, pc = [4*vec].  rti undoes it.  sr bit 30 = IE.
  push/pop order = VC4: highest register at the lowest address, lr/pc above.  The Nucleus interrupt
    frame (push r0-r5,lr; push r6-r15; ... pop r0-r15; ld lr) only restores with this order.
  4330 = wait for interrupt (idle loop ei;4330;di).  5230 = breakpoint, NOP without a debugger.
  writes to pc (mov/add/ld rd=31) jump: the GBA's record at VC 0x200 is `mov pc, #0xC0200`.

0x10000000  'current thread' debug register: Nucleus writes the TCB pointer on every switch [P]
0x10000400  clock/run control, 0xA5A5 password in bits 31-16 [P].  Boot writes 0xA5A58020 (ldr 0xC1A5A),
            the PLL code writes 0xA5A50000 | div<<4 (ldr 0xCF2F8).  The GBA's 0xA5A50032 here after an
            upload is the kick: model = reset peripherals, start the core at 0x200 [C].
0x10000408  clock divider (password; ldr writes 0xA5A50002 at boot), +0x0C fine divider [P].
            Firmware clock = 20 MHz*(N+1)/(M*P) * 16/(div*16 + fine>>6) (ldr 0xCEF66, 0xCF068) [P].
0x10000800 + 0x80*ch   DMA channels 0-7, IRQ 32+ch (ldr 0xC11A6 registers all 8, mus 0x177F0 ISR)
      +0x00 CS (0x70042 = start config, 0 = off)         +0x04 interrupt status, W1C (ISR writes -1)
      +0x20 next-CB of the current CB  +0x24 bytes moved (running)  +0x2C current CB length
      +0x30 bytes done in current CB   +0x34 current CB address; write = start / 0 = stop
      Control block (32 bytes): next, src, dst, ctrl (len = ctrl & 0xFFFFF, bit 24 = IRQ when done,
      0x49 data / 0x48 self-looping silence), 4 unused.  Used by the AC'97 driver (ldr 0xCECAC,
      mus 0x347B4): data CBs chained, each followed by a self-looping 4-byte silence CB that the
      next queued buffer overwrites in place [P].  Only channel 5 is used.  Paced by the AC'97
      DAC rate (4 bytes per stereo frame).  [C for the protocol: the music stream runs on it]
0x10000C00  boot handshake: write 0x40000000, poll bit 0, write 0x80000000 (ldr 0xC028C).  Model:
            bit 0 reads 1 [G].
0x10001000  interrupt controller [C]
      +0x00 current priority level (bits 2-0; Nucleus restores it from gp+848 on every return)
      +0x04 read: (priority<<8) | vector of the highest pending source (generic ISR ldr 0xC1B1E)
      +0x10..+0x1C priority, 4 bits per IRQ (32+8*reg+k at bit 4k), 0 = off (ldr 0xCF1F4)
      An IRQ is taken when IE=1 and its priority > level.  Sources: 32-39 DMA, 42 GPIO pin 2
      (GBA doorbell), 43 other GPIO pins (card detect, codec alert), 48-51 timer compares.
0x10001400  PLL: +0x08 control (password; bit 14 = locked, polled up to 10000 times), +0x0C M,
            +0x10 P<<12 | N-1 (ldr 0xCF228) [P]; model returns 'locked'.
0x10001800  system timer, BCM2835 style [C]: +0x00 match flags (W1C), +0x04 free-running counter,
            +0x08..+0x14 compare 0-3 -> IRQ 48-51.  Compare 0 = the 100 Hz Nucleus tick (ldr 0xC40DA
            reloads clk/100), compare 3 = SD timeout ('SDTIME1', ldr 0xCD080).  The counter runs at the
            firmware's own clock (so its microsecond delays are right in emulated time); instruction
            rate (IPS) is a separate parameter.
0x10002400  debug UART, 16550-like [C]: +0x00 THR, +0x14 LSR (bit 5 = THR empty; polled).  Output:
            "DEBUG START", "vc01pp_loader>" / "vc01pp_asf>" / "vc01pp_mp3>", Nucleus FILE errors.
            +0x04/+0x08/+0x0C init values, +0x20 (0x10002420) baud divider (ldr 0xCE5D8).
0x10002800  GPIO, register names from the music image's dumper (mus 0x1F722) [C]:
      +0x00 GPALT  +0x04 GPDIR  +0x08 set  +0x0C clear  +0x10 GPLEV  +0x14 GPEDS (event status, W1C)
      +0x18 GPREN  +0x1C GPFEN  +0x20 GPHEN  +0x24 GPLEN  +0x28 GPAREN  +0x2C GPAFEN
      pin 2  in : host doorbell (GBA CTRL bit 2); rising edge -> GPEDS bit 2 -> IRQ 42 -> ISR
                  ldr 0xC82F8 reads the command at 0xFF020 and acks with GPEDS=4 [C]
      pin 3  in : codec alert (ISR ldr 0xC8644: AC_ALERT / HP_JACK records) [P]
      pin 4  in : SD card detect, low = present (ldr 0xCF16E) [C]
      pin 9  out: codec reset pulse (ldr 0xC6092) [P];  pins 24-29: power switches (ldr 0xCF182) [P]
      pin 30 out: Game Pak IRQ to the GBA, pulsed set/~1000 loops/clear (ldr 0xC82CA) [C]
0x10003000  bit 3 set / bits 2,3 cleared at init (ldr 0xC10EE) [G]
0x1000340C  written 15 at init (ldr 0xCF04C) [G]
0x10003C00  SPI master for the SD card (SPI mode) [C]
      +0x00 control/status: write 0x30 idle, 0x83 init clocks, 0x80/0xB0 transfer; any write
            discards received bytes (the command sender never drains them); read bit 16 = idle,
            bit 17 = received byte available
      +0x04 data: write = shift a byte out, read = received byte (FIFO)
      +0x08 clock divider (from the PLL clock, ldr 0xCDB38)
      Card: CMD0, ACMD41 (CMD1 fallback for MMC), CMD58, CMD59 (CRC on), ACMD42, CMD9, CMD13,
      CMD16(512), CMD17 single-block reads; data CRC16 is checked (0xCD45E halts on mismatch);
      the model is an SD v1 card (CMD8 illegal) over a FAT16 superfloppy image (mkfs.fat + mcopy).
0x10004800  AC'97 controller, names from mus 0x1EF76 [C]: +0x00 ACCS, +0x04 ACFIFO (PCM out, DMA
            target), +0x08 ACCA (codec register), +0x0C ACCD (data).  ACCS bit 0 enable, bit 1
            read, bit 7 go, bit 16 done; bits 22/23 'codec ready': must stay 0 for >8192 polls after
            enable (ldr 0xCEE0A treats an immediate ready as failure and then checks the codec id),
            but the music image waits up to 65536 polls for them before setting the DAC rate
            (mus 0x34642); model: ready 3 ms after enable.
      Codec: Wolfson WM9712 - the loader requires vendor id 0x574D/0x4C12 (ldr 0xC6284).  Status 1
      in VC_STARTUP_INDICATION = this init failing.  Music sets VRA (reg 0x2A=1) and reg 0x2C =
      the MP3's rate (44100 for the sample).
```

## Coupling to the GBA in the emulator

The GBA side (STAT bit 7, RSTAT bit 4, the windows) is the register model of
[protocol.md](protocol.md); the VC01's memory is shared with it. Mode-0x70 writes to VC
0x1xxxxxxx reach the chip's registers (the kick). CTRL bit 3 low holds the core in reset; a rising
CTRL bit 2 is a rising edge on GPIO pin 2. Reads through the pixel window (+0x500-0x6FF) convert
RGB565 to BGR555 ([protocol.md](protocol.md) 8.2).

```
  The VC01 runs in slices of 35112 GBA cycles (1/8 frame), executing
  ips/16777216 instructions per GBA cycle (ips = 50e6 by default).  The timer counts
  at the firmware's own clock in emulated time, so Nucleus ticks, SD timeouts and codec delays are
  in real time whatever ips is.  The firmware's PLL formula gives 8 MHz after boot (16 MHz after
  FIRMWARE_LIST); 1 instruction per cycle at 8 MHz is far too slow for MPEG-4, so either the
  formula is misread or the VPU does most of the work; 50 M instructions/s is a free choice.
  The firmware never idles (tasks poll with NU_Relinquish), so the full budget is always used.
```
