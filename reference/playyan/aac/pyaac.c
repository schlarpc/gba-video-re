/* Play-Yan AAC-LC decoder: the FAAD2-derived decoder of the Play-Yan Micro / playan.fup video
 * firmware, reimplemented bit-exactly (fixed-point arithmetic, table values and quirks as the
 * VC01 code has them).  Non-standard tables are read from the firmware image; the Huffman
 * codebooks (hcb.h) are the ISO ones.  Addresses below are mi_fw0 VC addresses. */
#include "pyaac.h"
#include "hcb.h"
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ firmware table addresses */
typedef struct {
	uint32_t gp;
	uint32_t sf_pow2;      /* 4 x u32: 2^(k/4) * 2^31 (apply_scalefactors 0x88e4) */
	uint32_t cbrt;         /* 1280 x u16: round(4096 n^(1/3)) (iquant 0x885c) */
	uint32_t is_pow2;      /* 4 x s16 (IS gain 0x4260) */
	uint32_t pns_pow2;     /* 4 x s16 (PNS 0x407c) */
	uint32_t tns_coef[4];  /* int32 tables by (compress & 1) + ((res_bits >> 1) & 2) */
	uint32_t tns_max_sfb;  /* s16 [sf][6] */
	uint32_t win[8];       /* long/short window pointers: Lcur[2] Scur[2] Lrev[2] Srev[2] (by window_shape) */
	uint32_t imdct_long_c, imdct_long_s, imdct_short_c, imdct_short_s;
	uint32_t fft_tw4, fft_tw8, tw_b1, tw_b2, tw_b4, tw_b8, tw_b16;
	/* gp-relative */
	uint32_t num_swb_long, num_swb_short;     /* u8 [12] */
	uint32_t swb_long, swb_short;             /* u32 ptr [12] -> s16 [] */
	uint32_t pns_norm_long, pns_norm_short;   /* u32 ptr [12] -> s32 [] */
	uint32_t sample_rates;                    /* u32 [12+] */
	uint32_t rng;                             /* u32 PNS LCG state */
} fwaddr;

static const fwaddr MI_FW0 = {
	.gp = 0x438c8,
	.sf_pow2 = 0x8938, .cbrt = 0x8948, .is_pow2 = 0x438b0, .pns_pow2 = 0x438b8,
	.tns_coef = {0x4369c, 0x4368c, 0x436dc, 0x436bc}, .tns_max_sfb = 0x437fc,
	.win = {0x4d40, 0x4440, 0x5540, 0x4c40, 0x5f40, 0x5640, 0x6740, 0x5e40},
	.imdct_long_c = 0xa180, .imdct_long_s = 0x9d80, .imdct_short_c = 0xb0a0, .imdct_short_s = 0xb020,
	.fft_tw4 = 0xa9c0, .fft_tw8 = 0xaa40, .tw_b1 = 0xbd80, .tw_b2 = 0xbe00, .tw_b4 = 0xbf00, .tw_b8 = 0xb920, .tw_b16 = 0xb120,
	.num_swb_long = 0x438c8 + 0x5290, .num_swb_short = 0x438c8 + 0x529c,
	.swb_long = 0x438c8 + 0x5d7c, .swb_short = 0x438c8 + 0x5e64,
	.pns_norm_long = 0x438c8 + 0x583c, .pns_norm_short = 0x438c8 + 0x59b8,
	.sample_rates = 0x438c8 + 0x5a08, .rng = 0x438c8 + 756,
};

static const fwaddr FUP_SEC0 = {   /* same code, data moved: located by code signatures */
	.gp = 0x43c40,
	.sf_pow2 = 0x8938, .cbrt = 0x8948, .is_pow2 = 0x43c28, .pns_pow2 = 0x43c30,
	.tns_coef = {0x43a14, 0x43a04, 0x43a54, 0x43a34}, .tns_max_sfb = 0x43b74,
	.win = {0x4d40, 0x4440, 0x5540, 0x4c40, 0x5f40, 0x5640, 0x6740, 0x5e40},
	.imdct_long_c = 0xa180, .imdct_long_s = 0x9d80, .imdct_short_c = 0xb0a0, .imdct_short_s = 0xb020,
	.fft_tw4 = 0xa9c0, .fft_tw8 = 0xaa40, .tw_b1 = 0xbd80, .tw_b2 = 0xbe00, .tw_b4 = 0xbf00, .tw_b8 = 0xb920, .tw_b16 = 0xb120,
	.num_swb_long = 0x43c40 + 0x629c, .num_swb_short = 0x43c40 + 0x62a8,
	.swb_long = 0x43c40 + 0x6e24, .swb_short = 0x43c40 + 0x6f0c,
	.pns_norm_long = 0x4a488, .pns_norm_short = 0x4a604,
	.sample_rates = 0x43c40 + 0x6ab0, .rng = 0x43c40 + 996,
};

/* ------------------------------------------------------------------ structures
 * The per-channel ic_stream lives in the firmware's own byte layout (9272 = 0x2438 bytes, two per
 * element), read with the firmware's own load widths and signedness: the syntax has no bounds
 * checks (section counts, sfb_cb), so corrupt streams write across fields and into the second
 * ic_stream exactly as on the cartridge. */
#define ICS_SZ 9272
enum {
	O_MS_USED = 0,         /* s8 [8][51] */
	O_MAX_SFB = 408, O_NUM_SWB = 409, O_NUM_GROUPS = 410, O_NUM_WINDOWS = 411, O_WSEQ = 412,
	O_GROUP_LEN = 413,     /* [8] */
	O_WSHAPE = 421, O_SFGROUP = 422,
	O_SECT_SFB_OFF = 424,  /* s16 [8][120] */
	O_SWB_OFF = 2344,      /* s16 [52] */
	O_SECT_CB = 2448,      /* u8 [8][120] */
	O_SECT_START = 3408,   /* u8 [8][120] */
	O_SECT_END = 4368,     /* s16 [8][120] */
	O_SFB_CB = 6288,       /* u8 [8][120] */
	O_NUM_SEC = 7248,      /* s16 [8] */
	O_GLOBAL_GAIN = 7264, O_MS_MASK = 7266,
	O_PULSE = 7268, O_TNS = 7269, O_GAIN = 7270, O_PRED = 7271,
	O_SF = 7272,           /* s16 [8][51] */
	O_PNS_NORM = 8088,     /* u32 VC address */
	O_PULSE_DATA = 8092,   /* number_pulse, start_sfb, offset[4], amp[4] */
	O_TNS_DATA = 8102,     /* n_filt[8] coef_res[8] length[8][4] order direction compress coef[8][4][32] */
};
#ifndef PYAAC_LD11_UNSIGNED
#define LD11(p, o) ((int) (int8_t) (p)[o])     /* 48-bit '...11' byte load: signed (see notes) */
#else
#define LD11(p, o) ((int) (p)[o])
#endif
#define U8(p, o) ((int) (p)[o])
#define S8(p, o) ((int) (int8_t) (p)[o])
static inline int S16(const uint8_t* p, int o) { return (int16_t) (p[o] | p[o + 1] << 8); }
static inline void W16(uint8_t* p, int o, int v) { p[o] = v; p[o + 1] = v >> 8; }
static inline uint32_t U32(const uint8_t* p, int o) { return p[o] | p[o + 1] << 8 | p[o + 2] << 16 | (uint32_t) p[o + 3] << 24; }
static inline void W32le(uint8_t* p, int o, uint32_t v) { p[o] = v; p[o + 1] = v >> 8; p[o + 2] = v >> 16; p[o + 3] = v >> 24; }
#define T_NFILT(w) (O_TNS_DATA + (w))
#define T_RES(w) (O_TNS_DATA + 8 + (w))
#define T_LEN(w, f) (O_TNS_DATA + 16 + 4 * (w) + (f))
#define T_ORDER(w, f) (O_TNS_DATA + 48 + 4 * (w) + (f))
#define T_DIR(w, f) (O_TNS_DATA + 80 + 4 * (w) + (f))
#define T_COMP(w, f) (O_TNS_DATA + 112 + 4 * (w) + (f))
#define T_COEF(w, f, i) (O_TNS_DATA + 144 + 128 * (w) + 32 * (f) + (i))

