/* VC01 core in C.  Mirrors vc01.py (decode() + CPU.step()) instruction by instruction, plus the
 * vector model vc01v.py (lockstep.py checks both).  Additions over the Python model (documented in
 * docs/playyan/vc01-isa.md and vc01-hardware.md):
 *   - interrupts: entry pushes pc then sr (sp -= 8; [sp] = sr; [sp+4] = pc), clears IE, jumps to
 *     the 32-bit pointer at 4*vector.  rti restores sr (IE + flags) and pc.
 *   - sr (r30): bit 30 = IE (ei/di), flags packed in bits 3..0 (N Z C V) whenever sr is read.
 *   - 0x4330 = wait-for-interrupt (Nucleus idle loop, SLEEP handler); 0x5230 = halt.
 *   - 0x5430 = NOP (unknown, rare, vector code).
 */
#include "vc01core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define M32 0xffffffffu

static inline int32_t sx(uint32_t v, int b) {
	v &= (b == 32) ? M32 : ((1u << b) - 1);
	if (b < 32 && (v & (1u << (b - 1)))) return (int32_t) (v - (1u << b));
	return (int32_t) v;
}

/* ---------------------------------------------------------------- memory */
/* The bus ignores the low address bits of halfword and word accesses (py_fw0 0x13b7e reads the
 * heap bounds with pc-relative loads at pc+0x1a420 = 0x2df9e -> must read the word at 0x2df9c). */
uint32_t vc_read(VC01* c, uint32_t a, int n) {
	if (a & (n - 1)) { c->unaligned++; if (c->align) a &= ~(uint32_t) (n - 1); }
	if ((a & 0xf0000000u) == 0x10000000u) {
		return c->mmio_rd ? c->mmio_rd(c->ctx, a, n) : 0;
	}
	if (a + (uint32_t) n <= c->ram_size && a + (uint32_t) n >= a) {
		const uint8_t* p = c->ram + a;
		if (n == 4) return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t) p[3] << 24;
		if (n == 2) return p[0] | p[1] << 8;
		return p[0];
	}
	c->unmapped++;
	c->unmapped_last = a;
	uint32_t v = 0;
	for (int i = 0; i < n; i++) {
		uint32_t x = (a + i) & (c->ram_size - 1);
		v |= (uint32_t) c->ram[x] << (8 * i);
	}
	return v;
}

void vc_write(VC01* c, uint32_t a, int n, uint32_t v) {
	if (a & (n - 1)) { c->unaligned++; if (c->align) a &= ~(uint32_t) (n - 1); }
	if (c->watch && a <= c->watch && c->watch < a + n) c->watch_hit = 1;
	if ((a & 0xf0000000u) == 0x10000000u) {
		if (c->mmio_wr) c->mmio_wr(c->ctx, a, n, v);
		return;
	}
	if (a + (uint32_t) n <= c->ram_size && a + (uint32_t) n >= a) {
		uint8_t* p = c->ram + a;
		p[0] = v;
		if (n >= 2) p[1] = v >> 8;
		if (n == 4) { p[2] = v >> 16; p[3] = v >> 24; }
		return;
	}
	c->unmapped++;
	c->unmapped_last = a;
	for (int i = 0; i < n; i++) c->ram[(a + i) & (c->ram_size - 1)] = v >> (8 * i);
}

static inline uint16_t rd16(VC01* c, uint32_t a) {
	if (!(a & 1) && a + 2 <= c->ram_size) return c->ram[a] | c->ram[a + 1] << 8;
	return vc_read(c, a, 2);
}

/* ---------------------------------------------------------------- flags */
static inline void fsub(VC01* c, uint32_t a, uint32_t b) {
	uint32_t r = a - b;
	c->N = r >> 31; c->Z = r == 0; c->C = a < b;
	c->V = (((a ^ b) & (a ^ r)) >> 31) & 1;
}
static inline void fadd(VC01* c, uint32_t a, uint32_t b) {
	uint64_t s = (uint64_t) a + b; uint32_t r = (uint32_t) s;
	c->N = r >> 31; c->Z = r == 0; c->C = s > M32;
	c->V = ((~(a ^ b) & (a ^ r)) >> 31) & 1;
}
static inline int cond(VC01* c, int cc) {
	uint32_t N = c->N, Z = c->Z, C = c->C, V = c->V;
	switch (cc) {
	case 0: return Z; case 1: return !Z; case 2: return C; case 3: return !C;
	case 4: return N; case 5: return !N; case 6: return V; case 7: return !V;
	case 8: return !C && !Z; case 9: return C || Z; case 10: return N == V; case 11: return N != V;
	case 12: return !Z && N == V; case 13: return Z || N != V; case 14: return 1; default: return 0;
	}
}

uint32_t vc_get_sr(VC01* c) {
	return (c->r[30] & ~0xfu) | c->N << 3 | c->Z << 2 | c->C << 1 | c->V;
}
void vc_set_sr(VC01* c, uint32_t v) {
	c->r[30] = v;
	c->N = v >> 3 & 1; c->Z = v >> 2 & 1; c->C = v >> 1 & 1; c->V = v & 1;
}

/* ---------------------------------------------------------------- ALU */
enum {
	A_BTST, A_CMN, A_BMASK, A_BIC, A_ASR, A_EOR, A_SHL, A_AND, A_LSR, A_OP9, A_MUL, A_RSUB, A_ADD, A_SUB, A_MOV, A_OR,
	A_CMP, A_OP17, A_MAX, A_MIN, A_ADDS2, A_ADDS4, A_ADDS8, A_ADDS16, A_NEG, A_MSB, A_EXTS8, A_EXTS16, A_BREV, A_OP29, A_ABS, A_NOT,
	A_MULHD_SS, A_MULHD_SU, A_MULHD_US, A_MULHD_UU, A_DIVS, A_DIV_SU, A_DIV_US, A_DIVU,
	A_BAD
};
/* op4 (hb 0x60-0x77) -> ALU */
static const int OP4[16] = {A_BTST, A_BMASK, A_ASR, A_SHL, A_LSR, A_MUL, A_ADD, A_MOV, A_CMP, A_BAD, A_BAD, A_BAD, A_BAD, A_BAD, A_BAD, A_BAD};
__attribute__((unused)) static const char* ANAME[] = {"btst","cmn","bmask","bic","asr","eor","shl","and","lsr","op9","mul","rsub","add","sub","mov","or",
	"cmp","op17","max","min","adds2","adds4","adds8","adds16","neg","msb","exts8","exts16","brev","op29","abs","not",
	"mulhd.ss","mulhd.su","mulhd.us","mulhd.uu","divs","div.su","div.us","divu","bad"};

