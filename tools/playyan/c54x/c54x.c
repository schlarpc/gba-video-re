/* c54x.c - TMS320C54x CPU core.  Semantics from TI SPRU172C (mnemonic instruction set) and SPRU131G
 * (CPU), memory map from SPRS095P (TMS320VC5416).  Not cycle exact: one call of c54_step executes one
 * instruction (one iteration of a single repeat) with the manual's cycle count approximated.
 * Pipeline effects are not modelled except the explicit delay slots of delayed branches.
 *
 * Arithmetic conventions (SPRU131G 4.2):
 *  - accumulators are 40-bit two's complement;
 *  - overflow (OVA/OVB) is detected at 32 bits: a result outside -2^31..2^31-1 (NEG/ABS examples in
 *    SPRU172C); with OVM=1 it saturates to 00 7FFF FFFF / FF 8000 0000, otherwise wraps to 40 bits;
 *  - carry (C) is the carry out of bit 31 ("defined at the 32nd bit position"); a subtraction sets C
 *    when there is no borrow.
 */
#include "c54x.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define M40 0xFFFFFFFFFFULL
#define M32 0xFFFFFFFFULL
#define BIT(r, b) (((r) >> (b)) & 1)
#define SETB(r, b, v) ((r) = (uint16_t)(((r) & ~(1u << (b))) | ((v) ? (1u << (b)) : 0)))

#define OVM BIT(c->st1, ST1_OVM)
#define SXM BIT(c->st1, ST1_SXM)
#define C16 BIT(c->st1, ST1_C16)
#define FRCT BIT(c->st1, ST1_FRCT)
#define CPL BIT(c->st1, ST1_CPL)
#define CMPT BIT(c->st1, ST1_CMPT)
#define INTM BIT(c->st1, ST1_INTM)
#define ASM_ (((int)(c->st1 & 0x1F) ^ 0x10) - 0x10)
#define DP (c->st0 & 0x1FF)
#define ARP ((c->st0 >> 13) & 7)
#define CARRY BIT(c->st0, ST0_C)
#define TC BIT(c->st0, ST0_TC)
#define SMUL BIT(c->pmst, 1)
#define SST BIT(c->pmst, 0)
#define OVLY BIT(c->pmst, 5)
#define DROM BIT(c->pmst, 3)
#define MPMC BIT(c->pmst, 6)
#define IPTR (c->pmst >> 7)

static inline int64_t sx40(uint64_t v) { return ((int64_t)(v << 24)) >> 24; }
static inline int64_t sx16(uint16_t v) { return (int16_t)v; }
static inline int64_t sx17(int64_t v) { return ((int64_t)(v << 47)) >> 47; }
static inline int64_t ACC(C54 *c, int i) { return sx40(c->acc[i]); }

/* ---------------------------------------------------------------------------------------------
 * memory
 * --------------------------------------------------------------------------------------------- */
static uint16_t reg_read(C54 *c, uint16_t a)
{
    switch (a) {
    case 0x00: return c->imr;
    case 0x01: return c->ifr;
    case 0x06: return c->st0;
    case 0x07: return c->st1 & ~0x0400;
    case 0x08: return c->acc[0] & 0xFFFF;
    case 0x09: return (c->acc[0] >> 16) & 0xFFFF;
    case 0x0A: return (uint16_t)(int16_t)(int8_t)(c->acc[0] >> 32);
    case 0x0B: return c->acc[1] & 0xFFFF;
    case 0x0C: return (c->acc[1] >> 16) & 0xFFFF;
    case 0x0D: return (uint16_t)(int16_t)(int8_t)(c->acc[1] >> 32);
    case 0x0E: return c->t;
    case 0x0F: return c->trn;
    case 0x10: case 0x11: case 0x12: case 0x13: case 0x14: case 0x15: case 0x16: case 0x17:
        return c->ar[a - 0x10];
    case 0x18: return c->sp;
    case 0x19: return c->bk;
    case 0x1A: return c->brc;
    case 0x1B: return c->rsa;
    case 0x1C: return c->rea;
    case 0x1D: return c->pmst;
    case 0x1E: return c->xpc;
    default: return c->ram[a];
    }
}

static void reg_write(C54 *c, uint16_t a, uint16_t v)
{
    switch (a) {
    case 0x00: c->imr = v; break;
    case 0x01: c->ifr &= ~v; break;                 /* write 1 to clear */
    case 0x06: c->st0 = v; break;
    case 0x07: c->st1 = v & ~0x0400; break;
    case 0x08: c->acc[0] = (c->acc[0] & ~0xFFFFULL) | v; break;
    case 0x09: c->acc[0] = (c->acc[0] & ~0xFFFF0000ULL) | ((uint64_t)v << 16); break;
    case 0x0A: c->acc[0] = (c->acc[0] & 0xFFFFFFFFULL) | ((uint64_t)(v & 0xFF) << 32); break;
    case 0x0B: c->acc[1] = (c->acc[1] & ~0xFFFFULL) | v; break;
    case 0x0C: c->acc[1] = (c->acc[1] & ~0xFFFF0000ULL) | ((uint64_t)v << 16); break;
    case 0x0D: c->acc[1] = (c->acc[1] & 0xFFFFFFFFULL) | ((uint64_t)(v & 0xFF) << 32); break;
    case 0x0E: c->t = v; break;
    case 0x0F: c->trn = v; break;
    case 0x10: case 0x11: case 0x12: case 0x13: case 0x14: case 0x15: case 0x16: case 0x17:
        c->ar[a - 0x10] = v; break;
    case 0x18: c->sp = v; break;
    case 0x19: c->bk = v; break;
    case 0x1A: c->brc = v; break;
    case 0x1B: c->rsa = v; break;
    case 0x1C: c->rea = v; break;
    case 0x1D: c->pmst = v; break;
    case 0x1E: c->xpc = v & 0x7F; break;
    default: c->ram[a] = v; break;
    }
}

static inline void recacc(C54 *c, int kind, uint32_t a, uint16_t v)
{
    if (c->rec_on && c->nrec < 64) { c->rec[c->nrec].kind = (uint32_t)kind; c->rec[c->nrec].addr = a; c->rec[c->nrec].val = v; c->nrec++; }
}
static uint16_t dread_(C54 *c, uint16_t a);
uint16_t c54_dread(C54 *c, uint16_t a)
{
    uint16_t v = dread_(c, a);
    recacc(c, 0, a, v);
    return v;
}
static uint16_t dread_(C54 *c, uint16_t a)
{
    if (a < 0x20) return reg_read(c, a);
    if (a < 0x60) return c->bus.mmr_read ? c->bus.mmr_read(c->bus.ctx, a) : c->ram[a];
    if (a < 0x8000) return c->ram[a];
    if (DROM) return c->ram[0x10000 + a];
    c->ext_access++;
    return 0xFFFF;
}

void c54_dwrite(C54 *c, uint16_t a, uint16_t v)
{
    recacc(c, 1, a, v);
    if (c->ww_on && a >= c->ww_lo && a <= c->ww_hi) {
        c->ww_hits++;
        fprintf(stderr, "[c54 ww] %04x <- %04x  pc=%x:%04x\n", a, v, c->last_pc >> 16, c->last_pc & 0xFFFF);
    }
    if (a < 0x20) { reg_write(c, a, v); return; }
    if (a < 0x60) { if (c->bus.mmr_write) c->bus.mmr_write(c->bus.ctx, a, v); else c->ram[a] = v; return; }
    if (a < 0x8000) { c->ram[a] = v; return; }
    if (DROM) { c->ram[0x10000 + a] = v; return; }
    c->ext_access++;
}

/* program space: 23-bit address */
static inline int prog_index(C54 *c, uint32_t a)
{
    uint32_t page = (a >> 16) & 0x7F, off = a & 0xFFFF;
    if (off < 0x8000) {
        if (OVLY && off >= 0x80) return (int)off;
        return -1;
    }
    if (MPMC) return -1;                       /* microprocessor mode: high halves external */
    switch (page) {
    case 0: return off >= 0xC000 ? -2 : -1;     /* -2: on-chip ROM */
    case 1: return (int)(0x10000 + off);
    case 2: return (int)(0x20000 + off);
    case 3: return (int)(0x30000 + off);
    default: return -1;
    }
}

static uint16_t pread_(C54 *c, uint32_t a);
uint16_t c54_pread(C54 *c, uint32_t a)
{
    uint16_t v = pread_(c, a);
    recacc(c, 2, a, v);
    return v;
}
static uint16_t pread_(C54 *c, uint32_t a)
{
    int i = prog_index(c, a);
    if (i >= 0) return c->ram[i];
    if (i == -2) { c->rom_reads++; c->rom_last = a | (c->last_pc << 0); return c->rom[(a & 0xFFFF) - 0xC000]; }
    c->ext_access++;
    return 0xFFFF;
}

void c54_pwrite(C54 *c, uint32_t a, uint16_t v)
{
    recacc(c, 3, a, v);
    int i = prog_index(c, a);
    if (i >= 0) c->ram[i] = v; else c->ext_access++;
}

static inline uint16_t fetch(C54 *c) { return pread_(c, ((uint32_t)c->xpc << 16) | c->fpc++); }

/* ---------------------------------------------------------------------------------------------
 * address generation
 * --------------------------------------------------------------------------------------------- */
static uint16_t brev16(uint16_t v)
{
    v = ((v >> 1) & 0x5555) | ((v & 0x5555) << 1);
    v = ((v >> 2) & 0x3333) | ((v & 0x3333) << 2);
    v = ((v >> 4) & 0x0F0F) | ((v & 0x0F0F) << 4);
    return (uint16_t)((v >> 8) | (v << 8));
}

static uint16_t circ(C54 *c, uint16_t a, int step)
{
    uint16_t bk = c->bk;
    if (bk == 0) return (uint16_t)(a + step);
    int n = 0;
    while ((1u << n) <= bk) n++;           /* smallest N with 2^N > BK */
    uint16_t mask = (uint16_t)((1u << n) - 1);
    int idx = a & mask, ni = idx + step;
    if (ni >= 0 && ni < bk) ;
    else if (ni >= bk) ni -= bk;
    else ni += bk;
    return (uint16_t)((a & ~mask) | (ni & mask));
}

