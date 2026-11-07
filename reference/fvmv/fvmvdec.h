/* FVMV (Actimagine, GBA Video "Pokemon" Vol 1-4) video decoder -- public API. C99. */
#ifndef FVMVDEC_H
#define FVMVDEC_H
#include <stdint.h>
#include <stddef.h>

/* The player decodes into two 240x160 RGB555 buffers that live in one 256 KiB EWRAM
 * heap block.  Blocks near the picture edge can read and write outside the picture
 * (motion vectors up to 8 pixels out, edge blending one row/column out), so the
 * decoder models the whole EWRAM layout byte-exactly:
 *   0x02000000  heap header (8 bytes)
 *   0x02000008  frame buffer 0  (0x12c00 bytes)
 *   0x02012c08  frame buffer 1  (0x12c00 bytes)
 *   0x02025808  display buffer  (written by the menu only, never during an episode)
 *   0x02038408  PCM decode buffer
 * Frame f is decoded into buffer (f & 1) with buffer ((f & 1) ^ 1) as the reference. */
#define FVMV_EWRAM_BASE 0x02000000u
#define FVMV_EWRAM_SIZE 0x40000u
#define FVMV_BUF0       0x02000008u
#define FVMV_BUF_STRIDE 0x12c00u
#define FVMV_W 240
#define FVMV_H 160

typedef struct {
    uint8_t ewram[FVMV_EWRAM_SIZE];
    uint32_t frame_no;             /* frames decoded so far (selects the output buffer) */
    /* statistics */
    uint64_t mode_count[16][8];    /* [size index][mode] */
    uint64_t oob_reads, oob_writes;   /* accesses below 0x02000000 (open bus on hardware) */
    uint64_t offpic_reads, offpic_writes; /* accesses outside the current/reference picture */
    uint64_t err;                  /* read past the available data */
    uint64_t overrun;              /* frames whose readers ran past their substream */
    uint64_t mv_xwrap, mv_yout;
    uint64_t mv_yout_kind[4];      /* [below*2 + (ref above out in memory)] */    /* vectors whose source crosses a side / leaves top or bottom */
    uint64_t edge_x0, edge_y0;     /* blended FILL / 8x8 pattern at column 0 / row 0 */
    uint64_t inexact;              /* frames whose substreams were not consumed exactly */
} fvmv_dec;

typedef struct {
    uint32_t nframes, width, height, period, data_size, max_record, audio_buf, hdr_off;
} fvmv_header;

typedef struct {
    uint32_t off;        /* record offset from the FVMV header */
    uint32_t size;       /* bytes after the 16-byte record header */
    int32_t  back;       /* offset (relative to this record) of the previous seek point, <= 0 */
    uint32_t fwd;        /* offset (relative to this record) of the next seek point */
    uint32_t index;      /* frame index */
    uint32_t vlen;       /* video payload bytes */
    uint32_t alen;       /* audio payload bytes (multiple of 40) */
} fvmv_record;

/* mode numbers used in mode_count */
enum { M_SKIP, M_MV, M_SPLIT_H, M_SPLIT_V, M_MVOFF, M_FILL, M_PAT, M_RAW };

int fvmv_parse_header(const uint8_t *p, size_t n, fvmv_header *h);
/* walk records; returns count, fills up to max entries */
long fvmv_walk(const uint8_t *stream, size_t n, const fvmv_header *h, fvmv_record *rec, long max);
void fvmv_init(fvmv_dec *d);
/* decode one video payload (vlen bytes at v) into buffer (frame_no & 1); returns 0 on success */
int fvmv_decode_frame(fvmv_dec *d, const uint8_t *v, uint32_t vlen);
/* same with explicit buffer addresses; avail = readable bytes at v (>= vlen) */
int fvmv_decode_frame_at(fvmv_dec *d, const uint8_t *v, uint32_t vlen, uint32_t avail,
                         uint32_t out, uint32_t ref);
/* pointer to the 240x160 u16 buffer of the most recently decoded frame */
const uint8_t *fvmv_last_frame(const fvmv_dec *d);
size_t fvmv_dec_size(void);
uint8_t *fvmv_ewram(fvmv_dec *d);
#endif