static int fault(VC01* c, uint32_t pc, const char* fmt, uint32_t a, uint32_t b) {
	c->stop = VC_STOP_FAULT;
	c->fault_pc = pc;
	snprintf(c->fault_msg, sizeof(c->fault_msg), fmt, a, b);
	return -1;
}

/* returns 1 if rd written (value in *out), 0 if flags-only, -1 if unimplemented */
static inline int alu(VC01* c, int op, uint32_t d, uint32_t s, uint32_t* out) {
	uint32_t v;
	switch (op) {
	case A_MOV: v = s; break;
	case A_ADD: v = d + s; break;
	case A_SUB: v = d - s; break;
	case A_RSUB: v = s - d; break;
	case A_AND: v = d & s; break;
	case A_OR: v = d | s; break;
	case A_MUL: v = d * s; break;
	case A_SHL: v = d << (s & 31); break;
	case A_LSR: v = d >> (s & 31); break;
	case A_ASR: v = (uint32_t) ((int32_t) d >> (s & 31)); break;
	case A_NEG: v = -s; break;
	case A_EOR: v = d ^ s; break;
	case A_MSB: v = s ? 31 - __builtin_clz(s) : M32; break;
	case A_EXTS16: v = (uint32_t) (int32_t) (int16_t) s; break;
	case A_EXTS8: v = (uint32_t) (int32_t) (int8_t) s; break;
	case A_BIC: v = d & ~s; break;
	case A_ABS: v = (int32_t) s < 0 ? -s : s; break;
	case A_BREV: { v = 0; for (int i = 0; i < 32; i++) if (s >> i & 1) v |= 1u << (31 - i); break; }
	case A_MAX: v = (int32_t) d > (int32_t) s ? d : s; break;
	case A_MIN: v = (int32_t) d < (int32_t) s ? d : s; break;
	case A_ADDS2: v = d + (s << 1); break;
	case A_ADDS4: v = d + (s << 2); break;
	case A_ADDS8: v = d + (s << 3); break;
	case A_ADDS16: v = d + (s << 4); break;
	case A_NOT: v = ~s; break;
	case A_BMASK: v = (s & 31) ? d & ((1u << (s & 31)) - 1) : 0; break;
	case A_CMP: fsub(c, d, s); return 0;
	case A_CMN: fadd(c, d, s); return 0;
	case A_BTST: c->Z = !((d >> (s & 31)) & 1); return 0;
	case A_DIVU: v = s ? d / s : M32; break;
	case A_DIVS: {
		int64_t a = (int32_t) d, b = (int32_t) s;
		if (!b) { v = M32; break; }
		int64_t q = (a < 0 ? -a : a) / (b < 0 ? -b : b);
		v = (uint32_t) ((a < 0) == (b < 0) ? q : -q);
		break;
	}
	case A_MULHD_UU: v = (uint32_t) (((uint64_t) d * s) >> 32); break;
	case A_MULHD_SS: v = (uint32_t) (((int64_t) (int32_t) d * (int32_t) s) >> 32); break;
	case A_MULHD_SU: v = (uint32_t) (((__int128) (int32_t) d * (__int128) s) >> 32); break;
	case A_MULHD_US: v = (uint32_t) (((__int128) d * (__int128) (int32_t) s) >> 32); break;
	default: return -1;
	}
	*out = v;
	return 1;
}

/* ---------------------------------------------------------------- vector unit (g726_vemu.py) */
static void vf_log(VC01* c, uint32_t pc, const uint16_t* w, int n, uint32_t why) {
	for (int i = 0; i < c->nvf; i++) {
		if (c->vf_pc[i] == pc) { c->vf_count[i]++; return; }
	}
	if (c->nvf >= VC_NVF) return;
	int k = c->nvf++;
	c->vf_pc[k] = pc;
	for (int i = 0; i < 5; i++) c->vf_w[k][i] = i < n ? w[i] : 0;
	c->vf_len[k] = n;
	c->vf_count[k] = 1;
	c->vf_why[k] = why;
}

/* why codes for the fault log */
enum { VF_OP = 1, VF_COND = 2, VF_MEMB = 3, VF_SRU = 4, VF_W8 = 5, VF_NOPMODE = 6 };
const char* vc_vf_why(int w) {
	static const char* T[] = {"?", "opcode not modelled", "condition not modelled", "memory B form not modelled", "sru", "8-bit vector memory access", "vec NOP mode"};
	return (w >= 0 && w <= 6) ? T[w] : "?";
}

/* ---------------------------------------------------------------- vector unit
 * Exact port of vc01v.py VPU (merged model, CFG defaults hard-wired):
 * byte-addressed VRF P[64][64]; 16-bit lane at (y,x) = P[y][x] | P[y][x+16] << 8. */
typedef struct { int y, x, v, w, inc; } Opd;

static inline int bx(int c) { c &= 63; return (c & ~0x30) | ((c >> 1) & 0x10) | ((c << 1) & 0x20); }

static inline Opd mkopd(int y, int x, int v, int w, int inc) { Opd o = {y & 63, x & 63, v, w, inc}; return o; }

static inline void spec_yx(int s, int vert, int* y, int* x) {
	int n = s & 15, hi = s >> 6, mid = (s >> 4) & 3;
	if (vert) { *y = hi << 4; *x = (mid << 4) | n; }
	else { *y = (hi << 4) | n; *x = mid << 4; }
}

static inline Opd opd(int s, int vert, int w16, uint32_t off, int inc) {
	int y, x; spec_yx(s, vert, &y, &x);
	return mkopd(y + ((off >> 6) & 63), x + (off & 63), vert, w16, inc);
}

static inline void lanes(const Opd* o, int k, uint8_t* ys, uint8_t* xs) {
	if (!o->inc) k = 0;
	if (o->v) {
		int x = (o->w) ? bx(bx(o->x) + k) : ((o->x + k) & 63);
		for (int i = 0; i < 16; i++) { ys[i] = (o->y + i) & 63; xs[i] = x; }
	} else {
		int y = (o->y + k) & 63;
		for (int i = 0; i < 16; i++) { ys[i] = y; xs[i] = (o->x + i) & 63; }
	}
}

static inline int32_t get16(VC01* c, int y, int x) { return (int16_t) (c->P[y][x] | c->P[y][(x + 16) & 63] << 8); }
static inline void put16(VC01* c, int y, int x, int32_t v) { c->P[y][x] = v; c->P[y][(x + 16) & 63] = v >> 8; }

static void rdo(VC01* c, const Opd* o, int k, int32_t* out) {
	uint8_t ys[16], xs[16];
	lanes(o, k, ys, xs);
	for (int L = 0; L < 16; L++) out[L] = o->w ? get16(c, ys[L], xs[L]) : c->P[ys[L]][xs[L]];
}