/* Smem/Lmem address with post/pre modification.  lw = 1 (16-bit) or 2 (32-bit) */
static uint16_t ea_smem(C54 *c, uint8_t f, int lw)
{
    if (!(f & 0x80)) {
        uint16_t dma = f & 0x7F;
        return CPL ? (uint16_t)(c->sp + dma) : (uint16_t)((DP << 7) | dma);
    }
    int mod = (f >> 3) & 15, arf = f & 7, ar = arf;
    if (CMPT && arf == 0) ar = ARP;
    uint16_t a = c->ar[ar], addr = a, ar0 = c->ar[0], lk;
    switch (mod) {
    case 0: break;
    case 1: c->ar[ar] = a - lw; break;
    case 2: c->ar[ar] = a + lw; break;
    case 3: addr = a + lw; c->ar[ar] = addr; break;
    case 4: c->ar[ar] = brev16(brev16(a) - brev16(ar0)); break;
    case 5: c->ar[ar] = a - ar0; break;
    case 6: c->ar[ar] = a + ar0; break;
    case 7: c->ar[ar] = brev16(brev16(a) + brev16(ar0)); break;
    case 8: c->ar[ar] = circ(c, a, -lw); break;
    case 9: c->ar[ar] = circ(c, a, -(int)ar0); break;
    case 10: c->ar[ar] = circ(c, a, lw); break;
    case 11: c->ar[ar] = circ(c, a, ar0); break;
    case 12: lk = fetch(c); addr = a + lk; break;
    case 13: lk = fetch(c); addr = a + lk; c->ar[ar] = addr; break;
    case 14: lk = fetch(c); addr = circ(c, a, (int16_t)lk); c->ar[ar] = addr; break;
    case 15: lk = fetch(c); addr = lk; break;
    }
    if (CMPT && arf != 0) c->st0 = (c->st0 & 0x1FFF) | (arf << 13);
    return addr;
}

/* MMR addressing: page 0, 7 LSBs; indirect forces the 9 MSBs of ARx to 0 afterwards */
static uint16_t ea_mmr(C54 *c, uint8_t f)
{
    if (!(f & 0x80)) return f & 0x7F;
    int arf = f & 7, ar = arf;
    if (CMPT && arf == 0) ar = ARP;
    uint16_t addr = c->ar[ar] & 0x7F;
    ea_smem(c, f, 1);
    c->ar[ar] &= 0x7F;
    return addr;
}

/* dual operand: 4-bit field (mod:2, ar:2).  Returns address; modification applied separately. */
static inline int dual_ar(int f4) { return 2 + (f4 & 3); }
static void dual_mod(C54 *c, int f4)
{
    int ar = dual_ar(f4);
    switch ((f4 >> 2) & 3) {
    case 0: break;
    case 1: c->ar[ar]--; break;
    case 2: c->ar[ar]++; break;
    case 3: c->ar[ar] = circ(c, c->ar[ar], c->ar[0]); break;
    }
}
/* resolve Xmem/Ymem addresses from op bits 7-0, apply the modifications (Xmod wins when Xar==Yar) */
static void ea_xy(C54 *c, uint16_t op, uint16_t *xa, uint16_t *ya)
{
    int fx = (op >> 4) & 15, fy = op & 15;
    *xa = c->ar[dual_ar(fx)];
    *ya = c->ar[dual_ar(fy)];
    dual_mod(c, fx);
    if (dual_ar(fx) != dual_ar(fy)) dual_mod(c, fy);
}
static uint16_t ea_x(C54 *c, uint16_t op)
{
    int fx = (op >> 4) & 15;
    uint16_t a = c->ar[dual_ar(fx)];
    dual_mod(c, fx);
    return a;
}

/* 32-bit memory operand to 40 bits per SXM (DADD/DSUB/DRSUB/DADST/DSADT/DSUBT/DLD with C16=0) */
#define LEXT(L) (SXM ? (int64_t)(int32_t)(L) : (int64_t)(uint32_t)(L))

static uint32_t lread(C54 *c, uint16_t a)
{
    return ((uint32_t)c54_dread(c, a) << 16) | c54_dread(c, a ^ 1);
}
static void lwrite(C54 *c, uint16_t a, uint32_t v)
{
    c54_dwrite(c, a, v >> 16);
    c54_dwrite(c, a ^ 1, v & 0xFFFF);
}

/* ---------------------------------------------------------------------------------------------
 * ALU helpers
 * --------------------------------------------------------------------------------------------- */
static inline void set_ov(C54 *c, int d) { c->st0 |= 1u << (d ? ST0_OVB : ST0_OVA); }

/* store a full-precision result to accumulator d, with 32-bit overflow detection and OVM */
static void acc_res(C54 *c, int d, int64_t r)
{
    if (r > 0x7FFFFFFFLL || r < -0x80000000LL) {
        set_ov(c, d);
        if (OVM) r = (r > 0) ? 0x7FFFFFFFLL : -0x80000000LL;
    }
    c->acc[d] = (uint64_t)r & M40;
}
/* same without overflow handling (plain load) */
static inline void acc_set(C54 *c, int d, int64_t r) { c->acc[d] = (uint64_t)r & M40; }

static inline void set_c(C54 *c, int v) { SETB(c->st0, ST0_C, v); }
static inline void set_tc(C54 *c, int v) { SETB(c->st0, ST0_TC, v); }

static inline int carry_add(int64_t x, int64_t y, int cin)
{
    return (int)((((uint64_t)x & M32) + ((uint64_t)y & M32) + (uint64_t)cin) >> 32) & 1;
}
static inline int carry_sub(int64_t x, int64_t y, int bin)  /* 1 = no borrow */
{
    return ((uint64_t)x & M32) >= (((uint64_t)y & M32) + (uint64_t)bin);
}

/* 16-bit memory operand to 40-bit per SXM */
static inline int64_t ext16(C54 *c, uint16_t v) { return SXM ? sx16(v) : (int64_t)v; }

/* shift a 40-bit value: left wraps at 40 bits, right arithmetic if SXM else logical */
static int64_t shift40(C54 *c, int64_t v, int s)
{
    if (s >= 0) return sx40((uint64_t)v << s);
    s = -s;
    if (s >= 40) return SXM ? (v < 0 ? -1 : 0) : 0;
    if (SXM) return v >> s;
    return (int64_t)(((uint64_t)v & M40) >> s);
}

/* add y to accumulator s, result to d */
static void op_add(C54 *c, int s, int64_t y, int d)
{
    int64_t x = ACC(c, s), r = x + y;
    set_c(c, carry_add(x, y, 0));
    acc_res(c, d, r);
}
static void op_sub(C54 *c, int s, int64_t y, int d)   /* d = s - y */
{
    int64_t x = ACC(c, s), r = x - y;
    set_c(c, carry_sub(x, y, 0));
    acc_res(c, d, r);
}

/* multiplier: 17x17 signed, FRCT doubles, SMUL saturation for 8000*8000 */
static int64_t mul(C54 *c, int64_t x, int64_t y)
{
    int64_t p = x * y;
    if (FRCT) {
        p <<= 1;
        if (SMUL && OVM && p == 0x80000000LL) p = 0x7FFFFFFF;
    }
    return p;
}
static inline int64_t ahi17(C54 *c) { return sx17(ACC(c, 0) >> 16); }  /* A(32-16) */

static void mac_res(C54 *c, int d, int64_t r, int rnd)
{
    if (rnd) r = (r + 0x8000) & ~0xFFFFLL;
    acc_res(c, d, r);
}

/* value stored by STH/STL/ST|| : 40-bit acc shifted, optional SST saturation */
static int64_t store_shift(C54 *c, int s, int sh)
{
    int64_t v = shift40(c, ACC(c, s), sh);
    if (SST) {
        if (SXM) { if (v > 0x7FFFFFFFLL) v = 0x7FFFFFFF; else if (v < -0x80000000LL) v = -0x80000000LL; }
        else { uint64_t u = (uint64_t)v & M40; if (u > M32) v = (int64_t)M32; }
    }
    return v;
}
/* STH / ST||: shift by SHIFT, saturate to 32 bits (SST), then store bits 31-16 (SPRU131G 4.3.3, Example
 * 4-2: STH A,-4 of 7F FFFF 0000 stores 7FFF).  Shifting by SHIFT-16 first saturated the wrong word, so
 * clipped synthesis samples wrapped (found by the NMP decoder work, docs/playyan/decoders.md). */
static inline uint16_t st_hi(C54 *c, int s, int sh) { return (uint16_t)((store_shift(c, s, sh) >> 16) & 0xFFFF); }
static inline uint16_t st_lo(C54 *c, int s, int sh) { return (uint16_t)(store_shift(c, s, sh) & 0xFFFF); }

static inline int shift5(uint16_t op) { return ((int)(op & 0x1F) ^ 0x10) - 0x10; }
static inline int tshift(C54 *c) { return ((int)(c->t & 0x3F) ^ 0x20) - 0x20; } /* -32..31; manual says -16..31 */

/* ---------------------------------------------------------------------------------------------
 * conditions
 * --------------------------------------------------------------------------------------------- */
static int acc_cond(C54 *c, int acc, int code3)
{
    int64_t v = ACC(c, acc);
    switch (code3) {
    case 2: return v >= 0;
    case 3: return v < 0;
    case 4: return v != 0;
    case 5: return v == 0;
    case 6: return v > 0;
    case 7: return v <= 0;
    default: return 1;
    }
}

static int cond(C54 *c, uint8_t cc)
{
    if (cc == 0) return 1;
    if (cc & 0x40) {
        int acc = (cc >> 3) & 1, ok = 1;
        if (cc & 7) ok &= acc_cond(c, acc, cc & 7);
        if (cc & 0x20) {
            int ovb = acc ? ST0_OVB : ST0_OVA;
            int ov = BIT(c->st0, ovb);
            ok &= (cc & 0x10) ? ov : !ov;
            SETB(c->st0, ovb, 0);   /* testing OV clears it */
        }
        return ok;
    }
    int ok = 1;
    if (cc & 0x20) ok &= (cc & 0x10) ? TC : !TC;
    if (cc & 0x08) ok &= (cc & 0x04) ? CARRY : !CARRY;
    if (cc & 0x02) ok &= (cc & 0x01) ? !c->bio : c->bio;   /* BIO = pin low */
    return ok;
}

/* 4-bit cond for SACCD/SRCCD/STRCD: bit3 = B, bits 2-0 as above */
static int cond4(C54 *c, int cc) { return acc_cond(c, (cc >> 3) & 1, cc & 7); }

