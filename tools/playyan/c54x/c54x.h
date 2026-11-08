/* c54x.h - TMS320C54x CPU core (VC5416 memory map), written from TI SPRU131G / SPRU172C / SPRS095P.
 * Word-addressed.  The on-chip RAM is one 0x40000-word array indexed by the HPI address
 * (SPRS095P fig. 3-9): 0x00000-0x07FFF DARAM0-3 (data 0x0000-0x7FFF, program overlay),
 * 0x18000-0x1FFFF DARAM4-7 (program page 1 high half; data 0x8000-0xFFFF when DROM=1),
 * 0x28000-0x2FFFF SARAM0-3 (page 2), 0x38000-0x3FFFF SARAM4-7 (page 3).
 */
#ifndef C54X_H
#define C54X_H
#include <stdint.h>

#define C54_RAMW 0x40000

typedef struct C54Bus {
    void *ctx;
    uint16_t (*mmr_read)(void *ctx, uint16_t addr);            /* data 0x20..0x5F peripherals */
    void     (*mmr_write)(void *ctx, uint16_t addr, uint16_t v);
    uint16_t (*io_read)(void *ctx, uint16_t pa);               /* PORTR */
    void     (*io_write)(void *ctx, uint16_t pa, uint16_t v);  /* PORTW */
} C54Bus;

/* status bit positions */
#define ST0_TC 12
#define ST0_C 11
#define ST0_OVA 10
#define ST0_OVB 9
#define ST1_BRAF 15
#define ST1_CPL 14
#define ST1_XF 13
#define ST1_HM 12
#define ST1_INTM 11
#define ST1_OVM 9
#define ST1_SXM 8
#define ST1_C16 7
#define ST1_FRCT 6
#define ST1_CMPT 5

typedef struct C54 {
    uint64_t acc[2];          /* 40-bit, two's complement, kept masked to 40 bits */
    uint16_t t, trn, ar[8], sp, bk, brc, rsa, rea, st0, st1, pmst, imr, ifr;
    uint16_t xpc;             /* 7 bits */
    uint16_t pc;
    uint16_t *ram;            /* C54_RAMW words */
    uint16_t rom[0x4000];     /* page-0 0xC000-0xFFFF when MP/MC=0 (contents unknown: zero) */
    C54Bus bus;
    int bio;                  /* BIO pin level: 1 = high */

    /* instruction sequencing state */
    uint16_t fpc;             /* fetch pointer while decoding the current instruction */
    int rpt_active, rpt_first; uint16_t rc, rpt_pc;
    uint32_t par;             /* program address register for repeated MACD/MVPD/READA/... */
    uint16_t dar;             /* data address register for repeated MVKD/MVDK/MVDM/MVMD */
    int rpt_pending;          /* RPT just executed: next instruction is repeated */
    int delay_words;          /* words of delay slot left */
    int delay_pending;        /* delayed transfer queued */
    uint16_t delay_pc, delay_xpc;
    int xc_skip;              /* words to skip (false XC) */
    int nointr;               /* do not take interrupts before the next instruction */
    int branched;             /* set by an instruction that changed PC */
    int idle;                 /* IDLE k active */
    int halted;               /* stopped on an invalid opcode */

    uint64_t cycles, insns;
    uint32_t last_pc;         /* xpc<<16|pc of the last executed instruction */
    uint16_t last_op;
    int trace;                /* trace to stderr */
    uint64_t trace_lo, trace_hi; /* trace window (cycles) */
    uint32_t irq_taken[32];
    uint32_t *prof;
    /* access recorder for differential testing: kind 0 data read, 1 data write, 2 prog read, 3 prog write */
    int rec_on, nrec;
    struct { uint32_t kind, addr, val; } rec[64];           /* optional per-address execution counts (0x80000 entries) */
    uint32_t pc_ring[64]; int pc_ring_i;
    uint32_t bad_ops;         /* invalid opcodes executed */
    uint32_t ext_access;      /* accesses to unmapped/external memory */
    uint32_t rom_reads; uint32_t rom_last;
    uint32_t brk[8]; int nbrk; int hit_brk;
    /* write watch: data address range, hits recorded */
    uint16_t ww_lo, ww_hi; int ww_on; uint32_t ww_hits;
} C54;

void c54_init(C54 *c, uint16_t *ram);
void c54_reset(C54 *c);                 /* hardware reset (PC = FF80) */
int  c54_step(C54 *c);                  /* one instruction (or one repeat iteration); returns cycles */
int  c54_run(C54 *c, int64_t cycles);   /* run until budget used, idle, halt or breakpoint */
void c54_irq(C54 *c, int bit);          /* set IFR bit */
uint16_t c54_dread(C54 *c, uint16_t a);
void c54_dwrite(C54 *c, uint16_t a, uint16_t v);
uint16_t c54_pread(C54 *c, uint32_t a);
void c54_pwrite(C54 *c, uint32_t a, uint16_t v);
int  c54_insn_len(uint16_t op);          /* words, given the first word */
const char *c54_mnem(uint16_t op, uint16_t op2);  /* class name (binutils-compatible mnemonic) */

#endif