static void wro(VC01* c, const Opd* o, int k, const int32_t* v, const uint8_t* mask) {
	uint8_t ys[16], xs[16];
	lanes(o, k, ys, xs);
	for (int L = 0; L < 16; L++) {
		if (mask && !mask[L]) continue;
		if (o->w) put16(c, ys[L], xs[L], v[L]); else c->P[ys[L]][xs[L]] = v[L];
	}
}

/* vector memory accesses are not forced to alignment (vc01v.py uses raw Mem.read/write) */
static inline uint32_t vmr(VC01* c, uint32_t a, int n) { int al = c->align; c->align = 0; uint32_t v = vc_read(c, a, n); c->align = al; return v; }
static inline void vmw(VC01* c, uint32_t a, int n, uint32_t v) { int al = c->align; c->align = 0; vc_write(c, a, n, v); c->align = al; }

/* column base: added to the column of operands whose offset flag is set (80-bit ALU, memory ops) */
static inline Opd cb(VC01* c, Opd o, int flag) { if (flag && c->cbase) o.x = (o.x + c->cbase) & 63; return o; }

static inline uint32_t vreg(VC01* c, int n) { return n == 7 ? 0 : c->r[n]; }

/* 80-bit A operand (and 80-bit vst source); f = h3 bits 9..5 */
static Opd afield(int A10, uint16_t h3, uint32_t off, int inc, int w16) {
	int f = (h3 >> 5) & 31;
	if (f & 16)
		return mkopd(((((A10 >> 6) & 3) << 4) | (f & 15)) + ((off >> 6) & 63), (A10 & 63) + (off & 63), 0, 0, inc);
	int av = (A10 & 0x200) != 0, y, x;
	spec_yx(A10 & 0xff, av, &y, &x);
	if (f == 0 && w16) return mkopd(y + ((off >> 6) & 63), x + (off & 63), av, 0, 0);   /* 8-bit A, no REP step with a 16-bit D */
	if (av) x += f & 3; else y += f & 3;   /* f == 0 with an 8-bit D: 8-bit, steps per inc (f & 8 == 0) */
	return mkopd(y + ((off >> 6) & 63), x + (off & 63), av, (f & 8) != 0, inc);
}

static inline int vcond(VC01* c, int P, int L) {
	switch (P) {
	case 0: return 1;
	case 2: return c->vz[L];
	case 3: return !c->vz[L];
	case 4: return c->vn[L];
	case 5: return !c->vn[L];
	case 6: return c->vc[L];
	default: return !c->vc[L];   /* 7; P == 1 is rejected by the caller */
	}
}

static inline int32_t sat16(int64_t x, int* cy) { if (x > 32767) { *cy = 1; return 32767; } if (x < -32768) { *cy = 1; return -32768; } *cy = 0; return (int32_t) x; }

/* one lane; a, b: 16-bit signed (or 0..255 for 8-bit reads); returns result (caller truncates to
 * 16 bits); *cy = carry out or -1 (flag unchanged); dw = destination width (8/16) */
static inline int64_t lane_op(int op, int32_t a, int32_t b, int cin, int dw, int L, int* cy) {
	uint32_t ua = (uint16_t) a, ub = (uint16_t) b;
	int64_t v;
	*cy = -1;
	switch (op) {
	case 4: return b;
	case 5: return a & b;
	case 6: return a | b;
	case 7: return a ^ b;
	case 8: v = (int64_t) ua + ub; *cy = (int) (v >> 16) & 1; return v;
	case 9: v = (int64_t) a + b; if (dw == 8) v = v < 0 ? 0 : v > 255 ? 255 : v; return v;
	case 10: *cy = ua < ub; return (int64_t) ua - ub;
	case 11: return (int64_t) a - b;
	case 12: *cy = ub < ua; return (int64_t) ub - ua;
	case 13: return a < b;
	case 14: return (int64_t) a * b;
	case 15: { int t; return sat16((int64_t) a * b, &t); }
	case 16: return ((int64_t) a * b) >> 8;
	case 17: v = ((int64_t) a * b) >> 8;    /* saturated to the destination width */
		if (dw == 8) return v < 0 ? 0 : v > 255 ? 255 : v;
		return v < -32768 ? -32768 : v > 32767 ? 32767 : v;
	case 18: return ((int64_t) a * b) >> 16;
	case 19: return ((int64_t) a * ub) >> 16;
	case 20: return ((int64_t) ua * ub + 0x8000) >> 16;
	case 21: return ((int64_t) a * ub + 0x8000) >> 16;
	case 23: return ((int64_t) a * ub) / 65536;          /* truncation toward zero */
	case 24: { uint32_t r = ua << (b & 15); *cy = (r >> 16) & 1; return r; }
	case 25: { int t; v = sat16((int64_t) a * ((int64_t) 1 << (b & 15)), &t); *cy = t; return v; }
	case 26: { int s = b & 31; if (s) *cy = (a >> (s - 1)) & 1; else *cy = cin; return a >> s; }
	case 27: { int s = b & 31; if (s) *cy = (ua >> (s - 1)) & 1; else *cy = cin; return ua >> s; }
	case 28: return a < b ? a : b;
	case 29: return a > b ? a : b;
	case 30: return a > b ? (int64_t) a - b : (int64_t) b - a;
	case 31: return (a < 0 ? -a : a) >= b;
	case 34: v = (int64_t) ua + ub + cin; *cy = (int) (v >> 16) & 1; return v;
	case 35: v = (int64_t) ua - ub - cin; *cy = v < 0; return v;
	case 36: v = (int64_t) ub - ua - cin; *cy = v < 0; return v;
	case 37: { int32_t m = a < b ? a : b; return m < 0 ? 0 : m; }
	case 40: return (int64_t) b + (a > 0) - (a < 0);
	case 43:
		if (b >= 0) return b >= 32 ? 0 : (int64_t) ((uint64_t) (int64_t) a << b);
		return b <= -32 ? (a < 0 ? -1 : 0) : (a >> -b);
	case 44: return (b >> L) & 1 ? -1 : 0;
	}
	return 0;
}

static inline int vop_known(int op) {
	switch (op) {
	case 4: case 5: case 6: case 7: case 8: case 9: case 10: case 11: case 12: case 13: case 14: case 15:
	case 16: case 17: case 18: case 19: case 20: case 21: case 23: case 24: case 25: case 26: case 27: case 28:
	case 29: case 30: case 31: case 34: case 35: case 36: case 37: case 40: case 42: case 43: case 44:
		return 1;
	}
	return 0;
}