/* ---------------------------------------------------------------------------------------------
 * control flow helpers
 * --------------------------------------------------------------------------------------------- */
static inline void push(C54 *c, uint16_t v) { c->sp--; c54_dwrite(c, c->sp, v); }
static inline uint16_t pop(C54 *c) { uint16_t v = c54_dread(c, c->sp); c->sp++; return v; }

/* transfer (immediate or after delay slots) */
static void xfer(C54 *c, uint16_t pc, int xpc, int delayed)
{
    if (xpc < 0) xpc = c->xpc;
    if (delayed) {
        c->delay_pending = 1; c->delay_words = 2; c->delay_pc = pc; c->delay_xpc = (uint16_t)xpc;
    } else {
        c->pc = pc; c->xpc = (uint16_t)xpc; c->branched = 1;
    }
}

/* ---------------------------------------------------------------------------------------------
 * instruction length
 * --------------------------------------------------------------------------------------------- */
static inline int lkaddr(uint16_t op) { return (op & 0x80) && ((op >> 3) & 15) >= 12; }

int c54_insn_len(uint16_t op)
{
    int hi = op >> 8;
    if (hi < 0x48) return 1 + lkaddr(op);
    switch (hi) {
    case 0x48: case 0x49: case 0x4A: case 0x8A: return 1;   /* MMR: no lk */
    case 0x4B: case 0x4C: case 0x4D: case 0x4E: case 0x4F: return 1 + lkaddr(op);
    }
    if (hi >= 0x50 && hi <= 0x5F) return 1 + lkaddr(op);
    if (hi >= 0x60 && hi <= 0x6F) {
        if (hi == 0x6D) return 1 + lkaddr(op);
        return 2 + lkaddr(op);
    }
    if (hi >= 0x70 && hi <= 0x7D) {
        if (hi == 0x72 || hi == 0x73 || hi == 0x77) return 2;
        return 2 + lkaddr(op);
    }
    if (hi == 0x7E || hi == 0x7F) return 1 + lkaddr(op);
    if (hi >= 0x80 && hi <= 0x8F) {
        if (hi == 0x88 || hi == 0x89 || hi == 0x8A) return 1;
        return 1 + lkaddr(op);
    }
    if (hi < 0xE0) return 1;
    if (hi == 0xE0) return 2;               /* FIRS */
    if (hi < 0xF0) return 1;
    if (hi >= 0xF0 && hi <= 0xF3) {
        int lo = op & 0xFF;
        if (lo < 0x80) return 2;             /* lk forms, RPT #lk, RPTZ, RPTB, B, CALL */
        return 1;
    }
    if (hi >= 0xF4 && hi <= 0xF7) return 1;
    if (hi >= 0xF8 && hi <= 0xFB) return 2;
    return 1;                                /* FC-FF: RC/RET/XC */
}

/* ---------------------------------------------------------------------------------------------
 * repeat-aware address registers
 * --------------------------------------------------------------------------------------------- */
static uint32_t par_get(C54 *c, uint32_t init)
{
    if (!c->rpt_active || c->rpt_first) c->par = init;
    return c->par;
}
static void par_next(C54 *c) { if (c->rpt_active) c->par = (c->par & ~0xFFFFu) | ((c->par + 1) & 0xFFFF); }
static uint16_t dar_get(C54 *c, uint16_t init)
{
    if (!c->rpt_active || c->rpt_first) c->dar = init;
    return c->dar;
}
static void dar_next(C54 *c) { if (c->rpt_active) c->dar++; }

/* ---------------------------------------------------------------------------------------------
 * execute
 * --------------------------------------------------------------------------------------------- */
static void bad(C54 *c, uint16_t op)
{
    c->bad_ops++;
    if (c->bad_ops < 20)
        fprintf(stderr, "[c54] invalid opcode %04x at %x:%04x\n", op, c->xpc, c->last_pc & 0xFFFF);
}

static int exec_f4(C54 *c, uint16_t op);   /* F4xx-F7xx */
static int exec_f0(C54 *c, uint16_t op);   /* F0xx-F3xx */

