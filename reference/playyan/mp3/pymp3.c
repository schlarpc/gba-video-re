/* Play-Yan music firmware MP3 decoder: bit-exact C reference of VC01 py_fw1 0x222f2.
 * Addresses in comments are py_fw1. See docs/playyan/decoders.md for what each part is checked against.
 * MPEG-1 Layer III only (the player rejects every other format before the decoder). */
#include "pymp3.h"
#include "kernels.h"
#include "md5.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* ------------------------------------------------------------------ firmware layout */

typedef struct {
	const char* md5;
	uint32_t pow43, gain, dw, lin, slen1, slen2, pretab, iskl, iskr, alias_fn, ms_fn, cnt1a, cnt1b;
	uint32_t trees[32];
} fwlayout;

#define TREES_PY {0, 0x3b3d4, 0x3b3f0, 0x3b434, 0, 0x3b478, 0x3b4f4, 0x3b570, 0x3b68c, 0x3b7a8, 0x3b8c4, \
	0x3bac0, 0x3bcbc, 0x3beb8, 0, 0x3c6b4, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, 0x3abd8, \
	0x3abd8, 0x3a3dc, 0x3a3dc, 0x3a3dc, 0x3a3dc, 0x3a3dc, 0x3a3dc, 0x3a3dc, 0x3a3dc}
static const uint32_t trees_py[32] = TREES_PY;

/* py_fw1 addresses; the other images by section deltas (code -0x546/-0x600, data per section) */
static const struct { const char* md5; int32_t dcode, dgain, ddata1, dlin, dslen, dtree, dis; } IMGS[3] = {
	{"904dbf90365f8ac1cdfb507f04d0a302", 0, 0, 0, 0, 0, 0, 0},
	{"390ce952b1c269930628f2ca27ae3511", -0x546, -0x540, 0x4718, -0x13c0, 0x4718, 0x47b4, -0x538},
	{"3951130fb9c128bf3035852b51e2a732", -0x600, -0x600, 0x2f4, -0xbcc, 0x2f4, 0x2f4, -0x5f8},
};

/* md5 of each table region (identical in all three images unless noted) */

/* ------------------------------------------------------------------ standard tables (ISO 11172-3) */

static const int SFB_L[3][22] = {   /* widths, 44.1 / 48 / 32 kHz */
	{4, 4, 4, 4, 4, 4, 6, 6, 8, 8, 10, 12, 16, 20, 24, 28, 34, 42, 50, 54, 76, 158},
	{4, 4, 4, 4, 4, 4, 6, 6, 6, 8, 10, 12, 16, 18, 22, 28, 34, 40, 46, 54, 54, 192},
	{4, 4, 4, 4, 4, 4, 6, 6, 8, 10, 12, 16, 20, 24, 30, 38, 46, 56, 68, 84, 102, 26},
};
static const int SFB_S[3][13] = {
	{4, 4, 4, 4, 6, 8, 10, 12, 14, 18, 22, 30, 56},
	{4, 4, 4, 4, 6, 6, 10, 12, 14, 16, 20, 26, 66},
	{4, 4, 4, 4, 6, 8, 12, 16, 20, 26, 34, 42, 12},
};
static const int32_t BITRATE[16] = {999, 32000, 40000, 48000, 56000, 64000, 80000, 96000, 112000, 128000,
	160000, 192000, 224000, 256000, 320000, 999};
static const int32_t SRATE[4] = {44100, 48000, 32000, 999};

/* ------------------------------------------------------------------ decoder state */

#define RES_SIZE 2000
#define BITBUF 8000
#define GAIN_NEG 72

struct pymp3 {
	/* tables (from the firmware) */
	const uint8_t* img; size_t len;
	uint32_t pow43[8192];
	uint32_t gain[GAIN_NEG + 256];   /* gain[GAIN_NEG + i] = firmware word at 0x234a0 + 4i */
	int32_t dw[32][16];
	mp3consts kc;
	uint32_t tree[32], cnt1[2];
	int lin[32], slen1[16], slen2[16], pretab[22];
	uint32_t iskl[24], iskr[24];
	int32_t cs[8], ca[8];
	uint32_t ms;
	int strict;
	/* header / side info */
	int sri, nch, mode, mode_ext, prot, sr;
	int mdb, scfsi[2][4];
	int p23[2][2], bv[2][2], gg[2][2], sfc[2][2], ws[2][2], bt[2][2], mixed[2][2], ts[2][2][3], sbg[2][2][3];
	int r0c[2][2], r1c[2][2], pre[2][2], sfs[2][2], c1[2][2];
	int sf_l[2][2][22], sf_s[2][2][13][3];
	int16_t is[2][2][576 + 16];
	int32_t xr[2][2][592];
	/* main data reservoir + memory after it (sfb tables of the decoder struct) */
	uint8_t res[RES_SIZE + 216];
	uint32_t fill;
	int bp, bit, budget;
	/* bit-expanded Huffman buffer (gp+932, 8000 bytes, persists) */
	uint8_t bb[BITBUF];
	uint8_t bbw[BITBUF];       /* 1 = written at least once */
	int undefined;
	/* synthesis */
	int32_t ov[2][32][18];
	int32_t hist[2][16][34];
	unsigned slot[2];
};

static uint16_t rd16(const pymp3* d, uint32_t a) { return (uint16_t)(d->img[a] | d->img[a + 1] << 8); }
static uint32_t rd32(const pymp3* d, uint32_t a) { return (uint32_t)rd16(d, a) | (uint32_t)rd16(d, a + 2) << 16; }

