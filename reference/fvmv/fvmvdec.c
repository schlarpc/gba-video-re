/* FVMV video decoder: bit-exact model of the IWRAM ARM decoder of the Pokemon GBA Video
 * player (entry 0x03005fe8).  See docs/format-fvmv.md for the specification.  C99. */
#include <string.h>
#include "fvmvdec.h"

#define STRIDE 480u                 /* bytes per picture row (240 px * 2) */
#define M32 0x7bdf7bdfu             /* averaging mask: clears the LSB of G and B, and bit 15 */
#define L32 0x04200420u             /* LSB of G and B */

static inline uint32_t rd32le(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static inline uint32_t rd16le(const uint8_t *p) { return p[0] | p[1] << 8; }

typedef struct {
    fvmv_dec *d;
    const uint8_t *bits, *bend;     /* bit stream words */
    const uint8_t *col, *cend;      /* u16 colour stream */
    const uint8_t *mv, *mend;       /* u8 motion vector stream */
    uint32_t r8;                    /* bit register with sentinel, as in the ARM code */
    uint32_t out, ref;              /* GBA addresses of the current and reference buffers */
} ctx;

/* ---- memory model (GBA addresses) ---- */
static inline int in_pic(uint32_t a, uint32_t base) { return a >= base && a < base + FVMV_BUF_STRIDE; }

static uint32_t m16(ctx *c, uint32_t a)
{
    if (a < FVMV_EWRAM_BASE || a + 2 > FVMV_EWRAM_BASE + FVMV_EWRAM_SIZE) { c->d->oob_reads++; return 0; }
    if (!in_pic(a, c->out) && !in_pic(a, c->ref)) c->d->offpic_reads++;
    return rd16le(c->d->ewram + (a - FVMV_EWRAM_BASE));
}
static void w16(ctx *c, uint32_t a, uint32_t v)
{
    if (a < FVMV_EWRAM_BASE || a + 2 > FVMV_EWRAM_BASE + FVMV_EWRAM_SIZE) { c->d->oob_writes++; return; }
    if (!in_pic(a, c->out)) c->d->offpic_writes++;
    uint8_t *p = c->d->ewram + (a - FVMV_EWRAM_BASE);
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}
/* 32-bit access of two pixels.  The ARM code only issues word accesses at 4-byte aligned
 * addresses; an unaligned source is read as two halfwords, which gives the same value. */
static uint32_t m32(ctx *c, uint32_t a) { return m16(c, a) | m16(c, a + 2) << 16; }
static void w32(ctx *c, uint32_t a, uint32_t v) { w16(c, a, v & 0xffff); w16(c, a + 2, v >> 16); }

/* ---- streams ---- */
static int bit(ctx *c)
{
    uint32_t carry = c->r8 >> 31;
    c->r8 <<= 1;
    if (c->r8 == 0) {               /* sentinel shifted out: refill (adcs shifts in the carry=1) */
        uint32_t w = 0;
        if (c->bits + 4 <= c->bend) w = rd32le(c->bits); else c->d->err++;
        c->bits += 4;
        carry = w >> 31;
        c->r8 = (w << 1) | 1;
    }
    return (int)carry;
}
static uint32_t colour(ctx *c)
{
    if (c->col + 2 > c->cend) { c->d->err++; c->col += 2; return 0; }
    uint32_t v = rd16le(c->col); c->col += 2; return v;
}
static int32_t mvoff(ctx *c)
{
    uint32_t i = 0;
    if (c->mv < c->mend) i = *c->mv; else c->d->err++;
    c->mv++;
    int dx = (int)(i & 15) - 8, dy = (int)(i >> 4) - 8;
    return dy * (int32_t)STRIDE + dx * 2;
}

/* ---- block operations ---- */
/* copy w x h pixels row by row from src to dst (each row read completely, then written) */
static void copy_blk(ctx *c, uint32_t dst, uint32_t src, int w, int h)
{
    uint32_t row[8];
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) row[x] = m16(c, src + y * STRIDE + 2 * x);
        for (int x = 0; x < w; x++) w16(c, dst + y * STRIDE + 2 * x, row[x]);
    }
}
/* copy + colour offset: 32-bit add of (c | c<<16) to each pixel pair (carry from the left
 * pixel into the right one); 1-pixel-wide blocks add to the single halfword. */