static int vec_mem(VC01* c, uint32_t pc, const uint16_t* w, int n) {
	uint16_t h0 = w[0], h1 = w[1], h2 = w[2];
	int op = (h0 >> 3) & 63, cmpt = (h0 >> 9) & 1, vert = (h0 & 4) != 0, w16 = (h0 & 2) != 0;
	int D = h1 >> 8, A = ((h1 & 0x1f) << 5) | (h2 >> 11), ib = (h2 >> 10) & 1, lo = h2 & 0x3ff;
	int rc = (lo >> 6) & 15;
	uint32_t rep = 1u << rc;
	if (rc == 7 && c->memrep7 == 0) rep = 64;   /* patent REPn max 64 (US20030159023); mi_fw0 0x3d72 AAC short windows [C] */
	uint32_t base = c->r[(lo >> 3) & 7];
	int sreg = lo & 7;
	uint8_t actb[16], *act = 0;
	uint32_t addr;
	Opd o;
	if (op == 1) {
		addr = base + 32 * D + 16 * ib;
		if (cmpt) o = cb(c, opd(A & 0xff, (A & 0x200) != 0, w16, 0, 1), A & 0x100);
		else {
			int f = (w[3] >> 5) & 31;
			o = cb(c, afield(A, w[3], vreg(c, (w[3] >> 1) & 7), f != 8 || (w[3] & 0x10), w16), A & 0x100);
		}
	} else {
		int32_t a = sx(A, 10);
		addr = base + 16 * (uint32_t) ((a * 2) | ib);
		if (cmpt) o = cb(c, opd(D, vert, w16, 0, 1), h0 & 1);
		else {
			addr += 2 * (w[3] >> 13);
			uint16_t h4 = w[4];
			o = cb(c, opd(D, vert, w16, vreg(c, (h4 >> 5) & 7), (w[3] & 0x11) || rep == 1), h0 & 1);
			int P = (h4 >> 11) & 7;
			if (P) {
				if (P == 1) { vf_log(c, pc, w, n, VF_COND); return -2; }
				for (int L = 0; L < 16; L++) actb[L] = vcond(c, P, L);
				act = actb;
			}
		}
	}
	int nb = o.w ? 2 : 1;
	uint32_t stride = sreg != 7 ? c->r[sreg] : 16u * nb;
	for (uint32_t k = 0; k < rep; k++) {
		uint32_t a = addr + k * stride;
		if (op <= 1) a &= ~(uint32_t) (16 * nb - 1);
		int32_t vals[16];
		if (op == 1) {
			rdo(c, &o, k, vals);
			if (rc == 7 && c->memrep7 == 2 && k >= 64) for (int L = 0; L < 16; L++) vals[L] = 0;
			for (int L = 0; L < 16; L++) vmw(c, a + nb * L, nb, (uint32_t) vals[L]);
		} else {
			for (int L = 0; L < 16; L++) vals[L] = vmr(c, a + nb * L, nb);
			uint8_t m3[16], *mk = act;
			if (op == 3) {
				uint32_t blk = 16 * nb;
				for (int L = 0; L < 16; L++) m3[L] = ((a + nb * L) / blk != a / blk) && (!act || act[L]);
				mk = m3;
			}
			wro(c, &o, k, vals, mk);
		}
	}
	return 0;
}

static inline void vhist(VC01* c, uint32_t pc) {
	uint32_t k = (pc >> 1) * 2654435761u >> 19;
	for (int i = 0; i < 8192; i++, k = (k + 1) & 8191) {
		if (c->vh_pc[k] == pc) { c->vh_n[k]++; return; }
		if (!c->vh_n[k]) { c->vh_pc[k] = pc; c->vh_n[k] = 1; return; }
	}
}

static int vec_exec(VC01* c, uint32_t pc, const uint16_t* w, int n) {
	vhist(c, pc);
	uint16_t h0 = w[0], h1 = w[1], h2 = w[2];
	int op = (h0 >> 3) & 63, cmpt = (h0 >> 9) & 1;
	int vert = (h0 & 4) != 0, w16 = (h0 & 2) != 0;
	int D = h1 >> 8, dfl = (h1 >> 5) & 7;
	int A = ((h1 & 0x1f) << 5) | (h2 >> 11);
	int ib = (h2 >> 10) & 1, lo = h2 & 0x3ff;
	if (c->vec_mode == 2) { vf_log(c, pc, w, n, VF_NOPMODE); return 0; }
	if (op <= 3) {
		int e = vec_mem(c, pc, w, n);
		if (e == -2) goto unk;
		c->vec_exec++;
		return 0;
	}
	if (!vop_known(op)) { vf_log(c, pc, w, n, VF_OP); goto unk; }
	int setf = 0, P = 0, so = -1, sreg = 7, haveimm = 0, bscal = -1, hasb = 0, accm = 0, clra = 0, repcode = 0;
	int32_t imm = 0;
	uint32_t rep = 1;
	uint16_t h3 = 0, h4 = 0;
	Opd Do, Ao, Bo;
	if (cmpt) {
		if (ib) { imm = sx(h2 & 63, 6); haveimm = 1; setf = (h2 >> 9) & 1; P = (h2 >> 6) & 7; }
		else hasb = 1;
		uint32_t o = vreg(c, dfl);
		Do = opd(D, vert, w16, (h0 & 1) ? o : 0, 1);
		Ao = opd(A & 0xff, (A & 0x200) != 0, w16, (A & 0x100) ? o : 0, 1);
		if (hasb) Bo = opd(lo >> 2, lo & 1, w16, (lo & 2) ? o : 0, 1);
	} else {
		h3 = w[3]; h4 = w[4];
		if (ib) { imm = (int16_t) ((h3 & 0xfc00) | lo); haveimm = 1; }
		else if (h3 & 0x8000) bscal = (h3 >> 12) & 7;
		else hasb = 1;
		setf = (h4 >> 14) & 1; P = (h4 >> 11) & 7;
		clra = (h4 >> 10) & 1; accm = (h4 >> 9) & 1;
		repcode = (h4 >> 5) & 15; rep = 1u << repcode;
		if ((h4 & 7) != 7) { so = (h4 >> 3) & 3; sreg = h4 & 7; }
		int st = h3 & 1;
		Do = cb(c, opd(D, vert, w16, vreg(c, dfl), st), h0 & 1);
		Ao = cb(c, afield(A, h3, vreg(c, (h3 >> 1) & 7), st, w16), A & 0x100);
		if (hasb) { int bf = h3 >> 10; Bo = cb(c, opd(lo >> 2, lo & 1, (bf & 2) != 0, vreg(c, (bf >> 2) & 7), bf & 1), lo & 2); }
	}
	if (P == 1) { vf_log(c, pc, w, n, VF_COND); goto unk; }
	int dw = Do.w ? 16 : 8;
	if (op == 42) {
		Do = opd(D, vert, w16, cmpt ? 0 : vreg(c, (h4 >> 5) & 7), 1);
		rep = 1;
	}
	if (!cmpt && repcode == 7 && op != 42) rep = c->r[0];
	if (clra) memset(c->acc, 0, sizeof c->acc);
	int discard = Do.w && !Do.v && D == 0x10;
	for (uint32_t k = 0; k < rep; k++) {
		int32_t av[16], bv[16], out[16];
		int cy[16];
		uint8_t act[16];
		rdo(c, &Ao, k, av);
		if (haveimm) for (int L = 0; L < 16; L++) bv[L] = imm;
		else if (bscal >= 0) for (int L = 0; L < 16; L++) bv[L] = (int16_t) c->r[bscal];
		else rdo(c, &Bo, k, bv);
		for (int L = 0; L < 16; L++) act[L] = vcond(c, P, L);
		if (op == 42) {
			uint32_t base = c->r[(lo >> 3) & 7];
			for (int L = 0; L < 16; L++) { out[L] = (int16_t) vmr(c, base + 2 * (uint32_t) (uint16_t) c->acc[L], 2); cy[L] = -1; }
		} else {
			for (int L = 0; L < 16; L++) out[L] = (int16_t) lane_op(op, av[L], bv[L], c->vc[L], dw, L, &cy[L]);
		}
		if (accm) {
			for (int L = 0; L < 16; L++) if (act[L]) {
				int32_t t = c->acc[L] + out[L];
				c->acc[L] = t > 32767 ? 32767 : t < -32768 ? -32768 : t;
				out[L] = c->acc[L];
			}
		}
		if (!Do.w) for (int L = 0; L < 16; L++) out[L] &= 0xff;
		if (!discard) wro(c, &Do, k, out, act);
		if (setf) for (int L = 0; L < 16; L++) if (act[L]) {
			c->vn[L] = Do.w ? out[L] < 0 : (out[L] >> 7) & 1;
			c->vz[L] = out[L] == 0;
			if (cy[L] >= 0) c->vc[L] = cy[L];
		}
		if (so >= 0) {
			int64_t v = 0;
			int any = 0;
			for (int L = 0; L < 16; L++) any |= act[L];
			if (so == 0) { for (int L = 0; L < 16; L++) if (act[L]) v += Do.w ? (out[L] & 0xffff) : out[L]; }
			else if (so == 1) { for (int L = 0; L < 16; L++) if (act[L]) v += out[L]; }
			else if (!any) v = -1;
			else if (so == 2) { int m = -1; for (int L = 0; L < 16; L++) if (act[L] && (m < 0 || out[L] < out[m])) m = L; v = m; }
			else { int m = -1; for (int L = 0; L < 16; L++) if (act[L] && (m < 0 || out[L] >= out[m])) m = L; v = m; }
			c->r[sreg] = (uint32_t) v;
		}
	}
	c->vec_exec++;
	return 0;
unk:
	c->vec_unknown++;
	if (c->vec_mode == 0) return fault(c, pc, "vector instruction not modelled (h0=%04x)", h0, 0);
	return 0;
}