static uint32_t immv(const pymp3* d, uint32_t enc_base, int32_t enc) {
	uint32_t a = enc_base + (uint32_t)(enc >> 1);
	if (enc & 1) { uint32_t v = rd16(d, a + 4) & 63; return (v & 32 ? v - 64 : v) & 0xffff; }
	return (rd16(d, a + 6) & 0xfc00u) | (rd16(d, a + 4) & 0x3ffu);
}


static void rdk(const pymp3* d, const kaddr_t a, mp3k_t* k) {
	uint32_t base = MP3_FUNC_BASE[a[0]];
	k->c2 = immv(d, base, a[1]); k->c1 = immv(d, base, a[2]);
	k->c0 = 0;
	if (a[3] >= 0) { k->c0 = (int32_t)immv(d, base, a[3]); if (k->c0 > 32767) k->c0 -= 65536; }
}

int pymp3_image_kind(const uint8_t* img, size_t len) {
	char h[33];
	md5_hex(img, len, h);
	for (int i = 0; i < 3; i++)
		if (!strcmp(h, IMGS[i].md5)) return i;
	return -1;
}


/* md5 of every constant read from the image, serialised as little-endian words in the order of
 * the Rust Tables::digest (pow43, gain[0..255], window, K, linbits, slen1, slen2, pretab, IS
 * ratios, alias cs/ca, MS), then the bytes of each Huffman tree. Same for all three images. */
#define TABLES_MD5 "60ea08718b1d692ed485aa3ae9a86ab6"

static uint32_t tree_size(const pymp3* d, uint32_t root) {
	uint32_t stack[4096], max = root; int sp = 0;
	enum { NSEEN = 0x100000 / 4 };
	uint8_t* seen = calloc(NSEEN, 1);
	if (!seen) return 0;
	stack[sp++] = root;
	while (sp) {
		uint32_t n = stack[--sp];
		if (n + 4 > d->len || n / 4 >= NSEEN || seen[n / 4]) continue;
		seen[n / 4] = 1;
		if (n > max) max = n;
		uint32_t h0 = rd16(d, n);
		if (h0 && sp < 4094) { stack[sp++] = n + 4 * h0; stack[sp++] = n + 4 * rd16(d, n + 2); }
	}
	free(seen);
	return max + 4 - root;
}

void pymp3_tables_md5(const pymp3* d, char out[33]) {
	size_t cap = 4 * (8192 + 256 + 512 + 3 * 256 + 32 + 16 + 16 + 22 + 48 + 16 + 1) + 65536, n = 0;
	uint8_t* v = malloc(cap);
#define W(x) do { uint32_t w_ = (uint32_t)(x); for (int b_ = 0; b_ < 4; b_++) v[n++] = (uint8_t)(w_ >> (8 * b_)); } while (0)
	for (int i = 0; i < 8192; i++) W(d->pow43[i]);
	for (int i = 0; i < 256; i++) W(d->gain[GAIN_NEG + i]);
	for (int k = 0; k < 32; k++) for (int j = 0; j < 16; j++) W(d->dw[k][j]);
	{   /* in the order of the Rust ImdctConsts::all, then 2cos(k pi/64) */
		const mp3consts* k = &d->kc;
		mp3k_t all[6 + 2 + 27 + 108 + 2 + 9 + 12 + 31]; int n2 = 0;
		for (int i = 0; i < 6; i++) all[n2++] = k->nine[i];
		all[n2++] = k->sqrt3; all[n2++] = k->half_sqrt2;
		for (int j = 0; j < 9; j++) all[n2++] = k->sec[j];
		for (int j = 0; j < 9; j++) all[n2++] = k->post_p[j];
		for (int j = 0; j < 9; j++) all[n2++] = k->post_q[j];
		for (int w = 0; w < 3; w++) for (int i = 0; i < 36; i++) all[n2++] = k->win[w][i];
		all[n2++] = k->s_half; all[n2++] = k->s_sqrt3_2;
		for (int i = 0; i < 3; i++) all[n2++] = k->s_sec[i];
		for (int i = 0; i < 3; i++) all[n2++] = k->s_post_p[i];
		for (int i = 0; i < 3; i++) all[n2++] = k->s_post_q[i];
		for (int i = 0; i < 12; i++) all[n2++] = k->s_win[i];
		for (int i = 1; i < 32; i++) all[n2++] = k->ck[i];
		for (int i = 0; i < n2; i++) { W(all[i].c0); W(all[i].c1); W(all[i].c2); }
	}
	for (int i = 0; i < 32; i++) W(d->lin[i]);
	for (int i = 0; i < 16; i++) W(d->slen1[i]);
	for (int i = 0; i < 16; i++) W(d->slen2[i]);
	for (int i = 0; i < 22; i++) W(d->pretab[i]);
	for (int i = 0; i < 24; i++) W(d->iskl[i]);
	for (int i = 0; i < 24; i++) W(d->iskr[i]);
	for (int i = 0; i < 8; i++) W(d->cs[i]);
	for (int i = 0; i < 8; i++) W(d->ca[i]);
	W(d->ms);
#undef W
	uint32_t roots[34]; int nr = 0;
	for (int t = 0; t < 32; t++) if (d->tree[t]) roots[nr++] = d->tree[t];
	roots[nr++] = d->cnt1[0]; roots[nr++] = d->cnt1[1];
	for (int i = 0; i < nr; i++) {
		if (i && roots[i] == roots[i - 1]) continue;
		uint32_t sz = tree_size(d, roots[i]);
		if (n + sz > cap) break;
		memcpy(v + n, d->img + roots[i], sz); n += sz;
	}
	md5_hex(v, n, out);
	free(v);
}