static void copy_off_blk(ctx *c, uint32_t dst, uint32_t src, int w, int h, uint32_t col)
{
    uint32_t k = col | col << 16;
    for (int y = 0; y < h; y++) {
        if (w == 1) {
            w16(c, dst + y * STRIDE, (m16(c, src + y * STRIDE) + k) & 0xffff);
            continue;
        }
        uint32_t row[4];
        for (int x = 0; x < w / 2; x++) row[x] = m32(c, src + y * STRIDE + 4 * x) + k;
        for (int x = 0; x < w / 2; x++) w32(c, dst + y * STRIDE + 4 * x, row[x]);
    }
}
/* one edge-blending step (the ARM sequence at e.g. 0x03002844).
 * r0 = col32 & M32, r1 = col32 & L32 (both halves), n = neighbour pixel outside the block.
 * Writes the edge pixel e, the neighbour n' and the pixel one further inside i. */
static void blend3(ctx *c, uint32_t r0, uint32_t r1, uint32_t a_n, uint32_t a_e, uint32_t a_i, int odd)
{
    uint32_t r5 = m16(c, a_n);
    uint32_t r2 = r5 & L32;
    r5 &= M32;
    uint32_t r6 = ((r0 + r5) >> 1) | (odd ? r1 : r2);
    w16(c, a_e, r6 & 0xffff);
    r6 &= M32;
    uint32_t r7 = ((r6 + r5) >> 1) | (odd ? r2 : r1);
    w16(c, a_n, r7 & 0xffff);
    r7 = ((r6 + r0) >> 1) | (odd ? r2 : r1);
    w16(c, a_i, r7 & 0xffff);
}
static void fill_blk(ctx *c, uint32_t dst, int w, int h, uint32_t col)
{
    uint32_t k = col | col << 16;
    if (w == 1 || h == 1) {          /* no blending: plain fill */
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) w16(c, dst + y * STRIDE + 2 * x, col);
        return;
    }
    for (int y = 2; y < h; y++)      /* interior (32-bit stores of col|col<<16) */
        for (int x = 2; x < w; x++) w16(c, dst + y * STRIDE + 2 * x, col);
    uint32_t r0 = k & M32, r1 = k & L32;
    for (int x = 0; x < w; x++)      /* top edge: rows -1, 0, 1 */
        blend3(c, r0, r1, dst - STRIDE + 2 * x, dst + 2 * x, dst + STRIDE + 2 * x, x & 1);
    for (int y = 0; y < h; y++)      /* left edge: columns -1, 0, 1 */
        blend3(c, r0, r1, dst + y * STRIDE - 2, dst + y * STRIDE, dst + y * STRIDE + 2, y & 1);
}
/* 2-colour pattern palette: c0, c1, (2c0+c1)/3, (c0+2c1)/3 per 5-bit channel, rounded */
static void palette(ctx *c, uint32_t pal[4], uint32_t *idx)
{
    uint32_t c0 = colour(c), c1 = colour(c);
    uint32_t lo = colour(c), hi = colour(c);
    pal[0] = c0; pal[1] = c1; pal[2] = 0; pal[3] = 0;
    for (int s = 10; s >= 0; s -= 5) {
        uint32_t a = (c0 >> s) & 31, b = (c1 >> s) & 31;
        pal[2] = pal[2] << 5 | (2 * a + b + 1) / 3;
        pal[3] = pal[3] << 5 | (a + 2 * b + 1) / 3;
    }
    *idx = lo | hi << 16;
}
static void pat4(ctx *c, uint32_t dst)
{
    uint32_t pal[4], idx;
    palette(c, pal, &idx);
    for (int k = 0; k < 16; k++) w16(c, dst + (k >> 2) * STRIDE + 2 * (k & 3), pal[(idx >> 2 * k) & 3]);
}
static void pat8(ctx *c, uint32_t dst)
{
    uint32_t pal[4], idx;
    palette(c, pal, &idx);
    for (int r = 0; r < 4; r++) {    /* odd rows: palette pixels at odd columns, even columns averaged */
        uint32_t row = dst + (2 * r + 1) * STRIDE;
        uint32_t left = m16(c, row - 2) & M32;
        for (int k = 0; k < 4; k++) {
            uint32_t v = pal[(idx >> 2 * (4 * r + k)) & 3];
            w16(c, row + 4 * k + 2, v);
            w16(c, row + 4 * k, ((left + (v & M32)) >> 1) | (v & L32));
            left = v & M32;
        }
    }
    for (int k = 0; k < 4; k++) {    /* even rows: average of the rows above and below, 2 px at a time */
        uint32_t a = dst + 4 * k;
        uint32_t r0 = m32(c, a + 7 * STRIDE), r6 = r0 & L32; r0 &= M32;
        uint32_t r1 = m32(c, a + 5 * STRIDE), r7 = r1 & L32; r1 &= M32;
        w32(c, a + 6 * STRIDE, ((r0 + r1) >> 1) | r6);
        r0 = m32(c, a + 3 * STRIDE); r6 = r0 & L32; r0 &= M32;
        w32(c, a + 4 * STRIDE, ((r1 + r0) >> 1) | r7);
        r1 = m32(c, a + 1 * STRIDE); r7 = r1 & L32; r1 &= M32;
        w32(c, a + 2 * STRIDE, ((r0 + r1) >> 1) | r6);
        r0 = m32(c, a - STRIDE) & M32;
        w32(c, a, ((r1 + r0) >> 1) | r7);
    }
}

