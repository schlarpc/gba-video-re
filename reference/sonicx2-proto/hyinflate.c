/* Hydrogen "Inflate" (hyCompressionManager::Inflate) as used by the Sonic X Vol 2 prototype.
 *
 * Bit-exact port of the cart's IWRAM routines (proto addresses):
 *   0x030005c8 UncompressBlock state machine, 0x03002cac getbits, 0x030027ec dynamic-block loop,
 *   0x0300241c code-length decoder, 0x030025d8 table builder, 0x03000338 fixed table,
 *   0x03000144 fixed-block loop, 0x03000490 stored block.
 *
 * Blob = u32 uncompressed_size, then a bitstream read MSB-first out of little-endian 16-bit
 * halfwords. Blocks start with a bare 2-bit type: 0 stored, 1 fixed Huffman, 2 dynamic Huffman,
 * 3 end-of-stream. The bit-buffer refill points are replicated exactly because the stored-block
 * header behaviour depends on how many bits are buffered.
 */
#include "hyinflate.h"
#include <string.h>
#include <stdio.h>
int hy_trace;

typedef struct {
    uint32_t buf;    /* MSB-aligned bit buffer */
    uint32_t cnt;    /* valid bits in buf */
    const uint8_t *p, *end;
} br_t;

static inline uint32_t ld16(br_t *b)
{
    uint32_t v = 0;
    if (b->p + 2 <= b->end) v = b->p[0] | (b->p[1] << 8);
    b->p += 2;
    return v;
}
/* refill one halfword if fewer than n bits (cart: "cmp cnt,#n; bhs skip") */
static inline void refill_lt(br_t *b, uint32_t n)
{
    if (b->cnt < n) { b->buf |= ld16(b) << (16 - b->cnt); b->cnt += 16; }
}
/* 0x03002cac */
static inline uint32_t getbits(br_t *b, uint32_t n)
{
    refill_lt(b, n);
    uint32_t v = b->buf >> (32 - n);
    b->buf <<= n; b->cnt -= n;
    return v;
}

/* Canonical Huffman table, decoded as the cart's lookup tables do (lengths 1..15). */
typedef hy_huff_t huff_t;

static int huff_build(huff_t *h, const uint8_t *len, int n)
{
    memset(h->count, 0, sizeof h->count);
    for (int i = 0; i < n; i++) h->count[len[i] & 15]++;
    h->count[0] = 0;
    uint16_t code, idx;
    code = 0;
    for (int l = 1; l < 16; l++) {
        code = (uint16_t)((code + h->count[l - 1]) << 1);
        if (l == 1) code = 0;
        h->first[l] = code;
    }
    uint16_t off[16]; idx = 0;
    for (int l = 1; l < 16; l++) { off[l] = idx; h->index[l] = idx; idx += h->count[l]; }
    for (int i = 0; i < n; i++) if (len[i] & 15) h->sym[off[len[i] & 15]++] = (uint16_t)i;
    return idx;
}

/* decode one symbol; caller guarantees >= 15 buffered bits (cart refills at < 15) */
static inline int huff_dec(const huff_t *h, br_t *b)
{
    uint32_t code = 0;
    for (int l = 1; l < 16; l++) {
        code = (code << 1) | ((b->buf >> (32 - l)) & 1);
        uint32_t c = h->count[l];
        if (c && code - h->first[l] < c) {
            b->buf <<= l; b->cnt -= l;
            return h->sym[h->index[l] + code - h->first[l]];
        }
    }
    return -1;
}

static const uint16_t LBASE[30] = {0,3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258};
static const uint8_t  LEXT[30]  = {0,0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0};
static const uint16_t DBASE[30] = {1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577};
static const uint8_t  DEXT[30]  = {0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13};
static const uint8_t  CLPERM[19] = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};