/* returns cycles (approximate) */
static int exec(C54 *c, uint16_t op)
{
    int hi = op >> 8;
    int s8 = (op >> 8) & 1;         /* S or D in bit 8 */
    int s9 = (op >> 9) & 1;
    uint16_t a, xa, ya, v, w;
    int64_t x, y, p;
    uint32_t L;

    switch (hi) {
    /* ---- single memory operand arithmetic ---- */
    case 0x00: case 0x01: a = ea_smem(c, op, 1); op_add(c, s8, ext16(c, c54_dread(c, a)), s8); return 1;
    case 0x02: case 0x03: a = ea_smem(c, op, 1); op_add(c, s8, c54_dread(c, a), s8); return 1;
    case 0x04: case 0x05: a = ea_smem(c, op, 1); op_add(c, s8, shift40(c, ext16(c, c54_dread(c, a)), tshift(c)), s8); return 1;
    case 0x06: case 0x07: {
        a = ea_smem(c, op, 1);
        x = ACC(c, s8); y = c54_dread(c, a);
        int ci = CARRY;
        set_c(c, carry_add(x, y, ci));
        acc_res(c, s8, x + y + ci);
        return 1; }
    case 0x08: case 0x09: a = ea_smem(c, op, 1); op_sub(c, s8, ext16(c, c54_dread(c, a)), s8); return 1;
    case 0x0A: case 0x0B: a = ea_smem(c, op, 1); op_sub(c, s8, c54_dread(c, a), s8); return 1;
    case 0x0C: case 0x0D: a = ea_smem(c, op, 1); op_sub(c, s8, shift40(c, ext16(c, c54_dread(c, a)), tshift(c)), s8); return 1;
    case 0x0E: case 0x0F: {
        a = ea_smem(c, op, 1);
        x = ACC(c, s8); y = c54_dread(c, a);
        int bi = !CARRY;
        set_c(c, carry_sub(x, y, bi));
        acc_res(c, s8, x - y - bi);
        return 1; }
    case 0x10: case 0x11: a = ea_smem(c, op, 1); acc_set(c, s8, ext16(c, c54_dread(c, a))); return 1;
    case 0x12: case 0x13: a = ea_smem(c, op, 1); acc_set(c, s8, c54_dread(c, a)); return 1;
    case 0x14: case 0x15: a = ea_smem(c, op, 1); acc_res(c, s8, shift40(c, ext16(c, c54_dread(c, a)), tshift(c))); return 1;
    case 0x16: case 0x17: a = ea_smem(c, op, 1); acc_set(c, s8, (ext16(c, c54_dread(c, a)) << 16) + 0x8000); return 1;
    case 0x18: case 0x19: a = ea_smem(c, op, 1); c->acc[s8] &= c54_dread(c, a); return 1;
    case 0x1A: case 0x1B: a = ea_smem(c, op, 1); c->acc[s8] |= c54_dread(c, a); return 1;
    case 0x1C: case 0x1D: a = ea_smem(c, op, 1); c->acc[s8] ^= c54_dread(c, a); return 1;
    case 0x1E: case 0x1F: {
        a = ea_smem(c, op, 1);
        x = ACC(c, s8);
        y = ext16(c, c54_dread(c, a)) << 15;
        int64_t r = x - y;
        set_c(c, carry_sub(x, y, 0));
        if (r >= 0) acc_res(c, s8, (r << 1) + 1);
        else acc_res(c, s8, x << 1);
        return 1; }
    case 0x20: case 0x21: a = ea_smem(c, op, 1); acc_res(c, s8, mul(c, sx16(c->t), sx16(c54_dread(c, a)))); return 1;
    case 0x22: case 0x23: a = ea_smem(c, op, 1); mac_res(c, s8, mul(c, sx16(c->t), sx16(c54_dread(c, a))), 1); return 1;
    case 0x24: case 0x25: a = ea_smem(c, op, 1); acc_res(c, s8, mul(c, c->t, c54_dread(c, a))); return 1;
    case 0x26: case 0x27: a = ea_smem(c, op, 1); v = c54_dread(c, a); c->t = v; acc_res(c, s8, mul(c, sx16(v), sx16(v))); return 1;
    case 0x28: case 0x29: case 0x2A: case 0x2B:
        a = ea_smem(c, op, 1);
        mac_res(c, s8, ACC(c, s8) + mul(c, sx16(c->t), sx16(c54_dread(c, a))), hi & 2 ? 1 : 0); return 1;
    case 0x2C: case 0x2D: case 0x2E: case 0x2F:
        a = ea_smem(c, op, 1);
        mac_res(c, s8, ACC(c, s8) - mul(c, sx16(c->t), sx16(c54_dread(c, a))), hi & 2 ? 1 : 0); return 1;
    case 0x30: a = ea_smem(c, op, 1); c->t = c54_dread(c, a); return 1;
    case 0x31: a = ea_smem(c, op, 1); v = c54_dread(c, a); c->t = v; acc_res(c, 1, mul(c, sx16(v), ahi17(c))); return 1;
    case 0x32: a = ea_smem(c, op, 1); c->st1 = (c->st1 & ~0x1F) | (c54_dread(c, a) & 0x1F); return 1;
    case 0x33: a = ea_smem(c, op, 1); v = c54_dread(c, a); c->t = v; acc_res(c, 1, ACC(c, 1) - mul(c, sx16(v), ahi17(c))); return 1;
    case 0x34: a = ea_smem(c, op, 1); set_tc(c, (c54_dread(c, a) >> (15 - (c->t & 15))) & 1); return 1;
    case 0x35: case 0x37:
        a = ea_smem(c, op, 1); v = c54_dread(c, a); p = mul(c, sx16(v), ahi17(c)); c->t = v;
        mac_res(c, 1, ACC(c, 1) + p, hi == 0x37); return 1;
    case 0x36: {   /* POLY */
        a = ea_smem(c, op, 1); v = c54_dread(c, a);
        int64_t r = ACC(c, 1) + mul(c, ahi17(c), sx16(c->t));
        mac_res(c, 0, r, 1);
        acc_set(c, 1, ext16(c, v) << 16);
        return 1; }
    case 0x38: case 0x39: a = ea_smem(c, op, 1); v = c54_dread(c, a); c->t = v; acc_res(c, s8, ACC(c, s8) + mul(c, sx16(v), sx16(v))); return 1;
    case 0x3A: case 0x3B: a = ea_smem(c, op, 1); v = c54_dread(c, a); c->t = v; acc_res(c, s8, ACC(c, s8) - mul(c, sx16(v), sx16(v))); return 1;
    case 0x3C: case 0x3D: case 0x3E: case 0x3F: {   /* ADD Smem,16,src,dst: carry only set */
        a = ea_smem(c, op, 1);
        x = ACC(c, s9); y = ext16(c, c54_dread(c, a)) << 16;
        if (carry_add(x, y, 0)) set_c(c, 1);
        acc_res(c, s8, x + y);
        return 1; }
    case 0x40: case 0x41: case 0x42: case 0x43: {   /* SUB Smem,16: carry only cleared */
        a = ea_smem(c, op, 1);
        x = ACC(c, s9); y = ext16(c, c54_dread(c, a)) << 16;
        if (!carry_sub(x, y, 0)) set_c(c, 0);
        acc_res(c, s8, x - y);
        return 1; }
    case 0x44: case 0x45: a = ea_smem(c, op, 1); acc_set(c, s8, ext16(c, c54_dread(c, a)) << 16); return 1;
    case 0x46: a = ea_smem(c, op, 1); c->st0 = (c->st0 & ~0x1FF) | (c54_dread(c, a) & 0x1FF); return 3;
    case 0x47: a = ea_smem(c, op, 1); c->rc = c54_dread(c, a); c->rpt_pending = 1; return 3;
    case 0x48: case 0x49: a = ea_mmr(c, op); acc_set(c, s8, c54_dread(c, a)); return 1;
    case 0x4A: a = ea_mmr(c, op); v = c54_dread(c, a); push(c, v); return 1;
    case 0x4B: a = ea_smem(c, op, 1); v = c54_dread(c, a); push(c, v); return 1;
    case 0x4C: a = ea_smem(c, op, 1); v = c54_dread(c, a); c->t = v; c54_dwrite(c, a + 1, v); return 1;
    case 0x4D: a = ea_smem(c, op, 1); c54_dwrite(c, a + 1, c54_dread(c, a)); return 1;
    case 0x4E: case 0x4F: {   /* DST src,Lmem */
        a = ea_smem(c, op, 2);
        int64_t vv = ACC(c, s8);
        if (SST) vv = store_shift(c, s8, 0);
        lwrite(c, a, (uint32_t)vv);
        return 2; }
    /* ---- long word ---- */
    case 0x50: case 0x51: case 0x52: case 0x53: {   /* DADD Lmem,src,dst */
        a = ea_smem(c, op, 2); L = lread(c, a);
        int s = s9, d = s8;
        if (!C16) {
            x = ACC(c, s); y = LEXT(L);
            set_c(c, carry_add(x, y, 0));
            acc_res(c, d, x + y);
        } else {
            int64_t hi16 = (int16_t)(ACC(c, s) >> 16) + (int64_t)(int16_t)(L >> 16);
            int64_t lo16 = (int16_t)(ACC(c, s) & 0xFFFF) + (int64_t)(int16_t)(L & 0xFFFF);
            set_c(c, (((ACC(c, s) >> 16) & 0xFFFF) + (L >> 16)) >> 16 & 1);
            acc_set(c, d, (hi16 << 16) | (lo16 & 0xFFFF));
        }
        return 1; }
    case 0x54: case 0x55: {   /* DSUB Lmem,src: src - Lmem */
        a = ea_smem(c, op, 2); L = lread(c, a);
        if (!C16) { x = ACC(c, s8); y = LEXT(L); set_c(c, carry_sub(x, y, 0)); acc_res(c, s8, x - y); }
        else {
            int64_t hi16 = (int16_t)(ACC(c, s8) >> 16) - (int64_t)(int16_t)(L >> 16);
            int64_t lo16 = (int16_t)(ACC(c, s8) & 0xFFFF) - (int64_t)(int16_t)(L & 0xFFFF);
            set_c(c, ((ACC(c, s8) >> 16) & 0xFFFF) >= (L >> 16));
            acc_set(c, s8, (hi16 << 16) | (lo16 & 0xFFFF));
        }
        return 1; }
    case 0x56: case 0x57:     /* DLD */
        a = ea_smem(c, op, 2); L = lread(c, a);
        if (!C16) acc_set(c, s8, SXM ? (int64_t)(int32_t)L : (int64_t)L);
        else acc_set(c, s8, ((int64_t)(int16_t)(L >> 16) << 16) | (L & 0xFFFF));
        return 1;
    case 0x58: case 0x59: {   /* DRSUB: Lmem - src */
        a = ea_smem(c, op, 2); L = lread(c, a);
        if (!C16) { x = LEXT(L); y = ACC(c, s8); set_c(c, carry_sub(x, y, 0)); acc_res(c, s8, x - y); }
        else {
            int64_t hi16 = (int64_t)(int16_t)(L >> 16) - (int16_t)(ACC(c, s8) >> 16);
            int64_t lo16 = (int64_t)(int16_t)(L & 0xFFFF) - (int16_t)(ACC(c, s8) & 0xFFFF);
            set_c(c, (L >> 16) >= ((ACC(c, s8) >> 16) & 0xFFFF));
            acc_set(c, s8, (hi16 << 16) | (lo16 & 0xFFFF));
        }
        return 1; }
    case 0x5A: case 0x5B: case 0x5C: case 0x5D: case 0x5E: case 0x5F: {
        /* 5A DADST: hi+T, lo-T ; 5C DSUBT: hi-T, lo-T ; 5E DSADT: hi-T, lo+T.  C16=0: Lmem +/- (T + T<<16) */
        a = ea_smem(c, op, 2); L = lread(c, a);
        int kind = (hi >> 1) & 3;      /* 1 DADST, 2 DSUBT, 3 DSADT */
        int64_t tt = sx16(c->t);
        if (!C16) {
            x = LEXT(L);
            int64_t tv = (ext16(c, c->t) << 16) + ext16(c, c->t);   /* (T) + (T << 16), T per SXM */
            if (kind == 1) { set_c(c, carry_add(x, tv, 0)); acc_res(c, s8, x + tv); }
            else { set_c(c, carry_sub(x, tv, 0)); acc_res(c, s8, x - tv); }
        } else {
            int64_t h = (int16_t)(L >> 16), l = (int16_t)(L & 0xFFFF);
            int64_t rh = (kind == 1) ? h + tt : h - tt;
            int64_t rl = (kind == 3) ? l + tt : l - tt;
            acc_set(c, s8, (rh << 16) | (rl & 0xFFFF));
        }
        return 1; }
    /* ---- two-word immediate operand forms ---- */
    case 0x60: a = ea_smem(c, op, 1); w = fetch(c); set_tc(c, c54_dread(c, a) == w); return 2;
    case 0x61: a = ea_smem(c, op, 1); w = fetch(c); set_tc(c, (c54_dread(c, a) & w) != 0); return 2;
    case 0x62: case 0x63: a = ea_smem(c, op, 1); w = fetch(c); v = c54_dread(c, a); c->t = v;
        acc_res(c, s8, mul(c, sx16(v), sx16(w))); return 2;
    case 0x64: case 0x65: case 0x66: case 0x67:
        a = ea_smem(c, op, 1); w = fetch(c); v = c54_dread(c, a); c->t = v;
        acc_res(c, s8, ACC(c, s9) + mul(c, sx16(v), sx16(w))); return 2;
    case 0x68: a = ea_smem(c, op, 1); w = fetch(c); c54_dwrite(c, a, c54_dread(c, a) & w); return 2;
    case 0x69: a = ea_smem(c, op, 1); w = fetch(c); c54_dwrite(c, a, c54_dread(c, a) | w); return 2;
    case 0x6A: a = ea_smem(c, op, 1); w = fetch(c); c54_dwrite(c, a, c54_dread(c, a) ^ w); return 2;
    case 0x6B: {   /* ADDM #lk,Smem: OVA, C */
        a = ea_smem(c, op, 1); w = fetch(c);
        x = ext16(c, c54_dread(c, a)); y = ext16(c, w);
        int64_t r = x + y;
        set_c(c, (((uint64_t)x & 0xFFFF) + ((uint64_t)y & 0xFFFF)) >> 16 & 1);
        if (r > 0x7FFF || r < -0x8000) {
            if (OVM) { set_ov(c, 0); r = r > 0 ? 0x7FFF : -0x8000; }
        }
        c54_dwrite(c, a, (uint16_t)r);
        return 2; }
    case 0x6C: case 0x6E: {   /* BANZ[D] pmad, Sind */
        /* the generated address is tested (SPRU172C BANZ ex. 3: *AR3(-1) with AR3=1 is not taken);
           for post-modified forms that is ARx before the modification */
        uint16_t pmad;
        uint16_t test = ea_smem(c, op | 0x80, 1);   /* lk forms consume a word before pmad */
        pmad = fetch(c);
        if (test != 0) xfer(c, pmad, -1, hi == 0x6E);
        return test ? 4 : 2; }
    case 0x6D: ea_smem(c, op, 1); return 1;   /* MAR */
    case 0x6F: {   /* extended ADD/SUB/LD/STH/STL Smem, SHIFT */
        a = ea_smem(c, op, 1);
        w = fetch(c);
        int sh = shift5(w), sub = (w >> 5) & 7, sd = (w >> 9) & 1, dd = (w >> 8) & 1;
        if ((w & 0xFC00) != 0x0C00) { bad(c, op); return 1; }
        switch (sub) {
        case 0: op_add(c, sd, shift40(c, ext16(c, c54_dread(c, a)), sh), dd); break;
        case 1: op_sub(c, sd, shift40(c, ext16(c, c54_dread(c, a)), sh), dd); break;
        case 2: acc_res(c, dd, shift40(c, ext16(c, c54_dread(c, a)), sh)); break;
        case 3: c54_dwrite(c, a, st_hi(c, dd, sh)); break;
        case 4: c54_dwrite(c, a, st_lo(c, dd, sh)); break;
        default: bad(c, op);
        }
        return 2; }
    case 0x70: a = ea_smem(c, op, 1); w = fetch(c); a = a; { uint16_t d = dar_get(c, w); c54_dwrite(c, a, c54_dread(c, d)); dar_next(c); } return 2;
    case 0x71: a = ea_smem(c, op, 1); w = fetch(c); { uint16_t d = dar_get(c, w); c54_dwrite(c, d, c54_dread(c, a)); dar_next(c); } return 2;
    case 0x72: w = fetch(c); a = ea_mmr(c, op); { uint16_t d = dar_get(c, w); c54_dwrite(c, a, c54_dread(c, d)); dar_next(c); } return 2;
    case 0x73: w = fetch(c); a = ea_mmr(c, op); { uint16_t d = dar_get(c, w); c54_dwrite(c, d, c54_dread(c, a)); dar_next(c); } return 2;
    case 0x74: a = ea_smem(c, op, 1); w = fetch(c);
        c54_dwrite(c, a, c->bus.io_read ? c->bus.io_read(c->bus.ctx, w) : 0xFFFF); return 2;
    case 0x75: a = ea_smem(c, op, 1); w = fetch(c);
        if (c->bus.io_write) { c->bus.io_write(c->bus.ctx, w, c54_dread(c, a)); } return 2;
    case 0x76: a = ea_smem(c, op, 1); w = fetch(c); c54_dwrite(c, a, w); return 2;
    case 0x77: w = fetch(c); a = ea_mmr(c, op); c54_dwrite(c, a, w); return 2;
    case 0x78: case 0x79: case 0x7A: case 0x7B: {   /* MACP / MACD Smem,pmad,src */
        a = ea_smem(c, op, 1); w = fetch(c);
        uint32_t pa = par_get(c, ((uint32_t)c->xpc << 16) | w);
        v = c54_dread(c, a);
        uint16_t pv = c54_pread(c, pa);
        c->t = v;
        mac_res(c, s8, ACC(c, s8) + mul(c, sx16(v), sx16(pv)), 0);
        if (hi >= 0x7A) c54_dwrite(c, a + 1, v);
        par_next(c);
        return 3; }
    case 0x7C: a = ea_smem(c, op, 1); w = fetch(c); { uint32_t pa = par_get(c, ((uint32_t)c->xpc << 16) | w); c54_dwrite(c, a, c54_pread(c, pa)); par_next(c); } return 3;
    case 0x7D: a = ea_smem(c, op, 1); w = fetch(c); { uint32_t pa = par_get(c, ((uint32_t)c->xpc << 16) | w); c54_pwrite(c, pa, c54_dread(c, a)); par_next(c); } return 4;
    case 0x7E: a = ea_smem(c, op, 1); { uint32_t pa = par_get(c, (uint32_t)(c->acc[0] & 0x7FFFFF)); c54_dwrite(c, a, c54_pread(c, pa)); par_next(c); } return 5;
    case 0x7F: a = ea_smem(c, op, 1); { uint32_t pa = par_get(c, (uint32_t)(c->acc[0] & 0x7FFFFF)); c54_pwrite(c, pa, c54_dread(c, a)); par_next(c); } return 5;
    /* ---- stores ---- */
    case 0x80: case 0x81: a = ea_smem(c, op, 1); c54_dwrite(c, a, st_lo(c, s8, 0)); return 1;
    case 0x82: case 0x83: a = ea_smem(c, op, 1); c54_dwrite(c, a, st_hi(c, s8, 0)); return 1;
    case 0x84: case 0x85: a = ea_smem(c, op, 1); c54_dwrite(c, a, st_lo(c, s8, ASM_)); return 1;
    case 0x86: case 0x87: a = ea_smem(c, op, 1); c54_dwrite(c, a, st_hi(c, s8, ASM_)); return 1;
    case 0x88: case 0x89: a = ea_mmr(c, op); c54_dwrite(c, a, st_lo(c, s8, 0)); return 1;
    case 0x8A: a = ea_mmr(c, op); v = pop(c); c54_dwrite(c, a, v); return 1;
    case 0x8B: a = ea_smem(c, op, 1); v = pop(c); c54_dwrite(c, a, v); return 1;
    case 0x8C: a = ea_smem(c, op, 1); c54_dwrite(c, a, c->t); return 1;
    case 0x8D: a = ea_smem(c, op, 1); c54_dwrite(c, a, c->trn); return 1;
    case 0x8E: case 0x8F: {   /* CMPS src,Smem */
        a = ea_smem(c, op, 1);
        int16_t h = (int16_t)(c->acc[s8] >> 16), l = (int16_t)(c->acc[s8] & 0xFFFF);
        if (h > l) { c54_dwrite(c, a, (uint16_t)h); c->trn = (uint16_t)(c->trn << 1); set_tc(c, 0); }
        else { c54_dwrite(c, a, (uint16_t)l); c->trn = (uint16_t)((c->trn << 1) | 1); set_tc(c, 1); }
        return 1; }
    /* ---- dual-operand-format single operand ---- */
    case 0x90: case 0x91: a = ea_x(c, op); op_add(c, s8, ext16(c, c54_dread(c, a)) << (op & 15), s8); return 1;
    case 0x92: case 0x93: a = ea_x(c, op); op_sub(c, s8, ext16(c, c54_dread(c, a)) << (op & 15), s8); return 1;
    case 0x94: case 0x95: a = ea_x(c, op); acc_set(c, s8, ext16(c, c54_dread(c, a)) << (op & 15)); return 1;
    case 0x96: a = ea_x(c, op); set_tc(c, (c54_dread(c, a) >> (15 - (op & 15))) & 1); return 1;
    case 0x98: case 0x99: a = ea_x(c, op); c54_dwrite(c, a, st_lo(c, s8, op & 15)); return 1;
    case 0x9A: case 0x9B: a = ea_x(c, op); c54_dwrite(c, a, st_hi(c, s8, op & 15)); return 1;
    case 0x9C: a = ea_x(c, op); if (cond4(c, op & 15)) c54_dwrite(c, a, c->t); return 1;
    case 0x9D: a = ea_x(c, op); if (cond4(c, op & 15)) c54_dwrite(c, a, c->brc); return 1;
    case 0x9E: case 0x9F: a = ea_x(c, op); if (cond4(c, op & 15)) c54_dwrite(c, a, st_hi(c, s8, ASM_)); return 1;
    /* ---- dual operand ---- */
    case 0xA0: case 0xA1: {
        ea_xy(c, op, &xa, &ya);
        x = ext16(c, c54_dread(c, xa)) << 16; y = ext16(c, c54_dread(c, ya)) << 16;
        set_c(c, carry_add(x, y, 0)); acc_res(c, s8, x + y); return 1; }
    case 0xA2: case 0xA3: {
        ea_xy(c, op, &xa, &ya);
        x = ext16(c, c54_dread(c, xa)) << 16; y = ext16(c, c54_dread(c, ya)) << 16;
        set_c(c, carry_sub(x, y, 0)); acc_res(c, s8, x - y); return 1; }
    case 0xA4: case 0xA5:
        ea_xy(c, op, &xa, &ya); v = c54_dread(c, xa); c->t = v;
        acc_res(c, s8, mul(c, sx16(v), sx16(c54_dread(c, ya)))); return 1;
    case 0xA6: case 0xA7:
        ea_xy(c, op, &xa, &ya); v = c54_dread(c, xa); c->t = v;
        acc_res(c, s8, ACC(c, s8) + mul(c, (int64_t)v, sx16(c54_dread(c, ya)))); return 1;
    case 0xA8: case 0xA9: case 0xAA: case 0xAB: case 0xAC: case 0xAD: case 0xAE: case 0xAF: {
        /* LD Xmem,dst || MAC[R]/MAS[R] Ymem,dst_ */
        ea_xy(c, op, &xa, &ya);
        int d = s8, d2 = !s8;
        int rnd = (hi >> 1) & 1, sub = (hi >> 2) & 1;
        uint16_t xv = c54_dread(c, xa), yv = c54_dread(c, ya);
        p = mul(c, sx16(yv), sx16(c->t));
        acc_set(c, d, ext16(c, xv) << 16);
        mac_res(c, d2, sub ? ACC(c, d2) - p : ACC(c, d2) + p, rnd);
        return 1; }
    case 0xB0: case 0xB1: case 0xB2: case 0xB3: case 0xB4: case 0xB5: case 0xB6: case 0xB7:
    case 0xB8: case 0xB9: case 0xBA: case 0xBB: case 0xBC: case 0xBD: case 0xBE: case 0xBF: {
        /* MAC[R]/MAS[R] Xmem,Ymem,src,dst */
        ea_xy(c, op, &xa, &ya);
        int rnd = (hi >> 2) & 1, sub = (hi >> 3) & 1;
        uint16_t xv = c54_dread(c, xa), yv = c54_dread(c, ya);
        c->t = xv;
        p = mul(c, sx16(xv), sx16(yv));
        mac_res(c, s8, sub ? ACC(c, s9) - p : ACC(c, s9) + p, rnd);
        return 1; }
    case 0xC0: case 0xC1: case 0xC2: case 0xC3: case 0xC4: case 0xC5: case 0xC6: case 0xC7:
    case 0xC8: case 0xC9: case 0xCA: case 0xCB: case 0xCC: case 0xCD: case 0xCE: case 0xCF:
    case 0xD0: case 0xD1: case 0xD2: case 0xD3: case 0xD4: case 0xD5: case 0xD6: case 0xD7:
    case 0xD8: case 0xD9: case 0xDA: case 0xDB: case 0xDC: case 0xDD: case 0xDE: case 0xDF:
    case 0xE4: case 0xE6: {
        /* ST src,Ymem || op Xmem,dst.  src = bit 9, dst = bit 8 */
        ea_xy(c, op, &xa, &ya);
        int s = s9, d = s8;
        uint16_t xv = c54_dread(c, xa);           /* read before the store (same location) */
        uint16_t stv = st_hi(c, s, ASM_);
        int kind = (hi & 0xFC);
        switch (kind) {
        case 0xC0: { x = ACC(c, !d); y = ext16(c, xv) << 16; set_c(c, carry_add(x, y, 0)); acc_res(c, d, x + y); break; }
        case 0xC4: { x = ext16(c, xv) << 16; y = ACC(c, !d); set_c(c, carry_sub(x, y, 0)); acc_res(c, d, x - y); break; }
        case 0xC8: acc_set(c, d, ext16(c, xv) << 16); break;
        case 0xCC: acc_res(c, d, mul(c, sx16(c->t), sx16(xv))); break;
        case 0xD0: mac_res(c, d, ACC(c, d) + mul(c, sx16(c->t), sx16(xv)), 0); break;
        case 0xD4: mac_res(c, d, ACC(c, d) + mul(c, sx16(c->t), sx16(xv)), 1); break;
        case 0xD8: mac_res(c, d, ACC(c, d) - mul(c, sx16(c->t), sx16(xv)), 0); break;
        case 0xDC: mac_res(c, d, ACC(c, d) - mul(c, sx16(c->t), sx16(xv)), 1); break;
        case 0xE4: c->t = xv; break;
        }
        c54_dwrite(c, ya, stv);
        return 1; }
    case 0xE0: {   /* FIRS Xmem,Ymem,pmad */
        ea_xy(c, op, &xa, &ya);
        w = fetch(c);
        uint32_t pa = par_get(c, ((uint32_t)c->xpc << 16) | w);
        uint16_t pv = c54_pread(c, pa);
        int64_t rb = ACC(c, 1) + mul(c, ahi17(c), sx16(pv));
        x = ext16(c, c54_dread(c, xa)); y = ext16(c, c54_dread(c, ya));
        acc_res(c, 1, rb);
        acc_res(c, 0, (x + y) << 16);
        par_next(c);
        return 3; }
    case 0xE1: {   /* LMS Xmem,Ymem */
        ea_xy(c, op, &xa, &ya);
        uint16_t xv = c54_dread(c, xa), yv = c54_dread(c, ya);
        int64_t ra = ACC(c, 0) + (ext16(c, xv) << 16) + 0x8000;
        int64_t rb = ACC(c, 1) + mul(c, sx16(xv), sx16(yv));
        set_c(c, carry_add(ACC(c, 0), (ext16(c, xv) << 16) + 0x8000, 0));
        acc_res(c, 0, ra & ~0xFFFFLL);
        acc_res(c, 1, rb);
        return 1; }
    case 0xE2: case 0xE3: {   /* SQDST / ABDST */
        ea_xy(c, op, &xa, &ya);
        int64_t ah = ahi17(c);
        int64_t pb = (hi == 0xE2) ? mul(c, ah, ah) : (ah < 0 ? -ah : ah) << 0;
        if (hi == 0xE3 && FRCT) pb <<= 0;   /* ABDST adds |A(32-16)| (no multiplier) */
        x = ext16(c, c54_dread(c, xa)); y = ext16(c, c54_dread(c, ya));
        acc_res(c, 1, ACC(c, 1) + pb);
        set_c(c, carry_sub(x << 16, y << 16, 0));
        acc_res(c, 0, (x - y) << 16);
        return 1; }
    case 0xE5: ea_xy(c, op, &xa, &ya); c54_dwrite(c, ya, c54_dread(c, xa)); return 1;   /* MVDD */
    case 0xE7: {   /* MVMM */
        int mx = (op >> 4) & 15, my = op & 15;
        uint16_t vv = (mx == 8) ? c->sp : c->ar[mx & 7];
        if (my == 8) c->sp = vv; else c->ar[my & 7] = vv;
        return 1; }
    case 0xE8: case 0xE9: acc_set(c, s8, op & 0xFF); return 1;
    case 0xEA: case 0xEB: c->st0 = (c->st0 & ~0x1FF) | (op & 0x1FF); return 1;
    case 0xEC: c->rc = op & 0xFF; c->rpt_pending = 1; return 1;
    case 0xED:
        if ((op & 0xE0) == 0) { c->st1 = (c->st1 & ~0x1F) | (op & 0x1F); return 1; }
        bad(c, op); return 1;
    case 0xEE: c->sp = (uint16_t)(c->sp + (int8_t)(op & 0xFF)); return 1;
    case 0xF0: case 0xF1: case 0xF2: case 0xF3: return exec_f0(c, op);
    case 0xF4: case 0xF5: case 0xF6: case 0xF7: return exec_f4(c, op);
    case 0xF8: case 0xF9: case 0xFA: case 0xFB: {
        int dly = (hi & 2) != 0, call = hi & 1;
        w = fetch(c);
        if (op & 0x80) {   /* FB/FCALL extpmad: page in bits 6-0 */
            int page = op & 0x7F;
            if (call) { push(c, (uint16_t)(c->fpc + (dly ? 2 : 0))); push(c, c->xpc); }
            xfer(c, w, page, dly);
            return 4;
        }
        if (cond(c, op & 0xFF)) {
            if (call) push(c, (uint16_t)(c->fpc + (dly ? 2 : 0)));
            xfer(c, w, -1, dly);
            return 5;
        }
        return 3; }
    case 0xFC: case 0xFE: {   /* RC[D] / RET[D] */
        int dly = hi == 0xFE;
        if ((op & 0xFF) == 0 || cond(c, op & 0xFF)) { uint16_t r = pop(c); xfer(c, r, -1, dly); return 5; }
        return 3; }
    case 0xFD: case 0xFF: {   /* XC n,cond */
        int n = (hi == 0xFF) ? 2 : 1;
        if (!cond(c, op & 0xFF)) c->xc_skip = n;
        c->nointr = 1;
        return 1; }
    default:
        bad(c, op); return 1;
    }
}