struct pyaac {
	const uint8_t* img; size_t len;
	fwaddr A;
	int sf_index, object_type, channel_config, sample_rate;
	uint32_t rng;
	uint32_t frame_count;
	int32_t wshape_prev[2];      /* decoder + 0x411c: the window_shape as loaded (word) */
	int sce_seen, sce_tag;       /* decoder + 0x40f8 / 0x40fa: tag of the first SCE ever decoded */
	int undefined;
	int32_t time[2][2048];     /* per output channel: [output 1024 | overlap 1024] */
	uint8_t elem[2 * ICS_SZ + 12];   /* work + 0x1000: the element (two ic_streams), cleared per frame */
	uint8_t* ics[2];
	int err;
	int32_t spec[2][1024];
};

/* ------------------------------------------------------------------ image access */
static uint32_t rd8(const pyaac* d, uint32_t a) { return a < d->len ? d->img[a] : 0; }
static uint32_t rd16(const pyaac* d, uint32_t a) { return rd8(d, a) | rd8(d, a + 1) << 8; }
static int32_t rds16(const pyaac* d, uint32_t a) { return (int16_t) rd16(d, a); }
static uint32_t rd32(const pyaac* d, uint32_t a) { return rd16(d, a) | rd16(d, a + 2) << 16; }

/* ------------------------------------------------------------------ arithmetic helpers (VC01 semantics) */
static inline int32_t W32(int64_t x) { return (int32_t) (uint32_t) (uint64_t) x; }
static inline int32_t mulhd_ss(int32_t a, int32_t b) { return (int32_t) (((int64_t) a * b) >> 32); }
static inline int32_t mulhd_su(int32_t a, uint32_t b) { return (int32_t) (((int64_t) a * (int64_t) b) >> 32); }
static inline int msb32(uint32_t x) { return x ? 31 - __builtin_clz(x) : -1; }
static inline int32_t shl32(int32_t x, int s) { return (int32_t) ((uint32_t) x << (s & 31)); }
static inline int32_t asr32(int32_t x, int s) { return x >> (s & 31); }
static inline int32_t sat16(int32_t x) { return x < -32768 ? -32768 : x > 32767 ? 32767 : x; }
/* saturating left shift (0x9348 / 0x939e / 0x93d4 tail) */
static int32_t shl_sat(int32_t x, int s) {
	if (x >= 0) { if (s < 31 - msb32((uint32_t) x)) return shl32(x, s); return 0x7fffffff; }
	if (s < 31 - msb32(~(uint32_t) x)) return shl32(x, s);
	return (int32_t) 0x80000000u;
}
static int32_t shift_9348(int32_t x, int s) { return s >= 0 ? asr32(x, s) : shl_sat(x, -s); }

/* ------------------------------------------------------------------ bit reader */
typedef struct { const uint8_t* p; size_t len; size_t pos; } bits;
static uint32_t bget(bits* b, int n) {
	uint32_t v = 0;
	for (int i = 0; i < n; i++) {
		size_t byte = b->pos >> 3;
		uint32_t bit = byte < b->len ? (b->p[byte] >> (7 - (b->pos & 7))) & 1 : 0;
		v = v << 1 | bit; b->pos++;
	}
	return v;
}
static int huff(bits* b, const uint32_t* code, const uint8_t* nb, int n) {
	uint32_t acc = 0; int len = 0;
	while (len < 20) {
		acc = acc << 1 | bget(b, 1); len++;
		for (int i = 0; i < n; i++)
			if (nb[i] == len && code[i] == acc) return i;
	}
	return -1;
}

/* ------------------------------------------------------------------ syntax (writes the ic_stream bytes) */
enum { ONLY_LONG = 0, LONG_START = 1, EIGHT_SHORT = 2, LONG_STOP = 3 };
enum { ZERO_HCB = 0, ESC_HCB = 11, NOISE_HCB = 13, INTENSITY_HCB2 = 14, INTENSITY_HCB = 15 };

static int window_grouping_info(pyaac* d, uint8_t* ics) {   /* 0x3e8fc */
	int sf = d->sf_index;
	if (sf > 11) return 10;
	if (U8(ics, O_WSEQ) != EIGHT_SHORT) {
		ics[O_NUM_WINDOWS] = 1; ics[O_NUM_GROUPS] = 1; ics[O_GROUP_LEN] = 1;
		int n = ics[O_NUM_SWB] = rd8(d, d->A.num_swb_long + sf);
		W32le(ics, O_PNS_NORM, rd32(d, d->A.pns_norm_long + 4 * sf));
		uint32_t t = rd32(d, d->A.swb_long + 4 * sf);
		for (int i = 0; i <= n; i++) {
			W16(ics, O_SECT_SFB_OFF + 2 * i, rd16(d, t + 2 * i));
			W16(ics, O_SWB_OFF + 2 * i, rd16(d, t + 2 * i));
		}
		return 0;
	}
	ics[O_NUM_WINDOWS] = 8; ics[O_NUM_GROUPS] = 1; ics[O_GROUP_LEN] = 1;
	int n = ics[O_NUM_SWB] = rd8(d, d->A.num_swb_short + sf);
	W32le(ics, O_PNS_NORM, rd32(d, d->A.pns_norm_short + 4 * sf));
	uint32_t t = rd32(d, d->A.swb_short + 4 * sf);
	for (int i = 0; i <= n; i++) W16(ics, O_SWB_OFF + 2 * i, rd16(d, t + 2 * i));
	int grp = U8(ics, O_SFGROUP), ng = 1;
	for (int i = 0; i < 7; i++) {
		if (!(grp >> (6 - i) & 1)) { ng++; ics[O_GROUP_LEN + ng - 1] = 1; }
		else ics[O_GROUP_LEN + ng - 1]++;
	}
	ics[O_NUM_GROUPS] = ng;
	for (int g = 0; g < ng; g++) {
		int off = 0, i;
		for (i = 0; i < n; i++) {
			W16(ics, O_SECT_SFB_OFF + 240 * g + 2 * i, off);
			off += (rd16(d, t + 2 * i + 2) - rd16(d, t + 2 * i)) * ics[O_GROUP_LEN + g];
		}
		W16(ics, O_SECT_SFB_OFF + 240 * g + 2 * i, off);
	}
	return 0;
}

static int ics_info(pyaac* d, bits* b, uint8_t* ics) {   /* 0x21b24 */
	bget(b, 1);
	ics[O_WSEQ] = bget(b, 2);
	ics[O_WSHAPE] = bget(b, 1);
	if (ics[O_WSEQ] == EIGHT_SHORT) {
		ics[O_MAX_SFB] = bget(b, 4);
		ics[O_SFGROUP] = bget(b, 7);
	} else {
		ics[O_MAX_SFB] = bget(b, 6);
		ics[O_PRED] = bget(b, 1);      /* predictor_data_present: no prediction data is parsed */
		if (!(ics[O_PRED] & 1) && d->object_type == 0) ics[O_PRED] = bget(b, 1);
	}
	return window_grouping_info(d, ics);
}

/* Writes through the element buffer (ic_stream 1 runs into ic_stream 2); outside it the
 * firmware writes other decoder memory, which is not modelled. */