/* ---------------------------------------------------------------- interrupts */
int vc_take_irq(VC01* c, int vec) {
	uint32_t sr = vc_get_sr(c);
	uint32_t sp = c->r[25] - 8;
	vc_write(c, sp, 4, sr);
	vc_write(c, sp + 4, 4, c->r[31]);
	c->r[25] = sp;
	c->r[30] &= ~(1u << 30);
	c->r[31] = vc_read(c, 4 * vec, 4);
	c->wfi = 0;
	c->nirq++;
	return 1;
}

/* ---------------------------------------------------------------- execute */
static const int MSIZE[8] = {4, 4, 2, 2, 1, 1, 2, 1};   /* ld st ldh sth ldb stb ldsh ldsb */

static inline void do_mem(VC01* c, int w, int rd, uint32_t base, int32_t off, int idxreg, int postinc, int basereg) {
	int size = MSIZE[w];
	uint32_t ea = base + (uint32_t) off;
	if (idxreg >= 0) ea += c->r[idxreg] * (uint32_t) size;
	if (w == 1 || w == 3 || w == 5) {
		vc_write(c, ea, size, c->r[rd]);
	} else {
		uint32_t v = vc_read(c, ea, size);
		if (w == 6) v = (uint32_t) (int32_t) (int16_t) v;
		else if (w == 7) v = (uint32_t) (int32_t) (int8_t) v;
		c->r[rd] = v;
	}
	if (postinc) c->r[basereg] += size;
}

/* memsel: selector + imm16 -> (w, offset) ; w index in MSIZE order */
static inline void memsel(int sel, uint32_t imm, int* w, int32_t* off) {
	int32_t o = sx(imm, 16);
	if (sel == 0) {
		if (imm & 1) { *w = 2; *off = o & ~1; return; }
		*w = (imm & 2) ? 1 : 0; *off = o & ~3; return;
	}
	if (sel == 1) { *w = (imm & 1) ? 6 : 3; *off = o & ~1; return; }
	*w = sel == 2 ? 4 : 5; *off = o;
}

static inline uint32_t R(VC01* c, int r) {
	if (r == 30) return vc_get_sr(c);
	return c->r[r];
}
static int pcw;
static uint32_t pcv;
static inline void W(VC01* c, int r, uint32_t v) {
	if (r == 30) vc_set_sr(c, v);
	else if (r == 31) { pcw = 1; pcv = v; }   /* write to pc = jump (Python model drops it) */
	else c->r[r] = v;
}

static int step_inner(VC01* c);
int vc_step(VC01* c) {
	pcw = 0;
	int k = step_inner(c);
	if (pcw) { c->r[31] = pcv; pcw = 0; }
	return k;
}