pymp3* pymp3_new(const uint8_t* img, size_t len, int kind) {
	if (kind < 0 || kind > 2 || len < 0x3e000) return NULL;
	pymp3* d = calloc(1, sizeof(pymp3));
	if (!d) return NULL;
	d->img = img; d->len = len; d->strict = 1;
	const int32_t dc = IMGS[kind].dcode, dg = IMGS[kind].dgain, d1 = IMGS[kind].ddata1, dl = IMGS[kind].dlin,
		ds = IMGS[kind].dslen, dt = IMGS[kind].dtree, di = IMGS[kind].dis;
	for (int i = 0; i < 8192; i++) d->pow43[i] = rd32(d, 0x244a0 + dg + 4 * i);
	for (int i = -GAIN_NEG; i < 256; i++) d->gain[GAIN_NEG + i] = rd32(d, (uint32_t)(0x234a0 + dg + 4 * i));
	/* synthesis window (0x3ee2c, 33 x 16 words), rows mapped as the window routine reads them */
	{
		uint32_t T = 0x3ee2c + d1;
		for (int k = 0; k < 32; k++)
			for (int n = 0; n < 16; n++) {
				int32_t v;
				if (k < 16) v = (int32_t)rd32(d, T + 4 * (16 * (k ? 2 * k - 1 : 0) + n));
				else if (k > 16) {
					v = (int32_t)rd32(d, T + 4 * (16 * (63 - 2 * k) + 15 - n));
					if (n & 1) v = (int32_t)(0u - (uint32_t)v);
				} else v = (int32_t)rd32(d, T + 4 * ((n & 1) ? 496 + n : (n <= 8 ? n : 16 - n)));
				d->dw[k][n] = v;
			}
	}
	{
		mp3consts* k = &d->kc;
		for (int i = 0; i < 6; i++) rdk(d, L_NINE[i], &k->nine[i]);
		rdk(d, L_SQRT3, &k->sqrt3); rdk(d, L_HALF_SQRT2, &k->half_sqrt2);
		for (int j = 0; j < 9; j++) {
			if (L_SEC[j][0] >= 0) rdk(d, L_SEC[j], &k->sec[j]);
			rdk(d, L_POST_P[j], &k->post_p[j]); rdk(d, L_POST_Q[j], &k->post_q[j]);
		}
		for (int w = 0; w < 3; w++)
			for (int i = 0; i < 36; i++) {
				k->unit[w][i] = L_WIN[w][i][0] < 0;
				if (!k->unit[w][i]) rdk(d, L_WIN[w][i], &k->win[w][i]);
			}
		rdk(d, S_HALF, &k->s_half); rdk(d, S_SQRT3_2, &k->s_sqrt3_2);
		for (int i = 0; i < 3; i++) { rdk(d, S_SEC[i], &k->s_sec[i]); rdk(d, S_POST_P[i], &k->s_post_p[i]); rdk(d, S_POST_Q[i], &k->s_post_q[i]); }
		for (int i = 0; i < 12; i++) rdk(d, S_WIN[i], &k->s_win[i]);
		for (int i = 0; i < 31; i++) rdk(d, D_COS[i], &k->ck[i + 1]);
	}
	for (int t = 0; t < 32; t++) d->tree[t] = trees_py[t] ? trees_py[t] + dt : 0;
	d->cnt1[0] = 0x3ceb0 + dt; d->cnt1[1] = 0x3cf2c + dt;
	for (int t = 0; t < 32; t++) d->lin[t] = (int)rd32(d, 0x3a034 + dl + 24 * t);
	for (int i = 0; i < 16; i++) { d->slen1[i] = (int)rd32(d, 0x3d1dc + ds + 4 * i); d->slen2[i] = (int)rd32(d, 0x3d21c + ds + 4 * i); }
	for (int i = 0; i < 22; i++) d->pretab[i] = (int)rd32(d, 0x3cfac + dt + 4 * i);
	for (int i = 0; i < 24; i++) { d->iskl[i] = rd32(d, 0x24178 + di + 4 * i); d->iskr[i] = rd32(d, 0x241d8 + di + 4 * i); }
	/* alias reduction constants: 16 x 'mov rX, #imm32' (48-bit, imm at +2) in 0x221ee */
	{
		static const uint32_t csa[8] = {0x221f8, 0x22200, 0x22208, 0x22210, 0x22218, 0x22220, 0x22228, 0x22230};
		static const uint32_t caa[8] = {0x2223a, 0x22242, 0x2224a, 0x22252, 0x2225a, 0x22262, 0x2226a, 0x22272};
		for (int i = 0; i < 8; i++) {
			d->cs[i] = (int32_t)rd32(d, (uint32_t)((int32_t)csa[i] + dc + 2));
			d->ca[i] = (int32_t)rd32(d, (uint32_t)((int32_t)caa[i] + dc + 2));
		}
		d->ms = rd32(d, (uint32_t)(0x221c0 + dc + 2));
	}
	{   /* all constants must hash to the value recorded when they were located */
		char h[33];
		pymp3_tables_md5(d, h);
		if (strcmp(h, TABLES_MD5)) { free(d); return NULL; }
	}
	return d;
}

void pymp3_free(pymp3* d) { free(d); }

/* ------------------------------------------------------------------ main-data bit access */