static int elem_ok(pyaac* d, const uint8_t* p, long off) {
	long o = (long) (p - d->elem) + off;
	if (o < 0 || o >= (long) sizeof d->elem) { d->undefined = __LINE__; return 0; }
	return 1;
}
static int section_data(pyaac* d, uint8_t* ics, bits* b) {   /* 0x2e094: no bound on the section count */
	int sect_bits = LD11(ics, O_WSEQ) == EIGHT_SHORT ? 3 : 5;
	int esc = (1 << sect_bits) - 1;
	for (int g = 0; g < LD11(ics, O_NUM_GROUPS); g++) {
		int k = 0, i = 0;
		while (k < LD11(ics, O_MAX_SFB)) {
			int cb = bget(b, 4), len = 0, inc;
			while ((inc = bget(b, sect_bits)) == esc) {
				len += inc;
				if (b->pos > 8 * b->len + 64 * 1024) return -3;   /* reads all-ones forever: never in practice */
			}
			len += inc;
			if (!elem_ok(d, ics, O_SECT_END + 240 * g + 2 * i + 1)) return -2;
			ics[O_SECT_CB + 120 * g + i] = cb;
			ics[O_SECT_START + 120 * g + i] = k;
			W16(ics, O_SECT_END + 240 * g + 2 * i, k + len);
			for (int s = k; s < k + len; s++) {
				if (!elem_ok(d, ics, O_SFB_CB + 120 * g + s)) return -2;
				ics[O_SFB_CB + 120 * g + s] = cb;
			}
			k += len; i++;
			if (len == 0 && b->pos > 8 * b->len + 64 * 1024) return -3;   /* zero-length sections past the data: the firmware loops */
		}
		W16(ics, O_NUM_SEC + 2 * g, i);
	}
	return 0;
}

static int scale_factor_data(uint8_t* ics, bits* b) {   /* 0x2cbbc */
	int gg = S16(ics, O_GLOBAL_GAIN);
	int sf = gg, is_pos = 0, noise = gg - 90, npcm = 1;
	for (int g = 0; g < ics[O_NUM_GROUPS]; g++)
		for (int s = 0; s < ics[O_MAX_SFB]; s++) {
			int cb = ics[O_SFB_CB + 120 * g + s], t, v;
			switch (cb) {
			case ZERO_HCB: v = 0; break;
			case 12: return 2;   /* reserved codebook: the switch at 0x2cc14 returns 2 */
			case INTENSITY_HCB: case INTENSITY_HCB2:
				is_pos += huff(b, HCB_SF_CODE, HCB_SF_BITS, 121) - 60; v = is_pos; break;
			case NOISE_HCB:
				if (npcm) { npcm = 0; t = (int) bget(b, 9) - 256; }
				else t = huff(b, HCB_SF_CODE, HCB_SF_BITS, 121) - 60;
				noise += t; v = noise; break;
			default:
				sf += huff(b, HCB_SF_CODE, HCB_SF_BITS, 121) - 60;
				if (sf < 0) return 3;
				v = sf;
			}
			W16(ics, O_SF + 102 * g + 2 * s, v);
		}
	return 0;
}

static void tns_data(uint8_t* ics, bits* b) {   /* 0x3011e */
	int short_ = ics[O_WSEQ] == EIGHT_SHORT;
	int nfb = short_ ? 1 : 2, lb = short_ ? 4 : 6, ob = short_ ? 3 : 5;
	for (int w = 0; w < ics[O_NUM_WINDOWS]; w++) {
		int res = 3;
		int nf = ics[T_NFILT(w)] = bget(b, nfb);
		if (nf) { ics[T_RES(w)] = bget(b, 1); res = (ics[T_RES(w)] & 1) ? 4 : 3; }
		for (int f = 0; f < nf; f++) {
			ics[T_LEN(w, f)] = bget(b, lb);
			int ord = ics[T_ORDER(w, f)] = bget(b, ob);
			if (ord) {
				ics[T_DIR(w, f)] = bget(b, 1);
				ics[T_COMP(w, f)] = bget(b, 1);
				int cb = res - ics[T_COMP(w, f)];
				for (int i = 0; i < ord; i++) ics[T_COEF(w, f, i)] = bget(b, cb);
			}
		}
	}
}

static const int CB_DIM[12] = {0, 4, 4, 4, 4, 2, 2, 2, 2, 2, 2, 2};
static const int CB_SIGNED[12] = {0, 1, 1, 0, 0, 1, 1, 0, 0, 0, 0, 0};
static const int CB_MOD[12] = {0, 3, 3, 3, 3, 9, 9, 8, 8, 13, 13, 17};
static const int CB_OFF[12] = {0, 1, 1, 0, 0, 4, 4, 0, 0, 0, 0, 0};
static const uint32_t* const CB_CODE[12] = {0, HCB_CB1_CODE, HCB_CB2_CODE, HCB_CB3_CODE, HCB_CB4_CODE, HCB_CB5_CODE,
	HCB_CB6_CODE, HCB_CB7_CODE, HCB_CB8_CODE, HCB_CB9_CODE, HCB_CB10_CODE, HCB_CB11_CODE};
static const uint8_t* const CB_BITS[12] = {0, HCB_CB1_BITS, HCB_CB2_BITS, HCB_CB3_BITS, HCB_CB4_BITS, HCB_CB5_BITS,
	HCB_CB6_BITS, HCB_CB7_BITS, HCB_CB8_BITS, HCB_CB9_BITS, HCB_CB10_BITS, HCB_CB11_BITS};
static const int CB_N[12] = {0, 81, 81, 81, 81, 81, 81, 64, 64, 169, 169, 289};

/* 0x8014: the escape prefix is found in a 9-bit peek: N = leading ones (0..9), N + 1 bits are
 * skipped (with 9 ones the 10th bit is skipped unread), then N + 4 bits follow: 2^(N+4) + off */
static int get_escape(bits* b, int v) {
	int a = v < 0 ? -v : v;
	if (a != 16) return v;
	size_t p0 = b->pos;
	uint32_t peek = bget(b, 9);
	int n = 0;
	while (n < 9 && (peek >> (8 - n) & 1)) n++;
	b->pos = p0 + n + 1;
	int i = n + 4;
	int j = (int) bget(b, i) + (1 << i);
	return v < 0 ? -j : j;
}

/* Quantised values go to decoder + 0x800 * channel (int16).  The firmware does not bound the write
 * position: past the buffer it writes the other channel's quant buffer, then channel 0's overlap
 * (decoder + 0x1000), channel 1's dead output half and channel 1's overlap (decoder + 0x3000). */
static void tput32b(int32_t* w, int byteoff, int v) {   /* little-endian int16 store into an int32 array */
	uint32_t x = (uint32_t) w[byteoff >> 2];
	int sh = (byteoff & 2) * 8;
	x = (x & ~(0xffffu << sh)) | ((uint32_t) (uint16_t) v << sh);
	w[byteoff >> 2] = (int32_t) x;
}
static int tget32b(const int32_t* w, int byteoff) {
	return (int16_t) ((uint32_t) w[byteoff >> 2] >> ((byteoff & 2) * 8));
}
/* Quantised values (int16) live at decoder + 0x800 * channel, i.e. in channel 0's time buffer
 * (decoder + 0..0x2000, whose output half they overwrite until the filter bank runs). The firmware
 * does not bound the index: decoder + 0x2000..0x4000 is channel 1's time buffer; beyond that the
 * decoder's fields (not modelled). */
