/* Minimal raw LZMA decoder, written from the LZMA format description (Igor Pavlov's
 * "lzma-specification.txt", public domain). Ring-buffer output, no end marker, no dictionary
 * size limit other than the ring itself. */
#include "lzma_dec.h"
#include <string.h>

#define NSTATES 12
#define POSB_MAX 4
#define P_ISMATCH   0
#define P_ISREP     (P_ISMATCH + (NSTATES << POSB_MAX))
#define P_ISREPG0   (P_ISREP + NSTATES)
#define P_ISREPG1   (P_ISREPG0 + NSTATES)
#define P_ISREPG2   (P_ISREPG1 + NSTATES)
#define P_ISREP0L   (P_ISREPG2 + NSTATES)
#define P_POSSLOT   (P_ISREP0L + (NSTATES << POSB_MAX))
#define P_SPECPOS   (P_POSSLOT + (4 << 6))
#define P_ALIGN     (P_SPECPOS + 114)
#define P_LEN       (P_ALIGN + 16)
/* length coder: choice, choice2, low[16][8], mid[16][8], high[256] */
#define L_CHOICE 0
#define L_CHOICE2 1
#define L_LOW 2
#define L_MID (L_LOW + (16 << 3))
#define L_HIGH (L_MID + (16 << 3))
#define L_SIZE (L_HIGH + 256)
#define P_REPLEN    (P_LEN + L_SIZE)
#define P_LITERAL   (P_REPLEN + L_SIZE)

size_t lzma_probs_size(unsigned lc, unsigned lp) { return P_LITERAL + ((size_t)0x300 << (lc + lp)); }

#define TOP (1u << 24)

static inline int rc_norm(lzma_dec *d) {
    if (d->range < TOP) {
        if (d->in >= d->in_end) { d->error = 1; return 1; }
        d->range <<= 8;
        d->code = (d->code << 8) | *d->in++;
    }
    return 0;
}

static inline unsigned rc_bit(lzma_dec *d, uint16_t *p) {
    uint32_t bound = (d->range >> 11) * *p;
    unsigned b;
    if (d->code < bound) { d->range = bound; *p += (2048 - *p) >> 5; b = 0; }
    else { d->range -= bound; d->code -= bound; *p -= *p >> 5; b = 1; }
    rc_norm(d);
    return b;
}

static inline unsigned rc_direct(lzma_dec *d, unsigned n) {
    unsigned v = 0;
    while (n--) {
        d->range >>= 1;
        unsigned b = d->code >= d->range;
        if (b) d->code -= d->range;
        v = (v << 1) | b;
        rc_norm(d);
    }
    return v;
}

static unsigned tree(lzma_dec *d, uint16_t *p, unsigned bits) {
    unsigned m = 1;
    for (unsigned i = 0; i < bits; i++) m = (m << 1) | rc_bit(d, p + m);
    return m - (1u << bits);
}

static unsigned rtree(lzma_dec *d, uint16_t *p, unsigned bits) {
    unsigned m = 1, v = 0;
    for (unsigned i = 0; i < bits; i++) { unsigned b = rc_bit(d, p + m); m = (m << 1) | b; v |= b << i; }
    return v;
}

static unsigned len_dec(lzma_dec *d, uint16_t *p, unsigned ps) {
    if (!rc_bit(d, p + L_CHOICE)) return tree(d, p + L_LOW + (ps << 3), 3);
    if (!rc_bit(d, p + L_CHOICE2)) return 8 + tree(d, p + L_MID + (ps << 3), 3);
    return 16 + tree(d, p + L_HIGH, 8);
}

void lzma_init(lzma_dec *d, unsigned lc, unsigned lp, unsigned pb, uint16_t *probs,
               uint8_t *ring, uint32_t ring_mask, const uint8_t *in, const uint8_t *in_end) {
    memset(d, 0, sizeof *d);
    d->lc = lc; d->lp = lp; d->pb = pb;
    d->probs = probs; d->nprobs = lzma_probs_size(lc, lp);
    for (size_t i = 0; i < d->nprobs; i++) probs[i] = 1024;
    d->ring = ring; d->ring_mask = ring_mask;
    d->in = in; d->in_end = in_end;
    d->range = 0xFFFFFFFFu;
    for (int i = 0; i < 5; i++) {
        if (d->in >= d->in_end) { d->error = 1; return; }
        d->code = (d->code << 8) | *d->in++;
    }
}