static int exec_f0(C54 *c, uint16_t op)
{
    int lo = op & 0xFF, s = (op >> 9) & 1, d = (op >> 8) & 1;
    uint16_t w;
    int64_t v;
    if (lo < 0x60) {
        w = fetch(c);
        int sh = lo & 15;
        switch (lo >> 4) {
        case 0: op_add(c, s, ext16(c, w) << sh, d); return 2;
        case 1: op_sub(c, s, ext16(c, w) << sh, d); return 2;
        case 2: acc_set(c, d, ext16(c, w) << sh); return 2;
        case 3: acc_set(c, d, ACC(c, s) & ((int64_t)w << sh)); return 2;
        case 4: acc_set(c, d, ACC(c, s) | ((int64_t)w << sh)); return 2;
        case 5: acc_set(c, d, ACC(c, s) ^ ((int64_t)w << sh)); return 2;
        }
    }
    if (lo < 0x68) {
        w = fetch(c);
        switch (lo & 7) {
        case 0: { int64_t x = ACC(c, s), y = ext16(c, w) << 16; if (carry_add(x, y, 0)) set_c(c, 1); acc_res(c, d, x + y); return 2; }
        case 1: { int64_t x = ACC(c, s), y = ext16(c, w) << 16; if (!carry_sub(x, y, 0)) set_c(c, 0); acc_res(c, d, x - y); return 2; }
        case 2: acc_set(c, d, ext16(c, w) << 16); return 2;
        case 3: acc_set(c, d, ACC(c, s) & ((int64_t)w << 16)); return 2;
        case 4: acc_set(c, d, ACC(c, s) | ((int64_t)w << 16)); return 2;
        case 5: acc_set(c, d, ACC(c, s) ^ ((int64_t)w << 16)); return 2;
        case 6: acc_res(c, d, mul(c, sx16(c->t), sx16(w))); return 2;
        case 7: acc_res(c, d, ACC(c, s) + mul(c, sx16(c->t), sx16(w))); return 2;
        }
    }
    if (lo >= 0x70 && lo <= 0x74) {
        w = fetch(c);
        int dly = (op >> 9) & 1;
        switch (lo) {
        case 0x70: c->rc = w; c->rpt_pending = 1; return 2;
        case 0x71: acc_set(c, d, 0); c->rc = w; c->rpt_pending = 1; return 2;
        case 0x72:   /* RPTB[D] */
            c->rsa = (uint16_t)(c->fpc + (dly ? 2 : 0));
            c->rea = w;
            SETB(c->st1, ST1_BRAF, 1);
            return 4;
        case 0x73: xfer(c, w, -1, dly); return 4;
        case 0x74: push(c, (uint16_t)(c->fpc + (dly ? 2 : 0))); xfer(c, w, -1, dly); return 4;
        }
    }
    if (lo >= 0x80) {
        int sh = shift5(op);
        v = ACC(c, s);
        /* logical shift on the 40-bit value */
        int64_t sv = sh >= 0 ? sx40((uint64_t)v << sh) : (int64_t)(((uint64_t)v & M40) >> -sh);
        switch ((lo >> 5) & 3) {
        case 0: acc_set(c, d, ACC(c, d) & sv); return 1;
        case 1: acc_set(c, d, ACC(c, d) | sv); return 1;
        case 2: acc_set(c, d, ACC(c, d) ^ sv); return 1;
        case 3: {   /* SFTL src,SHIFT,dst on bits 31-0 */
            uint64_t u = (uint64_t)v & M32;
            if (sh < 0) { set_c(c, (u >> (-sh - 1)) & 1); u >>= -sh; }
            else if (sh == 0) set_c(c, 0);
            else { set_c(c, (u >> (32 - sh)) & 1); u = (u << sh) & M32; }
            c->acc[d] = u;
            return 1; }
        }
    }
    bad(c, op);
    return 1;
}