static int step_inner(VC01* c) {
	uint32_t* r = c->r;
	uint32_t pc = r[31];
	if (c->trace) c->trace(c->ctx, pc);
	if (pc == c->cap_pc && c->cap_buf && c->cap_n < c->cap_max) {
		uint8_t* d = c->cap_buf + (size_t) c->cap_n++ * (4096 + 128 + 1024);
		memcpy(d, c->P, 4096); memcpy(d + 4096, c->r, 128);
		for (int k = 0; k < 1024; k++) d[4224 + k] = vc_read(c, c->r[c->cap_reg] + k, 1);   /* memory at r[cap_reg] */
	}
	c->ring[c->ringi++ & 255] = pc;
	uint16_t h = rd16(c, pc);
	int hb = h >> 8;
	uint32_t npc;
	c->icount++;
	if (hb < 0x10) {                                 /* addcmpb */
		uint16_t h2 = rd16(c, pc + 2);
		int cc = hb & 15, aa = (h >> 4) & 15, d = h & 15, mode = h2 >> 14;
		uint32_t bv, av; int32_t off;
		if (mode <= 1) { bv = r[(h2 >> 10) & 15]; off = sx(h2 & 0x3ff, 10); }
		else { bv = (h2 >> 8) & 63; off = sx(h2 & 0xff, 8); }
		av = (mode == 0 || mode == 2) ? r[aa] : (uint32_t) sx(aa, 4);
		uint32_t v = r[d] + av;
		r[d] = v;
		uint32_t sN = c->N, sZ = c->Z, sC = c->C, sV = c->V;
		fsub(c, v, bv);
		int t = cond(c, cc);
		c->N = sN; c->Z = sZ; c->C = sC; c->V = sV;
		npc = t ? pc + 2 * off : pc + 4;
		r[31] = npc;
		return 0;
	}
	if (hb < 0x20) {                                 /* b / bl */
		uint16_t w1 = rd16(c, pc + 2);
		if (h & 0x80) {
			uint32_t off = ((uint32_t) (h >> 8 & 15) << 23) | ((uint32_t) w1 << 7) | (h & 0x7f);
			r[26] = pc + 4;
			r[31] = pc + 2 * sx(off, 27);
		} else {
			uint32_t off = ((uint32_t) w1 << 7) | (h & 0x7f);
			r[31] = cond(c, hb & 15) ? pc + 2 * sx(off, 23) : pc + 4;
		}
		return 0;
	}
	if (hb == 0x2f) {                                /* 48-bit */
		uint16_t w1 = rd16(c, pc + 2), w2 = rd16(c, pc + 4);
		int op = (h >> 5) & 7, rd = h & 31;
		npc = pc + 6;
		if (op == 3) { W(c, rd, w1 | (uint32_t) w2 << 16); r[31] = npc; return 0; }
		int rb = w2 >> 11;
		int32_t o = sx(((uint32_t) (w2 & 0x7ff) << 16) | w1, 27);
		uint32_t base = rb == 31 ? pc : R(c, rb);
		if (op == 1) { W(c, rd, base + o); r[31] = npc; return 0; }
		if (op == 0) {    /* offset bits 1..0: 00 ld, 01 st, 10 ldh (unsigned), 11 sth; address rb + (o & ~3) */
			uint32_t ea = base + (o & ~3);
			int sz = (o & 2) ? 2 : 4;
			if (o & 1) vc_write(c, ea, sz, R(c, rd)); else W(c, rd, vc_read(c, ea, sz));
			r[31] = npc; return 0;
		}
		if (op >= 4) {    /* lane k = op-4; offset bits 1..0: 00 ldb, 01 stb (at rb + (o & ~1) + k),
		                     * 10 ldsh, 11 ldsb (at rb + (o & ~3) + k).  11 was sth until 2026-11-08: the
		                     * AAC decoder work showed it is a (signed) byte load - mi_fw0 ics_info 0x21b24 reads
		                     * window_sequence back with 2f80 019f 4800 right after storing it, 0x3e90c / 0xcee8
		                     * load window_sequence / window_shape; every AAC frame failed (error 10) as sth. */
			int k = op - 4;
			if (o & 2) {
				uint32_t ea = base + (o & ~3) + k;
				if (o & 1) W(c, rd, (uint32_t) (int32_t) (int8_t) vc_read(c, ea, 1));
				else W(c, rd, (uint32_t) (int32_t) (int16_t) vc_read(c, ea, 2));
			} else {
				uint32_t ea = base + (o & ~1) + k;
				if (o & 1) vc_write(c, ea, 1, R(c, rd)); else W(c, rd, vc_read(c, ea, 1));
			}
			r[31] = npc; return 0;
		}
		return fault(c, pc, "unk48 op%d", op, 0);
	}
	if (hb == 0x2e) {
		uint16_t w1 = rd16(c, pc + 2);
		int op = (h >> 5) & 7, rd = h & 31;
		npc = pc + 4;
		if (op <= 3) {
			static const int T[4] = {A_CMP, A_MUL, A_ADD, A_MOV};
			uint32_t out;
			int k = alu(c, T[op], R(c, rd), (uint32_t) sx(w1, 16), &out);
			if (k > 0) W(c, rd, out);
			r[31] = npc; return 0;
		}
		if (op == 4 || op == 5) {
			int o6 = w1 >> 10, rb = (w1 >> 5) & 31, ra = w1 & 31, a;
			if (op == 5 && o6 < 32) a = o6;
			else a = o6 < 8 ? A_MULHD_SS + o6 : A_BAD;
			if (a == A_DIV_SU || a == A_DIV_US || a == A_BAD || a == A_OP9 || a == A_OP17 || a == A_OP29)
				return fault(c, pc, "alu3 op%d o6=%d not implemented", op, o6);
			uint32_t out;
			int k = alu(c, a, R(c, ra), R(c, rb), &out);
			if (k < 0) return fault(c, pc, "alu3 %d %d", op, o6);
			if (k > 0) W(c, rd, out);
			r[31] = npc; return 0;
		}
		return fault(c, pc, "2e op%d", op, 0);
	}
	if (hb == 0x2c) {
		uint16_t w1 = rd16(c, pc + 2);
		int rs = (h >> 4) & 15;
		r[h & 15] = R(c, rs) + sx(w1, 16);    /* rs 0..15 */
		r[31] = pc + 4; return 0;
	}
	if (hb == 0x2d) {
		uint16_t o = rd16(c, pc + 2);
		int m = (h >> 4) & 15, rr = h & 15;
		npc = pc + 4;
		if (m == 12 || m == 13) { r[rr] = r[m == 12 ? 24 : 25] + o; r[31] = npc; return 0; }
		if (m == 14) { r[rr] = pc + sx(o, 16); r[31] = npc; return 0; }
		if (m < 8) {
			int w; int32_t off;
			memsel(m & 3, o, &w, &off);
			r[31] = npc;   /* base is gp/sp, never pc */
			do_mem(c, w, rr, r[m < 4 ? 24 : 25], off, -1, 0, 0);
			return 0;
		}
		return fault(c, pc, "2d m%d", m, 0);
	}
	if (hb >= 0x28 && hb <= 0x2b) {
		uint16_t w1 = rd16(c, pc + 2);
		int w; int32_t off;
		memsel(hb - 0x28, w1, &w, &off);
		int b = (h >> 4) & 15;
		do_mem(c, w, h & 15, r[b], off, -1, 0, b);
		r[31] = pc + 4; return 0;
	}
	if (hb >= 0x20 && hb <= 0x23) {
		int n = hb <= 0x21 ? 5 : 3;
		uint16_t w[5];
		for (int i = 0; i < n; i++) w[i] = rd16(c, pc + 2 * i);
		if (vec_exec(c, pc, w, n) < 0) return -1;
		r[31] = pc + 2 * n; return 0;
	}
	if (hb >= 0x24 && hb <= 0x27) return fault(c, pc, "2x %04x", h, 0);
	if (hb >= 0x30 && hb <= 0x3f) {
		if (!(h & 0x80)) {
			if (hb == 0x3f) {
				if (h & 0x40) return fault(c, pc, "3f hi %04x", h, 0);
				r[25] += 4 * sx(h & 0x3f, 6);
				r[31] = pc + 2; return 0;
			}
			r[31] = cond(c, hb & 15) ? pc + 2 * sx(h & 0x7f, 7) : pc + 2;
			return 0;
		}
		if (((h >> 4) & 7) == 7) { r[h & 15] = cond(c, hb & 15); r[31] = pc + 2; return 0; }
		return fault(c, pc, "3x hi %04x", h, 0);
	}
	if (hb >= 0x40 && hb <= 0x5f) {
		int o = hb & 0x1f, x = (h >> 4) & 15, rr = h & 15;
		npc = pc + 2;
		if (x == 1) { r[rr] = vc_read(c, r[25] + 4 * o, 4); r[31] = npc; return 0; }
		if (x == 5) { vc_write(c, r[25] + 4 * o, 4, r[rr]); r[31] = npc; return 0; }
		if (x == 9) { r[rr] = r[25] + 4 * o; r[31] = npc; return 0; }
		if (x == 3) {
			if (rr == 0) {
				switch (o) {
				case 4: r[31] = r[26]; return 0;                                   /* rts */
				case 10: r[30] |= 1u << 30; r[31] = npc; c->irq_poll = 1; return 0;  /* ei */
				case 11: r[30] &= ~(1u << 30); r[31] = npc; return 0;              /* di */
				case 24: {                                                         /* rti */
					uint32_t sp = r[25];
					uint32_t s = vc_read(c, sp, 4);
					npc = vc_read(c, sp + 4, 4);
					r[25] = sp + 8;
					vc_set_sr(c, s);
					c->irq_poll = 1;
					r[31] = npc; return 0;
				}
				case 18:   /* 5230: debug breakpoint.  Error paths continue after it (py_fw0 0x9662 returns
				            * -21 to a caller that expects it), so without a debugger it is a NOP. */
					c->nbkpt++;
					c->bkpt_pc[c->nbkpt_ring++ & 15] = pc;
					if (c->bkpt_halt) { c->stop = VC_STOP_HALT; c->fault_pc = pc; r[31] = pc; return -1; }
					r[31] = npc; return 0;
				case 2: r[31] = npc; return 0;                                      /* nop */
				case 3: c->wfi = 1; r[31] = npc; c->irq_poll = 1; return 0;          /* 4330 wait for interrupt */
				case 20: c->cbase = 0; r[31] = npc; return 0;                        /* 5430 vcbase := 0 */
				case 21: case 22: case 23:                                         /* 5530/5630/5730 vcbase += 16/32/48 */
					c->cbase = (c->cbase + 16 * (o - 20)) & 63; r[31] = npc; return 0;
				}
			}
			switch (o) {
			case 6: r[31] = r[rr]; return 0;
			case 7: r[26] = npc; r[31] = r[rr]; return 0;
			case 12: case 13: {
				uint32_t idx = r[rr], tbl = npc; int32_t off;
				if (o == 12) off = (int8_t) vc_read(c, tbl + idx, 1);
				else off = (int16_t) vc_read(c, tbl + 2 * idx, 2);
				r[31] = tbl + 2 * off; return 0;
			}
			case 16: fsub(c, r[rr], 0); r[31] = npc; return 0;
			case 19: r[rr] = c->cfg; r[31] = npc; return 0;
			}
			return fault(c, pc, "misc%d r%d", o, rr);
		}
		if (!(x & 1)) { r[rr] = (pc & ~3u) + 4 * ((o << 3) | (x >> 1)); r[31] = npc; return 0; }
		return fault(c, pc, "4x x%x", x, 0);
	}
	if (hb >= 0x60 && hb <= 0x77) {
		int op = OP4[(h >> 9) & 15], i = (h >> 4) & 31, rr = h & 15;
		if (op == A_BAD) return fault(c, pc, "iop %d", (h >> 9) & 15, 0);
		uint32_t imm = (op == A_ADD || op == A_MOV || op == A_CMP || op == A_MUL) ? (uint32_t) sx(i, 5) : (uint32_t) i;   /* mul #imm5 signed (MP3 6bc0 = mul r0,#-4) */
		uint32_t out;
		if (alu(c, op, r[rr], imm, &out) > 0) r[rr] = out;
		r[31] = pc + 2; return 0;
	}
	if (hb >= 0x78 && hb <= 0x7f) {
		int b = (h >> 4) & 15;
		r[31] = pc + 2;
		do_mem(c, hb & 7, h & 15, r[b], 0, -1, 1, b);
		return 0;
	}
	if (hb >= 0x80 && hb <= 0xbf) {
		int i = (h >> 11) & 7, wl = (h >> 8) & 7, b = (h >> 4) & 15;
		r[31] = pc + 2;
		do_mem(c, wl, h & 15, r[b], 0, i == 7 ? -1 : i, 0, b);
		return 0;
	}
	if (hb >= 0xc0 && hb <= 0xdf) {
		int op = hb - 0xc0, rd = h & 15, rs = (h >> 4) & 15;
		if (op == A_OP9 || op == A_OP17 || op == A_OP29) return fault(c, pc, "alu2 op%d", op, 0);
		uint32_t out;
		if (alu(c, op, r[rd], r[rs], &out) > 0) r[rd] = out;
		r[31] = pc + 2; return 0;
	}
	if (hb == 0xe0 || hb == 0xf0) {
		/* VC4 stm/ldm order: the highest register sits at the lowest address, lr/pc above all of
		 * them.  Needed by the Nucleus interrupt frame: `push r0-r5,lr` then `push r6-r15` is
		 * undone by one `pop r0-r15` + `ld lr,(sp)` (music image 0x1A972, loader 0xC3AFE).
		 * vc01.py stores ascending (equivalent for matched push/pop pairs only). */
		static const int BASE[4] = {0, 6, 16, 24};
		int bb = (h >> 5) & 3, m = h & 31, lrpc = (h & 0x80) != 0;
		int regs[40], nr = 0;
		if (!(bb == 3 && m == 15)) for (int k = m; k >= 0; k--) regs[nr++] = (BASE[bb] + k) & 31;   /* descending */
		if (c->push_ascending) { for (int i = 0; i < nr / 2; i++) { int t = regs[i]; regs[i] = regs[nr - 1 - i]; regs[nr - 1 - i] = t; } }
		if (hb == 0xf0) {
			if (lrpc) regs[nr++] = 26;
			uint32_t sp = r[25] - 4 * nr;
			for (int k = 0; k < nr; k++) vc_write(c, sp + 4 * k, 4, R(c, regs[k]));
			r[25] = sp;
			r[31] = pc + 2;
		} else {
			if (lrpc) regs[nr++] = 31;
			uint32_t sp = r[25];
			npc = pc + 2;
			for (int k = 0; k < nr; k++) {
				uint32_t v = vc_read(c, sp + 4 * k, 4);
				if (regs[k] == 31) npc = v; else W(c, regs[k], v);
			}
			r[25] = sp + 4 * nr;
			r[31] = npc;
		}
		return 0;
	}
	/* hb >= 0xe1: ld / st rd, (rs + 4u) */
	{
		int b = (h >> 4) & 15;
		r[31] = pc + 2;
		do_mem(c, hb < 0xf0 ? 0 : 1, h & 15, r[b], 4 * (hb & 15), -1, 0, b);
		return 0;
	}
}