static int resb(pymp3* d, int p) {
	if (p < 0 || p >= RES_SIZE + 216) { d->undefined = 1; return 0; }
	return d->res[p];
}

/* 0x23a0c getbits (MSB first), decrements the budget */
static uint32_t getbits(pymp3* d, int n) {
	uint32_t v = 0;
	d->budget -= n;
	int left = 8 - d->bit;
	while (n > 0) {
		int take = n < left ? n : left;
		int b = resb(d, d->bp);
		v = v << take | (uint32_t)((b >> (left - take)) & ((1 << take) - 1));
		left -= take; n -= take;
		if (!left) { left = 8; d->bp++; }
	}
	d->bit = 8 - left;
	return v;
}

/* 0x239d4 getbit */
static int getbit(pymp3* d) {
	d->budget--;
	int b = resb(d, d->bp);
	int v;
	if (d->bit == 7) { v = b & 1; d->bit = 0; d->bp++; }
	else { v = (b >> (7 - d->bit)) & 1; d->bit++; }
	return v;
}

static int bbget(pymp3* d, int i) {
	if (i < 0 || i >= BITBUF) { d->undefined = 1; return 0; }
	if (!d->bbw[i] && d->strict) d->undefined = 1;
	return d->bb[i];
}
static void bbput(pymp3* d, int i, int v) {
	if (i < 0 || i >= BITBUF) { d->undefined = 1; return; }
	d->bb[i] = (uint8_t)v; d->bbw[i] = 1;
}

/* 0x35024: big-value Huffman pair; tree nodes are u16 pairs */
static void huff_pair(pymp3* d, int* p, uint32_t node, int* x, int* y) {
	if (!node) { *x = *y = 0; return; }
	for (;;) {
		uint32_t h0 = rd16(d, node);
		if (!h0) { *x = d->img[node + 2]; *y = d->img[node + 3]; return; }
		if (bbget(d, (*p)++) == 0) node += 4 * h0;
		else node += 4u * rd16(d, node + 2);
	}
}

/* 0x21010: count1 quadruple */
static void huff_quad(pymp3* d, int* p, uint32_t node, int* q) {
	for (;;) {
		uint32_t h0 = rd16(d, node);
		if (!h0) {
			uint32_t v = rd16(d, node + 2);
			q[0] = (int)(v >> 3 & 1); q[1] = (int)(v >> 2 & 1); q[2] = (int)(v >> 1 & 1); q[3] = (int)(v & 1);
			return;
		}
		if (bbget(d, (*p)++) == 0) node += 4 * h0;
		else node += 4u * rd16(d, node + 2);
	}
}

static int linval(pymp3* d, int* p, int x, int lb) {
	if (x == 15 && lb > 0) {
		int v = 0;
		for (int i = 0; i < lb; i++) v = (v << 1) + bbget(d, (*p)++);
		x = v + 15;
	}
	if (x && bbget(d, (*p)++)) x = -x;
	return x;
}

/* 0x2d22e: decode min(a, b) values as pairs */
static void huff_region(pymp3* d, int* p, int16_t* out, int tsel, int a, int b) {
	int n = a < b ? a : b;
	int lb = d->lin[tsel];
	uint32_t tree = d->tree[tsel];
	while (n > 0) {
		int x, y;
		huff_pair(d, p, tree, &x, &y);
		out[0] = (int16_t)x; out[1] = (int16_t)y;
		if (x) out[0] = (int16_t)linval(d, p, x, lb);   /* x == 0: no sign bit */
		if (y) out[1] = (int16_t)linval(d, p, y, lb);
		out += 2; n -= 2;
	}
}

/* ------------------------------------------------------------------ 0x22398: scalefactors + Huffman */