static int qput(pyaac* d, int ch, long p, int v) {
	long off = (long) ch * 0x800 + 2L * p;
	if (off >= 0 && off < 0x2000) { tput32b(d->time[0], (int) off, v); return 1; }
	if (off >= 0x2000 && off < 0x4000) { tput32b(d->time[1], (int) off - 0x2000, v); return 1; }
	d->undefined = __LINE__;
	return 0;
}
static int qget(pyaac* d, int ch, long p, int* v) {
	long off = (long) ch * 0x800 + 2L * p;
	if (off >= 0 && off < 0x2000) { *v = tget32b(d->time[0], (int) off); return 1; }
	if (off >= 0x2000 && off < 0x4000) { *v = tget32b(d->time[1], (int) off - 0x2000); return 1; }
	d->undefined = __LINE__;
	return 0;
}
static int spectral_data(pyaac* d, uint8_t* ics, bits* b, int ch) {   /* 0x2eca2 */
	memset((uint8_t*) d->time[0] + 0x800 * ch, 0, 0x800);   /* little-endian host assumed */
	int groups = 0;
	for (int g = 0; g < ics[O_NUM_GROUPS]; g++) {
		int p = groups * 128;
		for (int i = 0; i < S16(ics, O_NUM_SEC + 2 * g); i++) {
			int cb = ics[O_SECT_CB + 120 * g + i];
			int start = S16(ics, O_SECT_SFB_OFF + 240 * g + 2 * ics[O_SECT_START + 120 * g + i]);
			int end = S16(ics, O_SECT_SFB_OFF + 240 * g + 2 * S16(ics, O_SECT_END + 240 * g + 2 * i));
			if (cb == ZERO_HCB || cb >= 12) { p += end - start; continue; }
			int dim = CB_DIM[cb];
			for (int k = start; k < end; k += dim) {
				int idx = huff(b, CB_CODE[cb], CB_BITS[cb], CB_N[cb]);
				if (idx < 0) return 9;   /* not reachable with the firmware's tables: see notes */
				int v[4], m = CB_MOD[cb];
				if (dim == 4) { v[0] = idx / 27; v[1] = idx / 9 % 3; v[2] = idx / 3 % 3; v[3] = idx % 3; }
				else { v[0] = idx / m; v[1] = idx % m; }
				for (int t = 0; t < dim; t++) v[t] -= CB_OFF[cb];
				if (!CB_SIGNED[cb])
					for (int t = 0; t < dim; t++)
						if (v[t] && bget(b, 1)) v[t] = -v[t];
				if (cb == ESC_HCB) { v[0] = get_escape(b, v[0]); v[1] = get_escape(b, v[1]); }
				for (int t = 0; t < dim; t++) if (!qput(d, ch, p + t, v[t])) return -2;
				p += dim;
			}
		}
		groups += ics[O_GROUP_LEN + g];
	}
	return 0;
}

/* 0x2b064: no range checks; k = swb_offset[start_sfb] + offsets, int16 read-modify-write */
static int pulse_decode(pyaac* d, uint8_t* ics, int ch) {
	const uint8_t* pd = ics + O_PULSE_DATA;
	int k = S16(ics, O_SWB_OFF + 2 * LD11(ics, O_PULSE_DATA + 1));
	for (int i = 0; i <= LD11(ics, O_PULSE_DATA); i++) {
		k += LD11(ics, O_PULSE_DATA + 2 + i);
		int q, amp = LD11(ics, O_PULSE_DATA + 6 + i);
		if (!qget(d, ch, k, &q)) return -2;
		q = q > 0 ? q + amp : q - amp;
		if (!qput(d, ch, k, q)) return -2;
	}
	(void) pd;
	return 0;
}

static int individual_channel_stream(pyaac* d, bits* b, uint8_t* ics, int common_window, int ch) {   /* 0x21ec4 */
	W16(ics, O_GLOBAL_GAIN, bget(b, 8));
	if (!common_window) {
		int e = ics_info(d, b, ics);
		if (e != 0) return e;
	}
	int e = section_data(d, ics, b);
	if (e < 0) return e;

	e = scale_factor_data(ics, b);
	if (e > 0) return e;
	ics[O_PULSE] = bget(b, 1);
	if (ics[O_PULSE]) {
		uint8_t* pd = ics + O_PULSE_DATA;
		pd[0] = bget(b, 2); pd[1] = bget(b, 6);
		for (int i = 0; i <= pd[0]; i++) { pd[2 + i] = bget(b, 5); pd[6 + i] = bget(b, 4); }
	}
	ics[O_TNS] = bget(b, 1);
	if (ics[O_TNS]) tns_data(ics, b);
	ics[O_GAIN] = bget(b, 1);
	if (ics[O_GAIN]) return 8;
	e = spectral_data(d, ics, b, ch);
	if (e) return e;
	if (ics[O_PULSE]) {
		if (ics[O_WSEQ] == EIGHT_SHORT) return 1;
		return pulse_decode(d, ics, ch);
	}
	return 0;
}

/* ------------------------------------------------------------------ spectral reconstruction
 * Everything below reads the ic_stream with the firmware's load widths (LD11 / S8 / S16). */
#define CHK(d, idx) do { if ((unsigned) (idx) >= 1024u) { (d)->undefined = __LINE__; return; } } while (0)

static void iquant(pyaac* d, int ch, int32_t* x) {   /* 0x885c */
	for (int i = 0; i < 1024; i++) {
		int v = tget32b(d->time[0], 0x800 * ch + 2 * i), a = v < 0 ? -v : v;
		/* |q| >= 1280: 16-bit lanes: int16(2q) * table[(|q| + 4) >> 3] (2q wraps from 16384 on;
		 * indices past the 1280-entry table read the image bytes after it) */
		x[i] = a < 1280 ? W32((int64_t) v * rd16(d, d->A.cbrt + 2 * a))
		                : W32((int64_t) (int16_t) (2 * v) * rd16(d, d->A.cbrt + 2 * ((a + 4) >> 3)));
	}
}

static void scale_band(const pyaac* d, int sf, int32_t* x, int n) {   /* 0x88e4 */
	if (n <= 0) return;
	int e = (sf - 144) >> 2;
	uint32_t c = rd32(d, d->A.sf_pow2 + 4 * (sf & 3));
	if (e >= 0) { for (int i = 0; i < n; i++) x[i] = mulhd_su(shl32(x[i], e), c); return; }
	e = -e;
	if (e < 30) { for (int i = 0; i < n; i++) x[i] = asr32(mulhd_su(x[i], c), e); return; }
	for (int i = 0; i < ((n + 3) & ~3); i++) x[i] = 0;
}

static void apply_scalefactors(pyaac* d, const uint8_t* ics, int32_t* x) {   /* 0x18dd4 */
	int gstart = 0;
	for (int g = 0; g < LD11(ics, O_NUM_GROUPS); g++) {
		int base = 128 * gstart, k = 0;
		for (int s = 0; s < LD11(ics, O_MAX_SFB); s++) {
			int n = S16(ics, O_SECT_SFB_OFF + 240 * g + 2 * (s + 1)) - k;
			if (n < 0) n = 0;
			if (n) { CHK(d, base + k); CHK(d, base + k + n - 1); }
			scale_band(d, S16(ics, O_SF + 102 * g + 2 * s), x + base + k, n);
			k += n;
		}
		gstart += LD11(ics, O_GROUP_LEN + g);
	}
}

/* 0x2b334: grouped order -> window-major, built in the 64x64-byte VRF (0x3d50 clear, 0x3d66
 * 16-byte loads) and stored back by 0x3d72 (four REP-64 stores at a 64-byte stride: 4 KB). */
