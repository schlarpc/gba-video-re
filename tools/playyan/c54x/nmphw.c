/* nmphw.c - board model around the C54x core for the Nintendo MP3 Player cartridge (TMS320VC5416).
 * Peripherals are modelled from TI SPRS095P (VC5416 data manual), SPRU302 (enhanced peripherals:
 * McBSP, DMA, HPI) and SPRA602F (bootloader), and from what the firmware does with them
 * (docs/playyan/c54x-hardware.md has the evidence).  Board wiring found from the firmware:
 *   McBSP0  audio DAC (headphones): 2 x 16-bit words per 32-bit frame, SRG from CPU clock,
 *           fed by DMA channel 3 synchronised to XEVT0.
 *   McBSP1  GPIO pins (PCR RIOEN): DR1 = a debounced input (card/headphone detect).
 *   McBSP2  SPI master (CLKSTP) to the SD card, 8-bit words, polled RRDY.
 *   Timer0  DSP/BIOS CLK, 1 ms.
 *   HPI-8   GBA host: shared RAM, DSPINT -> HINT interrupt (IFR bit 9), HPIC.HINT -> GBA IRQ.
 */
#include "c54x.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>

/* ---------------------------------------------------------------- SD card (SPI mode) */
typedef struct SD {
    FILE *f; uint64_t size; int present;
    uint8_t cmd[6]; int cmdn;
    uint8_t out[1100]; int outh, outn;
    int idle, app; uint32_t blocklen;
    int reading; uint32_t rdaddr;
    int writing; uint32_t wraddr; uint8_t wbuf[520]; int wn, wstate;
    uint64_t reads, writes, cmds;
    uint32_t last_cmd, last_arg;
} SD;

typedef struct McBSP {
    uint16_t spsa;
    uint16_t sub[0x20];
    uint16_t drr1, drr2, dxr1, dxr2;
    int rrdy;
    uint64_t xcount, rcount;
    uint16_t pin_in;         /* input pin levels in PCR bit positions (0 CLKR,1 CLKX,2 FSR,3 FSX,4 DR,6 CLKS) */
    uint16_t pin_out_last;
    double slot_acc;         /* audio: cycles accumulated toward the next word slot */
    /* SPI (clock-stop) transmitter/receiver */
    int busy; int64_t left;  /* shifting a word: cycles left */
    uint16_t xsr;            /* word being shifted */
    int dxr_full;
} McBSP;

typedef struct DmaCh {
    int active;
    int elem, frame;
    uint64_t moved;
    uint32_t blocks;
} DmaCh;

#define PCM_RING (1 << 20)

typedef struct Nmp {
    C54 cpu;
    uint16_t *ram;
    int own_ram;
    int in_reset, boot_wait;
    uint16_t hpic; int hint;
    void (*host_irq)(void *ctx, int level);
    void *host_ctx;
    uint64_t hint_count, dspint_count;
    uint16_t tim, prd, tcr; uint32_t psc;
    uint16_t swwsr, bscr, swcr, clkmd, gpiocr, gpiosr;
    McBSP sp[3];
    uint16_t dmprec, dmsa, dmreg[0x40];
    DmaCh dma[6];
    SD sd;
    int16_t *pcm; uint32_t pcm_w, pcm_r; uint64_t pcm_total; uint32_t pcm_overrun;
    double cpu_hz, clkin_hz;
    double speed; double speed_acc;   /* >1: CPU runs faster than real hardware (diagnostic) */
    uint32_t mmr_r[0x60], mmr_w[0x60], mmr_pc[0x60]; uint16_t mmr_last[0x60];
    uint32_t sub_w[3][0x20]; uint16_t sub_last[3][0x20];
    uint32_t io_r[256], io_w[256]; uint16_t io_last[256]; uint32_t io_pc[256];
    FILE *log; int log_mmr; int pin_log; int sd_log; int spi_log;
    uint64_t cycles_total;
} Nmp;

#define C (&n->cpu)
static uint32_t curpc(Nmp *n) { return n->cpu.last_pc; }