static inline uint8_t ring_get(lzma_dec *d, uint32_t dist) { return d->ring[(d->pos - dist - 1) & d->ring_mask]; }
static inline void ring_put(lzma_dec *d, uint8_t b) { d->ring[d->pos & d->ring_mask] = b; d->pos++; }

static void copy_match(lzma_dec *d, uint32_t *n) {
    while (d->remain_len && *n) {
        ring_put(d, ring_get(d, d->rep[0]));
        d->remain_len--; (*n)--;
    }
}

int lzma_decode(lzma_dec *d, uint32_t n) {
    uint16_t *P = d->probs;
    const unsigned pbm = (1u << d->pb) - 1, lpm = (1u << d->lp) - 1;
    copy_match(d, &n);
    while (n && !d->error) {
        unsigned ps = d->pos & pbm;
        unsigned st = d->state;
        if (!rc_bit(d, P + P_ISMATCH + (st << POSB_MAX) + ps)) {
            uint8_t prev = (d->pos || d->lenient) ? ring_get(d, 0) : 0;
            uint16_t *lp = P + P_LITERAL + 0x300 * (((d->pos & lpm) << d->lc) + (prev >> (8 - d->lc)));
            unsigned sym = 1;
            if (st >= 7) {
                unsigned mb = ring_get(d, d->rep[0]);
                do {
                    unsigned mbit = (mb >> 7) & 1; mb <<= 1;
                    unsigned b = rc_bit(d, lp + ((1 + mbit) << 8) + sym);
                    sym = (sym << 1) | b;
                    if (mbit != b) break;
                } while (sym < 0x100);
            }
            while (sym < 0x100) sym = (sym << 1) | rc_bit(d, lp + sym);
            ring_put(d, (uint8_t)sym);
            d->state = st < 4 ? 0 : st < 10 ? st - 3 : st - 6;
            n--;
            continue;
        }
        unsigned len;
        if (rc_bit(d, P + P_ISREP + st)) {
            if (!d->pos && !d->lenient) { d->error = 2; break; }
            if (!rc_bit(d, P + P_ISREPG0 + st)) {
                if (!rc_bit(d, P + P_ISREP0L + (st << POSB_MAX) + ps)) {   /* short rep */
                    d->state = st < 7 ? 9 : 11;
                    ring_put(d, ring_get(d, d->rep[0]));
                    n--;
                    continue;
                }
            } else {
                uint32_t dist;
                if (!rc_bit(d, P + P_ISREPG1 + st)) dist = d->rep[1];
                else {
                    if (!rc_bit(d, P + P_ISREPG2 + st)) dist = d->rep[2];
                    else { dist = d->rep[3]; d->rep[3] = d->rep[2]; }
                    d->rep[2] = d->rep[1];
                }
                d->rep[1] = d->rep[0]; d->rep[0] = dist;
            }
            len = len_dec(d, P + P_REPLEN, ps);
            d->state = st < 7 ? 8 : 11;
        } else {
            d->rep[3] = d->rep[2]; d->rep[2] = d->rep[1]; d->rep[1] = d->rep[0];
            len = len_dec(d, P + P_LEN, ps);
            d->state = st < 7 ? 7 : 10;
            unsigned ls = len < 4 ? len : 3;
            unsigned slot = tree(d, P + P_POSSLOT + (ls << 6), 6);
            uint32_t dist;
            if (slot < 4) dist = slot;
            else {
                unsigned nd = (slot >> 1) - 1;
                dist = (2 | (slot & 1)) << nd;
                if (slot < 14) dist += rtree(d, P + P_SPECPOS + dist - slot - 1, nd);
                else { dist += rc_direct(d, nd - 4) << 4; dist += rtree(d, P + P_ALIGN, 4); }
            }
            d->rep[0] = dist;
        }
        len += 2;
        if (!d->lenient && (d->rep[0] >= d->pos || d->rep[0] > d->ring_mask)) { d->error = 2; break; }
        if (d->rep[0] + 1 > d->max_dist) d->max_dist = d->rep[0] + 1;
        d->remain_len = len;
        copy_match(d, &n);
    }
    return d->error;
}
