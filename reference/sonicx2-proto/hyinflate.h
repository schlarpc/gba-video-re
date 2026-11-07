#ifndef HYINFLATE_H
#define HYINFLATE_H
#include <stddef.h>
#include <stdint.h>
typedef struct {
    unsigned blocks[3];                 /* stored, fixed, dynamic block counts */
    unsigned long literals, matches, match_bytes;
    size_t consumed;                    /* input bytes up to the end-of-stream code */
    unsigned incomplete_codes, oversubscribed_codes;   /* Kraft check of every dynamic table */
    /* optional: input position (bytes, incl. the 4-byte prefix) when output first reaches
       k*mark_step bytes, k = 1..nmarks (set mark_step/marks/nmarks before the call) */
    size_t mark_step, nmarks, *marks;
} hy_inflate_stats;
/* Decode one Hydrogen inflate blob (u32 size prefix + bitstream). Returns bytes produced or <0. */
long hy_inflate(const uint8_t *blob, size_t bloblen, uint8_t *out, size_t outcap, hy_inflate_stats *st);
enum { HY_OK = 0, HY_OVERFLOW = 10, HY_BADDIST, HY_BADCODE, HY_UB, HY_END };
typedef struct { uint16_t count[16], first[16], index[16], sym[320]; } hy_huff_t;
typedef struct {
    const uint8_t *blob, *end, *p;      /* bitstream */
    uint32_t buf, cnt;                  /* bit buffer (MSB-aligned), valid bits */
    uint8_t *out; size_t outcap, o;     /* output (o = cart write pointer) */
    uint32_t usize;
    int32_t r38, r48, r4c;              /* cart object fields +0x38 (remaining/overshoot), +0x48 (total left), +0x4c (stored left) */
    int state, err, done;               /* +0x3c */
    hy_huff_t lit, dist;
    hy_inflate_stats *st;
} hy_stream;
void hy_stream_init(hy_stream *s, const uint8_t *blob, size_t bloblen, uint8_t *out, size_t outcap);
/* UncompressBlock(n): returns the output offset where this request's n bytes start, or <0 */
long hy_stream_read(hy_stream *s, uint32_t n);
long hy_inflate_calls(const uint8_t *blob, size_t bloblen, uint8_t *work, size_t workcap, const uint32_t *calls, size_t ncalls, uint8_t *collected);
#endif