uint64_t vc_run(VC01* c, uint64_t n) {
	uint64_t i = 0;
	c->stop = VC_RUN;
	while (i < n) {
		if (c->irq_poll && c->irq) {
			c->irq_poll = 0;
			int v = c->irq(c->ctx);
			if (v) {
				if (c->r[30] & (1u << 30)) vc_take_irq(c, v);
				else c->wfi = 0;   /* a pending interrupt wakes a WFI even with IE off */
			}
		}
		if (c->wfi) { c->stop = VC_STOP_WFI; return i; }
		if (c->r[31] == c->ret_addr && c->ret_addr) { c->stop = VC_STOP_RET; return i; }
		if (c->r[31] == c->break_pc && c->break_pc) { c->stop = VC_STOP_BREAK; return i; }
		if (vc_step(c) < 0) return i;
		i++;
		if (c->watch_hit) { c->watch_hit = 0; c->stop = VC_STOP_BREAK; return i; }
	}
	return i;
}

VC01* vc_new(uint8_t* ram, uint32_t ram_size) {
	VC01* c = calloc(1, sizeof(VC01));
	c->align = 1;
	c->ram = ram;
	c->ram_size = ram_size;
	return c;
}
void vc_free(VC01* c) { free(c); }
void vc_reset(VC01* c, uint32_t pc) {
	memset(c->r, 0, sizeof(c->r));
	c->N = c->Z = c->C = c->V = 0;
	c->r[31] = pc;
	c->wfi = 0;
	c->stop = 0;
	c->cbase = 0;
}
int vc_size(void) { return sizeof(VC01); }

