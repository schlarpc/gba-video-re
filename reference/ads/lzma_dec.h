/* Minimal LZMA ("LZMA1", raw stream, no end marker) decoder written from the published format
 * description. Output goes into a power-of-two ring buffer, the way the ADS player decodes.
 * Public domain / MIT, see LICENSE in the repository. */
#ifndef LZMA_DEC_H
#define LZMA_DEC_H
#include <stdint.h>
#include <stddef.h>

typedef struct {
    /* input */
    const uint8_t *in, *in_end;
    uint32_t range, code;
    /* output ring */
    uint8_t *ring;
    uint32_t ring_mask;     /* ring size - 1 (power of two) */
    uint32_t pos;           /* total bytes written so far */
    /* model */
    unsigned lc, lp, pb;
    unsigned state;
    uint32_t rep[4];
    uint32_t remain_len;    /* match bytes still to copy (match split across decode calls) */
    uint32_t max_dist;      /* largest match distance seen (diagnostics) */
    int error;              /* 1: input overrun, 2: distance beyond data written */
    int lenient;            /* 1: like the cartridge, do not check distances (reads stale ring bytes) */
    uint16_t *probs;
    size_t nprobs;
} lzma_dec;

/* params: lc/lp/pb. ring: buffer of (ring_mask+1) bytes. probs: >= lzma_probs_size(lc,lp) entries. */
size_t lzma_probs_size(unsigned lc, unsigned lp);
void lzma_init(lzma_dec *d, unsigned lc, unsigned lp, unsigned pb, uint16_t *probs,
               uint8_t *ring, uint32_t ring_mask, const uint8_t *in, const uint8_t *in_end);
/* decode exactly n more bytes into the ring; returns 0 on success */
int lzma_decode(lzma_dec *d, uint32_t n);

#endif