static int granule_ch(pymp3* d, int gr, int ch) {
	int slen1 = d->slen1[d->sfc[gr][ch]], slen2 = d->slen2[d->sfc[gr][ch]];
	d->budget = d->p23[gr][ch];
	if (d->ws[gr][ch] == 1 && d->bt[gr][ch] == 2) {
		if (d->mixed[gr][ch]) return -2;
		for (int s = 0; s < 6; s++) for (int w = 0; w < 3; w++) d->sf_s[gr][ch][s][w] = (int)getbits(d, slen1);
		for (int s = 6; s < 12; s++) for (int w = 0; w < 3; w++) d->sf_s[gr][ch][s][w] = (int)getbits(d, slen2);
		for (int w = 0; w < 3; w++) d->sf_s[gr][ch][12][w] = 0;
	} else {
		static const int band[5] = {0, 6, 11, 16, 21};
		for (int b = 0; b < 4; b++) {
			int sl = b < 2 ? slen1 : slen2;
			for (int s = band[b]; s < band[b + 1]; s++)
				d->sf_l[gr][ch][s] = (d->scfsi[ch][b] && gr) ? d->sf_l[0][ch][s] : (int)getbits(d, sl);
		}
		d->sf_l[gr][ch][21] = 0;
	}
	int part3 = d->budget;
	/* align the byte pointer to 16 bits with single-bit reads, expand the rest 16 bits at a time */
	int r8 = (d->bp & 1) ? 8 : 0;
	int r9 = d->bit > 0 ? 16 - r8 - d->bit : r8;
	int start = 16 - r9;
	for (int i = 0; i < r9; i++) bbput(d, start + i, getbit(d));
	int n = d->budget;
	if (n > 7968) return 0;   /* 0x22a2c: debug trap, granule not decoded */
	{
		int q = 16, left = n;
		int hp = d->bp & ~1;
		do {
			int h = resb(d, hp) | resb(d, hp + 1) << 8;
			for (int k = 0; k < 16; k++) bbput(d, q + k, (h >> ((k < 8) ? 7 - k : 23 - k)) & 1);
			q += 16; hp += 2; left -= 16;
		} while (left > 0);
	}
	d->bp += n >> 3; d->bit = n & 7;   /* the firmware sets the offset; the pointer was 16-bit aligned */
	d->budget -= n;
	int p = start, end = start + part3;
	int16_t* is = d->is[gr][ch];
	int pos;
	if (d->ws[gr][ch] == 1 && d->bt[gr][ch] == 2) {
		int r1 = d->sr == 8000 ? 72 : 36;  /* 8000 Hz is MPEG-2.5 only */
		for (pos = 0; pos < 2 * d->bv[gr][ch]; pos += 2) {
			int tsel = d->ts[gr][ch][pos < r1 ? 0 : 1];
			int x, y;
			huff_pair(d, &p, d->tree[tsel], &x, &y);
			int lb = d->lin[tsel];
			if (x) x = linval(d, &p, x, lb);
			if (y) y = linval(d, &p, y, lb);
			if (pos + 1 >= 576 + 16) { d->undefined = 1; break; }
			is[pos] = (int16_t)x; is[pos + 1] = (int16_t)y;
		}
	} else {
		int cum[22], c = 0;
		for (int s = 0; s < 22; s++) { c += SFB_L[d->sri][s]; cum[s] = c; }
		int a1 = d->r0c[gr][ch] < 21 ? d->r0c[gr][ch] : 21;
		int a2 = d->r0c[gr][ch] + d->r1c[gr][ch] + 1; if (a2 > 21) a2 = 21;
		int b1 = cum[a1], b2 = cum[a2], nb = 2 * d->bv[gr][ch];
		huff_region(d, &p, is, d->ts[gr][ch][0], b1, nb);
		huff_region(d, &p, is + b1, d->ts[gr][ch][1], b2 - b1, nb - b1);
		huff_region(d, &p, is + b2, d->ts[gr][ch][2], 576 - b2, nb - b2);
		pos = nb;
	}
	/* count1 region: one quadruple at least unless already at the end; the overrunning one is kept */
#ifdef PYMP3_DEBUG_COUNT1
	fprintf(stderr, "gr%d ch%d p23 %d part3 %d start %d p %d end %d pos %d bv %d\n", gr, ch, d->p23[gr][ch], part3, start, p, end, pos, d->bv[gr][ch]);
#endif
	if (p != end && pos < 574) {
		do {
			int q[4];
			huff_quad(d, &p, d->cnt1[d->c1[gr][ch]], q);
			for (int k = 0; k < 4; k++) if (q[k] && bbget(d, p++)) q[k] = -q[k];
			for (int k = 0; k < 4; k++) is[pos + k] = (int16_t)q[k];
			pos += 4;
		} while (p < end && pos < 574);   /* absolute addresses in the firmware: a signed compare of offsets */
	}
	for (; pos < 576; pos++) is[pos] = 0;
	return 1;
}

/* ------------------------------------------------------------------ 0x35058 requantiser */

static int32_t requant1(const pymp3* d, int x, uint32_t M) {
	int ax = x < 0 ? -x : x;
	uint32_t T = d->pow43[ax > 8191 ? 8191 : ax];
	int64_t TH = T >> 16, TL = T & 0xffff, M5 = (int16_t)(M >> 16), M3 = M & 0xffff;
	int64_t P0 = (TH * M5 << 16) + TH * M3 + TL * M5 + 2 * ((((M3 >> 1) * TL) + 0x8000) >> 16);
	int32_t P = (int32_t)(uint32_t)P0;
	int32_t p3 = P >> 3;
	uint32_t L3 = (uint32_t)p3 & 0xffff;
	int32_t H3 = p3 >> 16;
	uint32_t lo = (((L3 >> 1) + (L3 & 1)) | ((uint32_t)(H3 & 1) << 15)) & 0xffff;
	int32_t y = (int32_t)(((uint32_t)(H3 >> 1) << 16) | lo);
	return x < 0 ? (int32_t)(0u - (uint32_t)y) : y;
}

static void requant_block(const pymp3* d, int32_t* out, const int16_t* in, uint32_t M, int width) {
	int n = 16 * ((width + 15) >> 4);
	for (int i = 0; i < n; i++) out[i] = requant1(d, in[i], M);
}

static uint32_t gainw(pymp3* d, int i) {
	if (i < -GAIN_NEG) { d->undefined = 1; return 0; }
	return d->gain[GAIN_NEG + i];
}

static void requant(pymp3* d, int gr, int ch) {
	int mult = d->sfs[gr][ch] ? 4 : 2;
	int16_t* is = d->is[gr][ch];
	int32_t* xr = d->xr[gr][ch];
	int16_t tmp[576 + 64];
	/* the firmware reads past the 576 values into the next array; those outputs land in the
	 * buffer's 16-word padding and are never read */
	memcpy(tmp, is, 576 * 2); memset(tmp + 576, 0, 64 * 2);
	int pos = 0;
	if (d->bt[gr][ch] != 2) {
		for (int s = 0; s < 22; s++) {
			int w = SFB_L[d->sri][s];
			int i = d->gg[gr][ch] - mult * (d->sf_l[gr][ch][s] + d->pre[gr][ch] * d->pretab[s]);
			requant_block(d, xr + pos, tmp + pos, gainw(d, i), w);
			pos += w;
		}
	} else {
		for (int s = 0; s < 13; s++) {
			int w = SFB_S[d->sri][s];
			for (int win = 0; win < 3; win++) {
				int i = d->gg[gr][ch] - mult * d->sf_s[gr][ch][s][win] - 8 * d->sbg[gr][ch][win];
				requant_block(d, xr + pos, tmp + pos, i < 0 ? 0 : gainw(d, i), w);
				pos += w;
			}
		}
	}
}