/* ---------------------------------------------------------------- accessors for ctypes */
uint32_t* vc_regp(VC01* c) { return c->r; }
uint32_t vc_flags(VC01* c) { return c->N << 3 | c->Z << 2 | c->C << 1 | c->V; }
void vc_set_flags(VC01* c, uint32_t f) { c->N = f >> 3 & 1; c->Z = f >> 2 & 1; c->C = f >> 1 & 1; c->V = f & 1; }
uint64_t vc_icount(VC01* c) { return c->icount; }
int vc_stopcode(VC01* c) { return c->stop; }
uint32_t vc_fault_pc(VC01* c) { return c->fault_pc; }
const char* vc_fault_msg(VC01* c) { return c->fault_msg; }
void vc_set_ret(VC01* c, uint32_t a) { c->ret_addr = a; }
void vc_set_break(VC01* c, uint32_t a) { c->break_pc = a; }
void vc_set_vecmode(VC01* c, int m) { c->vec_mode = m; }
void vc_set_memrep7(VC01* c, int m) { c->memrep7 = m; }
void vc_set_cfg(VC01* c, uint32_t v) { c->cfg = v; }
int vc_get_wfi(VC01* c) { return c->wfi; }
void vc_set_wfi(VC01* c, int v) { c->wfi = v; }
void vc_set_hooks(VC01* c, void* ctx, vc_mmio_rd_t rd, vc_mmio_wr_t wr, vc_irq_t irq) { c->ctx = ctx; c->mmio_rd = rd; c->mmio_wr = wr; c->irq = irq; }
void vc_poll_irq(VC01* c) { c->irq_poll = 1; }
uint64_t vc_unmapped(VC01* c) { return c->unmapped; }
uint32_t vc_unmapped_last(VC01* c) { return c->unmapped_last; }
uint64_t vc_nirq(VC01* c) { return c->nirq; }
int vc_nvf(VC01* c) { return c->nvf; }
void vc_vf(VC01* c, int i, uint32_t* out) {  /* pc, count, why, len, w0..w4 */
	out[0] = c->vf_pc[i]; out[1] = c->vf_count[i]; out[2] = c->vf_why[i]; out[3] = c->vf_len[i];
	for (int k = 0; k < 5; k++) out[4 + k] = c->vf_w[i][k];
}
void vc_vf_clear(VC01* c) { c->nvf = 0; }
uint64_t vc_vec_exec(VC01* c) { return c->vec_exec; }
int vc_vhist(VC01* c, int i, uint32_t* out) { out[0] = c->vh_pc[i & 8191]; out[1] = c->vh_n[i & 8191]; return 8192; }
void vc_vhist_clear(VC01* c) { memset(c->vh_pc, 0, sizeof c->vh_pc); memset(c->vh_n, 0, sizeof c->vh_n); }
uint8_t* vc_vrf(VC01* c) { return &c->P[0][0]; }      /* byte VRF P[64][64] */
uint8_t* vc_vflags(VC01* c) { return c->vn; }   /* vn[16], vz[16], vc[16] */
int16_t* vc_vacc(VC01* c) { return c->acc; }
int vc_get_cbase(VC01* c) { return c->cbase; }
void vc_set_cbase(VC01* c, int v) { c->cbase = v & 63; }
void vc_ring(VC01* c, uint32_t* out) { for (int i = 0; i < 256; i++) out[i] = c->ring[(c->ringi + i) & 255]; }
void vc_set_cap_reg(VC01* c, int r) { c->cap_reg = r & 31; }
void vc_set_cap(VC01* c, uint32_t pc, uint8_t* buf, uint32_t max) { c->cap_pc = pc; c->cap_buf = buf; c->cap_max = max; c->cap_n = 0; }
uint32_t vc_cap_n(VC01* c) { return c->cap_n; }
void vc_set_watch(VC01* c, uint32_t a) { c->watch = a; }
void vc_set_align(VC01* c, int on) { c->align = on; }
uint64_t vc_unaligned(VC01* c) { return c->unaligned; }
void vc_set_bkpt_halt(VC01* c, int on) { c->bkpt_halt = on; }
uint64_t vc_nbkpt(VC01* c) { return c->nbkpt; }
void vc_bkpts(VC01* c, uint32_t* out) { for (int i = 0; i < 16; i++) out[i] = c->bkpt_pc[(c->nbkpt_ring + i) & 15]; }
void vc_set_push_ascending(VC01* c, int on) { c->push_ascending = on; }
