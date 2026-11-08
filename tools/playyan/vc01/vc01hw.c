/* VC01 peripheral model for the Play-Yan firmware (loader, video and music images).
 * Register maps are inferred from the firmware code; evidence is in docs/playyan/vc01-hardware.md.
 *
 * Time base: hw->cycles counts VC01 core clock cycles (1 instruction = 1 cycle).  The system
 * timer counter (0x10001804) is this cycle count, which matches how the firmware uses it: it
 * converts microseconds to timer ticks with its own idea of the core clock (gp+620, computed
 * from the PLL registers), so a 1-IPC core whose timer counts core cycles is self-consistent.
 */
#include "vc01core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MMIO_WORDS 0x8000   /* 0x10000000 .. 0x1001ffff */

typedef struct {
	uint32_t a;
	uint8_t w;
	uint32_t n, pc, last;
} AccLog;
#define NLOG 4096

typedef struct HW HW;
typedef void (*hw_host_irq_t)(void* ctx, int level);
typedef void (*hw_event_t)(void* ctx, int ev, uint32_t a, uint32_t b);

struct HW {
	VC01* c;
	uint8_t* ram;
	uint32_t ram_size;
	uint32_t reg[MMIO_WORDS];
	uint64_t cycles;           /* emulated time in instruction slots (1 slot = 1/ips s) */
	double ips;                /* VC01 instructions per emulated second (free parameter) */
	double tcount;             /* system timer counter (runs at clk_hz, the firmware's own clock) */
	uint64_t last_icount;
	/* timer */
	uint32_t tm_cs;
	/* gpio / host */
	uint32_t g_out, g_in, g_ist;
	uint64_t host_irqs;
	hw_host_irq_t host_irq;    /* GBA Game Pak IRQ line (rising edge of GPIO 30) */
	void* host_ctx;
	hw_event_t event;          /* misc events to the embedding harness */
	void* event_ctx;
	/* uart */
	char uart[1 << 20];
	uint32_t uartn;
	/* SPI + SD card (SPI mode) */
	uint32_t spi_ctrl;
	uint8_t spi_rx[64];
	int spi_rxh, spi_rxt;
	FILE* sd_file;
	uint64_t sd_size;
	int sd_present;
	int sd_state;              /* 0 idle/cmd, 1 response pending */
	uint8_t sd_cmd[6];
	int sd_cmdn;
	uint8_t sd_out[1024];      /* bytes to send back */
	int sd_outh, sd_outn;
	int sd_idle;               /* in idle state (after CMD0, until ACMD41 done) */
	int sd_app;                /* next command is ACMD */
	uint32_t sd_blocklen;
	int sd_reading;            /* CMD18 multi-block read active */
	uint32_t sd_rdaddr;
	int sd_writing;            /* 1 = CMD24 awaiting data, 2 = CMD25 */
	uint32_t sd_wraddr;
	uint8_t sd_wbuf[520];
	int sd_wn;
	int sd_wstate;             /* 0 wait token, 1 data, 2 crc */
	uint64_t sd_reads, sd_writes, sd_cmds;
	/* AC97 */
	uint16_t ac_reg[64];
	uint32_t ac_ctrl;
	int16_t* pcm;              /* captured PCM (interleaved stereo s16) */
	uint64_t pcm_n, pcm_cap;
	uint64_t* mk;              /* headphone block log: per data CB started on ACFIFO: pcm_n, src, bytes, cycles, fnv1a64 */
	uint64_t mk_n, mk_cap;
	uint64_t ac_silence;       /* words of silence-CB data sent to ACFIFO (underrun / idle) */
	/* logging */
	AccLog log[NLOG];
	int nlog;
	int log_on;
	uint32_t trace_mmio;       /* 1 = print every MMIO access */
	/* misc */
	double clk_hz;             /* derived from PLL registers (firmware formula) */
	/* DMA channels (0x10000800 + 0x80*ch).  Control blocks of 8 words: next, src, dst, ctrl
	 * (len = ctrl & 0xfffff, bit 24 = interrupt when done), 4 unused. */
	struct {
		uint32_t cb, next, src, dst, ctrl, len, done, total, ist;
		double frac;
	} dma[8];
	double ac_rate;            /* AC'97 DAC rate, Hz */
	double ac_scale;           /* experiment knob: AC'97 consumption speed factor (1.0) */
	int hp_jack;               /* headphones plugged (WM9712 reg 0x54 bit 1) */
	uint16_t ac_gpio_level;    /* WM9712 GPIO pin levels: bit 1 jack sense, bit 11 alert line (high) */
	uint16_t ac_gpio_latch;    /* sticky status bits (reg 0x50 pins), cleared by writing 0 to reg 0x54 */
	uint64_t ac_gpio_irqs;     /* codec interrupts raised (VC GPIO pin 3) */
	int ac_gpio_irqline;
	uint64_t ac_ready_at;      /* ACCS bits 22/23 (codec ready) read 1 from this time on */
	uint64_t dma_cbs, dma_bytes;
	/* LLE coupling to the GBA */
	int running;               /* CPU released by the host kick */
	int in_reset;              /* host holds CTRL bit 3 low */
	double slot_frac;
	double slot_debt;          /* slots run ahead of the GBA (doorbell service), taken from later slices */
	uint64_t db_sync_slots, db_sync_max;
	uint64_t kicks, wakes;
	int last_stop;
	uint32_t last_stop_pc;
};

/* ---------------------------------------------------------------- logging */
static void acc(HW* h, uint32_t a, int w, uint32_t v) {
	if (!h->log_on) return;
	int k = (a >> 2) * 2 + w;
	(void) k;
	for (int i = 0; i < h->nlog; i++) {
		if (h->log[i].a == a && h->log[i].w == w) { h->log[i].n++; h->log[i].last = v; return; }
	}
	if (h->nlog < NLOG) {
		AccLog* L = &h->log[h->nlog++];
		L->a = a; L->w = w; L->n = 1; L->pc = h->c->r[31]; L->last = v;
	}
}

static inline uint32_t* R(HW* h, uint32_t a) { return &h->reg[((a - 0x10000000u) >> 2) & (MMIO_WORDS - 1)]; }