/* ------------------------------------------------------------------ stereo */

static int32_t mulsu(int32_t a, uint32_t b) { return (int32_t)(((int64_t)a * (int64_t)b) >> 32); }
static int32_t mulss(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * (int64_t)b) >> 32); }

/* 0x221b8 */
static void ms(const pymp3* d, int32_t* l, int32_t* r, int n) {
	for (int i = 0; i < n; i++) {
		int32_t a = l[i], b = r[i];
		l[i] = mulsu((int32_t)((uint32_t)a + (uint32_t)b), d->ms);
		r[i] = mulsu((int32_t)((uint32_t)a - (uint32_t)b), d->ms);
	}
}

/* 0x2c4a0: 1 if the right channel has a nonzero line in [end - w, end), else intensity-code it */
static int is_band(const pymp3* d, int32_t* l, int32_t* r, int ispos, int end, int w) {
	for (int i = end - w; i < end; i++) if (r[i]) return 1;
	if (ispos == 7 || ispos == 15 || ispos == 23 || ispos < 0) return 0;
	uint32_t kl = d->iskl[ispos], kr = d->iskr[ispos];
	for (int i = end - w; i < end; i++) {
		int32_t v = l[i];
		l[i] = mulsu(v, kl); r[i] = mulsu(v, kr);
	}
	return 0;
}

/* 0x21ef6 */
static void intensity(pymp3* d, int gr, int msf) {
	int32_t* l = d->xr[gr][0], * r = d->xr[gr][1];
	if (d->bt[gr][0] != 2) {
		int end = 576;
		for (int s = 21; s >= 0; s--) {
			int w = SFB_L[d->sri][s], ip = d->sf_l[gr][1][s];
			if (is_band(d, l, r, ip, end, w)) { if (msf) ms(d, l, r, end); return; }
			end -= w;
			if (msf && ip == 7) ms(d, l + end, r + end, w);
		}
		return;
	}
	for (int win = 2; win >= 0; win--) {
		int end = 576;
		for (int s = 12; s >= 0; s--) {
			int w = SFB_S[d->sri][s], ip = d->sf_s[gr][1][s][win];
			end -= (2 - win) * w;
			if (is_band(d, l, r, ip, end, w)) {
				if (!msf) break;
				ms(d, l + end - w, r + end - w, w);
				end -= (win + 1) * w;
				for (s--; s >= 0; s--) {
					w = SFB_S[d->sri][s];
					end -= w * (2 - win);
					ms(d, l + end - w, r + end - w, w);
					end -= w * (win + 1);
				}
				break;
			}
			if (msf && ip == 7) ms(d, l + end - w, r + end - w, w);
			end -= (win + 1) * w;
		}
	}
}

/* ------------------------------------------------------------------ reorder, alias */

/* 0x2c52a (non-mixed): band/window order -> subband x 18 (window * 6 + k) */
static void reorder(pymp3* d, int gr, int ch) {
	int32_t tmp[576];
	int32_t* xr = d->xr[gr][ch];
	memcpy(tmp, xr, sizeof tmp);
	int src = 0, f0 = 0;
	for (int s = 0; s < 13; s++) {
		int w = SFB_S[d->sri][s];
		for (int win = 0; win < 3; win++)
			for (int k = 0; k < w; k++) {
				int idx = k + f0;
				xr[(idx / 6) * 18 + idx % 6 + 6 * win] = tmp[src++];
			}
		f0 += w;
	}
}

/* 0x221ee */
static void alias(const pymp3* d, int32_t* xr, int count) {
	for (int i = 0; i < 8; i++)
		for (int sb = 1; sb < count; sb++) {
			int32_t a = xr[18 * sb - 1 - i], b = xr[18 * sb + i];
			int32_t lo = (int32_t)((uint32_t)(mulss(a, d->cs[i]) - mulss(b, d->ca[i])) << 1);
			int32_t hi = (int32_t)((uint32_t)(mulss(b, d->cs[i]) + mulss(a, d->ca[i])) << 1);
			xr[18 * sb - 1 - i] = lo; xr[18 * sb + i] = hi;
		}
}

/* ------------------------------------------------------------------ synthesis */

static int16_t synth_dot(const int32_t* xs, const int32_t* ds) {
	int64_t S = 0;
	for (int n = 0; n < 16; n++) {
		int64_t xh = xs[n] >> 16, xl = xs[n] & 0xffff, dh = ds[n] >> 16, dl = ds[n] & 0xffff;
		S += xh * dh + ((xh * dl + 0x8000) >> 16) + ((dh * xl + 0x8000) >> 16);
	}
	int32_t v = (int32_t)(uint32_t)(S + 128) >> 8;
	return (int16_t)(v < -32768 ? -32768 : v > 32767 ? 32767 : v);
}