static int exec_f4(C54 *c, uint16_t op)
{
    int lo = op & 0xFF, s = (op >> 9) & 1, d = (op >> 8) & 1;
    int64_t x, r;
    if (lo < 0x80) {
        int sh = shift5(op);
        switch (lo >> 5) {
        case 0: op_add(c, d, shift40(c, ACC(c, s), sh), d); return 1;   /* (dst) + (src)<<SHIFT */
        case 1: op_sub(c, d, shift40(c, ACC(c, s), sh), d); return 1;
        case 2: acc_res(c, d, shift40(c, ACC(c, s), sh)); return 1;
        case 3: {   /* SFTA */
            x = ACC(c, s);
            if (sh < 0) { set_c(c, (x >> (-sh - 1)) & 1); }
            else if (sh > 0) { set_c(c, (x >> (32 - sh)) & 1); }   /* carry at the 32-bit boundary (SPRU172C SFTA ex. 2) */
            acc_res(c, d, shift40(c, x, sh));
            return 1; }
        }
    }
    switch (lo) {
    case 0x80: op_add(c, d, shift40(c, ACC(c, s), ASM_), d); return 1;
    case 0x81: op_sub(c, d, shift40(c, ACC(c, s), ASM_), d); return 1;
    case 0x82: acc_res(c, d, shift40(c, ACC(c, s), ASM_)); return 1;
    case 0x83: if (s) break;   /* SAT src (src in bit 8) */
        x = ACC(c, d);
        if (x > 0x7FFFFFFFLL) { acc_set(c, d, 0x7FFFFFFF); set_ov(c, d); }
        else if (x < -0x80000000LL) { acc_set(c, d, -0x80000000LL); set_ov(c, d); }
        else SETB(c->st0, d ? ST0_OVB : ST0_OVA, 0);
        return 1;
    case 0x84: x = ACC(c, s); r = -x; set_c(c, x == 0); acc_res(c, d, r); return 1;   /* NEG */
    case 0x85: x = ACC(c, s); r = x < 0 ? -x : x; acc_res(c, d, r); set_c(c, (c->acc[d]) == 0); return 1;  /* ABS */
    case 0x86: case 0x87: if (s) break; {
        int64_t A = ACC(c, 0), B = ACC(c, 1);
        int pickA = (lo == 0x86) ? (A >= B) : (A <= B);
        acc_set(c, d, pickA ? A : B); set_c(c, !pickA); return 1; }
    case 0x88: case 0x89: case 0x8A: case 0x8B: {   /* MACA[R]/MASA[R] T,src,dst */
        int64_t p = mul(c, sx16(c->t), ahi17(c));
        int rnd = lo & 1, sub = (lo >> 1) & 1;
        mac_res(c, d, sub ? ACC(c, s) - p : ACC(c, s) + p, rnd);
        return 1; }
    case 0x8C: if (s) break; acc_res(c, d, mul(c, sx16(c->t), ahi17(c))); return 1;   /* MPYA dst */
    case 0x8D: if (s) break; { int64_t ah = ahi17(c); acc_res(c, d, mul(c, ah, ah)); return 1; }   /* SQUR A,dst */
    case 0x8E: if (s) break; {   /* EXP src (bit 8) */
        uint64_t u = c->acc[d] & M40;
        if (u == 0) { c->t = 0; return 1; }
        int sign = (u >> 39) & 1, n = 0;
        for (int b = 38; b >= 0 && (int)((u >> b) & 1) == sign; b--) n++;
        c->t = (uint16_t)(n - 8);
        return 1; }
    case 0x8F: acc_res(c, d, shift40(c, ACC(c, s), tshift(c))); return 1;   /* NORM */
    case 0x90: if (s) break; {   /* ROR */
        uint64_t u = c->acc[d] & M32; int cin = CARRY;
        set_c(c, u & 1); c->acc[d] = (u >> 1) | ((uint64_t)cin << 31); return 1; }
    case 0x91: case 0x92: if (s) break; {   /* ROL / ROLTC */
        uint64_t u = c->acc[d] & M32; int cin = (lo == 0x91) ? CARRY : TC;
        set_c(c, (u >> 31) & 1); c->acc[d] = ((u << 1) & M32) | (uint64_t)cin; return 1; }
    case 0x93: acc_set(c, d, ~ACC(c, s)); return 1;   /* CMPL */
    case 0x94: if (s) break; {   /* SFTC */
        uint64_t u = c->acc[d] & M40;
        if (u == 0) { set_tc(c, 1); return 1; }
        if ((((u >> 31) ^ (u >> 30)) & 1) == 0) { set_tc(c, 0); c->acc[d] = (u << 1) & M40; }
        else set_tc(c, 1);
        return 1; }
    case 0x95: if (s || d) break; return 1;   /* NOP */
    case 0x9B: if (d) break;   /* RETF[D] (no RTN modelled: use stack) */
        { uint16_t rr = pop(c); SETB(c->st1, ST1_INTM, 0); xfer(c, rr, -1, s); return 3; }
    case 0x9F: acc_res(c, d, ACC(c, s) + 0x8000); return 1;   /* RND */
    case 0xA0: case 0xA1: case 0xA2: case 0xA3: case 0xA4: case 0xA5: case 0xA6: case 0xA7:
        if (s || d) break;
        c->st0 = (c->st0 & 0x1FFF) | ((lo & 7) << 13); return 1;   /* LD #k3,ARP */
    case 0xA8: case 0xA9: case 0xAA: case 0xAB: case 0xAC: case 0xAD: case 0xAE: case 0xAF: {
        int cc = (op >> 8) & 3; uint16_t arx = c->ar[lo & 7], ar0 = c->ar[0];
        int res = cc == 0 ? arx == ar0 : cc == 1 ? arx < ar0 : cc == 2 ? arx > ar0 : arx != ar0;
        set_tc(c, res); return 1; }
    default: break;
    }
    if (lo >= 0xB0 && lo <= 0xBF) {   /* RSBX/SSBX: bit 9 = N, bit 8 = set */
        int bitn = lo & 15;
        if (s) SETB(c->st1, bitn, d); else SETB(c->st0, bitn, d);
        return 1;
    }
    if (lo >= 0xC0 && lo <= 0xDF) {
        int k = lo & 31;
        if ((op >> 8) == 0xF4) {   /* TRAP */
            push(c, c->fpc);
            c->pc = (uint16_t)((IPTR << 7) | (k << 2)); c->branched = 1;
            return 3;
        }
        if ((op >> 8) == 0xF7) {   /* INTR */
            push(c, c->fpc);
            c->pc = (uint16_t)((IPTR << 7) | (k << 2)); c->branched = 1;
            SETB(c->st1, ST1_INTM, 1);
            if (k >= 16 && k < 30) c->ifr &= ~(1u << (k - 16));
            return 3;
        }
    }
    if (op == 0xF7E0) { c54_reset(c); return 3; }
    if (lo == 0xE1) { c->idle = ((op >> 8) & 3) + 1; return 4; }   /* IDLE 1/2/3 */
    if (lo >= 0xE2 && lo <= 0xEB) {
        int dly = s;   /* F6/F7 = delayed */
        uint16_t sv = (uint16_t)(c->acc[d] & 0xFFFF);
        int spage = (int)((c->acc[d] >> 16) & 0x7F);
        switch (lo) {
        case 0xE2: xfer(c, sv, -1, dly); return 4;                                  /* BACC */
        case 0xE3: push(c, (uint16_t)(c->fpc + (dly ? 2 : 0))); xfer(c, sv, -1, dly); return 4;   /* CALA */
        case 0xE4: if (d) break; { uint16_t px = pop(c); uint16_t pp = pop(c); xfer(c, pp, px & 0x7F, dly); return 6; }   /* FRET */
        case 0xE5: if (d) break; { uint16_t px = pop(c); uint16_t pp = pop(c); SETB(c->st1, ST1_INTM, 0); xfer(c, pp, px & 0x7F, dly); return 6; }  /* FRETE */
        case 0xE6: xfer(c, sv, spage, dly); return 4;                                 /* FBACC */
        case 0xE7: push(c, (uint16_t)(c->fpc + (dly ? 2 : 0))); push(c, c->xpc); xfer(c, sv, spage, dly); return 4;   /* FCALA */
        case 0xEB: if (d) break; { uint16_t pp = pop(c); SETB(c->st1, ST1_INTM, 0); xfer(c, pp, -1, dly); return 5; }   /* RETE */
        }
    }
    if (op == 0xF4F0) return 1;   /* ESTOP */
    bad(c, op);
    return 1;
}

