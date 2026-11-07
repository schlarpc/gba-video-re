/* Hydrogen (Majesco) GBA Video: movie containers, video stream parsing and frame decoding.
 * See docs/format-hydrogen.md. */
#ifndef HYDEC_H
#define HYDEC_H
#include <stdint.h>
#include <stddef.h>
#include "hyinflate.h"
#include "../majesco/vq.h"

#define HY_MAX_STREAMS 16
#define HY_MAX_CHAPTERS 32

typedef struct {
    uint32_t container;          /* ROM offset of the container word */
    uint32_t video, video_len;   /* ROM offset/length of the type-1 (video) entry payload */
    uint32_t audio, audio_len;   /* type-2 (audio) entry payload, 0 if absent */
} hy_container;

typedef struct {
    uint32_t off, frame, nframes;
    int mode, mbits, ncb;
    uint32_t cb_off, cb_len, idx_off, idx_len;
} hy_segment;

typedef struct {
    const uint8_t *rom; size_t rom_len;
    hy_container c;
    uint32_t w0, w1;
    int nchap, codec, fps, width, height, flags;
    uint32_t total_frames, frames;
    int nseg;
    hy_segment *seg;
    struct { int segment; char name[64]; } chap[HY_MAX_CHAPTERS];
} hy_stream;

int hy_find_containers(const uint8_t *rom, size_t len, hy_container *out, int max);
int hy_open(hy_stream *s, const uint8_t *rom, size_t len, const hy_container *c);
void hy_close(hy_stream *s);
/* the player's dither table: the 16 bytes before the block-geometry table {4,4,4,4,4,3,4,3,3,3,3,3,4,2,4,2} */
int hy_rom_tables(const uint8_t *rom, size_t len, mvq_tables *t);

typedef struct {
    hy_stream *s;
    mvq_tables tab;
    int cur_seg;                  /* segment whose codebook/index blob is loaded, -1 none */
    uint8_t *idx; size_t idx_cap; long idx_len;
    hyinf_stream ix;              /* index stream, requested one frame at a time like the player */
    uint32_t ix_next;             /* next frame (segment-relative) to request */
    long frame_off;               /* offset in idx of the last requested frame */
    uint8_t *cb; size_t cb_cap;
    uint16_t vcb[256][16];        /* VRAM codebook as the cartridge holds it */
    uint16_t pal24[24];
    hyinf_stats st_cb, st_idx;
} hy_decoder;

int hy_dec_init(hy_decoder *d, hy_stream *s);
void hy_dec_free(hy_decoder *d);
/* decode stream frame f (0-based) into fb (240x160 BGR555). Returns 0 on success. */
int hy_dec_frame(hy_decoder *d, uint32_t f, uint16_t fb[MVQ_W * MVQ_H]);
int hy_seg_of_frame(const hy_stream *s, uint32_t f);
const char *hy_name(const uint8_t *b, char *out, size_t cap);
#endif