/* ---- faithful streaming port of Initialize (0x03000094) / UncompressBlock (0x030005c8) ------
 * The player calls UncompressBlock(n) once per frame. Its byte accounting has quirks that only
 * show up with stored blocks (none occur on this cart); this port reproduces them so that any
 * sequence of calls gives the cart's bytes:
 *   - a stored block that starts inside a call after another block ended in the same call is asked
 *     for twice the remaining count (r38 is not reset as the Huffman headers do), so it produces
 *     too much and the excess is not credited to the next call (bytes are skipped);
 *   - a 1-byte stored block whose byte comes out of the bit buffer and exactly ends a call leaves the
 *     block "open" and the next call reads a new 16-bit length;
 *   - a stored copy entered with a non-positive remaining count moves the pointers backwards
 *     (memcpy with a negative size on the cart: memory corruption). Reported as HY_UB.
 */
static const int8_t TYPE2STATE[4] = {5, 2, 1, 7};

void hy_stream_init(hy_stream *s, const uint8_t *blob, size_t bloblen, uint8_t *out, size_t outcap)
{
    memset(s, 0, sizeof *s);
    s->blob = blob; s->end = blob + bloblen;
    s->usize = bloblen >= 4 ? (blob[0] | blob[1] << 8 | blob[2] << 16 | (uint32_t)blob[3] << 24) : 0;
    s->out = out; s->outcap = outcap; s->o = 0;
    s->r48 = (int32_t)s->usize; s->r38 = 0; s->state = 0; s->r4c = 0;
    s->p = blob + 4; s->buf = 0; s->cnt = 0;
}

#define S_BR br_t b = { s->buf, s->cnt, s->p, s->end }
#define S_SAVE do { s->buf = b.buf; s->cnt = b.cnt; s->p = b.p; } while (0)
static inline int s_put(hy_stream *s, uint8_t v) { if (s->o >= s->outcap) { s->err = HY_OVERFLOW; return 0; } s->out[s->o++] = v; return 1; }
static inline int s_copy(hy_stream *s, uint32_t d, uint32_t len)
{
    if (d > s->o) { s->err = HY_BADDIST; return 0; }
    while (len--) { if (!s_put(s, s->out[s->o - d])) return 0; }
    return 1;
}

/* 0x030027ec / 0x03000144 symbol loops; r6 = r38 at entry */
static void s_huff(hy_stream *s, int fixed)
{
    S_BR;
    int32_t r6 = s->r38;
    if (fixed && r6 < 0) goto done;
    for (;;) {
        refill_lt(&b, 15);
        int sym = huff_dec(&s->lit, &b);
        if (sym < 0) { s->err = HY_BADCODE; goto done; }
        if (sym < 256) {
            if (!s_put(s, (uint8_t)sym)) goto done;
            r6--;
            if (fixed ? r6 < 0 : r6 == 0) goto done;
            continue;
        }
        if (sym == 256) { s->state = 0; goto done; }
        sym -= 256;
        if (sym >= 30) { s->err = HY_BADCODE; goto done; }
        uint32_t len = LBASE[sym], d;
        int ds;
        if (fixed) {
            if (LEXT[sym]) len += getbits(&b, LEXT[sym]);
            ds = (int)getbits(&b, 5);
        } else {
            if (LEXT[sym]) { refill_lt(&b, LEXT[sym]); len += b.buf >> (32 - LEXT[sym]); b.buf <<= LEXT[sym]; b.cnt -= LEXT[sym]; }
            refill_lt(&b, 15); ds = huff_dec(&s->dist, &b);
        }
        if (ds < 0 || ds >= 30) { s->err = HY_BADCODE; goto done; }
        d = DBASE[ds];
        if (DEXT[ds]) {
            if (fixed) d += getbits(&b, DEXT[ds]);
            else { refill_lt(&b, DEXT[ds]); d += b.buf >> (32 - DEXT[ds]); b.buf <<= DEXT[ds]; b.cnt -= DEXT[ds]; }
        }
        r6 -= (int32_t)len;
        if (!s_copy(s, d, len)) goto done;
        if (s->st) { s->st->matches++; s->st->match_bytes += len; }
        if (fixed ? r6 < 0 : r6 <= 0) goto done;
    }
done:
    s->r38 = r6; S_SAVE;
}