/* ---------------------------------------------------------------------------------------------
 * sequencing
 * --------------------------------------------------------------------------------------------- */
void c54_init(C54 *c, uint16_t *ram)
{
    memset(c, 0, sizeof(*c));
    c->ram = ram;
    c->bio = 1;
    c54_reset(c);
}

void c54_reset(C54 *c)
{
    c->st0 = (1u << ST0_TC) | (1u << ST0_C);
    c->st1 = (1u << ST1_XF) | (1u << ST1_INTM) | (1u << ST1_SXM);
    c->pmst = (uint16_t)((c->pmst & 0x0040) | 0xFF80);   /* IPTR = 1FF, keep MP/MC */
    c->ifr = 0; c->imr = 0; c->xpc = 0;
    c->pc = 0xFF80;
    c->rpt_active = c->rpt_pending = 0;
    c->delay_pending = 0; c->xc_skip = 0; c->idle = 0; c->halted = 0;
}

void c54_irq(C54 *c, int bit) { c->ifr |= (uint16_t)(1u << bit); }

static int take_interrupt(C54 *c)
{
    uint16_t pend = c->ifr & c->imr & 0x3FFF;
    if (!pend) return 0;
    if (INTM) {
        if (c->idle == 1 || c->idle == 2 || c->idle == 3) c->idle = 0;   /* wake, continue without servicing */
        return 0;
    }
    int b = 0;
    while (!(pend & (1u << b))) b++;
    c->ifr &= (uint16_t)~(1u << b);
    c->idle = 0;
    c->irq_taken[b]++;
    if (c->cycles >= c->trace_lo && c->cycles < c->trace_hi) fprintf(stderr, "[c54 irq] %d at %x:%04x\n", b, c->xpc, c->pc);
    push(c, c->pc);
    SETB(c->st1, ST1_INTM, 1);
    c->pc = (uint16_t)((IPTR << 7) | ((16 + b) << 2));
    return 1;
}

static void ring(C54 *c, uint32_t pc)
{
    c->pc_ring[c->pc_ring_i++ & 63] = pc;
}