/* ---------------------------------------------------------------- clock */
static void update_clk(HW* h) {
	uint32_t r10 = *R(h, 0x10001410), r0c = *R(h, 0x1000140c);
	uint32_t n = (r10 & 0x3ff) + 1, p = (r10 >> 12) & 15, m = r0c & 0x3ff;
	uint32_t d6 = *R(h, 0x10000408) & 0xffff, d7 = *R(h, 0x1000040c) & 0x3ff;
	double f = (p && m) ? 20e6 * n / ((double) m * p) : 20e6;
	double div = d6 * 16.0 + (d7 >> 6);
	h->clk_hz = div > 0 ? f * 16 / div : f;
}

/* ---------------------------------------------------------------- interrupts */
static int prio(HW* h, int irq) {
	uint32_t r = *R(h, 0x10001010 + 4 * ((irq - 32) >> 3));
	return (r >> (4 * ((irq - 32) & 7))) & 7;
}

static uint64_t pending_mask(HW* h) {   /* bit (irq-32) */
	uint64_t m = 0;
	for (int k = 0; k < 4; k++) if (h->tm_cs & (1u << k)) m |= 1ull << (16 + k);
	for (int k = 0; k < 8; k++) if (h->dma[k].ist) m |= 1ull << k;   /* 32..39: DMA channel k */
	uint32_t en = *R(h, 0x10002818);
	if (h->g_ist & en & 4) m |= 1ull << 10;              /* 42: host doorbell */
	if (h->g_ist & en & ~4u) m |= 1ull << 11;            /* 43: other GPIO */
	return m;
}

static int best_irq(HW* h, int* pr) {
	uint64_t m = pending_mask(h);
	int best = 0, bp = 0;
	for (int i = 0; i < 32; i++) {
		if (!(m >> i & 1)) continue;
		int p = prio(h, 32 + i);
		if (p > bp) { bp = p; best = 32 + i; }
	}
	*pr = bp;
	return best;
}

static int hw_irq(void* ctx) {
	HW* h = ctx;
	int p;
	int v = best_irq(h, &p);
	if (!v) return 0;
	int level = *R(h, 0x10001000) & 7;
	if (p <= level) return 0;
	return v;
}

static void poll(HW* h) { h->c->irq_poll = 1; }

/* ---------------------------------------------------------------- timer */
static void timer_check(HW* h) {
	uint32_t now = (uint32_t) (uint64_t) h->tcount;
	for (int k = 0; k < 4; k++) {
		uint32_t cmp = *R(h, 0x10001808 + 4 * k);
		uint32_t armed = *R(h, 0x10001808 + 4 * k) != 0 || 1;
		(void) armed;
		/* match when the counter has passed the compare value since the compare was written */
		uint32_t last = h->reg[0x7f00 + k];   /* counter value at the last check (scratch) */
		if ((uint32_t) (cmp - last - 1) < (uint32_t) (now - last)) {
			if (!(h->tm_cs & (1u << k))) { h->tm_cs |= 1u << k; poll(h); }
		}
		h->reg[0x7f00 + k] = now;
	}
}

/* cycles until the next timer match (for skipping idle time) */
static uint64_t timer_next(HW* h) {
	uint32_t now = (uint32_t) (uint64_t) h->tcount;
	uint64_t best = ~0ull;
	for (int k = 0; k < 4; k++) {
		if (!(prio(h, 48 + k))) continue;
		if (h->tm_cs & (1u << k)) continue;
		uint32_t d = *R(h, 0x10001808 + 4 * k) - now;
		if (d && d < best) best = d;
	}
	if (best == ~0ull) return best;
	/* timer counts -> instruction slots */
	double slots = (double) best * h->ips / h->clk_hz;
	return slots < 1 ? 1 : (uint64_t) slots + 1;
}

static void dma_advance(HW* h, uint64_t slots);
static uint64_t dma_next(HW* h);
static inline void advance(HW* h, uint64_t slots) {
	h->cycles += slots;
	h->tcount += (double) slots * h->clk_hz / h->ips;
	dma_advance(h, slots);
}

/* ---------------------------------------------------------------- SD card (SPI mode) */
static void sd_push(HW* h, const uint8_t* b, int n) {
	for (int i = 0; i < n && h->sd_outn < (int) sizeof(h->sd_out); i++) h->sd_out[h->sd_outn++] = b[i];
}