static void logf_(Nmp *n, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void logf_(Nmp *n, const char *fmt, ...)
{
    if (!n->log) return;
    va_list ap;
    fprintf(n->log, "[%10llu %x:%04x] ", (unsigned long long)n->cpu.cycles, curpc(n) >> 16, curpc(n) & 0xFFFF);
    va_start(ap, fmt);
    vfprintf(n->log, fmt, ap);
    va_end(ap);
    fputc('\n', n->log);
}

/* ---------------------------------------------------------------- SD */
static void sd_push(SD *s, const uint8_t *b, int k)
{
    for (int i = 0; i < k && s->outn < (int)sizeof(s->out); i++) s->out[s->outn++] = b[i];
}
static uint16_t crc16(const uint8_t *d, int k)
{
    uint16_t c = 0;
    for (int i = 0; i < k; i++) {
        c ^= (uint16_t)d[i] << 8;
        for (int j = 0; j < 8; j++) c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
    }
    return c;
}
static void sd_send_block(Nmp *n, uint32_t addr)
{
    SD *s = &n->sd;
    uint8_t blk[516];
    memset(blk, 0xFF, sizeof(blk));
    blk[1] = 0xFE;
    memset(blk + 2, 0, 512);
    if (s->f && addr + 512 <= s->size) {
        fseek(s->f, (long)addr, SEEK_SET);
        if (fread(blk + 2, 1, 512, s->f) != 512) { }
    }
    uint16_t c = crc16(blk + 2, 512);
    blk[514] = (uint8_t)(c >> 8); blk[515] = (uint8_t)c;
    sd_push(s, blk, 516);
    s->reads++;
    if (n->sd_log) logf_(n, "SD read block %08x", addr);
}
static void sd_command(Nmp *n)
{
    SD *s = &n->sd;
    int cmd = s->cmd[0] & 0x3F;
    uint32_t arg = (uint32_t)s->cmd[1] << 24 | s->cmd[2] << 16 | s->cmd[3] << 8 | s->cmd[4];
    int app = s->app;
    s->app = 0; s->cmds++;
    s->last_cmd = (uint32_t)(app ? 100 + cmd : cmd); s->last_arg = arg;
    if (n->sd_log) logf_(n, "SD %sCMD%d arg %08x crc %02x", app ? "A" : "", cmd, arg, s->cmd[5]);
    uint8_t r1 = s->idle ? 1 : 0, pre = 0xFF;
    sd_push(s, &pre, 1);   /* Ncr = 1 */
    if (!s->present || !s->f) { uint8_t ff = 0xFF; sd_push(s, &ff, 1); return; }
    if (app) {
        switch (cmd) {
        case 41: s->idle = 0; r1 = 0; sd_push(s, &r1, 1); return;
        case 51: { sd_push(s, &r1, 1); uint8_t d[10] = {0xFF, 0xFE, 0x00, 0x25, 0, 0, 0, 0, 0, 0}; uint16_t c = crc16(d + 2, 8); sd_push(s, d, 10); uint8_t cc[2] = {(uint8_t)(c >> 8), (uint8_t)c}; sd_push(s, cc, 2); return; }
        case 13: { uint8_t r2[2] = {r1, 0}; sd_push(s, r2, 2); uint8_t blk[65]; memset(blk, 0, 65); blk[0] = 0xFE; sd_push(s, blk, 65); uint16_t c = crc16(blk + 1, 64); uint8_t cc[2] = {(uint8_t)(c >> 8), (uint8_t)c}; sd_push(s, cc, 2); return; }
        case 23: sd_push(s, &r1, 1); return;
        default: break;
        }
    }
    switch (cmd) {
    case 0: s->idle = 1; s->reading = 0; s->writing = 0; r1 = 1; sd_push(s, &r1, 1); return;
    case 1: s->idle = 0; r1 = 0; sd_push(s, &r1, 1); return;
    case 8: { uint8_t r = 0x05; sd_push(s, &r, 1); return; }   /* SD v1: illegal command */
    case 55: s->app = 1; sd_push(s, &r1, 1); return;
    case 58: { uint8_t r3[5] = {r1, 0x80, 0xFF, 0x80, 0x00}; sd_push(s, r3, 5); return; }
    case 59: sd_push(s, &r1, 1); return;
    case 16: s->blocklen = arg; sd_push(s, &r1, 1); return;
    case 9: case 10: {
        uint8_t reg[16];
        memset(reg, 0, 16);
        if (cmd == 9) {
            uint64_t blocks = s->size / 512;
            int mult = 7;
            uint32_t csize = (uint32_t)(blocks / 512) - 1;
            if (csize > 4095) csize = 4095;
            reg[0] = 0x00; reg[1] = 0x26; reg[2] = 0x00; reg[3] = 0x32; reg[4] = 0x5F;
            reg[5] = 0x59;
            reg[6] = (uint8_t)(0x80 | ((csize >> 10) & 3));
            reg[7] = (uint8_t)((csize >> 2) & 0xFF);
            reg[8] = (uint8_t)(((csize & 3) << 6) | 0x2D);
            reg[9] = (uint8_t)(0x80 | ((mult >> 1) & 3));
            reg[10] = (uint8_t)(((mult & 1) << 7) | 0x7F);
            reg[11] = 0x80; reg[12] = 0x0A; reg[13] = 0x40; reg[14] = 0x00; reg[15] = 0x01;
        } else memcpy(reg, "\x03SDSU01\x10\x00\x00\x00\x01\x00\x5a\x01", 16);
        sd_push(s, &r1, 1);
        uint8_t t[2] = {0xFF, 0xFE};
        sd_push(s, t, 2);
        sd_push(s, reg, 16);
        uint16_t c = crc16(reg, 16);
        uint8_t cc[2] = {(uint8_t)(c >> 8), (uint8_t)c};
        sd_push(s, cc, 2);
        return;
    }
    case 12: s->reading = 0; { uint8_t b[2] = {0xFF, r1}; sd_push(s, b, 2); } return;
    case 13: { uint8_t r2[2] = {r1, 0}; sd_push(s, r2, 2); return; }
    case 17: sd_push(s, &r1, 1); sd_send_block(n, arg); return;
    case 18: sd_push(s, &r1, 1); s->reading = 1; s->rdaddr = arg; sd_send_block(n, arg); s->rdaddr += 512; return;
    case 24: sd_push(s, &r1, 1); s->writing = 1; s->wraddr = arg; s->wstate = 0; s->wn = 0; return;
    case 25: sd_push(s, &r1, 1); s->writing = 2; s->wraddr = arg; s->wstate = 0; s->wn = 0; return;
    default: { uint8_t r = (uint8_t)(r1 | 4); sd_push(s, &r, 1); return; }
    }
}
static uint8_t sd_xfer(Nmp *n, uint8_t in)
{
    SD *s = &n->sd;
    uint8_t out = 0xFF;
    if (s->outh < s->outn) {
        out = s->out[s->outh++];
        if (s->outh == s->outn) s->outh = s->outn = 0;
    } else if (s->reading) {
        sd_send_block(n, s->rdaddr);
        s->rdaddr += 512;
        out = s->out[s->outh++];
    }
    if (s->writing && s->cmdn == 0) {
        if (s->wstate == 0) {
            if (in == 0xFE || in == 0xFC) { s->wstate = 1; s->wn = 0; return out; }
            if (in == 0xFD && s->writing == 2) { s->writing = 0; uint8_t b[2] = {0xFF, 0x00}; sd_push(s, b, 2); return out; }
            if (in == 0xFF) return out;
        } else if (s->wstate == 1) {
            s->wbuf[s->wn++] = in;
            if (s->wn == 514) {
                if (s->f) { fseek(s->f, (long)s->wraddr, SEEK_SET); fwrite(s->wbuf, 1, 512, s->f); fflush(s->f); }
                if (n->sd_log) logf_(n, "SD write block %08x", s->wraddr);
                s->writes++;
                s->wraddr += 512;
                uint8_t b[3] = {0x05, 0x00, 0xFF};
                sd_push(s, b, 3);
                s->wstate = 0;
                if (s->writing == 1) s->writing = 0;
            }
            return out;
        }
    }
    if (s->cmdn == 0) {
        if ((in & 0xC0) == 0x40) s->cmd[s->cmdn++] = in;
    } else {
        s->cmd[s->cmdn++] = in;
        if (s->cmdn == 6) {
            s->cmdn = 0;
            if ((s->cmd[0] & 0x3F) == 12) s->outh = s->outn = 0;
            sd_command(n);
        }
    }
    return out;
}

/* ---------------------------------------------------------------- HPI */
static void set_hint(Nmp *n, int on)
{
    if (on && !n->hint) {
        n->hint = 1; n->hint_count++;
        if (n->host_irq) n->host_irq(n->host_ctx, 1);
    } else if (!on && n->hint) {
        n->hint = 0;
        if (n->host_irq) n->host_irq(n->host_ctx, 0);
    }
}

/* ---------------------------------------------------------------- DMA */
static void dma_irq(Nmp *n, int ch)
{
    int intosel = (n->dmprec >> 6) & 3;
    int bit = -1;
    if (ch == 4) bit = 12;
    else if (ch == 5) bit = 13;
    else if (ch == 2 && intosel >= 1) bit = 10;
    else if (ch == 3 && intosel >= 1) bit = 11;
    else if (ch == 0 && intosel >= 2) bit = 6;
    else if (ch == 1 && intosel >= 2) bit = 7;
    if (bit >= 0) c54_irq(C, bit);
}

static void dma_load(Nmp *n, int ch)
{
    DmaCh *d = &n->dma[ch];
    d->active = 1;
    d->elem = n->dmreg[5 * ch + 2];
    d->frame = n->dmreg[5 * ch + 3] & 0xFF;
}

static void dma_element(Nmp *n, int ch)
{
    DmaCh *d = &n->dma[ch];
    uint16_t *src = &n->dmreg[5 * ch + 0], *dst = &n->dmreg[5 * ch + 1];
    uint16_t mcr = n->dmreg[5 * ch + 4];
    int dms = (mcr >> 6) & 3, dmd = mcr & 3;
    uint16_t v;
    if (dms == 0) v = c54_pread(C, ((uint32_t)(n->dmreg[0x1E] & 0x7F) << 16) | *src);
    else if (dms == 1) v = c54_dread(C, *src);
    else v = 0xFFFF;
    if (dmd == 0) c54_pwrite(C, ((uint32_t)(n->dmreg[0x1F] & 0x7F) << 16) | *dst, v);
    else if (dmd == 1) c54_dwrite(C, *dst, v);
    d->moved++;
    int last_in_frame = (d->elem == 0);
    int sind = (mcr >> 8) & 7, dind = (mcr >> 2) & 7;
    for (int which = 0; which < 2; which++) {
        int m = which ? dind : sind;
        uint16_t *p = which ? dst : src;
        switch (m) {
        case 1: (*p)++; break;
        case 2: (*p)--; break;
        case 3: *p += n->dmreg[0x20]; break;
        case 4: *p += n->dmreg[0x21]; break;
        case 5: *p += last_in_frame ? n->dmreg[0x22] : n->dmreg[0x20]; break;
        case 6: *p += last_in_frame ? n->dmreg[0x23] : n->dmreg[0x21]; break;
        default: break;
        }
    }
    if (!last_in_frame) { d->elem--; return; }
    int autoinit = (mcr >> 15) & 1, dinm = (mcr >> 14) & 1, imod = (mcr >> 13) & 1;
    if (d->frame > 0) {
        d->frame--;
        d->elem = n->dmreg[5 * ch + 2];
        if (dinm && imod) dma_irq(n, ch);
        return;
    }
    d->blocks++;
    if (dinm) dma_irq(n, ch);
    if (autoinit) {
        int autoix = (n->dmprec >> 14) & 1;
        int g = autoix ? (ch == 0 ? 0x24 : 0x2A + 4 * (ch - 1)) : 0x24;
        *src = n->dmreg[g]; *dst = n->dmreg[g + 1];
        n->dmreg[5 * ch + 2] = n->dmreg[g + 2];
        n->dmreg[5 * ch + 3] = (uint16_t)((n->dmreg[5 * ch + 3] & 0xFF00) | (n->dmreg[g + 3] & 0xFF));
        dma_load(n, ch);
    } else {
        d->active = 0;
        n->dmprec &= (uint16_t)~(1u << ch);
    }
}

static void dma_event(Nmp *n, int ev)
{
    for (int ch = 0; ch < 6; ch++) {
        if (!(n->dmprec & (1u << ch)) || !n->dma[ch].active) continue;
        if (((n->dmreg[5 * ch + 3] >> 12) & 15) != ev) continue;
        dma_element(n, ch);
    }
}

static void dma_free(Nmp *n)
{
    for (int ch = 0; ch < 6; ch++) {
        if (!(n->dmprec & (1u << ch)) || !n->dma[ch].active) continue;
        if (((n->dmreg[5 * ch + 3] >> 12) & 15) != 0) continue;
        int guard = 1 << 20;
        while (n->dma[ch].active && (n->dmprec & (1u << ch)) && guard--) {
            uint32_t b = n->dma[ch].blocks;
            dma_element(n, ch);
            if (n->dma[ch].blocks != b && ((n->dmreg[5 * ch + 4] >> 15) & 1)) break;
        }
    }
}

static void spi_xevt_pending(Nmp *n);
static void dmprec_write(Nmp *n, uint16_t v)
{
    uint16_t old = n->dmprec;
    n->dmprec = v;
    for (int ch = 0; ch < 6; ch++) {
        if ((v & (1u << ch)) && !(old & (1u << ch))) dma_load(n, ch);
        else if (!(v & (1u << ch))) n->dma[ch].active = 0;
    }
    dma_free(n);
}

/* ---------------------------------------------------------------- McBSP */
static int64_t spi_word_cycles(McBSP *s)
{
    int bits = 8;
    switch ((s->sub[4] >> 5) & 7) { case 0: bits = 8; break; case 1: bits = 12; break; case 2: bits = 16; break;
                                     case 3: bits = 20; break; case 4: bits = 24; break; default: bits = 32; }
    return (int64_t)bits * ((s->sub[6] & 0xFF) + 1);
}
static void spi_start(Nmp *n, McBSP *s, int which)
{
    s->xsr = s->dxr1; s->dxr_full = 0;
    s->busy = 1; s->left = spi_word_cycles(s);
    s->xcount++;
    dma_event(n, which == 0 ? 2 : which == 1 ? 6 : 4);   /* DXR empty: XEVT */
}
static void spi_run(Nmp *n, int which, int64_t cyc)
{
    McBSP *s = &n->sp[which];
    while (s->busy && cyc > 0) {
        if (cyc < s->left) { s->left -= cyc; return; }
        cyc -= s->left;
        s->busy = 0;
        uint8_t rx = 0xFF;
        if (which == 2) rx = sd_xfer(n, (uint8_t)s->xsr);
        if (n->spi_log) logf_(n, "SPI%d %02x -> %02x", which, s->xsr & 0xFF, rx);
        if (s->sub[0] & 1) {
            s->drr1 = rx; s->rrdy = 1;
            dma_event(n, which == 0 ? 1 : which == 1 ? 5 : 3);   /* REVT */
        }
        if (s->dxr_full && (s->sub[1] & 1)) spi_start(n, s, which);
    }
}
static McBSP *sp_of(Nmp *n, uint16_t a, int *idx)
{
    switch (a) {
    case 0x20: case 0x21: case 0x22: case 0x23: case 0x38: case 0x39: *idx = 0; return &n->sp[0];
    case 0x40: case 0x41: case 0x42: case 0x43: case 0x48: case 0x49: *idx = 1; return &n->sp[1];
    case 0x30: case 0x31: case 0x32: case 0x33: case 0x34: case 0x35: *idx = 2; return &n->sp[2];
    }
    *idx = -1; return NULL;
}
static int is_spsa(uint16_t a) { return a == 0x38 || a == 0x48 || a == 0x34; }
static int is_spsd(uint16_t a) { return a == 0x39 || a == 0x49 || a == 0x35; }
static int sp_clkstp(McBSP *s) { return ((s->sub[0] >> 11) & 3) >= 2; }

static uint16_t mcbsp_read(Nmp *n, McBSP *s, int which, uint16_t a)
{
    (void)n; (void)which;
    if (is_spsa(a)) return s->spsa;
    if (is_spsd(a)) {
        uint16_t sa = s->spsa & 0x1F;
        uint16_t v = s->sub[sa];
        if (sa == 0) { v &= (uint16_t)~2; if ((v & 1) && s->rrdy) v |= 2; }
        if (sa == 1) {
            v &= (uint16_t)~6;
            if (v & 1) {
                if (!s->dxr_full) v |= 2;                     /* XRDY */
                if (!(s->busy || s->dxr_full)) v |= 4;        /* XEMPTY_ = 1 while not empty?  see below */
            }
            /* XEMPTY_ is active low: 0 = transmit shift register empty */
            if (v & 1) v = (uint16_t)((v & ~4) | ((s->busy || s->dxr_full) ? 4 : 0));
        }
        if (sa == 0x0E) {
            uint16_t inmask = 0x50;
            if (!(v & 0x100)) inmask |= 0x01;
            if (!(v & 0x200)) inmask |= 0x02;
            if (!(v & 0x400)) inmask |= 0x04;
            if (!(v & 0x800)) inmask |= 0x08;
            v = (uint16_t)((v & ~inmask) | (s->pin_in & inmask));
        }
        return v;
    }
    switch (a & 3) {
    case 0: return s->drr2;
    case 1: s->rcount++; s->rrdy = 0; return s->drr1;
    case 2: return s->dxr2;
    default: return s->dxr1;
    }
}

static void mcbsp_write(Nmp *n, McBSP *s, int which, uint16_t a, uint16_t v)
{
    if (is_spsa(a)) { s->spsa = v; return; }
    if (is_spsd(a)) {
        uint16_t sa = s->spsa & 0x1F;
        uint16_t oldv = s->sub[sa];
        n->sub_w[which][sa]++;
        n->sub_last[which][sa] = v;
        s->sub[sa] = v;
        if (n->log_mmr) logf_(n, "McBSP%d sub %02x <- %04x", which, sa, v);
        if (sa == 0 && !(v & 1)) s->rrdy = 0;
        if (sa == 1 && !(v & 1)) { s->busy = 0; s->dxr_full = 0; }
        /* XEVT is the XRDY rising edge: taking the transmitter out of reset raises it */
        if (sa == 1 && (v & 1) && !(oldv & 1) && sp_clkstp(s) && !s->busy) dma_event(n, which == 0 ? 2 : which == 1 ? 6 : 4);
        if (sa == 0x0E && n->pin_log) {
            uint16_t outs = v & 0x2F;
            if (outs != s->pin_out_last) logf_(n, "McBSP%d pins out %02x", which, outs);
            s->pin_out_last = outs;
        }
        return;
    }
    switch (a & 3) {
    case 0: s->drr2 = v; break;
    case 1: s->drr1 = v; break;
    case 2: s->dxr2 = v; break;
    default:
        s->dxr1 = v;
        if (sp_clkstp(s) && (s->sub[1] & 1)) {   /* SPI master: word goes to XSR when the shifter is free */
            s->dxr_full = 1;
            if (!s->busy) spi_start(n, s, which);
        }
        break;
    }
}

/* XEVT is level-like: a newly enabled DMA channel synced to an idle SPI transmitter starts it */
static void __attribute__((unused)) spi_xevt_pending(Nmp *n)
{
    for (int w = 0; w < 3; w++) {
        McBSP *s = &n->sp[w];
        if (sp_clkstp(s) && (s->sub[1] & 1) && !s->dxr_full) dma_event(n, w == 0 ? 2 : w == 1 ? 6 : 4);
    }
}

/* audio transmitter: word slots at the sample-rate-generator rate */
static void mcbsp_audio_run(Nmp *n, int which, int64_t cyc)
{
    McBSP *s = &n->sp[which];
    if (sp_clkstp(s)) return;
    if (!(s->sub[1] & 1) || !(s->sub[1] & 0x40) || !(s->sub[1] & 0x80)) return;   /* XRST, GRST, FRST */
    double clkgdv = (s->sub[6] & 0xFF) + 1;
    double fper = (s->sub[7] & 0xFFF) + 1;
    int words = ((s->sub[4] >> 8) & 0x7F) + 1;
    if (s->sub[5] & 0x8000) words += ((s->sub[5] >> 8) & 0x7F) + 1;
    double slot = clkgdv * fper / words;
    if (slot < 4) slot = 4;
    s->slot_acc += (double)cyc;
    while (s->slot_acc >= slot) {
        s->slot_acc -= slot;
        if (which == 0 && n->pcm) {
            if (((n->pcm_w + 1) & (PCM_RING - 1)) == n->pcm_r) n->pcm_overrun++;
            else { n->pcm[n->pcm_w] = (int16_t)s->dxr1; n->pcm_w = (n->pcm_w + 1) & (PCM_RING - 1); }
            n->pcm_total++;
        }
        s->xcount++;
        int ev = which == 0 ? 2 : which == 1 ? 6 : 4;   /* XEVT0 / XEVT1 / XEVT2 */
        dma_event(n, ev);
    }
}

/* ---------------------------------------------------------------- MMR bus */
static uint16_t mmr_read(void *ctx, uint16_t a)
{
    Nmp *n = ctx;
    n->mmr_r[a]++;
    if (!n->mmr_pc[a]) n->mmr_pc[a] = curpc(n) | 0x80000000u;
    uint16_t v = n->ram[a];
    int idx;
    McBSP *s = sp_of(n, a, &idx);
    if (s) v = mcbsp_read(n, s, idx, a);
    else switch (a) {
    case 0x24: v = n->tim; break;
    case 0x25: v = n->prd; break;
    case 0x26: v = n->tcr; break;
    case 0x28: v = n->swwsr; break;
    case 0x29: v = n->bscr; break;
    case 0x2B: v = n->swcr; break;
    case 0x2C: v = (uint16_t)((n->hpic & ~0x0C) | (n->hint ? 0x08 : 0)); break;
    case 0x3C: v = n->gpiocr; break;
    case 0x3D: v = n->gpiosr; break;
    case 0x3E: v = 0x1600; break;
    case 0x54: v = n->dmprec; break;
    case 0x55: v = n->dmsa; break;
    case 0x56: v = n->dmreg[n->dmsa & 0x3F]; n->dmsa++; break;
    case 0x57: v = n->dmreg[n->dmsa & 0x3F]; break;
    case 0x58: v = (uint16_t)(n->clkmd | 1); break;
    }
    if (n->log_mmr > 1) logf_(n, "MMR R %02x -> %04x", a, v);
    return v;
}

static void mmr_write(void *ctx, uint16_t a, uint16_t v)
{
    Nmp *n = ctx;
    n->mmr_w[a]++;
    n->mmr_last[a] = v;
    if (!n->mmr_pc[a]) n->mmr_pc[a] = curpc(n) | 0x80000000u;
    int idx;
    McBSP *s = sp_of(n, a, &idx);
    if (n->log_mmr && !(s && (a & 0x0F) <= 3)) logf_(n, "MMR W %02x <- %04x", a, v);
    if (s) { mcbsp_write(n, s, idx, a, v); return; }
    switch (a) {
    case 0x24: n->tim = v; return;
    case 0x25: n->prd = v; return;
    case 0x26:
        n->tcr = v & ~0x20;
        if (v & 0x20) { n->tim = n->prd; n->psc = v & 15; }
        return;
    case 0x28: n->swwsr = v; return;
    case 0x29: n->bscr = v; return;
    case 0x2B: n->swcr = v; return;
    case 0x2C:
        if (v & 0x08) set_hint(n, 1);
        n->hpic = v & ~0x0C;
        return;
    case 0x3C: n->gpiocr = v; return;
    case 0x3D: n->gpiosr = v; return;
    case 0x54: dmprec_write(n, v); return;
    case 0x55: n->dmsa = v; return;
    case 0x56: n->dmreg[n->dmsa & 0x3F] = v; n->dmsa++; return;
    case 0x57: n->dmreg[n->dmsa & 0x3F] = v; return;
    case 0x58:
        n->clkmd = v;
        /* PLL: CPU = CLKIN x (PLLMUL+1) with PLLNDIV=1/PLLDIV=0 (SPRU131 PLL); CLKIN = 11.2896 MHz on this board
           (the firmware's own constant 0x00AC4400 at 2:dfaf and its PRD arithmetic) */
        if ((v & 4) && (v & 2) && !(v & 0x800)) n->cpu_hz = n->clkin_hz * (((v >> 12) & 15) + 1);
        else if ((v & 4) && !(v & 2) && !(v & 0x800)) n->cpu_hz = n->clkin_hz * (((v >> 12) & 15) + 1) / 2.0;
        else if (!(v & 4)) n->cpu_hz = n->clkin_hz / 2.0;
        logf_(n, "CLKMD %04x: CPU %.4f MHz", v, n->cpu_hz / 1e6);
        return;
    }
    n->ram[a] = v;
}

static uint16_t io_read(void *ctx, uint16_t pa)
{
    Nmp *n = ctx;
    n->io_r[pa & 255]++;
    if (!n->io_pc[pa & 255]) n->io_pc[pa & 255] = curpc(n) | 0x80000000u;
    logf_(n, "PORTR %04x", pa);
    return 0xFFFF;
}
static void io_write(void *ctx, uint16_t pa, uint16_t v)
{
    Nmp *n = ctx;
    n->io_w[pa & 255]++;
    n->io_last[pa & 255] = v;
    if (!n->io_pc[pa & 255]) n->io_pc[pa & 255] = curpc(n) | 0x80000000u;
    logf_(n, "PORTW %04x <- %04x", pa, v);
}

/* ---------------------------------------------------------------- timer */
static void timer_run(Nmp *n, int64_t cyc)
{
    if (n->tcr & 0x10) return;
    uint64_t div = (uint64_t)(n->tcr & 15) + 1;
    uint64_t period = (uint64_t)(n->prd + 1) * div;
    uint64_t left = (uint64_t)n->tim * div + n->psc + 1;   /* cycles to the next underflow */
    uint64_t c = (uint64_t)cyc;
    if (c < left) {
        uint64_t rem = left - c - 1;
        n->tim = (uint16_t)(rem / div); n->psc = (uint32_t)(rem % div);
        return;
    }
    c -= left;
    c54_irq(C, 3);
    c %= period;
    uint64_t rem = period - c - 1;
    n->tim = (uint16_t)(rem / div); n->psc = (uint32_t)(rem % div);
}

/* ---------------------------------------------------------------- API */
Nmp *nmp_new(uint16_t *ram)
{
    Nmp *n = calloc(1, sizeof(Nmp));
    if (!ram) { ram = calloc(C54_RAMW, 2); n->own_ram = 1; }
    n->ram = ram;
    c54_init(C, ram);
    n->cpu.bus.ctx = n;
    n->cpu.bus.mmr_read = mmr_read;
    n->cpu.bus.mmr_write = mmr_write;
    n->cpu.bus.io_read = io_read;
    n->cpu.bus.io_write = io_write;
    n->in_reset = 1;
    n->pcm = calloc(PCM_RING, sizeof(int16_t));
    n->speed = 1.0;
    n->clkin_hz = 11289600.0;
    n->cpu_hz = n->clkin_hz / 2.0;   /* reset: CLKMD pins -> 1/2 until the firmware programs the PLL */
    n->sp[1].pin_in = 0x10;
    return n;
}

C54 *nmp_cpu(Nmp *n) { return C; }
uint16_t *nmp_ram(Nmp *n) { return n->ram; }
void nmp_set_host_irq(Nmp *n, void (*cb)(void *, int), void *ctx) { n->host_irq = cb; n->host_ctx = ctx; }
void nmp_set_log(Nmp *n, const char *path, int level)
{
    if (n->log) { fclose(n->log); n->log = NULL; }
    if (path && *path) n->log = fopen(path, "w");
    n->log_mmr = level & 3;
    n->sd_log = (level >> 2) & 1;
    n->pin_log = (level >> 3) & 1;
    n->spi_log = (level >> 4) & 1;
}
void nmp_flush_log(Nmp *n) { if (n->log) fflush(n->log); }

int nmp_sd_open(Nmp *n, const char *path)
{
    SD *s = &n->sd;
    if (s->f) fclose(s->f);
    memset(s, 0, sizeof(*s));
    if (!path || !*path) return 0;
    s->f = fopen(path, "r+b");
    if (!s->f) return -1;
    fseek(s->f, 0, SEEK_END);
    s->size = (uint64_t)ftell(s->f);
    s->present = 1;
    return 0;
}

void nmp_reset(Nmp *n, int asserted)
{
    if (asserted) { n->in_reset = 1; return; }
    if (!n->in_reset) return;
    n->in_reset = 0;
    c54_reset(C);
    n->cpu.pmst |= 0x20;
    n->swwsr = 0x7FFF;
    n->ram[0x7F] = 0;
    n->boot_wait = 1;
    n->hint = 0; n->hpic = 0;
    n->tcr = 0x10; n->tim = 0xFFFF; n->prd = 0xFFFF; n->psc = 0;
    { uint16_t keep[3]; for (int i = 0; i < 3; i++) keep[i] = n->sp[i].pin_in;
      memset(n->sp, 0, sizeof(n->sp));
      for (int i = 0; i < 3; i++) n->sp[i].pin_in = keep[i]; }
    n->dmprec = 0; memset(n->dma, 0, sizeof(n->dma)); memset(n->dmreg, 0, sizeof(n->dmreg));
    n->sd.idle = 0; n->sd.app = 0; n->sd.reading = 0; n->sd.writing = 0; n->sd.cmdn = 0; n->sd.outh = n->sd.outn = 0;
    logf_(n, "reset released: ROM bootloader (HPI boot) waiting for 007Fh");
}

void nmp_host_hpic(Nmp *n, uint8_t v)
{
    if (v & 0x08) set_hint(n, 0);
    if ((v & 0x04) && !n->in_reset) { c54_irq(C, 9); n->dspint_count++; }
}
int nmp_hint(Nmp *n) { return n->hint; }
void nmp_set_speed(Nmp *n, double s) { n->speed = s; }
void nmp_set_pins(Nmp *n, int port, uint16_t levels) { n->sp[port].pin_in = levels; }

int64_t nmp_run(Nmp *n, int64_t cycles)
{
    if (n->in_reset) { n->cycles_total += (uint64_t)cycles; return cycles; }
    if (n->boot_wait) {
        if (n->ram[0x7F] == 0) { n->cycles_total += (uint64_t)cycles; return cycles; }
        n->boot_wait = 0;
        n->cpu.xpc = n->ram[0x7E] & 0x7F;
        n->cpu.pc = n->ram[0x7F];
        logf_(n, "bootloader: branch to %x:%04x", n->cpu.xpc, n->cpu.pc);
    }
    int64_t done = 0;
    while (done < cycles) {
        int64_t slice = cycles - done;
        if (slice > 500) slice = 500;
        uint64_t c0 = n->cpu.cycles;
        c54_run(C, slice);
        int64_t used = (int64_t)(n->cpu.cycles - c0);
        if (used <= 0) used = slice;
        int64_t pused = used;
        if (n->speed != 1.0) {
            n->speed_acc += (double)used / n->speed;
            pused = (int64_t)n->speed_acc; n->speed_acc -= (double)pused;
        }
        timer_run(n, pused);
        mcbsp_audio_run(n, 0, pused);
        spi_run(n, 1, pused);
        spi_run(n, 2, pused);
        done += pused;
        if (n->cpu.hit_brk || n->cpu.halted) break;
    }
    n->cycles_total += (uint64_t)done;
    return done;
}

int nmp_pcm_read(Nmp *n, int16_t *dst, int max)
{
    int k = 0;
    while (k < max && n->pcm_r != n->pcm_w) { dst[k++] = n->pcm[n->pcm_r]; n->pcm_r = (n->pcm_r + 1) & (PCM_RING - 1); }
    return k;
}
double nmp_cpu_hz(Nmp *n) { return n->cpu_hz; }
double nmp_pcm_rate(Nmp *n)
{
    McBSP *s = &n->sp[0];
    double clkgdv = (s->sub[6] & 0xFF) + 1, fper = (s->sub[7] & 0xFFF) + 1;
    return n->cpu_hz / (clkgdv * fper);
}
uint64_t nmp_stat(Nmp *n, int k)
{
    switch (k) {
    case 0: return n->hint_count; case 1: return n->dspint_count; case 2: return n->pcm_total;
    case 3: return n->dma[3].blocks; case 4: return n->pcm_overrun; case 5: return n->cycles_total;
    case 20: case 21: case 22: case 23: case 24: case 25: return n->dma[k - 20].moved;
    case 30: case 31: case 32: case 33: case 34: case 35: return n->dma[k - 30].blocks;
    case 10: return n->sd.cmds; case 11: return n->sd.reads; case 12: return n->sd.writes;
    case 13: return n->sd.last_cmd; case 14: return n->sd.last_arg;
    }
    return 0;
}

void nmp_report(Nmp *n, FILE *f)
{
    if (!f) f = stderr;
    fprintf(f, "MMR accesses (addr: reads writes last-write first-pc)\n");
    for (int a = 0x20; a < 0x60; a++)
        if (n->mmr_r[a] || n->mmr_w[a])
            fprintf(f, "  %02x: r=%u w=%u last=%04x pc=%x:%04x\n", a, n->mmr_r[a], n->mmr_w[a], n->mmr_last[a],
                    (n->mmr_pc[a] >> 16) & 0x7F, n->mmr_pc[a] & 0xFFFF);
    for (int s = 0; s < 3; s++)
        for (int k = 0; k < 0x20; k++)
            if (n->sub_w[s][k]) fprintf(f, "  McBSP%d sub %02x: w=%u last=%04x\n", s, k, n->sub_w[s][k], n->sub_last[s][k]);
    for (int p = 0; p < 256; p++)
        if (n->io_r[p] || n->io_w[p])
            fprintf(f, "  IO %02x: r=%u w=%u last=%04x pc=%x:%04x\n", p, n->io_r[p], n->io_w[p], n->io_last[p],
                    (n->io_pc[p] >> 16) & 0x7F, n->io_pc[p] & 0xFFFF);
    fprintf(f, "  timer tcr=%04x prd=%04x tim=%04x psc=%u\n", n->tcr, n->prd, n->tim, n->psc);
    fprintf(f, "  DMA dmprec=%04x ch3 moved=%llu blocks=%u regs:", n->dmprec, (unsigned long long)n->dma[3].moved, n->dma[3].blocks);
    for (int k = 0; k < 0x3E; k++) fprintf(f, "%s%04x", (k % 5) ? " " : " | ", n->dmreg[k]);
    fprintf(f, "\n  SD cmds=%llu reads=%llu writes=%llu last=CMD%u(%08x)\n", (unsigned long long)n->sd.cmds,
            (unsigned long long)n->sd.reads, (unsigned long long)n->sd.writes, n->sd.last_cmd, n->sd.last_arg);
    fprintf(f, "  PCM words=%llu rate=%.1f Hz overrun=%u\n", (unsigned long long)n->pcm_total, nmp_pcm_rate(n), n->pcm_overrun);
    fprintf(f, "  ROM reads %u (last %x from %x:%04x)\n", n->cpu.rom_reads, n->cpu.rom_last, n->cpu.last_pc >> 16, n->cpu.last_pc & 0xFFFF);
    fprintf(f, "  HINT raised %llu, DSPINT %llu, cpu cycles %llu insns %llu bad %u ext %u\n",
            (unsigned long long)n->hint_count, (unsigned long long)n->dspint_count,
            (unsigned long long)n->cpu.cycles, (unsigned long long)n->cpu.insns, n->cpu.bad_ops, n->cpu.ext_access);
}
void nmp_report_stderr(Nmp *n) { nmp_report(n, stderr); fflush(stderr); }

/* ---------------------------------------------------------------- mGBA coupling (pyan.c NMP LLE hooks)
 * run: called every retro_pyan_lle_period GBA cycles; converts GBA time to DSP cycles at the current
 * PLL setting.  reset: the GBA's GPIO bit 3.  hpic: GBA writes to CONTROL (HPIC). */
#define GBA_HZ 16777216.0
static double lle_acc;
void nmp_lle_run(void *ctx, uint32_t gba_cycles)
{
    Nmp *n = ctx;
    lle_acc += (double)gba_cycles * n->cpu_hz / GBA_HZ;
    int64_t c = (int64_t)lle_acc;
    if (c <= 0) return;
    int64_t done = nmp_run(n, c);
    lle_acc -= (double)done;
    if (lle_acc < -1e6) lle_acc = 0;
}
void nmp_lle_reset(void *ctx, int asserted) { nmp_reset((Nmp *)ctx, asserted); }
void nmp_lle_hpic(void *ctx, uint32_t v) { nmp_host_hpic((Nmp *)ctx, (uint8_t)v); }
