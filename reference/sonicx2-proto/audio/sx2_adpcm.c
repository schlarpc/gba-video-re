#include "sx2_adpcm.h"
#include <string.h>

static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static unsigned rd16(const uint8_t *p) { return p[0] | p[1] << 8; }
/* ARM register-specified shifts: amounts >= 32 give 0. */
static uint32_t shl(uint32_t v, uint32_t s) { return s >= 32 ? 0 : v << s; }
static uint32_t shr(uint32_t v, uint32_t s) { return s >= 32 ? 0 : v >> s; }
static int32_t asr(int32_t v, uint32_t s) { s &= 0xff; if (s >= 32) return v < 0 ? -1 : 0; return v >> s; }

/* VLC on the top 3 bits: low nibble = code length, (high nibble) = rank 0..3 in the context's
   permutation.  Rank r: 0 -> '0', 1 -> '10', 2 -> '110', 3 -> '111'. */
static const uint8_t vlc_len[8] = {1, 1, 1, 1, 2, 2, 3, 3};
static const uint8_t vlc_rank[8] = {0, 0, 0, 0, 1, 1, 2, 3};

static uint32_t load(sx2a_ctx *c)
{
    uint32_t w = 0;
    if (c->ptr + 4 <= c->end) w = rd32(c->ptr);
    c->ptr += 4;
    return w;
}

/* Refill: identical behaviour of the Thumb (0x08005d6c) and ARM (0x03002c3c) versions. */
static void refill(sx2a_ctx *c)
{
    if (c->nbits) {
        c->word |= shr(c->next, c->bits);
        if (c->nbits + c->bits > 32) {
            c->next = shl(c->next, 32 - c->bits);
            c->nbits = c->nbits + c->bits - 32;
            c->bits = 32;
            return;
        }
        c->bits += c->nbits;
    }
    uint32_t w = load(c);
    c->word |= shr(w, c->bits);
    c->next = shl(w, 32 - c->bits);
    c->nbits = c->bits;
    c->bits = 32;
}

static void skip(sx2a_ctx *c, uint32_t n) { c->bits -= n; c->word = shl(c->word, n); }

static void reset_pred(sx2a_ctx *c)
{
    c->b = 0; c->dprev = 1; c->a1 = c->a2 = 0; c->p1 = c->p2 = 1; c->s1 = c->s2 = 0;
}

int sx2a_parse(const uint8_t *res, size_t len, sx2a_info *in)
{
    if (len < 16) return -1;
    in->w0 = rd32(res); in->w1 = rd32(res + 4); in->w2 = rd32(res + 8);
    in->codec = (in->w1 >> 6) & 15;
    in->last_frames = rd16(res + 12);
    in->nblocks = rd16(res + 14);
    in->sizes = res + 16;
    in->data = res + 16 + ((in->nblocks * 2 + 3) & ~3u);
    if (in->codec != 3 || in->nblocks == 0 || in->data > res + len) return -1;
    in->frames = in->nblocks * 12 + in->last_frames - 12;
    in->samples = (size_t)SX2A_BLOCK_SAMPLES * in->nblocks + in->last_frames * SX2A_FRAME_SAMPLES
                  + (in->last_frames >> 1) - SX2A_BLOCK_SAMPLES;
    return 0;
}

static void start_reader(sx2a_ctx *c, const uint8_t *p)
{
    c->word = rd32(p); c->next = rd32(p + 4); c->ptr = p + 8; c->bits = 32; c->nbits = 32;
}

void sx2a_init(sx2a_ctx *c, const sx2a_info *in, int clip)
{
    memset(c, 0, sizeof *c);
    c->x = 0x8800; reset_pred(c);   /* 0x08005e5c */
    c->end = in->data;
    for (unsigned i = 0; i < in->nblocks; i++) c->end += 4 * rd16(in->sizes + 2 * i);
    start_reader(c, in->data);
    c->clip = clip;
}

void sx2a_seek_block(sx2a_ctx *c, const sx2a_info *in, unsigned k)
{
    const uint8_t *p = in->data;
    for (unsigned i = 0; i < k; i++) p += 4 * rd16(in->sizes + 2 * i);
    c->x = 0x8800; reset_pred(c); c->rem = 0; c->st = 0;
    start_reader(c, p);
}