static void reorder_short(const uint8_t* ics, int32_t* x) {
	int32_t vrf[1024];
	memset(vrf, 0, sizeof vrf);
	int win0 = 0, src = 0;
	for (int g = 0; g < LD11(ics, O_NUM_GROUPS); g++) {
		int k = 0;
		int glen = LD11(ics, O_GROUP_LEN + g);
		for (int s = 0; s < LD11(ics, O_NUM_SWB); s++) {
			int w0 = S16(ics, O_SWB_OFF + 2 * s), width = S16(ics, O_SWB_OFF + 2 * s + 2) - w0;
			for (int w = 0; w < glen; w++) {
				for (int bin = 0; bin < width; bin += 4) {
					int dst = (win0 + w) * 128 + w0 + bin;   /* (r8 + r5) words: k + w*128 within the group */
					(void) k;
					for (int t = 0; t < 4; t++) vrf[(dst + t) & 1023] = x[(src + t) & 1023];
					src += 4;
				}
			}
		}
		win0 += glen;
	}
	memcpy(x, vrf, sizeof vrf);
}

static int is_intensity(const uint8_t* ics, int g, int s) {   /* 0x421e */
	int cb = LD11(ics, O_SFB_CB + 120 * g + s);
	return cb == INTENSITY_HCB ? 1 : cb == INTENSITY_HCB2 ? -1 : 0;
}
static int is_noise(const uint8_t* ics, int g, int s) { return LD11(ics, O_SFB_CB + 120 * g + s) == NOISE_HCB; }
static int invert_intensity(const uint8_t* ics, int g, int s) {   /* 0x4244 */
	return S16(ics, O_MS_MASK) == 1 ? 1 - 2 * S8(ics, O_MS_USED + 51 * g + s) : 1;
}

static void ms_decode(pyaac* d, const uint8_t* l, const uint8_t* r, int32_t* xl, int32_t* xr) {   /* 0x263ae */
	int mm = S16(l, O_MS_MASK);
	if (mm < 1) return;
	int win = 0;
	for (int g = 0; g < LD11(l, O_NUM_GROUPS); g++)
		for (int b = 0; b < LD11(l, O_GROUP_LEN + g); b++, win++)
			for (int s = 0; s < LD11(l, O_MAX_SFB); s++) {
				if (!(S8(l, O_MS_USED + 51 * g + s) || mm == 2)) continue;
				if (is_intensity(r, g, s) || is_noise(l, g, s)) continue;
				int k = S16(l, O_SWB_OFF + 2 * s), n = S16(l, O_SWB_OFF + 2 * s + 2) - k;
				for (int i = 0; i < n; i++) {
					CHK(d, win * 128 + k + i);
					int32_t a = xl[win * 128 + k + i], c = xr[win * 128 + k + i];
					xl[win * 128 + k + i] = W32((int64_t) a + c);
					xr[win * 128 + k + i] = W32((int64_t) a - c);
				}
			}
}

static uint32_t rnd(pyaac* d) { d->rng = d->rng * 1664525u + 1013904223u; return d->rng; }

static void pns_decode(pyaac* d, const uint8_t* ics, int32_t* x) {   /* 0x2ab88 / 0x407c */
	int win = 0;
	for (int g = 0; g < LD11(ics, O_NUM_GROUPS); g++)
		for (int b = 0; b < LD11(ics, O_GROUP_LEN + g); b++, win++)
			for (int s = 0; s < LD11(ics, O_MAX_SFB); s++) {
				if (!is_noise(ics, g, s)) continue;
				int k = S16(ics, O_SWB_OFF + 2 * s), n = S16(ics, O_SWB_OFF + 2 * s + 2) - k;
				int sf = S16(ics, O_SF + 102 * g + 2 * s);
				int32_t norm = (int32_t) rd32(d, U32(ics, O_PNS_NORM) + 4 * s);
				if (n > 0) { CHK(d, win * 128 + k); CHK(d, win * 128 + k + n - 1); }
				int32_t* p = x + win * 128 + k;
				for (int i = 0; i < n; i++) p[i] = (int32_t) rnd(d);
				int32_t m = rds16(d, d->A.pns_pow2 + 2 * (sf & 3));
				int sh = 14 - (sf >> 2) + 16;
				int32_t r8 = asr32(shl32(mulhd_ss(shl32(m, 16), norm), 1), 16);
				for (int i = 0; i < n; i++)
					p[i] = shift_9348(shl32(mulhd_ss(shl32(r8, 16), p[i]), 1), sh);
			}
}

static void is_scale(const int32_t* src, int32_t* dst, int n, int exp, int32_t gain) {   /* 0x93d4 */
	int32_t g16 = shl32(gain, 16);
	for (int i = 0; i < n; i++) {
		int32_t x = src[i];
		if (x == 0) { dst[i] = 0; continue; }
		uint32_t ax = x < 0 ? -(uint32_t) x : (uint32_t) x;
		int s = 30 - msb32(ax);
		x = shl32(x, s);
		x = shl32(mulhd_ss(g16, x), 1);
		s += exp;
		dst[i] = s >= 0 ? asr32(x, s) : shl_sat(x, -s);
	}
}

static void is_decode(pyaac* d, const uint8_t* l, const uint8_t* r, int32_t* xl, int32_t* xr) {   /* 0x228f6 */
	int win = 0;
	for (int g = 0; g < LD11(r, O_NUM_GROUPS); g++)
		for (int b = 0; b < LD11(r, O_GROUP_LEN + g); b++, win++)
			for (int s = 0; s < LD11(r, O_MAX_SFB); s++) {
				if (LD11(r, O_SFB_CB + 120 * g + s) <= 11) continue;
				int sign = is_intensity(r, g, s) * invert_intensity(l, g, s);
				int pos = S16(r, O_SF + 102 * g + 2 * s);
				int e = (int16_t) (60 - pos), mant, exp;
				if (e < 0) { exp = 30; mant = 0; }
				else { exp = (int16_t) (29 - (e >> 2)); mant = rds16(d, d->A.is_pow2 + 2 * (e & 3)); }
				int32_t gain = (int16_t) (mant * sign);
				int k = S16(r, O_SWB_OFF + 2 * s), n = (int16_t) (S16(r, O_SWB_OFF + 2 * s + 2) - k);
				if (n > 0) { CHK(d, win * 128 + k); CHK(d, win * 128 + k + n - 1); }
				is_scale(xl + win * 128 + k, xr + win * 128 + k, n, (int16_t) (exp - 15), gain);
			}
}