int c54_step(C54 *c)
{
    if (c->halted) return 1;
    if (c->idle) {
        if (take_interrupt(c)) return 4;
        if (c->idle) { c->cycles += 1; return 1; }
    }
    /* interrupts between instructions (not in delay slots / repeat / XC slots) */
    if (!c->delay_pending && !c->rpt_active && !c->nointr && !c->xc_skip) {
        if (take_interrupt(c)) { c->cycles += 4; return 4; }
    }
    c->nointr = 0;

    int cyc = 1;
    uint16_t pc0 = c->pc;
    uint32_t full = ((uint32_t)c->xpc << 16) | pc0;
    c->fpc = pc0;
    uint16_t op = fetch(c);

    if (c->xc_skip) {   /* false XC: skip the next n words as NOPs */
        int len = c54_insn_len(op);
        c->xc_skip -= len;
        if (c->xc_skip < 0) c->xc_skip = 0;
        c->pc = (uint16_t)(pc0 + len);
        c->cycles += len;
        c->branched = 0;
        if (c->delay_pending) {   /* a skipped word in a delay slot still counts as a slot */
            c->delay_words -= len;
            if (c->delay_words <= 0) {
                c->delay_pending = 0;
                c->pc = c->delay_pc; c->xpc = c->delay_xpc;
                c->branched = 1;
            }
        }
        goto after;
    }

    for (int i = 0; i < c->nbrk; i++) if (c->brk[i] == full) { c->hit_brk = 1; return 0; }

    c->last_pc = full; c->last_op = op;
    ring(c, full);
    if (c->prof) c->prof[full & 0x7FFFF]++;
    c->branched = 0;
    if (c->trace || (c->cycles >= c->trace_lo && c->cycles < c->trace_hi)) fprintf(stderr, "[c54 t] %x:%04x %04x A=%010llx B=%010llx T=%04x ST0=%04x ST1=%04x AR=%04x %04x %04x %04x %04x %04x %04x %04x SP=%04x\n", c->xpc, pc0, op,
                          (unsigned long long)c->acc[0], (unsigned long long)c->acc[1], c->t, c->st0, c->st1,
                          c->ar[0], c->ar[1], c->ar[2], c->ar[3], c->ar[4], c->ar[5], c->ar[6], c->ar[7], c->sp);


    if (c->rpt_active) {
        cyc = exec(c, op);
        c->rpt_first = 0;
        c->insns++;
        if (c->rc != 0) { c->rc--; c->pc = pc0; c->cycles += 1; return 1; }
        c->rpt_active = 0; c->pc = c->fpc; c->branched = 0;
        c->cycles += cyc;
        goto after;   /* last iteration: block-repeat check */
    }

    int was_delay = c->delay_pending;
    cyc = exec(c, op);
    c->insns++;
    if (!c->branched) c->pc = c->fpc;

    if (c->rpt_pending) {
        c->rpt_pending = 0; c->rpt_active = 1; c->rpt_first = 1;
    }

    /* delay slots */
    if (was_delay) {
        c->delay_words -= (int)(uint16_t)(c->fpc - pc0);
        if (c->delay_words <= 0) {
            c->delay_pending = 0;
            c->pc = c->delay_pc; c->xpc = c->delay_xpc;
            c->branched = 1;
        }
    }
    c->cycles += cyc;

after:
    /* block repeat: instruction ending at REA loops back */
    if ((c->st1 & (1u << ST1_BRAF)) && !c->branched && !c->delay_pending) {
        uint16_t end = (uint16_t)(c->pc - 1);
        if (end == c->rea && c->pc != pc0) {
            if (c->brc != 0) { c->brc--; c->pc = c->rsa; }
            else SETB(c->st1, ST1_BRAF, 0);
        }
    }
    return cyc;
}

int c54_run(C54 *c, int64_t budget)
{
    int64_t start = (int64_t)c->cycles;
    c->hit_brk = 0;
    while ((int64_t)c->cycles - start < budget) {
        if (c->halted) break;
        if (c->idle && !(c->ifr & c->imr & 0x3FFF)) {   /* sleep: burn the rest of the budget */
            c->cycles = (uint64_t)(start + budget);
            break;
        }
        int n = c54_step(c);
        if (c->hit_brk) break;
        (void)n;
    }
    return (int)((int64_t)c->cycles - start);
}

/* ---------------------------------------------------------------------------------------------
 * mnemonic class (for traces and the decode cross-check against GNU objdump)
 * --------------------------------------------------------------------------------------------- */
const char *c54_mnem(uint16_t op, uint16_t op2)
{
    int hi = op >> 8, lo = op & 0xFF;
    static const char *t00[0x48] = {
        "add","add","adds","adds","add","add","addc","addc","sub","sub","subs","subs","sub","sub","subb","subb",
        "ld","ld","ldu","ldu","ld","ld","ldr","ldr","and","and","or","or","xor","xor","subc","subc",
        "mpy","mpy","mpyr","mpyr","mpyu","mpyu","squr","squr","mac","mac","macr","macr","mas","mas","masr","masr",
        "ld","mpya","ld","masa","bitt","maca","poly","macar","squra","squra","squrs","squrs","add","add","add","add",
        "sub","sub","sub","sub","ld","ld","ld","rpt" };
    if (hi < 0x48) return t00[hi];
    static const char *t48[0x38] = {
        "ldm","ldm","pshm","pshd","ltd","delay","dst","dst","dadd","dadd","dadd","dadd","dsub","dsub","dld","dld",
        "drsub","drsub","dadst","dadst","dsubt","dsubt","dsadt","dsadt","cmpm","bitf","mpy","mpy","mac","mac","mac","mac",
        "andm","orm","xorm","addm","banz","mar","banzd","?6f","mvkd","mvdk","mvdm","mvmd","portr","portw","st","stm",
        "macp","macp","macd","macd","mvpd","mvdp","reada","writa" };
    if (hi < 0x80) {
        if (hi == 0x6F) {
            static const char *x6f[8] = { "add","sub","ld","sth","stl","?","?","?" };
            return x6f[(op2 >> 5) & 7];
        }
        return t48[hi - 0x48];
    }
    static const char *t80[0x20] = {
        "stl","stl","sth","sth","stl","stl","sth","sth","stlm","stlm","popm","popd","st","st","cmps","cmps",
        "add","add","sub","sub","ld","ld","bit","???","stl","stl","sth","sth","strcd","srccd","saccd","saccd" };
    if (hi < 0xA0) return t80[hi - 0x80];
    if (hi < 0xA8) { static const char *ta0[8] = {"add","add","sub","sub","mpy","mpy","macsu","macsu"}; return ta0[hi-0xA0]; }
    if (hi < 0xB0) return "ld";
    if (hi < 0xC0) { static const char *tb0[4] = {"mac","macr","mas","masr"}; return tb0[(hi >> 2) & 3]; }
    if (hi < 0xE0) return "st";
    switch (hi) {
    case 0xE0: return "firs"; case 0xE1: return "lms"; case 0xE2: return "sqdst"; case 0xE3: return "abdst";
    case 0xE4: case 0xE6: return "st"; case 0xE5: return "mvdd"; case 0xE7: return "mvmm";
    case 0xE8: case 0xE9: return "ld"; case 0xEA: case 0xEB: return "ld"; case 0xEC: return "rpt";
    case 0xED: return (lo & 0xE0) ? "???" : "ld"; case 0xEE: return "frame"; case 0xEF: return "???";
    }
    if (hi <= 0xF3) {
        if (lo < 0x60) { static const char *f[6] = {"add","sub","ld","and","or","xor"}; if ((lo>>4)==2 && (hi&2)) return "???"; return f[lo >> 4]; }
        if (lo < 0x68) { static const char *f[8] = {"add","sub","ld","and","or","xor","mpy","mac"}; return f[lo & 7]; }
        if (lo == 0x70) return (hi == 0xF0) ? "rpt" : "???";
        if (lo == 0x71) return (hi & 2) ? "???" : "rptz";
        if (lo == 0x72) return (hi == 0xF0) ? "rptb" : (hi == 0xF2) ? "rptbd" : "???";
        if (lo == 0x73) return (hi == 0xF0) ? "b" : (hi == 0xF2) ? "bd" : "???";
        if (lo == 0x74) return (hi == 0xF0) ? "call" : (hi == 0xF2) ? "calld" : "???";
        if (lo >= 0x80) { static const char *f[4] = {"and","or","xor","sftl"}; return f[(lo >> 5) & 3]; }
        return "???";
    }
    if (hi <= 0xF7) {
        if (lo < 0x80) { static const char *f[4] = {"add","sub","ld","sfta"}; return f[lo >> 5]; }
        switch (lo) {
        case 0x80: return "add"; case 0x81: return "sub"; case 0x82: return "ld"; case 0x83: return "sat";
        case 0x84: return "neg"; case 0x85: return "abs"; case 0x86: return "max"; case 0x87: return "min";
        case 0x88: return "maca"; case 0x89: return "macar"; case 0x8A: return "masa"; case 0x8B: return "masar";
        case 0x8C: return "mpya"; case 0x8D: return "squr"; case 0x8E: return "exp"; case 0x8F: return "norm";
        case 0x90: return "ror"; case 0x91: return "rol"; case 0x92: return "roltc"; case 0x93: return "cmpl";
        case 0x94: return "sftc"; case 0x95: return "nop"; case 0x9B: return (hi & 2) ? "retfd" : "retf"; case 0x9F: return "rnd";
        }
        if (lo >= 0xA0 && lo <= 0xA7) return "ld";
        if (lo >= 0xA8 && lo <= 0xAF) return "cmpr";
        if (lo >= 0xB0 && lo <= 0xBF) return (hi & 1) ? "ssbx" : "rsbx";
        if (lo >= 0xC0 && lo <= 0xDF) return hi == 0xF4 ? "trap" : hi == 0xF7 ? "intr" : "???";
        if (op == 0xF7E0) return "reset";
        if (lo == 0xE1) return "idle";
        if (op == 0xF4F0) return "estop";
        { int dly = hi & 2;
        switch (lo) {
        case 0xE2: return dly ? "baccd" : "bacc"; case 0xE3: return dly ? "calad" : "cala";
        case 0xE4: return dly ? "fretd" : "fret"; case 0xE5: return dly ? "freted" : "frete";
        case 0xE6: return dly ? "fbaccd" : "fbacc"; case 0xE7: return dly ? "fcalad" : "fcala";
        case 0xEB: return dly ? "reted" : "rete"; } }
        return "???";
    }
    switch (hi) {
    case 0xF8: return (lo & 0x80) ? "fb" : "bc";
    case 0xF9: return (lo & 0x80) ? "fcall" : "cc";
    case 0xFA: return (lo & 0x80) ? "fbd" : "bcd";
    case 0xFB: return (lo & 0x80) ? "fcalld" : "ccd";
    case 0xFC: return lo ? "rc" : "ret";
    case 0xFE: return lo ? "rcd" : "retd";
    default: return "xc";
    }
}

/* debugging helpers for ctypes */
void c54_set_trace(C54 *c, uint64_t lo, uint64_t hi) { c->trace_lo = lo; c->trace_hi = hi; }
void c54_set_brk(C54 *c, int i, uint32_t a) { c->brk[i] = a; if (i + 1 > c->nbrk) c->nbrk = i + 1; }
void c54_set_ww(C54 *c, uint16_t lo, uint16_t hi, int on) { c->ww_lo = lo; c->ww_hi = hi; c->ww_on = on; }
uint32_t c54_ring(C54 *c, int k) { return c->pc_ring[(c->pc_ring_i - 1 - k) & 63]; }
uint32_t c54_irq_count(C54 *c, int b) { return c->irq_taken[b]; }

void c54_set_prof(C54 *c, uint32_t *buf) { c->prof = buf; }

void c54_rec(C54 *c, int on) { c->rec_on = on; c->nrec = 0; }
int c54_rec_n(C54 *c) { return c->nrec; }
void c54_rec_get(C54 *c, int i, uint32_t *out) { out[0] = c->rec[i].kind; out[1] = c->rec[i].addr; out[2] = c->rec[i].val; }