void sx2a_samples(sx2a_ctx *c, int8_t *out, uint32_t n)
{
    c->rem -= n;
    while (n--) {
        if (c->bits <= 2) refill(c);
        uint32_t i = c->word >> 29;
        c->word = shl(c->word, vlc_len[i]);
        c->bits -= vlc_len[i];
        uint32_t sym = (c->perm[c->st] >> (2 * vlc_rank[i])) & 3;
        c->st = ((c->st << 2) & 0x3f) | sym;

        int32_t x = c->x;
        int32_t t = (x >> 8) + 0x74 + ((sym & 1) ? 0xf9 : 0);
        int32_t m = (int32_t)((((uint32_t)t & 0x7f) | 0x80) << 7);
        int32_t dq = asr(m, (uint32_t)(14 - (asr(t, 7) & 15)));
        if (sym & 2) dq = -dq;
        int32_t lx = x >> 6, f;
        if (sym & 1) f = lx > 0x12e0 ? 0x1400 : lx + ((0x36e0 - lx) >> 5);
        else         f = lx < 0x248 ? 0x220 : lx + ((-(lx + 0x2c0)) >> 5);
        c->x = x + f + ((int32_t)(0u - (uint32_t)x) >> 6);

        int32_t pr = (int32_t)((uint32_t)c->dprev * (uint32_t)c->b);
        int32_t acc = ((int32_t)((uint32_t)c->p2 * (uint32_t)(c->a2 >> 2)) >> 11)
                    + ((int32_t)((uint32_t)c->p1 * (uint32_t)(c->a1 >> 2)) >> 11) + (pr >> 11);
        int32_t s = (int32_t)((uint32_t)dq + (uint32_t)(acc >> 1));
        if (s == 0) s = 1;
        int32_t sg_src = dq + (pr >> 12);
        c->p2 = c->p1; c->p1 = s;
        int32_t o = (int32_t)((uint32_t)s + ((uint32_t)(s >> 31) >> 26)) >> 6;
        if (c->clip) { if (o > 127) o = 127; if (o < -128) o = -128; }
        if (out) *out++ = (int8_t)(uint8_t)o;

        int32_t a1l = c->a1 - (c->a1 >> 8);
        int32_t a2 = c->a2 - (c->a2 >> 7), a1 = a1l;
        uint8_t sg = (uint32_t)sg_src >> 31;
        if (sg_src != 0) {
            int32_t r4 = a1l;
            if (sg == c->s1) { a1 = a1l + 0xc0; r4 = -a1l; } else a1 = a1l - 0xc0;
            if ((uint32_t)(r4 + 0x1fff) <= 0x3fff) a2 += r4 >> 5;
            else if (r4 < 0) a2 -= 0x100; else a2 += 0xff;
            if (sg != c->s2) a2 -= 0x80; else a2 += 0x80;
        }
        if (a1 > 0x3c00 - a2) a1 = 0x3c00 - a2;
        c->a1 = a1; c->a2 = a2;
        c->b = c->b - (c->b >> 8) + (((dq ^ c->dprev) >= 0) ? 0x20 : -0x20);
        c->s2 = c->s1; c->s1 = sg;
        c->dprev = dq;
    }
}

/* Block header (0x08005ea0): continuation bit or [0, pad to 32-bit boundary, 1 bit, s24 x],
   then 64 x 5-bit permutation entries. */
unsigned long sx2a_hdr_resync, sx2a_hdr_cont;
static void block_header(sx2a_ctx *c)
{
    if (c->bits < 1) refill(c);
    if (c->word >> 31) {
        sx2a_hdr_cont++;
        if (c->bits < 1) refill(c);
        skip(c, 1);
    } else {
        sx2a_hdr_resync++;
        uint32_t k = (c->bits + c->nbits) & 31;
        if (c->bits < k) refill(c);
        skip(c, k);
        if (c->bits < 1) refill(c);
        skip(c, 1);
        if (c->bits < 24) refill(c);
        uint32_t w = c->word;
        skip(c, 24);
        c->x = (int32_t)w >> 8;
        reset_pred(c);
    }
    for (int j = 0; j < 64; j++) {
        if (c->bits < 5) refill(c);
        uint32_t w = c->word;
        skip(c, 5);
        unsigned a = w >> 30, b = (w >> 28) & 3, sw = (w >> 27) & 1;
        unsigned mask = (1u << a) | (1u << b);
        /* remaining two symbols: lo = lowest unused of {0,1} (else 2), hi = highest unused of
           {2,3} (else 1); evaluated per 2-bit half exactly like the player's 4-byte table. */
        unsigned ml = mask & 3, mh = mask >> 2;
        unsigned lo = !(ml & 1) ? 0 : !(ml & 2) ? 1 : 2;
        unsigned hi = !(mh & 2) ? 3 : !(mh & 1) ? 2 : 1;
        c->perm[j] = a | b << 2 | (sw ? (hi << 4 | lo << 6) : (lo << 4 | hi << 6));
    }
    c->st = 0;
    c->rem = SX2A_BLOCK_SAMPLES;
}

void sx2a_decode(sx2a_ctx *c, int8_t *out, uint32_t n)
{
    if (c->rem >= n) { sx2a_samples(c, out, n); return; }
    if (c->rem) { uint32_t r = c->rem; n -= r; sx2a_samples(c, out, r); if (out) out += r; }
    while (n) {
        block_header(c);
        uint32_t r = n < c->rem ? n : c->rem;
        sx2a_samples(c, out, r); if (out) out += r;
        n -= r;
    }
}

int sx2_adpcm_decode(const uint8_t *res, size_t len, int8_t *out, size_t max)
{
    sx2a_info in; sx2a_ctx c;
    if (sx2a_parse(res, len, &in)) return -1;
    sx2a_init(&c, &in, 0);
    size_t n = in.samples < max ? in.samples : max;
    sx2a_decode(&c, out, (uint32_t)n);
    return (int)n;
}