/* TNS (0x3032a, 0x3020e, 0x300ca) */
static int tns_max_order(int object_type, int short_) {   /* 0x43c0 */
	if (short_) return 7;
	switch (object_type) { case 0: case 3: return 20; case 1: case 2: return 12; default: return 0; }
}
static void tns_decode_coef(pyaac* d, int order, int res_bits, int compress, const uint8_t* ics, int w, int f, int16_t* lpc, int* exp) {
	int32_t tmp2[32], a[33], b[33];
	uint32_t tab = d->A.tns_coef[(compress & 1) + ((res_bits >> 1) & 2)];
	for (int i = 0; i < order; i++) tmp2[i] = (int32_t) rd32(d, tab + 4 * S8(ics, T_COEF(w, f, i)));
	a[0] = 1 << 18;
	for (int m = 1; m <= order; m++) {
		int32_t t = shl32(asr32(tmp2[m - 1], 3), 16);
		for (int i = 1; i < m; i++) b[i] = W32((int64_t) a[i] + 2 * (int64_t) mulhd_ss(t, a[m - i]));
		for (int i = 1; i < m; i++) a[i] = b[i];
		a[m] = tmp2[m - 1];
	}
	uint32_t mx = 0;
	for (int i = 0; i <= order; i++) mx |= (uint32_t) (a[i] < 0 ? -a[i] : a[i]);
	int sh = mx == 0 ? 0 : (int32_t) mx > 0 ? 30 - msb32(mx) : 30 - msb32(~mx);    /* 0x9388 */
	*exp = (int16_t) (sh + 2);
	lpc[0] = 32767;
	for (int i = 1; i <= order; i++) lpc[i] = (int16_t) asr32(shl32(a[i], sh), 16);
}
static void tns_ar_filter(int32_t* x, int size, int inc, const int16_t* lpc, int order, int exp) {
	int32_t st[33] = {0};
	int sh = (int16_t) (15 - exp);
	for (int n = 0; n < size; n++) {
		int32_t y = *x;
		for (int j = n < order ? n : order; j > 0; j--) {
			int32_t p = shl32(mulhd_ss(shl32(lpc[j], 16), st[j - 1]), 1);
			y = W32((int64_t) y - p);
			st[j] = st[j - 1];
		}
		st[0] = shl32(y, sh);
		*x = y;
		x += inc;
	}
}
static void tns_decode(pyaac* d, const uint8_t* ics, int32_t* x) {
	if (!LD11(ics, O_TNS)) return;
	int wseq = LD11(ics, O_WSEQ);
	int short_ = wseq == EIGHT_SHORT;
	int mts = rds16(d, d->A.tns_max_sfb + 12 * d->sf_index + 2 * short_);   /* 0x43fc */
	int msfb = LD11(ics, O_MAX_SFB);
	for (int w = 0; w < LD11(ics, O_NUM_WINDOWS); w++) {
		int bottom = LD11(ics, O_NUM_SWB);
		for (int f = 0; f < S8(ics, T_NFILT(w)); f++) {
			int top = bottom;
			bottom = top - LD11(ics, T_LEN(w, f)); if (bottom < 0) bottom = 0;
			int order = LD11(ics, T_ORDER(w, f)), mo = tns_max_order(d->object_type, short_);
			if (order > mo) order = mo;
			if (!order) continue;
			int16_t lpc[33]; int exp;
			tns_decode_coef(d, order, LD11(ics, T_RES(w)) + 3, LD11(ics, T_COMP(w, f)), ics, w, f, lpc, &exp);
			int s0 = bottom < mts ? bottom : mts; if (s0 > msfb) s0 = msfb;
			int s1 = top < mts ? top : mts; if (s1 > msfb) s1 = msfb;
			int start = S16(ics, O_SWB_OFF + 2 * s0), end = S16(ics, O_SWB_OFF + 2 * s1), size = end - start;
			if (size <= 0) continue;
			int inc = 1;
			if (LD11(ics, T_DIR(w, f))) { inc = -1; start = end - 1; }
			CHK(d, w * 128 + start); CHK(d, w * 128 + start + inc * (size - 1));
			tns_ar_filter(x + w * 128 + start, size, inc, lpc, order, exp);
		}
	}
}

/* ------------------------------------------------------------------ IMDCT (aac_model.py, 0x97d6 / 0x99d8) */
static int32_t Mw(int32_t x, int32_t w) {
	int32_t xh = x >> 16, xl = x & 0xffff, wh = w >> 16, wl = w & 0xffff;
	return W32((int64_t) xh * wh + (((int64_t) xh * wl + 0x8000) >> 16) + (((int64_t) wh * xl + 0x8000) >> 16));
}
static void cmul(int32_t x, int32_t y, int32_t c, int32_t s, int32_t* re, int32_t* im) {
	*re = W32((int64_t) Mw(x, c) - Mw(y, s));
	*im = W32((int64_t) Mw(x, s) + Mw(y, c));
}
static int32_t shl2_sat(int32_t x) {
	int32_t lo = x & 0xffff, hi = x >> 16;
	int32_t nhi = sat16(sat16(hi * 4) + (lo >> 14));
	return (int32_t) ((uint32_t) nhi << 16 | (uint32_t) ((lo << 2) & 0xffff));
}
static void tw_block(const pyaac* d, uint32_t a, int32_t* c, int32_t* s) {
	for (int t = 0; t < 16; t++) {
		c[t] = (int32_t) ((uint32_t) rd16(d, a + 2 * (16 + t)) << 16 | rd16(d, a + 2 * t));
		s[t] = (int32_t) ((uint32_t) rd16(d, a + 2 * (48 + t)) << 16 | rd16(d, a + 2 * (32 + t)));
	}
}
typedef struct { int32_t re[16], im[16]; } blk;
static void fft16(const pyaac* d, blk* B) {
	int32_t nr[16], ni[16];
	for (int dd = 1; dd <= 8; dd <<= 1) {
		if (dd == 2) for (int t = 3; t < 16; t += 4) { int32_t r = B->re[t]; B->re[t] = B->im[t]; B->im[t] = W32(-(int64_t) r); }
		if (dd >= 4) {
			uint32_t tab = dd == 4 ? d->A.fft_tw4 : d->A.fft_tw8;
			int32_t c[16], s[16];
			tw_block(d, tab, c, s);
			for (int t = 0; t < 16; t++) {
				if (rd16(d, tab + 2 * (48 + t)) == 0) continue;
				cmul(shl2_sat(B->re[t]), shl2_sat(B->im[t]), c[t], s[t], &B->re[t], &B->im[t]);
			}
		}
		for (int t = 0; t < 16; t++) {
			if ((t / dd) % 2 == 0) { nr[t] = W32((int64_t) B->re[t] + B->re[t + dd]); ni[t] = W32((int64_t) B->im[t] + B->im[t + dd]); }
			else { nr[t] = W32((int64_t) B->re[t - dd] - B->re[t]); ni[t] = W32((int64_t) B->im[t - dd] - B->im[t]); }
		}
		memcpy(B->re, nr, sizeof nr); memcpy(B->im, ni, sizeof ni);
	}
}
/* radix-2 stage over block lists A[k], B[k] (k < n) with twiddle table tw */
static void radix2(const pyaac* d, blk* A, blk* Bv, int n, uint32_t tw) {
	for (int k = 0; k < n; k++) {
		int32_t c[16], s[16];
		tw_block(d, tw + 128 * k, c, s);
		for (int t = 0; t < 16; t++) {
			int32_t tr, ti;
			cmul(shl32(Bv[k].re[t], 2), shl32(Bv[k].im[t], 2), c[t], s[t], &tr, &ti);
			int32_t ar = A[k].re[t], ai = A[k].im[t];
			A[k].re[t] = W32((int64_t) ar + tr); A[k].im[t] = W32((int64_t) ai + ti);
			Bv[k].re[t] = W32((int64_t) ar - tr); Bv[k].im[t] = W32((int64_t) ai - ti);
		}
	}
}
static void fft_blocks(const pyaac* d, blk* bl, int nb) {
	for (int b = 0; b < nb; b++) fft16(d, &bl[b]);
	if (nb == 4) {
		radix2(d, &bl[0], &bl[1], 1, d->A.tw_b1); radix2(d, &bl[2], &bl[3], 1, d->A.tw_b1);
		radix2(d, &bl[0], &bl[2], 2, d->A.tw_b2);
		return;
	}
	for (int q = 0; q < 4; q++) {
		blk* Q = bl + 8 * q;
		for (int p = 0; p < 4; p++) radix2(d, &Q[2 * p], &Q[2 * p + 1], 1, d->A.tw_b1);
		radix2(d, &Q[0], &Q[2], 2, d->A.tw_b2); radix2(d, &Q[4], &Q[6], 2, d->A.tw_b2);
		radix2(d, &Q[0], &Q[4], 4, d->A.tw_b4);
	}
	radix2(d, &bl[0], &bl[8], 8, d->A.tw_b8); radix2(d, &bl[16], &bl[24], 8, d->A.tw_b8);
	radix2(d, &bl[0], &bl[16], 16, d->A.tw_b16);
}
static int32_t shift32(int32_t x, int n) {
	if (n > 0) {
		int32_t lo = x & 0xffff, hi = x >> 16;
		int64_t h = (int64_t) hi * ((int64_t) 1 << n);
		int32_t nhi = sat16((int32_t) (h < -32768 ? -32768 : h > 32767 ? 32767 : h) + (lo >> (16 - n)));
		return (int32_t) ((uint32_t) nhi << 16 | (uint32_t) ((lo << n) & 0xffff));
	}
	if (n < 0) { int m = -n; return W32((int64_t) W32((int64_t) x + (1LL << (m - 1))) >> m); }
	return x;
}
static int32_t P16(uint32_t c, int32_t v) { return W32((int64_t) (v >> 16) * c + (((int64_t) c * (v & 0xffff) + 0x8000) >> 16)); }
static void imdct(const pyaac* d, const int32_t* X, int N, int32_t* out) {
	int N4 = N / 4, N8 = N / 8, N2 = N / 2;
	int bf = N == 2048 ? 4 : 6, r8 = N == 2048 ? -8 : -5;
	uint32_t ct = N == 2048 ? d->A.imdct_long_c : d->A.imdct_short_c, stt = N == 2048 ? d->A.imdct_long_s : d->A.imdct_short_s;
	static int32_t zr[512], zi[512], tr[512], ti[512];
	int32_t Mx = 0;
	for (int k = 0; k < N4; k++) {
		zr[k] = X[2 * k]; zi[k] = X[2 * k + 1];
		int32_t h1 = zr[k] >> 16, h2 = zi[k] >> 16;
		int32_t m1 = h1 > ~h1 ? h1 : ~h1, m2 = h2 > ~h2 ? h2 : ~h2;
		if (m1 > Mx) Mx = m1;
		if (m2 > Mx) Mx = m2;
	}
	int r7 = msb32((uint32_t) Mx) - bf;
	for (int k = 0; k < N4; k++) { zr[k] = shift32(zr[k], -r7); zi[k] = shift32(zi[k], -r7); }
	for (int k = 0; k < N4; k++) { tr[k] = W32(-(int64_t) zr[k]); ti[k] = zi[N4 - 1 - k]; }
	for (int k = 0; k < N4; k++) {
		uint32_t c = rd16(d, ct + 2 * k), s = rd16(d, stt + 2 * k);
		int32_t a = tr[k], b = ti[k];
		zr[k] = W32((int64_t) P16(c, a) - P16(s, b));
		zi[k] = W32(-(int64_t) W32((int64_t) P16(s, a) + P16(c, b)));
	}
	int nb = N4 == 512 ? 9 : 6;
	static blk bl[32];
	for (int k = 0; k < N4; k++) {
		int r = 0;
		for (int i = 0; i < nb; i++) r |= ((k >> i) & 1) << (nb - 1 - i);
		bl[k >> 4].re[k & 15] = zr[r]; bl[k >> 4].im[k & 15] = zi[r];
	}
	fft_blocks(d, bl, N4 / 16);
	int r0 = r7 + 10 + r8;
	for (int k = 0; k < N4; k++) {
		int32_t a = bl[k >> 4].re[k & 15], b = W32(-(int64_t) bl[k >> 4].im[k & 15]);
		uint32_t c = rd16(d, ct + 2 * k), s = rd16(d, stt + 2 * k);
		int32_t pr = W32((int64_t) P16(c, a) - P16(s, b)), pi = W32((int64_t) P16(s, a) + P16(c, b));
		tr[k] = shift32(pr, r0); ti[k] = shift32(pi, r0);
	}
	for (int j = 0; j < N8; j++) {
		out[2 * j] = W32(-(int64_t) tr[N8 + j]); out[2 * j + 1] = W32(-(int64_t) ti[N8 - 1 - j]);
		out[N4 + 2 * j] = ti[j]; out[N4 + 2 * j + 1] = tr[N4 - 1 - j];
		out[N2 + 2 * j] = ti[N8 + j]; out[N2 + 2 * j + 1] = tr[N8 - 1 - j];
		out[N2 + N4 + 2 * j] = tr[j]; out[N2 + N4 + 2 * j + 1] = ti[N4 - 1 - j];
	}
}
static int32_t wmul(int32_t a, int32_t w) { return W32(2 * ((int64_t) (a >> 16) * w + (((int64_t) w * (a & 0xffff) + 0x8000) >> 16))); }

