/* VC01 (Alphamosaic VideoCore 1) scalar + partial vector core in C.
 * Reference model: vc01.py (scalar) and vc01v.py (vector), checked against it by lockstep.py.
 * Validated against the Python model by lockstep.py / ctests.py in this directory. */
#ifndef VC01CORE_H
#define VC01CORE_H
#include <stdint.h>

enum {
	VC_RUN = 0,
	VC_STOP_HALT = 1,      /* 0x5230 'halt' executed */
	VC_STOP_FAULT = 2,     /* undecodable / unimplemented scalar instruction */
	VC_STOP_BREAK = 3,     /* pc == break address */
	VC_STOP_WFI = 4,       /* waiting for interrupt (returned from vc_run when idle) */
	VC_STOP_RET = 5,       /* pc == ret_addr (function-call harness) */
};

typedef struct VC01 VC01;
typedef uint32_t (*vc_mmio_rd_t)(void* ctx, uint32_t a, int n);
typedef void (*vc_mmio_wr_t)(void* ctx, uint32_t a, int n, uint32_t v);
typedef int (*vc_irq_t)(void* ctx);   /* returns vector number (32..63) to take, or 0 */

#define VC_NVF 4096

struct VC01 {
	uint32_t r[32];        /* r30 = sr (bit 30 = IE; flags packed on read/write), r31 = pc */
	uint32_t N, Z, C, V;
	uint8_t* ram;          /* flat memory at VC address 0 */
	uint32_t ram_size;     /* bytes; accesses beyond are counted in unmapped */
	uint64_t icount;
	uint32_t cfg;          /* value returned by misc o=19 ('rdcfg') */
	int stop;
	uint32_t fault_pc;
	char fault_msg[160];
	uint32_t break_pc;     /* stop when pc reaches this (0 = none) */
	uint32_t ret_addr;     /* stop when pc reaches this (function-call harness) */
	int wfi;               /* 1 while waiting for interrupt */
	int vec_mode;          /* 0: vector insns via model, unknown ones fault; 1: model, unknown = NOP + log; 2: all NOP */
	/* hardware hooks */
	void* ctx;
	vc_mmio_rd_t mmio_rd;
	vc_mmio_wr_t mmio_wr;
	vc_irq_t irq;          /* polled when IE=1 (or WFI) */
	int irq_poll;          /* hardware sets this nonzero when an interrupt may be pending */
	/* statistics */
	uint64_t unmapped;
	uint32_t unmapped_last;
	uint64_t nirq;
	uint64_t vec_exec, vec_unknown;
	/* vector-fault log: (pc, h0..h4) -> count */
	uint32_t vf_pc[VC_NVF];
	uint16_t vf_w[VC_NVF][5];
	uint8_t vf_len[VC_NVF];
	uint32_t vf_count[VC_NVF];
	uint32_t vf_why[VC_NVF];
	int nvf;
	/* vector unit (port of vc01v.py VPU): byte VRF, lane flags, accumulators */
	uint8_t P[64][64];
	uint8_t vn[16], vz[16], vc[16];
	int16_t acc[16];
	int cbase;                 /* VRF column base (scalar 0x5430 = 0, 0x5530/5630/5730 += 16/32/48) */
	/* executed-vector-instruction histogram (pc -> count), open addressing, for diagnostics */
	uint32_t vh_pc[8192];
	uint32_t vh_n[8192];
	/* state capture at a pc (diagnostics): record = P[64][64] then r[0..31] */
	uint32_t cap_pc; uint8_t* cap_buf; uint32_t cap_max, cap_n; int cap_reg;
	/* trace hook (optional, slow): called before each instruction */
	void (*trace)(void* ctx, uint32_t pc);
	uint32_t ring[256];    /* last executed pcs */
	uint32_t ringi;
	uint32_t watch;        /* stop (VC_STOP_BREAK) after a write touching this address (0 = off) */
	int watch_hit;
	int align;             /* 1 = halfword/word accesses ignore the low address bits (hardware); 0 = Python model */
	uint64_t unaligned;
	int push_ascending;    /* 1 = vc01.py push/pop order (for lockstep) */
	int bkpt_halt;         /* 1 = 0x5230 stops the core (VC_STOP_HALT) */
	uint64_t nbkpt;
	uint32_t bkpt_pc[16];
	uint32_t nbkpt_ring;
	int memrep7;           /* vld/vst REP code 7 (experiment switch): 0 = 64 (patent REPn max; default, [C] AAC),
	                        * 1 = 128 wrapping the 64-row VRF (old model), 2 = 128 with rows 64..127 zero */
};

VC01* vc_new(uint8_t* ram, uint32_t ram_size);
void vc_free(VC01* c);
void vc_reset(VC01* c, uint32_t pc);
int vc_step(VC01* c);
/* run up to n instructions; returns the number executed; c->stop says why it stopped early */
uint64_t vc_run(VC01* c, uint64_t n);
uint32_t vc_read(VC01* c, uint32_t a, int n);
void vc_write(VC01* c, uint32_t a, int n, uint32_t v);
uint32_t vc_get_sr(VC01* c);
void vc_set_sr(VC01* c, uint32_t v);
int vc_take_irq(VC01* c, int vec);
int vc_size(void);
#endif