/* 0x03000490 */
static void s_stored(hy_stream *s)
{
    S_BR;
    if (s->r4c == 0) {
        s->r4c = (int32_t)getbits(&b, 16);
        if (b.cnt >= 8) {
            s_put(s, (uint8_t)(b.buf >> 24));
            b.buf <<= 8; b.cnt -= 8; s->r4c--; s->r38--;
        }
        b.buf <<= b.cnt; b.cnt = 0;
        if (s->r38 < 1) { S_SAVE; return; }
    }
    int32_t r0 = s->r38, r5 = s->r4c;
    if (r0 < r5) r5 = (r0 & ~1) + ((r0 & 1) ? 2 : 0);
    int32_t r7 = r5 & ~1;
    if (r7 < 0) { s->err = HY_UB; S_SAVE; return; }
    for (int32_t i = 0; i < r7; i++) { uint8_t v = (b.p + i < b.end) ? b.p[i] : 0; if (!s_put(s, v)) break; }
    b.p += r7;
    if (r5 & 1) s_put(s, (uint8_t)getbits(&b, 8));
    s->r38 -= r5; s->r4c -= r5;
    if (s->r4c == 0) s->state = 0;
    S_SAVE;
}

static int s_dyn_header(hy_stream *s)
{
    S_BR;
    refill_lt(&b, 14);
    uint32_t w = b.buf;
    int hlit = (w >> 27) + 257, hdist = ((w << 5) >> 27) + 1, hclen = ((w << 10) >> 28) + 4;
    b.buf <<= 14; b.cnt -= 14;
    uint8_t cll[19]; memset(cll, 0, sizeof cll);
    for (int i = 0; i < hclen; i++) cll[CLPERM[i]] = (uint8_t)getbits(&b, 3);
    huff_t cl; huff_build(&cl, cll, 19);
    uint8_t lens[320]; memset(lens, 0, sizeof lens);
    int n = hlit + hdist, i = 0;
    while (i < n) {
        refill_lt(&b, 15);
        int sy = huff_dec(&cl, &b);
        if (sy < 0) { s->err = HY_BADCODE; S_SAVE; return 0; }
        if (sy < 16) { lens[i++] = (uint8_t)sy; continue; }
        int rep; uint8_t v;
        if (sy == 16) { refill_lt(&b, 2); rep = 3 + (b.buf >> 30); b.buf <<= 2; b.cnt -= 2; v = i ? lens[i - 1] : 0; }
        else if (sy == 17) { refill_lt(&b, 3); rep = 3 + (b.buf >> 29); b.buf <<= 3; b.cnt -= 3; v = 0; }
        else { refill_lt(&b, 7); rep = 11 + (b.buf >> 25); b.buf <<= 7; b.cnt -= 7; v = 0; }
        if (i + rep > n) { s->err = HY_UB; S_SAVE; return 0; }   /* cart writes past its table */
        while (rep--) lens[i++] = v;
    }
    huff_build(&s->lit, lens, hlit);
    huff_build(&s->dist, lens + hlit, hdist);
    if (s->st) {
        const hy_huff_t *t[3] = { &cl, &s->lit, &s->dist };
        for (int k = 0; k < 3; k++) {
            uint32_t kraft = 0;
            for (int l = 1; l < 16; l++) kraft += (uint32_t)t[k]->count[l] << (15 - l);
            if (kraft < 0x8000) s->st->incomplete_codes++;
            if (kraft > 0x8000) s->st->oversubscribed_codes++;
        }
    }
    S_SAVE;
    return 1;
}