static void filter_bank(pyaac* d, int ch, int seq, int shape, int shape_prev, const int32_t* freq, int32_t* T) {   /* 0x21bb2 */
	static int32_t t[2048];
	(void) ch;
	/* window_sequence (2 bits) and window_shape (1 bit) come straight from the bitstream */
	uint32_t Lprev = d->A.win[shape_prev], Lrev = d->A.win[4 + shape];
	uint32_t Sprev = d->A.win[2 + shape_prev], S = d->A.win[2 + shape], Srev = d->A.win[6 + shape];
	memmove(T, T + 1024, 1024 * 4);
	if (seq != EIGHT_SHORT) {
		imdct(d, freq, 2048, t);
		if (seq == LONG_STOP) {
			memset(t, 0, 448 * 4);
			for (int i = 0; i < 128; i++) t[448 + i] = wmul(t[448 + i], rds16(d, Sprev + 2 * i));
		} else
			for (int i = 0; i < 1024; i++) t[i] = wmul(t[i], rds16(d, Lprev + 2 * i));
		for (int i = 0; i < 1024; i++) T[i] = W32((int64_t) t[i] + T[i]);
		if (seq == LONG_START) {
			memcpy(T + 1024, t + 1024, 448 * 4);
			for (int i = 0; i < 128; i++) T[1472 + i] = wmul(t[1472 + i], rds16(d, Srev + 2 * i));
			memset(T + 1600, 0, 448 * 4);
		} else
			for (int i = 0; i < 1024; i++) T[1024 + i] = wmul(t[1024 + i], rds16(d, Lrev + 2 * i));
		return;
	}
	memset(T + 1024, 0, 1024 * 4);
	for (int w = 0; w < 8; w++) {
		imdct(d, freq + 128 * w, 256, t);
		uint32_t wp = w == 0 ? Sprev : S;
		int o = 448 + 128 * w;
		for (int i = 0; i < 128; i++) T[o + i] = W32((int64_t) wmul(t[i], rds16(d, wp + 2 * i)) + T[o + i]);
		for (int i = 0; i < 128; i++) T[o + 128 + i] = W32((int64_t) T[o + 128 + i] + wmul(t[128 + i], rds16(d, Srev + 2 * i)));
	}
	memset(T + 1600, 0, 448 * 4);
}

/* ------------------------------------------------------------------ frame */
pyaac* pyaac_open(const uint8_t* image, size_t len, int which) {
	pyaac* d = calloc(1, sizeof *d);
	if (!d) return 0;
	d->img = image; d->len = len;
	d->A = which == 1 ? FUP_SEC0 : MI_FW0;
	d->rng = rd32(d, d->A.rng);
	d->ics[0] = d->elem; d->ics[1] = d->elem + ICS_SZ;
	return d;
}
void pyaac_close(pyaac* d) { free(d); }
uint32_t pyaac_rng(pyaac* d) { return d->rng; }
int pyaac_error(pyaac* d) { return d->err; }
void pyaac_set_rng(pyaac* d, uint32_t v) { d->rng = v; }

int pyaac_init(pyaac* d, const uint8_t* asc, size_t len) {   /* 0xd4d0 -> 0xd712 */
	bits b = {asc, len, 0};
	int aot = bget(&b, 5), sf = bget(&b, 4), ch = bget(&b, 4);
	d->sf_index = sf; d->channel_config = ch;
	d->sample_rate = rd32(d, d->A.sample_rates + 4 * sf);
	d->object_type = aot < 5 ? aot - 1 : aot;
	memset(d->time, 0, sizeof d->time);
	d->wshape_prev[0] = d->wshape_prev[1] = 0;
	d->frame_count = 0;
	d->sce_seen = d->sce_tag = 0;
	return 0;
}

