/* bits.h -- VX++ (Shrek GBA Video) video bit reader.
 *
 * OWNER: video-bitstream agent. The residual agent codes against this; do not change
 * the struct layout or function semantics without updating notes/video.md.
 *
 * This is an exact model of the player's register-level reader (IWRAM 0x030007a8 refill,
 * 0x0300076c ue(v), 0x0300081c se(v)):
 *   r1 = halfword read pointer  -> bs->pos (byte offset into bs->base, always even)
 *   r2 = "count"                -> bs->cnt   valid bits in buf = cnt + 16  (cnt >= 0 between ops)
 *   r3 = bit buffer, MSB-first, left aligned -> bs->buf
 * Stream = little-endian u16 halfwords, consumed MSB-first within each halfword.
 * Refill (one halfword) happens only when an operation drives cnt below 0:
 *     hw = next u16; cnt += 16; buf |= hw << (16 - cnt);
 * Every primitive does at most ONE refill, exactly like the ARM code, so an operation may
 * consume at most cnt+16 (>= 16) bits. All the decoder's primitives respect that.
 *
 * Bit position: bitpos = pos*8 - (cnt + 16)  (bits from bs->base).
 * The seek table's video_bit is relative to the VX header + 0x38 (= first video halfword);
 * pass base = header + 0x38 and that bit offset to vx_bits_init.
 */
#ifndef VX_BITS_H
#define VX_BITS_H
#include <stdint.h>
#include <stddef.h>

typedef struct vx_bits {
    const uint8_t *base;  /* start of the video elementary stream (VX header + 0x38) */
    size_t size;          /* bytes available; reads past the end return 0 halfwords */
    size_t pos;           /* byte offset of the next halfword to load (r1) */
    uint32_t buf;         /* r3 */
    int cnt;              /* r2 */
} vx_bits;

static inline uint32_t vx_bits_hw(const vx_bits *bs, size_t pos)
{
    if (pos + 2 > bs->size) return 0;
    return (uint32_t)bs->base[pos] | ((uint32_t)bs->base[pos + 1] << 8);
}

/* Position the reader at an absolute bit offset (as the player's seek 0x02000584 does:
 * buf = (hw[i]<<16 | hw[i+1]) << skip, cnt = 16 - skip, pos -> hw[i+2]). */
static inline void vx_bits_init(vx_bits *bs, const uint8_t *base, size_t size, uint32_t bitoff)
{
    size_t hw = (bitoff >> 4) * 2;
    int skip = bitoff & 15;
    bs->base = base;
    bs->size = size;
    bs->buf = ((vx_bits_hw(bs, hw) << 16) | vx_bits_hw(bs, hw + 2)) << skip;
    bs->cnt = 16 - skip;
    bs->pos = hw + 4;
}

/* The player's power-on init (player_run 0x03006df4, start frame 0, no seek):
 * buf = hw[0] << 16, cnt = 0, pos -> hw[1]. Same bit position as vx_bits_init(..,0) but a
 * different refill phase; use this one to be register-exact from frame 0. */
static inline void vx_bits_init_start(vx_bits *bs, const uint8_t *base, size_t size)
{
    bs->base = base;
    bs->size = size;
    bs->buf = vx_bits_hw(bs, 0) << 16;
    bs->cnt = 0;
    bs->pos = 2;
}

static inline uint32_t vx_bits_pos(const vx_bits *bs)
{
    return (uint32_t)(bs->pos * 8 - (size_t)(bs->cnt + 16));
}

/* 0x030007a8: called when cnt < 0 */
static inline void vx_refill(vx_bits *bs)
{
    uint32_t hw = vx_bits_hw(bs, bs->pos);
    int sh;
    bs->pos += 2;
    bs->cnt += 16;
    sh = 16 - bs->cnt;                 /* ARM lsl by register: >= 32 gives 0 */
    if (sh >= 0 && sh < 32) bs->buf |= hw << sh;
}

/* peek the top n bits (1..16) without consuming */
static inline uint32_t vx_peek(const vx_bits *bs, int n)
{
    return bs->buf >> (32 - n);
}

/* consume n bits (0..16) that were peeked; one refill if needed */
static inline void vx_skip(vx_bits *bs, int n)
{
    bs->buf = n >= 32 ? 0 : bs->buf << n;
    bs->cnt -= n;
    if (bs->cnt < 0) vx_refill(bs);
}

/* read n bits (1..16), MSB first */
static inline uint32_t vx_getbits(vx_bits *bs, int n)
{
    uint32_t v = vx_peek(bs, n);
    vx_skip(bs, n);
    return v;
}

static inline uint32_t vx_get1(vx_bits *bs) { return vx_getbits(bs, 1); }

/* 0x0300076c ue(v): count leading zeros z (each zero and the terminating 1 are shifted
 * out of buf), value = (1<<z) + next z bits - 1, cnt -= 2z+1, one refill. */
static inline uint32_t vx_ue(vx_bits *bs)
{
    int z = 0;
    uint32_t v;
    while (!(bs->buf & 0x80000000u) && z < 32) { bs->buf <<= 1; z++; }
    bs->buf <<= 1;                                   /* the '1' */
    v = z ? (bs->buf >> (32 - z)) : 0;
    v += (1u << z) - 1;
    bs->buf = z >= 32 ? 0 : bs->buf << z;
    bs->cnt -= 2 * z + 1;
    if (bs->cnt < 0) vx_refill(bs);
    return v;
}

/* 0x0300081c se(v): k = ue+1; odd k -> -(k>>1)... i.e. 0,1,-1,2,-2,... */
static inline int32_t vx_se(vx_bits *bs)
{
    uint32_t k = vx_ue(bs) + 1;
    int32_t v = (int32_t)k;
    if (k & 1) v = 1 - v;
    return v >> 1;
}

#endif
