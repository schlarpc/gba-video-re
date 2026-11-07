/* Hydrogen "hyCompressionManager::Inflate": the DEFLATE variant of Majesco's Hydrogen library.
 * See docs/format-hydrogen.md section 5. */
#ifndef HYINFLATE_H
#define HYINFLATE_H
#include <stdint.h>
#include <stddef.h>

typedef struct { uint16_t count[16]; uint16_t sym[320]; } hyinf_huff;

typedef struct {
    const uint8_t *start, *p, *end;   /* blob, halfword read pointer, end */
    uint32_t buf; unsigned n;         /* bit buffer (MSB-aligned) and valid bits */
    uint8_t *out; size_t cap, o;      /* output buffer, capacity, write offset */
    uint32_t size;                    /* u32 prefix */
    int32_t rem;                      /* obj+0x38: bytes still owed to the current request (<=0: overshoot) */
    int32_t left;                     /* obj+0x48: bytes left in the stream */
    int32_t sleft;                    /* obj+0x4c: bytes left in the current stored block */
    int state;                        /* obj+0x3c: 0 header, 1/3 dynamic, 2/4 fixed, 5 stored, 7 end */
    hyinf_huff lit, dist;
    unsigned blocks[4];               /* block headers seen, by type */
    int err;
} hyinf_stream;

typedef struct {
    unsigned blocks[4];          /* count per block type: 0 stored, 1 fixed, 2 dynamic, 3 end */
    size_t in_bytes;             /* compressed bytes consumed incl. the 4-byte size prefix */
} hyinf_stats;

/* Inflate::Initialize */
void hyinf_init(hyinf_stream *s, const uint8_t *in, size_t in_len, uint8_t *out, size_t out_cap);
/* Inflate::UncompressBlock(n): returns the offset of this request's first byte; errors set s->err */
long hyinf_request(hyinf_stream *s, long n);

/* one-shot: decode a whole blob (u32 LE size, then the bit stream) in one request.
 * Returns the size, or -1 on a malformed stream. */
long hyinf_decode(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_cap, hyinf_stats *st);
/* after the whole blob: 1 if the last block ends and block type 3 follows */
int hyinf_check_end(hyinf_stream *s);

static inline uint32_t hyinf_size(const uint8_t *in)
{
    return in[0] | in[1] << 8 | in[2] << 16 | (uint32_t)in[3] << 24;
}
#endif