/* 0x1cab8 dynamic_range_info: FAAD2's, but band_top is read once whatever num_bands is (the loop
 * has no body); returns the bytes it claims to have read, or -1 when the excluded-channel list is
 * present: its words land at decoder + 0x40e0.., over the ADTS/ADIF flags (not modelled). */
static int drc_info(bits* b) {
	int n = 1, bands = 1;
	if (bget(b, 1)) { bget(b, 4); bget(b, 4); n++; }
	if (bget(b, 1)) return -1;
	if (bget(b, 1)) {
		int incr = bget(b, 4);
		bget(b, 4);
		n++;
		bands += incr;
		bget(b, 8);
		n++;
	}
	if (bget(b, 1)) { bget(b, 7); bget(b, 1); n++; }
	for (int i = 0; i < bands; i++) { bget(b, 1); bget(b, 7); n++; }
	return n;
}
/* 0x1e19a fill_element / 0x1deae extension_payload */
static int fill_element(bits* b) {
	int cnt = bget(b, 4);
	if (cnt == 15) cnt += (int) bget(b, 8) - 1;
	while (cnt > 0) {
		int type = bget(b, 4), used;
		if (type == 11) {
			used = drc_info(b);
			if (used < 0) return -2;
		} else {
			bget(b, 4);
			for (int i = 0; i < cnt - 1; i++) bget(b, 8);
			used = cnt;
		}
		cnt -= used;
	}
	return 0;
}

static int fail(pyaac* d, int e) {
	if (e > 0 || e == -14) { d->err = e; return PYAAC_ERROR; }
	return e == -3 ? PYAAC_HANG : PYAAC_UNDEFINED;
}

int pyaac_frame(pyaac* d, const uint8_t* raw, size_t len, int16_t* pcm) {   /* 0xcee8 */
	bits b = {raw, len, 0};
	int nch = 0, nelem = 0, second_sce = 0;   /* r8, r7, r11 */
	int first_ch = 0;                          /* element + 18546: channel of the element's first ic_stream */
	memset(d->elem, 0, sizeof d->elem);    /* 0x266a0: the element structs are cleared every frame */
	d->undefined = 0;
	d->err = 0;
	for (;;) {
		int id = bget(&b, 3);
		if (id == 7) break;
		if (id == 0 || id == 3) {           /* SCE / LFE (0x2e8a0) */
			if (nch > 1 || nelem > 1) return fail(d, -14);
			int tag = bget(&b, 4);
			if (!d->sce_seen) { d->sce_seen = 1; d->sce_tag = tag; }
			int k = tag == d->sce_tag ? 0 : 1;    /* ic_stream and buffers by tag, not by channel */
			first_ch = nch;
			if (nch == 1) second_sce = 1;
			int e = individual_channel_stream(d, &b, d->ics[k], 0, k);
			if (e) return fail(d, e);
			nch++; nelem = 1;
		} else if (id == 1) {               /* CPE (0x1b14e) */
			if (nelem > 0) return fail(d, -14);
			bget(&b, 4);
			uint8_t* l = d->ics[0]; uint8_t* r = d->ics[1];
			int common = bget(&b, 1);
			if (common) {
				int e = ics_info(d, &b, l);
				if (e) return fail(d, e);
				int mm = bget(&b, 2);
				W16(l, O_MS_MASK, mm);
				if (mm == 1)
					for (int g = 0; g < LD11(l, O_NUM_GROUPS); g++)
						for (int s = 0; s < LD11(l, O_MAX_SFB); s++) {
							if (!elem_ok(d, l, O_MS_USED + 51 * g + s)) return fail(d, -2);
							l[O_MS_USED + 51 * g + s] = bget(&b, 1);
						}
				memcpy(r, l, ICS_SZ);
			}
			first_ch = nch;
			int e = individual_channel_stream(d, &b, l, common, 0);
			if (e) return fail(d, e);
			e = individual_channel_stream(d, &b, r, common, 1);
			if (e) return fail(d, e);
			nch += 2; nelem++;
		} else if (id == 2) {
			return fail(d, 16);
		} else if (id == 4) {               /* DSE (0x1be10) */
			bget(&b, 4);
			int align = bget(&b, 1), cnt = bget(&b, 8);
			if (cnt == 255) cnt += bget(&b, 8);
			if (align) b.pos = (b.pos + 7) & ~(size_t) 7;
			b.pos += 8 * (size_t) cnt;
		} else if (id == 6) {               /* FIL */
			if (fill_element(&b)) return PYAAC_UNDEFINED;
		} else {
			return PYAAC_UNSUPPORTED;   /* PCE (0x2ad70): not modelled */
		}
	}
	if (nch > 2) nch = 2;
	/* channel c -> ic_stream: the element's first channel gets ic_stream 0, the next ic_stream 1;
	 * after two SCEs (r11) simply c */
#define ICS(c) (d->ics[second_sce ? (c) : (c) != first_ch])
	for (int c = 0; c < nch; c++) {
		iquant(d, c, d->spec[c]);
		apply_scalefactors(d, ICS(c), d->spec[c]);
	}
	for (int c = 0; c < nch; c++)
		if (LD11(ICS(c), O_WSEQ) == EIGHT_SHORT)
			reorder_short(ICS(c), d->spec[c]);
	for (int c = 0; c < nch; c++) {
		uint8_t* ic = ICS(c);
		int pair = !second_sce && c == first_ch;   /* M/S and IS run for the element's first channel */
		if (pair) ms_decode(d, d->ics[0], d->ics[1], d->spec[0], d->spec[1]);
		pns_decode(d, ic, d->spec[c]);
		if (pair) is_decode(d, d->ics[0], d->ics[1], d->spec[0], d->spec[1]);
		tns_decode(d, ic, d->spec[c]);
		int ws = LD11(ic, O_WSEQ), wsh = LD11(ic, O_WSHAPE);
		filter_bank(d, c, ws, wsh, d->wshape_prev[c], d->spec[c], d->time[c]);
		d->wshape_prev[c] = wsh;
	}
	if (d->undefined) return PYAAC_UNDEFINED;
	/* 0x266de writes the PCM even without a channel element: channel 0's output half, to both sides */
	const int32_t* L = d->time[0]; const int32_t* R = nch >= 2 ? d->time[1] : d->time[0];
	for (int i = 0; i < 1024; i++) {
		pcm[2 * i] = (int16_t) sat16(W32((int64_t) L[i] + 512) >> 10);
		pcm[2 * i + 1] = (int16_t) sat16(W32((int64_t) R[i] + 512) >> 10);
	}
	/* frame info samples (0xd37a): channels * 1024, but 0 for the first two decoded frames */
	d->frame_count++;
	return d->frame_count > 2 ? nch * 1024 : 0;
}

/* debug accessors (differential tests) */
const int32_t* pyaac_dbg_spec(pyaac* d, int ch) { return d->spec[ch & 1]; }
int pyaac_dbg_ics(pyaac* d, int ch, int what) {
	const uint8_t* i = d->ics[ch & 1];
	switch (what) {
	case 0: return LD11(i, O_WSEQ); case 1: return LD11(i, O_WSHAPE); case 2: return LD11(i, O_MAX_SFB);
	case 3: return S16(i, O_MS_MASK); case 4: return LD11(i, O_TNS); case 5: return LD11(i, O_PULSE);
	case 6: return LD11(i, O_NUM_GROUPS);
	}
	return -1;
}
int pyaac_dbg_undefined(pyaac* d) { return d->undefined; }
const int32_t* pyaac_dbg_time(pyaac* d, int ch) { return d->time[ch & 1]; }
const uint8_t* pyaac_dbg_elem(pyaac* d) { return d->elem; }