/* one 32-sample output slot of channel ch from its dct32 rows */
static void synth_slot(pymp3* d, int ch, const int32_t* row, int16_t* out) {
	unsigned t = d->slot[ch]++;
	memcpy(d->hist[ch][t & 15], row, 34 * sizeof(int32_t));
	for (int k = 0; k < 32; k++) {
		int32_t xs[16];
		for (int n = 0; n < 16; n++) {
			const int32_t* h = d->hist[ch][(t - (unsigned)n) & 15];
			int r;
			if (k < 16) r = (n & 1) ? 17 + k : k;
			else if (k == 16) r = (n & 1) ? 33 : -1;
			else r = (n & 1) ? 49 - k : 32 - k;
			xs[n] = r < 0 ? 0 : h[r];
		}
		out[2 * k] = synth_dot(xs, d->dw[k]);
	}
}

/* 0x213b0 + per-slot reorder/dct32/window for one granule and channel */
static void imdct_synth(pymp3* d, int gr, int ch, int16_t* pcm) {
	int32_t out[18][32];
	int32_t* xr = d->xr[gr][ch];
	for (int sb = 0; sb < 32; sb++) {
		int32_t O[18], V[18];
		int bt = d->bt[gr][ch];
		if (bt == 2) mp3_imdct_short(xr + 18 * sb, d->ov[ch][sb], O, V, &d->kc);
		else mp3_imdct_long(xr + 18 * sb, d->ov[ch][sb], O, V, bt == 1 ? 1 : bt == 3 ? 2 : 0, &d->kc);
		for (int k = 0; k < 18; k++) { out[k][sb] = O[k]; d->ov[ch][sb][k] = V[k]; }
	}
	for (int s = 0; s < 18; s++) {
		int32_t x[32], row[34];
		for (int sb = 0; sb < 32; sb++) x[sb] = ((s & 1) && (sb & 1)) ? (int32_t)(0u - (uint32_t)out[s][sb]) : out[s][sb];
		mp3_dct32(x, row, &d->kc);
		synth_slot(d, ch, row, pcm + (gr * 18 + s) * 64 + ch);
	}
}

/* ------------------------------------------------------------------ frame */

typedef struct { const uint8_t* p; size_t n; size_t pos; } fbits;
static uint32_t fget(fbits* b, int n) {
	uint32_t v = 0;
	while (n--) {
		size_t i = b->pos >> 3;
		int bit = i < b->n ? (b->p[i] >> (7 - (b->pos & 7))) & 1 : 0;
		v = v << 1 | (uint32_t)bit; b->pos++;
	}
	return v;
}

int pymp3_decode_frame(pymp3* d, const uint8_t* fr, size_t avail, int16_t* pcm, int* consumed) {
	fbits b = {fr, avail, 0};
	*consumed = 0;
	d->undefined = 0;
	uint32_t sync = fget(&b, 12);
	if (sync != 0xfff) return sync == 0xffe ? PYMP3_UNSUPPORTED : PYMP3_BADHDR;
	int id = (int)fget(&b, 1), layer = (int)fget(&b, 2);
	d->prot = (int)fget(&b, 1);
	int bri = (int)fget(&b, 4), sri = (int)fget(&b, 2), pad = (int)fget(&b, 1);
	fget(&b, 1);
	d->mode = (int)fget(&b, 2); d->mode_ext = (int)fget(&b, 2);
	fget(&b, 4);
	if (layer != 1) return PYMP3_BADHDR;
	if (!id) return PYMP3_UNSUPPORTED;
	int32_t br = BITRATE[bri], sr = SRATE[sri];
	if (br == 999 || sr == 999) return PYMP3_BADHDR;
	if (!d->prot) fget(&b, 16);
	d->nch = d->mode == 3 ? 1 : 2;
	d->sri = sri; d->sr = sr;
	int32_t bits = (int32_t)((int64_t)2 * 576 * br / sr) + 8 * pad;
	int fbytes = bits >> 3;
	*consumed = fbytes;
	/* side info (0x23b62) */
	d->mdb = (int)fget(&b, 9);
	fget(&b, d->nch == 1 ? 5 : 3);
	for (int ch = 0; ch < d->nch; ch++) for (int k = 0; k < 4; k++) d->scfsi[ch][k] = (int)fget(&b, 1);
	for (int gr = 0; gr < 2; gr++)
		for (int ch = 0; ch < d->nch; ch++) {
			d->p23[gr][ch] = (int)fget(&b, 12);
			int bv = (int)fget(&b, 9);
			d->bv[gr][ch] = bv > 288 ? 288 : bv;
			d->gg[gr][ch] = (int)fget(&b, 8);
			d->sfc[gr][ch] = (int)fget(&b, 4);
			d->ws[gr][ch] = (int)fget(&b, 1);
			if (d->ws[gr][ch]) {
				d->bt[gr][ch] = (int)fget(&b, 2);
				d->mixed[gr][ch] = (int)fget(&b, 1);
				d->ts[gr][ch][0] = (int)fget(&b, 5); d->ts[gr][ch][1] = (int)fget(&b, 5); d->ts[gr][ch][2] = 0;
				for (int w = 0; w < 3; w++) d->sbg[gr][ch][w] = (int)fget(&b, 3);
				d->r0c[gr][ch] = (d->bt[gr][ch] == 2 && d->mixed[gr][ch]) ? 8 : 7;
				d->r1c[gr][ch] = 36;
			} else {
				d->bt[gr][ch] = 0;
				for (int k = 0; k < 3; k++) d->ts[gr][ch][k] = (int)fget(&b, 5);
				d->r0c[gr][ch] = (int)fget(&b, 4); d->r1c[gr][ch] = (int)fget(&b, 3);
			}
			d->pre[gr][ch] = (int)fget(&b, 1); d->sfs[gr][ch] = (int)fget(&b, 1); d->c1[gr][ch] = (int)fget(&b, 1);
		}
	/* main data reservoir (0x22f88) */
	int sil = d->nch == 1 ? 21 : 36;
	if (!d->prot) sil += 2;
	int n = fbytes - sil;
	if (n + d->mdb > RES_SIZE) return PYMP3_BADHDR;
	if ((size_t)fbytes > avail || n < 0) return PYMP3_UNDEFINED;
	memmove(d->res + RES_SIZE - n - d->mdb, d->res + RES_SIZE - d->mdb, (size_t)d->mdb);
	memcpy(d->res + RES_SIZE - n, fr + sil, (size_t)n);
	{   /* the decoder struct after the reservoir: long sfb widths, their running sums, short widths */
		int32_t t[57]; int c = 0;
		for (int s = 0; s < 22; s++) { t[s] = SFB_L[sri][s]; c += t[s]; t[22 + s] = c; }
		for (int s = 0; s < 13; s++) t[44 + s] = SFB_S[sri][s];
		for (int i = 0; i < 54 && 4 * i + 3 < 216; i++)
			for (int k = 0; k < 4; k++) d->res[RES_SIZE + 4 * i + k] = (uint8_t)((uint32_t)t[i] >> (8 * k));
	}
	d->bp = RES_SIZE - n - d->mdb; d->bit = 0;
	uint32_t old = d->fill;
	d->fill += (uint32_t)n;
	if (old < (uint32_t)d->mdb) { memset(pcm, 0, 2304 * sizeof(int16_t)); return PYMP3_SILENCE; }
	for (int gr = 0; gr < 2; gr++)
		for (int ch = 0; ch < d->nch; ch++) {
			if (d->ws[gr][ch] && d->bt[gr][ch] == 2 && d->mixed[gr][ch]) return PYMP3_UNSUPPORTED;
			granule_ch(d, gr, ch);
		}
	for (int gr = 0; gr < 2; gr++)
		for (int ch = 0; ch < d->nch; ch++) requant(d, gr, ch);
	if (d->mode == 1)
		for (int gr = 0; gr < 2; gr++) {
			switch (d->mode_ext) {
			case 1: intensity(d, gr, 0); break;
			case 2: ms(d, d->xr[gr][0], d->xr[gr][1], 576); break;
			case 3: intensity(d, gr, 1); break;
			default: break;
			}
		}
	for (int gr = 0; gr < 2; gr++)
		for (int ch = 0; ch < d->nch; ch++) {
			if (d->bt[gr][ch] == 2) reorder(d, gr, ch);
			else alias(d, d->xr[gr][ch], 32);
		}
	for (int gr = 0; gr < 2; gr++)
		for (int ch = 0; ch < d->nch; ch++) imdct_synth(d, gr, ch, pcm);
	if (d->nch == 1)
		for (int i = 0; i < 1152; i++) pcm[2 * i + 1] = pcm[2 * i];
	return d->undefined ? PYMP3_UNDEFINED : PYMP3_OK;
}