static int size_index(int w, int h)
{
    static const int lw[9] = { 0, 0, 1, 0, 2, 0, 0, 0, 3 };
    return lw[w] * 4 + lw[h];
}

/* edge statistics: does a source rectangle leave the reference picture? */
static void mv_check(ctx *c, uint32_t pos, int32_t o, int w, int h)
{
    /* o = 480*dy + 2*dx with dx, dy in -8..7: dx is recovered uniquely from o mod 480 */
    int dx = ((o % 480) + 480 + 16) % 480 / 2 - 8;
    int x = (int)(pos % STRIDE) / 2;
    if (x + dx < 0 || x + dx + w > FVMV_W) c->d->mv_xwrap++;
    uint32_t src = c->ref + pos + (uint32_t)o, end = src + (uint32_t)(h - 1) * STRIDE + 2u * (uint32_t)w;
    if (src < c->ref || end > c->ref + FVMV_BUF_STRIDE) {
        c->d->mv_yout++;
        int below = end > c->ref + FVMV_BUF_STRIDE;
        int ref1 = c->ref > c->out;
        c->d->mv_yout_kind[below * 2 + ref1]++;
    }
}

/* recursive block grammar (one function per size in the ARM code) */
static void block(ctx *c, int w, int h, uint32_t pos)
{
    uint64_t *mc = c->d->mode_count[size_index(w, h)];
    uint32_t dst = c->out + pos;
    if (!bit(c)) {
        if (!bit(c)) { mc[M_SKIP]++; copy_blk(c, dst, c->ref + pos, w, h); }
        else { mc[M_MV]++; int32_t o = mvoff(c); mv_check(c, pos, o, w, h); copy_blk(c, dst, c->ref + pos + o, w, h); }
        return;
    }
    int can_h = h > 1, can_v = w > 1, pat = (w >= 4 && h >= 4);
    if (can_h || can_v) {
        if (w * h > 2) {             /* 2x1 and 1x2 have no split */
            if (!bit(c)) {
                int vert;
                if (can_h && can_v) vert = bit(c); else vert = !can_h;
                if (!vert) {
                    mc[M_SPLIT_H]++;
                    block(c, w, h / 2, pos);
                    block(c, w, h / 2, pos + (uint32_t)(h / 2) * STRIDE);
                } else {
                    mc[M_SPLIT_V]++;
                    block(c, w / 2, h, pos);
                    block(c, w / 2, h, pos + (uint32_t)w);   /* w/2 pixels = w bytes */
                }
                return;
            }
        }
    }
    if (pat) {
        if (bit(c)) goto mvoff_mode;
        if (!bit(c)) goto fill_mode;
        mc[M_PAT]++;
        if (w == 8 && h == 8) {
            if (bit(c)) { if (pos % STRIDE == 0) c->d->edge_x0++; if (pos < STRIDE) c->d->edge_y0++; pat8(c, dst); }
            else { pat4(c, dst); pat4(c, dst + 8); pat4(c, dst + 4 * STRIDE); pat4(c, dst + 4 * STRIDE + 8); }
        } else if (w == 8) { pat4(c, dst); pat4(c, dst + 8); }
        else if (h == 8) { pat4(c, dst); pat4(c, dst + 4 * STRIDE); }
        else pat4(c, dst);
        return;
    }
    if (!bit(c)) goto mvoff_mode;
    if (w * h == 2 && bit(c)) {      /* 2x1 / 1x2: two raw pixels */
        mc[M_RAW]++;
        uint32_t a = colour(c);
        w16(c, dst, a);
        a = colour(c);
        w16(c, dst + (w == 2 ? 2 : STRIDE), a);
        return;
    }
fill_mode:
    mc[M_FILL]++;
    if (w > 1 && h > 1) { if (pos % STRIDE == 0) c->d->edge_x0++; if (pos < STRIDE) c->d->edge_y0++; }
    fill_blk(c, dst, w, h, colour(c));
    return;
mvoff_mode:
    mc[M_MVOFF]++;
    {
        int32_t o = mvoff(c);
        mv_check(c, pos, o, w, h);
        uint32_t k = colour(c);
        copy_off_blk(c, dst, c->ref + pos + o, w, h, k);
    }
}