long hy_stream_read(hy_stream *s, uint32_t n)
{
    int32_t r5 = s->r48 - s->r38;
    if ((int32_t)n < r5) r5 = (int32_t)n;
    int r7 = r5 > 0;
    long ret = (long)s->o + s->r38;
    while (r7 && !s->err) {
        switch (s->state) {
        case 0: { S_BR; uint32_t t = getbits(&b, 2); S_SAVE; s->state = TYPE2STATE[t]; if (s->st && t < 3) s->st->blocks[t]++; break; }
        case 1:
            if (!s_dyn_header(s)) break;
            s->r38 = 0; s->state = 3;
            /* fall through */
        case 3:
        case 4:
        case 5: {
            if (s->state == 2) break;
            int st0 = s->state;
            s->r38 += r5; s->r48 -= s->r38;
            if (st0 == 5) s_stored(s); else s_huff(s, st0 == 4);
            s->r48 += s->r38;
            r5 = s->r38;
            if (s->state == st0) r7 = r5 > 0;
            break; }
        case 2: {
            static uint8_t fl[288]; static int built;
            if (!built) {
                for (int i = 0; i < 144; i++) fl[i] = 8;
                for (int i = 144; i < 256; i++) fl[i] = 9;
                for (int i = 256; i < 280; i++) fl[i] = 7;
                for (int i = 280; i < 288; i++) fl[i] = 8;
                built = 1;
            }
            huff_build(&s->lit, fl, 288);
            s->r38 = 0; s->state = 4;
            s->r38 += r5; s->r48 -= s->r38;
            s_huff(s, 1);
            s->r48 += s->r38; r5 = s->r38;
            if (s->state == 4) r7 = r5 > 0;
            break; }
        case 7:
            if (s->o != s->usize || s->r48 != 0) s->err = HY_END;   /* cart: "Buffer overflow!" assert */
            s->done = 1; r7 = 0; break;
        default:
            s->err = HY_UB; break;
        }
    }
    return s->err ? -(long)s->err : ret;
}

long hy_inflate(const uint8_t *blob, size_t bloblen, uint8_t *out, size_t outcap, hy_inflate_stats *st)
{
    if (bloblen < 4) return -1;
    uint32_t usize = blob[0] | blob[1] << 8 | blob[2] << 16 | (uint32_t)blob[3] << 24;
    if (usize > outcap) return -2;
    static hy_stream s;
    hy_stream_init(&s, blob, bloblen, out, outcap);
    size_t mstep = 0, mn = 0, *marks = NULL;
    if (st) { mstep = st->mark_step; mn = st->nmarks; marks = st->marks; memset(st, 0, sizeof *st); st->mark_step = mstep; st->nmarks = mn; st->marks = marks; }
    s.st = st;
    if (marks && mstep) {               /* one call per mark: the player's per-frame calls */
        for (size_t k = 0; k < mn; k++) {
            long r = hy_stream_read(&s, (uint32_t)mstep);
            if (r < 0) return r;
            marks[k] = (size_t)(s.p - blob);
        }
    }
    long r = hy_stream_read(&s, usize);   /* rest (or everything) */
    if (r < 0) return r;
    /* consume the end-of-stream code the player never reads: verifies the terminator */
    if (!s.err) {
        uint32_t left = 1;
        while (s.state != 7 && !s.err && left) { s.r48 = 1; r = hy_stream_read(&s, 1); left = s.state != 7; if (s.o > s.usize) break; }
    }
    if (st) { st->consumed = (size_t)(s.p - blob); st->literals = s.o; }
    return s.o == s.usize ? (long)usize : -8;
}

/* Replay a sequence of UncompressBlock(n) calls the way the player consumes them: after each call
 * the n bytes starting at the returned offset are appended to collected[]. Calls continue (with the
 * last size) until usize bytes were collected. Returns the number of bytes collected or <0. */
long hy_inflate_calls(const uint8_t *blob, size_t bloblen, uint8_t *work, size_t workcap,
                      const uint32_t *calls, size_t ncalls, uint8_t *collected)
{
    static hy_stream s;
    hy_stream_init(&s, blob, bloblen, work, workcap);
    size_t got = 0, k = 0;
    while (got < s.usize) {
        uint32_t n = ncalls ? calls[k < ncalls ? k : ncalls - 1] : s.usize;
        k++;
        if (n > s.usize - got) n = (uint32_t)(s.usize - got);
        long r = hy_stream_read(&s, n);
        if (hy_trace) fprintf(stderr, "call %zu n %u ret %ld o %zu r38 %d r48 %d r4c %d state %d\n", k - 1, n, r, s.o, s.r38, s.r48, s.r4c, s.state);
        if (r < 0) return r;
        if ((size_t)r + n > workcap) return -HY_OVERFLOW;
        memcpy(collected + got, work + r, n);
        got += n;
    }
    return (long)got;
}