/* ------------------------------------------------------------------ 0x3185e GBA 8-bit stream */

void pygba_init(pygba* g, int rate, int vol) { memset(g, 0, sizeof *g); g->rate = rate; g->vol = vol; }

static int pyvol(const pygba* g, int s) {
	int p = s * g->vol;
	p = (p + (p < 0 ? 31 : 0)) >> 5;
	return p < -32768 ? -32768 : p > 32767 ? 32767 : p;
}

static void pyemit(pygba* g, int l, int r, void (*emit)(void*, const int8_t*, const int8_t*), void* user) {
	g->a[g->n] = (int8_t)(r >> 8); g->b[g->n] = (int8_t)(l >> 8);
	if (++g->n == 576) { emit(user, g->a, g->b); g->n = 0; }
}

void pygba_push(pygba* g, const int16_t* lr, int n, void (*emit)(void*, const int8_t*, const int8_t*), void* user) {
	for (int i = 0; i < n; i++) {
		int l = pyvol(g, lr[2 * i]), r = pyvol(g, lr[2 * i + 1]);
		int sh = g->rate > 16384 ? 14 : 13;
		long long q = ((long long)g->c << sh) / g->rate;
		int v = (int)(q < 0 ? -q : q);
		g->c++;
		if (g->c > g->rate) g->c = 0;
		if (g->rate > 16384) {
			if (v != g->last) { g->last = v; pyemit(g, l, r, emit, user); }
		} else {
			pyemit(g, l, r, emit, user);
			int dup = g->last + 1 != v;
			g->last = v;
			if (dup) pyemit(g, l, r, emit, user);
		}
	}
}

/* strict = 1 (default): reading bits the firmware never wrote (heap contents) is reported as
 * PYMP3_UNDEFINED; 0: they read as 0, like a zeroed heap (the emulator harness). */
void pymp3_set_strict(pymp3* d, int strict) { d->strict = strict; }

/* debugging access for the differential tools: 0 = is[2][2][592] int16, 1 = xr[2][2][592] int32,
 * 2 = ov[2][32][18] int32, 3 = hist, 4 = sf_l[2][2][22] int */
void* pymp3_debug(pymp3* d, int which) {
	switch (which) {
	case 0: return d->is;
	case 1: return d->xr;
	case 2: return d->ov;
	case 3: return d->hist;
	case 4: return d->sf_l;
	default: return NULL;
	}
}