static uint16_t crc16(const uint8_t* d, int n) {
	uint16_t c = 0;
	for (int i = 0; i < n; i++) {
		c ^= (uint16_t) d[i] << 8;
		for (int k = 0; k < 8; k++) c = (c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1;
	}
	return c;
}

static void sd_send_block(HW* h, uint32_t addr) {
	uint8_t blk[512 + 4];
	memset(blk, 0xff, sizeof(blk));
	blk[0] = 0xff;
	blk[1] = 0xfe;
	memset(blk + 2, 0, 512);
	if (h->sd_file && addr + 512 <= h->sd_size) {
		fseek(h->sd_file, addr, SEEK_SET);
		if (fread(blk + 2, 1, 512, h->sd_file) != 512) { /* short read: zeros */ }
	}
	uint16_t c = crc16(blk + 2, 512);
	sd_push(h, blk, 2);
	sd_push(h, blk + 2, 512);
	uint8_t cc[2] = {c >> 8, c & 0xff};
	sd_push(h, cc, 2);
	h->sd_reads++;
	if (h->event) h->event(h->event_ctx, 1, addr, 512);
}

static void sd_command(HW* h) {
	int cmd = h->sd_cmd[0] & 0x3f;
	uint32_t arg = (uint32_t) h->sd_cmd[1] << 24 | h->sd_cmd[2] << 16 | h->sd_cmd[3] << 8 | h->sd_cmd[4];
	int app = h->sd_app;
	h->sd_app = 0;
	h->sd_cmds++;
	uint8_t r1 = h->sd_idle ? 1 : 0;
	uint8_t pre = 0xff;
	sd_push(h, &pre, 1);      /* Ncr = 1 */
	if (h->event) h->event(h->event_ctx, 2, (app ? 100 : 0) + cmd, arg);
	if (!h->sd_present || !h->sd_file) { uint8_t ff = 0xff; sd_push(h, &ff, 1); return; }
	if (app) {
		switch (cmd) {
		case 41: h->sd_idle = 0; r1 = 0; sd_push(h, &r1, 1); return;
		case 51: { sd_push(h, &r1, 1); uint8_t d[10] = {0xff, 0xfe, 0x00, 0x25, 0, 0, 0, 0, 0, 0}; uint16_t c = crc16(d + 2, 8); sd_push(h, d, 10); uint8_t cc[2] = {c >> 8, c}; sd_push(h, cc, 2); return; }
		case 13: { uint8_t r2[2] = {r1, 0}; sd_push(h, r2, 2); uint8_t blk[66]; memset(blk, 0, 66); blk[0] = 0xfe; sd_push(h, blk, 1); sd_push(h, blk + 1, 64); uint16_t c = crc16(blk + 1, 64); uint8_t cc[2] = {c >> 8, c}; sd_push(h, cc, 2); return; }
		case 23: sd_push(h, &r1, 1); return;
		default: break;   /* fall through to normal handling */
		}
	}
	switch (cmd) {
	case 0: h->sd_idle = 1; h->sd_reading = 0; h->sd_writing = 0; r1 = 1; sd_push(h, &r1, 1); return;
	case 1: h->sd_idle = 0; r1 = 0; sd_push(h, &r1, 1); return;
	case 8: { uint8_t r = 0x05; sd_push(h, &r, 1); return; }   /* v1 card: illegal command */
	case 55: h->sd_app = 1; sd_push(h, &r1, 1); return;
	case 58: { uint8_t r3[5] = {r1, 0x80, 0xff, 0x80, 0x00}; sd_push(h, r3, 5); return; }   /* OCR: powered up, 2.7-3.6 V, SDSC */
	case 59: sd_push(h, &r1, 1); return;
	case 16: h->sd_blocklen = arg; sd_push(h, &r1, 1); return;
	case 9: case 10: {
		uint8_t reg[16];
		memset(reg, 0, 16);
		if (cmd == 9) {
			/* CSD v1.0: READ_BL_LEN 9, C_SIZE / C_SIZE_MULT from the image size */
			uint64_t blocks = h->sd_size / 512;
			int mult = 7;   /* 2^(7+2) = 512 */
			uint32_t csize = (uint32_t) (blocks / 512) - 1;
			while (csize > 4095 && mult < 7) mult++;
			if (csize > 4095) csize = 4095;
			reg[0] = 0x00; reg[1] = 0x26; reg[2] = 0x00; reg[3] = 0x32; reg[4] = 0x5f;
			reg[5] = 0x59;   /* CCC high + READ_BL_LEN 9 */
			reg[6] = 0x80 | ((csize >> 10) & 3);
			reg[7] = (csize >> 2) & 0xff;
			reg[8] = ((csize & 3) << 6) | 0x2d;
			reg[9] = 0x80 | ((mult >> 1) & 3);
			reg[10] = ((mult & 1) << 7) | 0x7f;
			reg[11] = 0x80; reg[12] = 0x0a; reg[13] = 0x40; reg[14] = 0x00; reg[15] = 0x01;
		} else {
			memcpy(reg, "\x03SDSU01\x10\x00\x00\x00\x01\x00\x5a\x01", 16);
		}
		sd_push(h, &r1, 1);
		uint8_t t[2] = {0xff, 0xfe};
		sd_push(h, t, 2);
		sd_push(h, reg, 16);
		uint16_t c = crc16(reg, 16);
		uint8_t cc[2] = {c >> 8, c};
		sd_push(h, cc, 2);
		return;
	}
	case 12: h->sd_reading = 0; { uint8_t b[2] = {0xff, r1}; sd_push(h, b, 2); } return;
	case 13: { uint8_t r2[2] = {r1, 0}; sd_push(h, r2, 2); return; }
	case 17: sd_push(h, &r1, 1); sd_send_block(h, arg); return;
	case 18: sd_push(h, &r1, 1); h->sd_reading = 1; h->sd_rdaddr = arg; sd_send_block(h, arg); h->sd_rdaddr += 512; return;
	case 24: sd_push(h, &r1, 1); h->sd_writing = 1; h->sd_wraddr = arg; h->sd_wstate = 0; h->sd_wn = 0; return;
	case 25: sd_push(h, &r1, 1); h->sd_writing = 2; h->sd_wraddr = arg; h->sd_wstate = 0; h->sd_wn = 0; return;
	default: { uint8_t r = r1 | 4; sd_push(h, &r, 1); return; }
	}
}

static uint8_t sd_xfer(HW* h, uint8_t in) {
	uint8_t out = 0xff;
	if (h->sd_outh < h->sd_outn) {
		out = h->sd_out[h->sd_outh++];
		if (h->sd_outh == h->sd_outn) h->sd_outh = h->sd_outn = 0;
	} else if (h->sd_reading) {
		sd_send_block(h, h->sd_rdaddr);
		h->sd_rdaddr += 512;
		out = h->sd_out[h->sd_outh++];
	}
	/* data phase of a write */
	if (h->sd_writing && h->sd_cmdn == 0) {
		if (h->sd_wstate == 0) {
			if (in == 0xfe || in == 0xfc) { h->sd_wstate = 1; h->sd_wn = 0; return out; }
			if (in == 0xfd && h->sd_writing == 2) { h->sd_writing = 0; uint8_t b[2] = {0xff, 0x00}; sd_push(h, b, 2); return out; }
			if (in == 0xff) return out;
		} else if (h->sd_wstate == 1) {
			h->sd_wbuf[h->sd_wn++] = in;
			if (h->sd_wn == 514) {
				if (h->sd_file) {
					fseek(h->sd_file, h->sd_wraddr, SEEK_SET);
					fwrite(h->sd_wbuf, 1, 512, h->sd_file);
					fflush(h->sd_file);
				}
				if (h->event) h->event(h->event_ctx, 3, h->sd_wraddr, 512);
				h->sd_writes++;
				h->sd_wraddr += 512;
				uint8_t b[3] = {0x05, 0x00, 0xff};   /* data accepted, busy one byte */
				sd_push(h, b, 3);
				h->sd_wstate = 0;
				if (h->sd_writing == 1) h->sd_writing = 0;
			}
			return out;
		}
	}
	/* command parser */
	if (h->sd_cmdn == 0) {
		if ((in & 0xc0) == 0x40) { h->sd_cmd[h->sd_cmdn++] = in; }
	} else {
		h->sd_cmd[h->sd_cmdn++] = in;
		if (h->sd_cmdn == 6) {
			h->sd_cmdn = 0;
			if ((h->sd_cmd[0] & 0x3f) == 12) { h->sd_outh = h->sd_outn = 0; }
			sd_command(h);
		}
	}
	return out;
}


/* ---------------------------------------------------------------- DMA */
static void ac97_fifo(HW* h, uint32_t v);
static void dma_load(HW* h, int k) {
	uint32_t a = h->dma[k].cb;
	if (!a || a + 16 > h->ram_size) { h->dma[k].cb = 0; return; }
	const uint8_t* p = h->ram + a;
	#define W32(o) ((uint32_t) p[o] | (uint32_t) p[o + 1] << 8 | (uint32_t) p[o + 2] << 16 | (uint32_t) p[o + 3] << 24)
	h->dma[k].next = W32(0); h->dma[k].src = W32(4); h->dma[k].dst = W32(8); h->dma[k].ctrl = W32(12);
	#undef W32
	/* the length field counts 32-bit transfers, not bytes: py_fw0 0x13f46 builds an r10/2-word
	 * buffer and sets len = r10/2; music (py_fw1 0x321a0) hard-codes 1152 = one MPEG-1 L3 frame
	 * of stereo words; the silence CB has len 4 over a 16-byte zeroed buffer (0x3458e).
	 * Counting bytes made the DAC drain each buffer 4x too fast and the decoder ran free. */
	h->dma[k].len = (h->dma[k].ctrl & 0xfffff) * 4;
	h->dma[k].done = 0;
	h->dma_cbs++;
	if (h->dma[k].dst == 0x10004804 && (h->dma[k].ctrl & (1u << 24)) && h->mk && h->mk_n + 5 <= h->mk_cap) {
		/* FNV-1a of the buffer as it is when the DMA starts it: a different hash over the PCM that
		 * was actually played means the firmware rewrote the buffer while it was playing (overrun) */
		uint64_t f = 1469598103934665603ull;
		for (uint32_t i = 0; i < h->dma[k].len && h->dma[k].src + i < h->ram_size; i++) { f ^= h->ram[h->dma[k].src + i]; f *= 1099511628211ull; }
		h->mk[h->mk_n++] = h->pcm_n; h->mk[h->mk_n++] = h->dma[k].src;
		h->mk[h->mk_n++] = h->dma[k].len; h->mk[h->mk_n++] = h->cycles; h->mk[h->mk_n++] = f;
	}
}

static inline int dma_active(HW* h, int k) {
	return h->dma[k].cb && *R(h, 0x10000800 + 0x80 * k) != 0;
}

/* bytes per instruction slot for a channel (paced by its destination) */
static double dma_rate(HW* h, int k) {
	if (h->dma[k].dst == 0x10004804) return h->ac_rate * h->ac_scale * 4.0 / h->ips;
	return 1e9;   /* memory: effectively immediate */
}

static void dma_advance(HW* h, uint64_t slots) {
	for (int k = 0; k < 8; k++) {
		if (!dma_active(h, k)) continue;
		double avail = h->dma[k].frac + slots * dma_rate(h, k);
		int guard = 0;
		while (avail >= 4 && h->dma[k].cb && guard++ < 100000) {
			uint32_t left = h->dma[k].len > h->dma[k].done ? h->dma[k].len - h->dma[k].done : 0;
			uint32_t n = left < (uint32_t) avail ? left : ((uint32_t) avail & ~3u);
			for (uint32_t i = 0; i < n; i += 4) {
				uint32_t s = h->dma[k].src + h->dma[k].done + i;
				uint32_t v = s + 4 <= h->ram_size ? (uint32_t) h->ram[s] | h->ram[s + 1] << 8 | h->ram[s + 2] << 16 | (uint32_t) h->ram[s + 3] << 24 : 0;
				if (h->dma[k].dst == 0x10004804) { ac97_fifo(h, v); if (!(h->dma[k].ctrl & (1u << 24))) h->ac_silence++; }
				else {
					uint32_t d = h->dma[k].dst + h->dma[k].done + i;
					if (d + 4 <= h->ram_size) memcpy(h->ram + d, &v, 4);
				}
			}
			h->dma[k].done += n;
			h->dma[k].total += n;
			h->dma_bytes += n;
			avail -= n ? n : 4;
			if (h->dma[k].done >= h->dma[k].len) {
				if (h->dma[k].ctrl & (1u << 24)) { h->dma[k].ist |= 1; poll(h); }
				h->dma[k].cb = h->dma[k].next;
				dma_load(h, k);
			}
		}
		h->dma[k].frac = avail > 0 ? avail : 0;
		if (h->dma[k].frac > 64) h->dma[k].frac = 64;
	}
}

/* instruction slots until the next DMA completion interrupt (for idle skipping) */
static uint64_t dma_next(HW* h) {
	uint64_t best = ~0ull;
	for (int k = 0; k < 8; k++) {
		if (!dma_active(h, k)) continue;
		double r = dma_rate(h, k);
		uint32_t left = h->dma[k].len > h->dma[k].done ? h->dma[k].len - h->dma[k].done : 4;
		double s = left / r;
		uint64_t n = s < 1 ? 1 : (uint64_t) s + 1;
		if (n < best) best = n;
	}
	return best;
}

static uint32_t dma_rd(HW* h, int k, uint32_t off) {
	switch (off) {
	case 0x04: return h->dma[k].ist;
	case 0x20: return h->dma[k].next;
	case 0x24: return h->dma[k].total / 4;    /* transfers (words), like the CB length field */
	case 0x2c: return h->dma[k].len / 4;
	case 0x30: return h->dma[k].done / 4;
	case 0x34: return h->dma[k].cb;
	}
	return *R(h, 0x10000800 + 0x80 * k + off);
}

static void dma_wr(HW* h, int k, uint32_t off, uint32_t v) {
	switch (off) {
	case 0x04: h->dma[k].ist &= ~v; poll(h); return;
	case 0x34:
		h->dma[k].cb = v;
		if (v) dma_load(h, k);
		return;
	}
	*R(h, 0x10000800 + 0x80 * k + off) = v;
}

/* ---------------------------------------------------------------- AC97 */
/* WM9712 GPIO (regs 0x4C config, 0x4E polarity: 1 = active high, 0x50 sticky, 0x52 wake-up/IRQ enable,
 * 0x54 status).  A pin is active when its level equals its polarity bit; sticky pins latch activity
 * until a 0 is written to their 0x54 bit; non-sticky pins read live.  A newly latched wake-up-enabled
 * bit raises the codec's IRQ, wired to VC01 GPIO pin 3 (GPEDS bit 3 -> IRQ 43 -> py_fw1 ISR 0x20f4e,
 * which reads 0x54: bit 1 -> headphone state = polarity bit 1, flip polarity, record 0xC0000000 /
 * HP_JACK_STATUS_CHANGE; bit 11 with polarity 0 -> AC_ALERT 0xC0000001). */
static void ac_gpio_update(HW* h) {
	uint16_t pol = h->ac_reg[0x4e >> 1], sticky = h->ac_reg[0x50 >> 1], wake = h->ac_reg[0x52 >> 1];
	uint16_t active = (uint16_t) ~(h->ac_gpio_level ^ pol);
	uint16_t newl = (uint16_t) (active & sticky & ~h->ac_gpio_latch);
	h->ac_gpio_latch |= newl;
	/* the codec IRQ output is a level (any latched wake-up bit); VC01 GPIO pin 3 latches its rising edge */
	int lvl = (h->ac_gpio_latch & sticky & wake) != 0;
	if (lvl && !h->ac_gpio_irqline) { h->g_ist |= 8; h->ac_gpio_irqs++; poll(h); }
	h->ac_gpio_irqline = lvl;
	h->ac_reg[0x54 >> 1] = (uint16_t) ((h->ac_gpio_latch & sticky) | (active & ~sticky & 0x0fff));
}

static void ac97_op(HW* h) {
	uint32_t ctl = h->ac_ctrl;
	int reg = *R(h, 0x10004808) & 0x7e;
	if (ctl & 2) {   /* read */
		*R(h, 0x1000480c) = h->ac_reg[reg >> 1];
	} else {
		uint16_t v = *R(h, 0x1000480c) & 0xffff;
		if (reg == 0x26) v = (h->ac_reg[0x13] & 0x000f) | (v & 0xff00);   /* power down ctrl/status: ready bits read-only */
		else if (reg == 0x54) { h->ac_gpio_latch &= v; v = h->ac_reg[0x54 >> 1]; }   /* write 0 clears sticky bits */
		else if (reg == 0) { /* reset */ }
		else h->ac_reg[reg >> 1] = v;
		if (reg == 0x4c || reg == 0x4e || reg == 0x50 || reg == 0x52 || reg == 0x54) { ac_gpio_update(h); }
		if (reg == 0x2c) h->ac_rate = v ? v : 48000;
	}
	h->ac_ctrl |= 1u << 16;   /* done */
}

static void ac97_fifo(HW* h, uint32_t v) {
	if (h->pcm && h->pcm_n + 2 <= h->pcm_cap) {
		h->pcm[h->pcm_n++] = (int16_t) (v & 0xffff);
		h->pcm[h->pcm_n++] = (int16_t) (v >> 16);
	}
}

static void ac97_reset(HW* h) {
	memset(h->ac_reg, 0, sizeof(h->ac_reg));
	h->ac_reg[0x00 >> 1] = 0x0d40;   /* reset: capabilities */
	h->ac_reg[0x02 >> 1] = 0x8000;
	h->ac_reg[0x04 >> 1] = 0x8000;
	h->ac_reg[0x18 >> 1] = 0x8808;
	h->ac_reg[0x26 >> 1] = 0x000f;   /* ADC/DAC/ANL/REF ready */
	h->ac_reg[0x28 >> 1] = 0x0001;   /* extended audio id: VRA */
	h->ac_reg[0x2a >> 1] = 0x0000;
	h->ac_reg[0x2c >> 1] = 0xbb80;
	h->ac_reg[0x32 >> 1] = 0xbb80;
	h->ac_rate = 48000;
	if (h->ac_scale == 0) h->ac_scale = 1.0;
	/* WM9712 reg 0x54 GPIO pin status, read as live pin levels: bit 11 high = no alert (the Micro
	 * treats bit 11 LOW as AC_ALERT, posts 0xC0000001 and powers the codec down: mi_fw1 0x1d924 ->
	 * 0x1ee22 -> 0x33e4c); bit 1 = headphone jack (1 = plugged: no GBA speaker stream, py_fw1
	 * 0x215b2 / mi_fw1 0x1d8f6).  hw_set_hp_jack() sets bit 1. */
	h->ac_reg[0x4c >> 1] = 0xfffe;   /* WM9712 reset defaults: all inputs, active high, not sticky, no wake-up */
	h->ac_reg[0x4e >> 1] = 0xffff;
	h->ac_gpio_level = 0x0800 | (h->hp_jack ? 2 : 0);
	h->ac_gpio_latch = 0;
	h->ac_gpio_irqline = 0;
	ac_gpio_update(h);
	h->ac_reg[0x7c >> 1] = 0x574d;   /* vendor id 'WM' + 0x4c12 = Wolfson WM9712 (loader 0xc6284 requires it) */
	h->ac_reg[0x7e >> 1] = 0x4c12;
}

/* ---------------------------------------------------------------- MMIO */
static uint32_t hw_rd(void* ctx, uint32_t a, int n) {
	HW* h = ctx;
	uint32_t v = *R(h, a & ~3u);
	if ((a & ~0x3ffu) == 0x10000800) v = dma_rd(h, (a >> 7) & 7, a & 0x7c);
	else switch (a & ~3u) {
	case 0x10000c00: v |= 1; break;                       /* boot: bit 0 = ready */
	case 0x10001004: { int p; int irq = best_irq(h, &p); v = irq ? (uint32_t) (p << 8 | irq) : 0; break; }
	case 0x10001408: v |= 1u << 14; break;                /* PLL locked */
	case 0x10001800: v = h->tm_cs; break;
	case 0x10001804: v = (uint32_t) (uint64_t) h->tcount; break;
	case 0x10002400: v = 0; break;                        /* UART rx */
	case 0x10002414: v = 0x60; break;                     /* UART LSR: THRE | TEMT */
	case 0x10002800: v = h->g_out; break;
	case 0x10002810: v = (h->g_in & ~(1u << 4)) | (h->sd_present ? 0 : (1u << 4)); break;   /* bit 4: card detect, low = present */
	case 0x10002814: v = h->g_ist; break;
	case 0x10003c00: {
		v = h->spi_ctrl & 0xffff;
		v |= 1u << 16;                                    /* idle */
		if (h->spi_rxh != h->spi_rxt) v |= 1u << 17;      /* rx data available */
		break;
	}
	case 0x10003c04: {
		if (h->spi_rxh != h->spi_rxt) { v = h->spi_rx[h->spi_rxh]; h->spi_rxh = (h->spi_rxh + 1) & 63; }
		else v = 0xff;
		break;
	}
	case 0x10004800:
		/* bits 22/23: codec ready, set AC_READY_MS after ACCS bit 0 goes high.  The loader
		 * (0xcee0a) requires them clear right after enabling and through an 8192-poll wait (it
		 * then takes the 0xc873a path that checks the WM9712 id); the music image (0x34642)
		 * waits up to 65536 polls for them to be set before programming the DAC rate. */
		v = h->ac_ctrl & ~(3u << 22);
		if ((h->ac_ctrl & 1) && h->cycles >= h->ac_ready_at) v |= 3u << 22;
		break;
	}
	if (h->trace_mmio) fprintf(stderr, "  pc=%08x R%d %08x -> %08x\n", h->c->r[31], n, a, v);
	acc(h, a, 0, v);
	if (n == 1) return (v >> (8 * (a & 3))) & 0xff;
	if (n == 2) return (v >> (8 * (a & 2))) & 0xffff;
	return v;
}

static void hw_wr(void* ctx, uint32_t a, int n, uint32_t v) {
	HW* h = ctx;
	uint32_t a4 = a & ~3u;
	if (n != 4) {   /* sub-word write: merge */
		uint32_t old = *R(h, a4);
		int sh = 8 * (a & 3);
		uint32_t m = (n == 1 ? 0xffu : 0xffffu) << sh;
		v = (old & ~m) | ((v << sh) & m);
	}
	if (h->trace_mmio) fprintf(stderr, "  pc=%08x W%d %08x <- %08x\n", h->c->r[31], n, a, v);
	acc(h, a, 1, v);
	if ((a4 & ~0x3ffu) == 0x10000800) { dma_wr(h, (a4 >> 7) & 7, a4 & 0x7c, v); return; }
	switch (a4) {
	case 0x10001000: *R(h, a4) = v; poll(h); return;
	case 0x10001800: h->tm_cs &= ~v; return;
	case 0x10001808: case 0x1000180c: case 0x10001810: case 0x10001814: *R(h, a4) = v; return;
	case 0x10001010: case 0x10001014: case 0x10001018: case 0x1000101c: *R(h, a4) = v; poll(h); return;
	case 0x10001408: case 0x1000140c: case 0x10001410: case 0x10000408: case 0x1000040c:
		*R(h, a4) = v; update_clk(h); return;
	case 0x10002400:
		if (h->uartn < sizeof(h->uart) - 1) h->uart[h->uartn++] = v & 0xff;
		return;
	case 0x10002800: {
		uint32_t old = h->g_out; h->g_out = v; *R(h, a4) = v;
		if (!(old >> 30 & 1) && (v >> 30 & 1)) { h->host_irqs++; if (h->host_irq) h->host_irq(h->host_ctx, 1); }
		return;
	}
	case 0x10002808: {   /* GPIO set */
		uint32_t old = h->g_out; h->g_out |= v; *R(h, 0x10002800) = h->g_out;
		if (!(old >> 30 & 1) && (h->g_out >> 30 & 1)) { h->host_irqs++; if (h->host_irq) h->host_irq(h->host_ctx, 1); }
		return;
	}
	case 0x1000280c: h->g_out &= ~v; *R(h, 0x10002800) = h->g_out; return;   /* GPIO clear */
	case 0x10002814: h->g_ist &= ~v; poll(h); return;
	case 0x10002818: *R(h, a4) = v; poll(h); return;
	case 0x10003c00: h->spi_ctrl = v; *R(h, a4) = v;
		h->spi_rxh = h->spi_rxt;   /* a control write discards received bytes (the command sender never drains them) */
		return;
	case 0x10003c04: {
		uint8_t o = sd_xfer(h, v & 0xff);
		int nt = (h->spi_rxt + 1) & 63;
		if (nt != h->spi_rxh) { h->spi_rx[h->spi_rxt] = o; h->spi_rxt = nt; }
		return;
	}
	case 0x10004800: {
		uint32_t old = h->ac_ctrl;
		h->ac_ctrl = v & ~(1u << 16);
		/* codec-ready bits 22/23 behave as flags that a write storing them as 0 clears (re-armed: the
		 * codec reports ready again 3 ms later); read-modify-writes that copy them back keep them.
		 * Needed by images the loader downloads (FIRMWARE_DOWNLOAD jumps to 0x200 with the codec
		 * still running): fup_sec1 0x33b40 / py_fw1 0x34912 write ACCS = 1 and treat ready-at-once as
		 * a failed init -> VC_STARTUP_INDICATION(1).  Rising edge of bit 0 as before. */
		if (((v & 1) && !(old & 1)) || ((v & 1) && !(v & (3u << 22))))
			h->ac_ready_at = h->cycles + (uint64_t) (0.003 * h->ips);
		if ((v & 0x80) && !(old & 0x80)) ac97_op(h);
		else if (v & 0x80) ac97_op(h);
		if ((v & 1) && !(old & 1)) { /* cold reset release */ }
		return;
	}
	case 0x10004804:   /* ACFIFO: PCM out (one stereo s16 pair per word) */
		ac97_fifo(h, v);
		return;
	}
	*R(h, a4) = v;
}

/* ---------------------------------------------------------------- API */
HW* hw_new(uint8_t* ram, uint32_t ram_size) {
	HW* h = calloc(1, sizeof(HW));
	h->ram = ram;
	h->ram_size = ram_size;
	h->c = vc_new(ram, ram_size);
	h->c->ctx = h;
	h->c->mmio_rd = hw_rd;
	h->c->mmio_wr = hw_wr;
	h->c->irq = hw_irq;
	h->clk_hz = 20e6;
	h->ips = 50e6;
	h->log_on = 1;
	h->sd_blocklen = 512;
	ac97_reset(h);
	return h;
}

VC01* hw_cpu(HW* h) { return h->c; }

/* full chip reset (GBA CTRL bit 3 low): peripherals back to power-on state; RAM kept */
void hw_reset(HW* h, uint32_t pc) {
	VC01* c = h->c;
	FILE* f = h->sd_file; uint64_t sz = h->sd_size; int pres = h->sd_present;
	int16_t* pcm = h->pcm; uint64_t pn = h->pcm_n, pcap = h->pcm_cap;
	hw_host_irq_t hi = h->host_irq; void* hc = h->host_ctx; hw_event_t ev = h->event; void* ec = h->event_ctx;
	int running = h->running, inr = h->in_reset; uint64_t kicks = h->kicks, wakes = h->wakes;
	uint64_t cyc = h->cycles; uint32_t un = h->uartn; uint64_t hirq = h->host_irqs; double ips = h->ips, tc = h->tcount, acs = h->ac_scale;
	char* uart = malloc(un + 1); memcpy(uart, h->uart, un);
	AccLog* lg = malloc(sizeof(h->log)); memcpy(lg, h->log, sizeof(h->log)); int nl = h->nlog;
	uint8_t* ram = h->ram; uint32_t rs = h->ram_size;
	int hpj = h->hp_jack; uint64_t* mk = h->mk; uint64_t mkn = h->mk_n, mkc = h->mk_cap, gpi = h->ac_gpio_irqs;
	memset(h, 0, sizeof(*h));
	h->hp_jack = hpj; h->mk = mk; h->mk_n = mkn; h->mk_cap = mkc; h->ac_gpio_irqs = gpi;   /* board state, not chip state */
	h->c = c; h->ram = ram; h->ram_size = rs;
	h->sd_file = f; h->sd_size = sz; h->sd_present = pres;
	h->pcm = pcm; h->pcm_n = pn; h->pcm_cap = pcap;
	h->host_irq = hi; h->host_ctx = hc; h->event = ev; h->event_ctx = ec;
	h->cycles = cyc; h->uartn = un; memcpy(h->uart, uart, un); free(uart);
	memcpy(h->log, lg, sizeof(h->log)); h->nlog = nl; free(lg);
	h->host_irqs = hirq;
	h->ips = ips; h->tcount = tc; h->ac_scale = acs;
	h->running = running; h->in_reset = inr; h->kicks = kicks; h->wakes = wakes;
	h->clk_hz = 20e6;
	h->log_on = 1;
	h->sd_blocklen = 512;
	ac97_reset(h);
	vc_reset(c, pc);
	c->irq_poll = 0;
	h->last_icount = c->icount;
}

int hw_sd_open(HW* h, const char* path) {
	if (h->sd_file) fclose(h->sd_file);
	h->sd_file = NULL;
	h->sd_present = 0;
	if (!path || !*path) return 0;
	h->sd_file = fopen(path, "r+b");
	if (!h->sd_file) return -1;
	fseek(h->sd_file, 0, SEEK_END);
	h->sd_size = ftell(h->sd_file);
	h->sd_present = 1;
	return 0;
}

void hw_pcm_buffer(HW* h, int16_t* buf, uint64_t cap) { h->pcm = buf; h->pcm_cap = cap; h->pcm_n = 0; }
uint64_t hw_pcm_count(HW* h) { return h->pcm_n; }
void hw_pcm_marks(HW* h, uint64_t* buf, uint64_t cap) { h->mk = buf; h->mk_cap = cap; h->mk_n = 0; }
uint64_t hw_pcm_marks_n(HW* h) { return h->mk_n; }
uint64_t hw_ac_silence(HW* h) { return h->ac_silence; }
void hw_set_hp_jack(HW* h, int on) {
	h->hp_jack = on;
	h->ac_gpio_level = (uint16_t) ((h->ac_gpio_level & ~2) | (on ? 2 : 0));
	ac_gpio_update(h);
}
uint64_t hw_ac_gpio_irqs(HW* h) { return h->ac_gpio_irqs; }

/* host (GBA) side */
void hw_doorbell(HW* h) { h->g_ist |= 4; poll(h); }
void hw_set_host_irq(HW* h, hw_host_irq_t f, void* ctx) { h->host_irq = f; h->host_ctx = ctx; }
void hw_set_event(HW* h, hw_event_t f, void* ctx) { h->event = f; h->event_ctx = ctx; }
void hw_mmio_write(HW* h, uint32_t a, uint32_t v) { hw_wr(h, a, 4, v); }
uint32_t hw_mmio_read(HW* h, uint32_t a) { return hw_rd(h, a, 4); }

/* Run for `cycles` VC clock cycles (1 instruction = 1 cycle; idle time skipped to the next timer
 * event).  Returns the stop code (VC_RUN if the budget was used up). */
int hw_run(HW* h, uint64_t cycles) {
	VC01* c = h->c;
	uint64_t end = h->cycles + cycles;
	while (h->cycles < end) {
		uint64_t slice = end - h->cycles;
		uint64_t tn = timer_next(h);
		uint64_t dn = dma_next(h);
		if (dn < tn) tn = dn;
		if (tn < slice) slice = tn ? tn : 1;
		if (slice > 20000) slice = 20000;
		uint64_t n = vc_run(c, slice);
		advance(h, n);
		timer_check(h);
		if (c->stop == VC_STOP_WFI) {
			/* idle: jump to the next timer event (or the end of the budget) */
			uint64_t t2 = timer_next(h);
			uint64_t d2 = dma_next(h);
			if (d2 < t2) t2 = d2;
			uint64_t adv = end - h->cycles;
			if (t2 < adv) adv = t2;
			if (adv == 0) adv = 1;
			advance(h, adv);
			timer_check(h);
			poll(h);
			/* still idle and nothing pending: keep skipping */
			int p;
			if (!best_irq(h, &p) && h->cycles >= end) return VC_STOP_WFI;
			continue;
		}
		if (c->stop != VC_RUN && c->stop != VC_STOP_WFI) return c->stop;
	}
	return VC_RUN;
}

uint64_t hw_cycles(HW* h) { return h->cycles; }
double hw_clk(HW* h) { return h->clk_hz; }
const char* hw_uart(HW* h) { h->uart[h->uartn] = 0; return h->uart; }
uint32_t hw_uartn(HW* h) { return h->uartn; }
void hw_uart_clear(HW* h) { h->uartn = 0; }
int hw_nlog(HW* h) { return h->nlog; }
void hw_log(HW* h, int i, uint32_t* out) { out[0] = h->log[i].a; out[1] = h->log[i].w; out[2] = h->log[i].n; out[3] = h->log[i].pc; out[4] = h->log[i].last; }
void hw_log_clear(HW* h) { h->nlog = 0; }
void hw_trace_mmio(HW* h, int on) { h->trace_mmio = on; }
uint64_t hw_host_irqs(HW* h) { return h->host_irqs; }
void hw_stats(HW* h, uint64_t* out) {
	out[0] = h->sd_cmds; out[1] = h->sd_reads; out[2] = h->sd_writes; out[3] = h->host_irqs; out[4] = h->pcm_n;
	out[5] = h->c->nirq; out[6] = h->c->icount; out[7] = h->cycles; out[8] = h->dma_cbs; out[9] = h->dma_bytes; out[10] = (uint64_t) h->ac_rate; out[11] = h->ac_reg[0x2a >> 1]; out[12] = h->ac_reg[0x2c >> 1];
}
uint32_t* hw_regs(HW* h) { return h->reg; }
uint16_t* hw_ac97(HW* h) { return h->ac_reg; }
void hw_set_ips(HW* h, double ips) { h->ips = ips; }
double hw_tcount(HW* h) { return h->tcount; }

/* ---------------------------------------------------------------- GBA coupling (pyan.c LLE hooks) */
#define GBA_HZ 16777216.0
void hw_lle_run(HW* h, uint32_t gba_cycles) {
	if (!h->running || h->in_reset) return;
	double slots = gba_cycles * h->ips / GBA_HZ + h->slot_frac - h->slot_debt;
	h->slot_debt = 0;
	if (slots < 0) { h->slot_debt = -slots; h->slot_frac = 0; return; }
	uint64_t n = (uint64_t) slots;
	h->slot_frac = slots - n;
	int st = hw_run(h, n);
	if (st == VC_STOP_FAULT || st == VC_STOP_HALT) {
		h->last_stop = st;
		h->last_stop_pc = h->c->fault_pc;
		h->running = 0;   /* stop the chip; the harness reports it */
		if (h->event) h->event(h->event_ctx, 10, st, h->c->fault_pc);
	}
}

void hw_lle_reset(HW* h, int asserted) {
	h->in_reset = asserted;
	if (asserted) h->running = 0;
	if (h->event) h->event(h->event_ctx, 11, asserted, 0);
}

void hw_lle_doorbell(HW* h) {
	hw_doorbell(h);
	/* Service the doorbell now.  The real chip takes the GPIO interrupt within microseconds, and the
	 * GBA writes its next command without waiting for an answer (orig: 0x906 then 0xB00 ~1000 GBA
	 * cycles apart; Micro: 0x400 then 0xB00).  Run the core until its ISR has acknowledged the edge
	 * (GPEDS bit 2 cleared) plus a short tail for the command copy, at most 1 ms; the time is
	 * charged to the following slices so the chip stays in step with the GBA on average. */
	if (h->running && !h->in_reset) {
		uint64_t n = 0, cap = (uint64_t) (0.001 * h->ips), tail = 0;
		while (n < cap) {
			int st = hw_run(h, 256);
			n += 256;
			if (st == VC_STOP_FAULT || st == VC_STOP_HALT) break;
			if (!(h->g_ist & 4)) { if (++tail >= 8) break; }
		}
		h->slot_debt += (double) n;
		h->db_sync_slots += n;
		if (n > h->db_sync_max) h->db_sync_max = n;
	}
	if (h->event) h->event(h->event_ctx, 12, 0, 0);
}

/* host mode-0x70 write to VC 0x1xxxxxxx.  0xA5A50032 to 0x10000400 after an upload starts the core
 * at 0x200 (protocol.md 3.7); the same write while the core runs (after SLEEP) wakes it. */
void hw_lle_mmio_write(HW* h, uint32_t a, uint32_t v) {
	if (a == 0x10000400 && !h->running) {
		hw_reset(h, 0x200);
		h->running = 1;
		h->kicks++;
		*R(h, a) = v;
		if (h->event) h->event(h->event_ctx, 13, a, v);
		return;
	}
	if (a == 0x10000400) {
		h->wakes++;
		h->c->wfi = 0;
		poll(h);
	}
	hw_wr(h, a, 4, v);
	if (h->event) h->event(h->event_ctx, 14, a, v);
}

uint32_t hw_lle_mmio_read(HW* h, uint32_t a) { return hw_rd(h, a, 4); }
int hw_running(HW* h) { return h->running; }
void hw_set_ac_scale(HW* h, double s) { h->ac_scale = s; }
double hw_ac_rate(HW* h) { return h->ac_rate; }