void fvmv_init(fvmv_dec *d) { memset(d, 0, sizeof *d); }

const uint8_t *fvmv_last_frame(const fvmv_dec *d)
{
    uint32_t b = FVMV_BUF0 + ((d->frame_no - 1) & 1) * FVMV_BUF_STRIDE;
    return d->ewram + (b - FVMV_EWRAM_BASE);
}

int fvmv_decode_frame_at(fvmv_dec *d, const uint8_t *v, uint32_t vlen, uint32_t avail,
                         uint32_t out, uint32_t ref)
{
    ctx c;
    memset(&c, 0, sizeof c);
    c.d = d;
    if (vlen < 8 || avail < vlen) { d->err++; return -1; }
    uint32_t nbits = rd32le(v), ncol = rd32le(v + 4);
    /* The ARM code never checks the substream lengths: each reader just runs on through
     * whatever bytes follow.  The readers here are bounded by 'avail' only; running past
     * the nominal end of a substream is counted in d->overrun (never happens in the carts). */
    const uint8_t *end = v + avail;
    c.bits = v + 8; c.bend = end;
    c.col = v + 8 + (size_t)nbits; c.cend = end;
    c.mv = c.col + (size_t)ncol; c.mend = end;
    if ((size_t)nbits + ncol + 8 > avail) { c.col = c.mv = end; }
    const uint8_t *nb_end = v + 8 + (size_t)nbits, *nc_end = c.mv, *nm_end = v + vlen;
    c.r8 = 0x80000000u;
    (void)bit(&c);                   /* the driver primes the bit register: the first bit is unused */
    c.out = out;
    c.ref = ref;
    uint64_t e0 = d->err;
    for (uint32_t by = 0; by < FVMV_H / 8; by++)
        for (uint32_t bx = 0; bx < FVMV_W / 8; bx++)
            block(&c, 8, 8, by * 8 * STRIDE + bx * 16);
    if (c.bits > nb_end || c.col > nc_end || c.mv > nm_end) d->overrun++;
    /* bit and colour streams end exactly; the MV byte stream is padded to a word */
    if (c.bits != nb_end || c.col != nc_end || c.mv > nm_end || nm_end - c.mv > 3 || (vlen & 3)) d->inexact++;
    d->frame_no++;
    return d->err != e0 ? -1 : 0;
}

int fvmv_decode_frame(fvmv_dec *d, const uint8_t *v, uint32_t vlen)
{
    uint32_t sel = d->frame_no & 1;
    return fvmv_decode_frame_at(d, v, vlen, vlen, FVMV_BUF0 + sel * FVMV_BUF_STRIDE,
                                FVMV_BUF0 + (sel ^ 1) * FVMV_BUF_STRIDE);
}

int fvmv_parse_header(const uint8_t *p, size_t n, fvmv_header *h)
{
    if (n < 0x20 || memcmp(p, "FVMV", 4)) return -1;
    h->nframes = rd32le(p + 4); h->width = rd32le(p + 8); h->height = rd32le(p + 12);
    h->period = rd32le(p + 16); h->data_size = rd32le(p + 20); h->max_record = rd32le(p + 24);
    h->audio_buf = rd32le(p + 28); h->hdr_off = 0;
    return 0;
}

long fvmv_walk(const uint8_t *s, size_t n, const fvmv_header *h, fvmv_record *rec, long max)
{
    size_t p = 0x20, end = 0x20 + (size_t)h->data_size;
    long i = 0;
    if (end > n) return -1;
    while (p + 0x18 <= end && (uint32_t)i < h->nframes) {
        fvmv_record r;
        r.off = (uint32_t)p; r.size = rd32le(s + p); r.back = (int32_t)rd32le(s + p + 4);
        r.fwd = rd32le(s + p + 8); r.index = rd32le(s + p + 12); r.vlen = rd32le(s + p + 16);
        if (p + 0x18 + r.vlen > end) return -1;
        r.alen = rd32le(s + p + 0x14 + r.vlen);
        if (rec && i < max) rec[i] = r;
        i++;
        p += 16 + r.size;
    }
    return i;
}

/* helpers for FFI users (Python ctypes) */
size_t fvmv_dec_size(void) { return sizeof(fvmv_dec); }
uint8_t *fvmv_ewram(fvmv_dec *d) { return d->ewram; }
