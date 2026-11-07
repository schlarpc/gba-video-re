/* Hydrogen DEFLATE variant decoder. Bit-exact with IWRAM hyCompressionManager::Inflate
 * (Initialize / UncompressBlock / CoreExpand_Static / the dynamic-block core / the stored copy),
 * including its behaviour when the output is requested in pieces (one video frame per request).
 *
 * Differences from RFC 1951:
 *  - bits are consumed MSB-first from 16-bit little-endian words (the bit buffer is refilled
 *    with whole halfwords), so every multi-bit field and every Huffman code is read
 *    most-significant bit first;
 *  - a block header is a bare 2-bit type: 0 stored, 1 fixed, 2 dynamic, 3 end of stream;
 *    there is no BFINAL bit;
 *  - a stored block has a 16-bit LEN and no NLEN, and its byte alignment follows the
 *    halfword reader (see stored() below);
 *  - the blob starts with a u32 little-endian uncompressed size.
 * Huffman construction (canonical codes), the code-length alphabet (16/17/18 with 2/3/7 extra
 * bits), the code-length order and the length/distance base tables are those of RFC 1951.
 *
 * The request/residual bookkeeping (rem = obj+0x38, left = obj+0x48, state = obj+0x3c,
 * sleft = obj+0x4c) is a literal transcription of the cartridge, quirks included; see
 * docs/format-hydrogen.md section 5.7. */
#include "hyinflate.h"
#include <string.h>

/* 32-bit wrapping arithmetic, as on the ARM7 */
static inline int32_t add32(int32_t a, int32_t b) { return (int32_t)((uint32_t)a + (uint32_t)b); }
static inline int32_t sub32(int32_t a, int32_t b) { return (int32_t)((uint32_t)a - (uint32_t)b); }

static inline void refill(hyinf_stream *s, unsigned need)
{
    while (s->n < need) {
        uint32_t w = 0;
        /* the cartridge tops up eagerly, so it may load a halfword past the end of the blob
         * (it reads whatever follows in ROM); those bits are never consumed by a valid stream */
        if (s->p + 2 <= s->end) w = s->p[0] | s->p[1] << 8;
        s->p += 2;
        s->buf |= w << (16 - s->n);
        s->n += 16;
    }
}

static inline unsigned getb(hyinf_stream *s, unsigned k)
{
    if (!k) return 0;
    refill(s, k);
    unsigned v = s->buf >> (32 - k);
    s->buf <<= k; s->n -= k;
    return v;
}

/* returns -1 for an over-subscribed length set (err 3). The cartridge builds its lookup tables
 * anyway (later codes overwrite earlier entries, possibly spilling into the sub-table pool), so its
 * result for such a set is implementation-defined; no valid stream has one. */
static int huff_build(hyinf_huff *h, const uint8_t *len, int n)
{
    uint16_t offs[16];
    memset(h->count, 0, sizeof h->count);
    for (int i = 0; i < n; i++) h->count[len[i]]++;
    h->count[0] = 0;
    int left = 1;
    for (int l = 1; l < 16; l++) { left <<= 1; left -= h->count[l]; if (left < 0) return -1; }
    offs[1] = 0;
    for (int l = 1; l < 15; l++) offs[l + 1] = offs[l] + h->count[l];
    for (int i = 0; i < n; i++) if (len[i]) h->sym[offs[len[i]]++] = (uint16_t)i;
    return 0;
}

/* Huffman symbol: the cartridge first tops the bit buffer up with ONE halfword if fewer than 15
 * bits are buffered (all three table decoders do this), then looks the code up in the buffered
 * bits. The eager refill does not change the decoded symbols, but it decides how many bits are
 * buffered when a stored block starts (stored() below). Canonical decode, MSB-first. */
static inline int huff_dec(hyinf_stream *s, const hyinf_huff *h)
{
    if (s->n < 15) refill(s, s->n + 1);
    int code = 0, first = 0, index = 0;
    for (int l = 1; l < 16; l++) {
        code |= (int)(s->buf >> (32 - l)) & 1;
        int c = h->count[l];
        if (code - c < first) {
            s->buf <<= l; s->n -= l;
            return h->sym[index + (code - first)];
        }
        index += c; first += c; first <<= 1; code <<= 1;
    }
    s->err = 1;
    return 0;
}

static const uint16_t LBASE[29] = {3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59,
                                   67, 83, 99, 115, 131, 163, 195, 227, 258};
static const uint8_t LEXT[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
static const uint16_t DBASE[30] = {1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769,
                                   1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577};
static const uint8_t DEXT[30] = {0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};
static const uint8_t CLORD[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};

static hyinf_huff FIXED_LIT;
static int fixed_ready;

static void fixed_init(void)
{
    uint8_t l[288];
    for (int i = 0; i < 144; i++) l[i] = 8;
    for (int i = 144; i < 256; i++) l[i] = 9;
    for (int i = 256; i < 280; i++) l[i] = 7;
    for (int i = 280; i < 288; i++) l[i] = 8;
    huff_build(&FIXED_LIT, l, 288);
    fixed_ready = 1;
}

static inline void put(hyinf_stream *s, uint8_t v)
{
    if (s->o < s->cap) s->out[s->o] = v; else s->err = 1;
    s->o++;
}

/* match copy; the cart has no bounds check other than the window assert */
static inline void copy(hyinf_stream *s, unsigned len, unsigned d)
{
    if ((size_t)d > s->o) { s->err = 1; return; }
    for (unsigned i = 0; i < len; i++) put(s, s->o - d < s->cap ? s->out[s->o - d] : 0);
}

static int dyn_header(hyinf_stream *s)
{
    if (s->n < 14) refill(s, s->n + 1);        /* the 14 header bits are taken from one top-up */
    unsigned hlit = getb(s, 5) + 257, hdist = getb(s, 5) + 1, hclen = getb(s, 4) + 4;
    uint8_t cl[19] = {0}, lens[320];
    for (unsigned i = 0; i < hclen; i++) cl[CLORD[i]] = (uint8_t)getb(s, 3);
    hyinf_huff clh;
    if (huff_build(&clh, cl, 19) < 0) { s->err = 3; return -1; }
    unsigned n = 0;
    /* the cartridge loops until the count reaches exactly zero; a run that jumps past the end
     * never terminates there, so it is an error here */
    while (n < hlit + hdist) {
        int c = huff_dec(s, &clh);
        if (s->err) return -1;
        if (c < 16) { lens[n++] = (uint8_t)c; continue; }
        unsigned rep; uint8_t v = 0;
        if (c == 16) { v = n ? lens[n - 1] : 0; rep = getb(s, 2) + 3; }
        else if (c == 17) rep = getb(s, 3) + 3;
        else rep = getb(s, 7) + 11;
        if (n + rep > hlit + hdist) return -1;
        while (rep--) lens[n++] = v;
    }
    if (huff_build(&s->lit, lens, hlit) < 0 || huff_build(&s->dist, lens + hlit, hdist) < 0) { s->err = 3; return -1; }
    return 0;
}

/* IWRAM 0x03002870 (dynamic) / 0x03000144 (fixed): decode symbols against s->rem */
static void core(hyinf_stream *s, int fixed)
{
    const hyinf_huff *L = fixed ? &FIXED_LIT : &s->lit;
    if (fixed && s->rem < 0) return;
    for (;;) {
        int c = huff_dec(s, L);
        if (s->err) return;
        if (c < 256) {
            put(s, (uint8_t)c); s->rem = sub32(s->rem, 1);
            if (!fixed && s->rem == 0) return;          /* dynamic: stop exactly at the request */
        } else if (c == 256) {
            s->state = 0; return;
        } else {
            c -= 257;
            if (c >= 29) { s->err = 1; return; }
            unsigned len = LBASE[c] + getb(s, LEXT[c]);
            int dc = fixed ? (int)getb(s, 5) : huff_dec(s, &s->dist);
            if (dc >= 30) { s->err = 1; return; }
            unsigned d = DBASE[dc] + getb(s, DEXT[dc]);
            s->rem = sub32(s->rem, (int32_t)len);
            copy(s, len, d);
            if (!fixed && !(s->rem > 0)) return;         /* dynamic: while (0 < rem) */
        }
        if (fixed && s->rem < 0) return;                 /* fixed: while (rem >= 0) */
    }
}

/* IWRAM 0x03000490: stored block. If 8 or more bits are buffered after LEN, the top 8 bits are
 * the first data byte (even when LEN is 0, which then underflows: an error here). The rest of
 * the buffer is dropped, whole bytes are copied in memory order, and an odd last byte of a piece
 * is an 8-bit read (the high byte of the next halfword). */
static void stored(hyinf_stream *s)
{
    if (s->sleft == 0) {
        s->sleft = (int32_t)getb(s, 16);
        if (s->n >= 8) {
            if (s->sleft == 0) { s->err = 1; return; }
            put(s, (uint8_t)(s->buf >> 24)); s->buf <<= 8; s->n -= 8;
            s->sleft = sub32(s->sleft, 1); s->rem = sub32(s->rem, 1);
        }
        s->buf = 0; s->n = 0;
        if (s->rem <= 0) return;
    }
    int32_t c = s->rem >= s->sleft ? s->sleft : add32(s->rem & ~1, (s->rem & 1) ? 2 : 0);
    /* a negative size goes to the cartridge's memcpy as a huge unsigned count (it runs off the
     * end of memory); a copy past the end of the blob reads unrelated ROM: both are errors here */
    if (c < 0) { s->err = 1; return; }
    int32_t ev = c & ~1;
    if (s->p + ev > s->end) { s->err = 2; return; }   /* 2: the cartridge would copy bytes past the blob */
    for (int32_t i = 0; i < ev; i++) put(s, s->p[i]);
    s->p += ev;
    if (c & 1) put(s, (uint8_t)getb(s, 8));
    s->rem = sub32(s->rem, c); s->sleft = sub32(s->sleft, c);
    if (s->sleft == 0) s->state = 0;
}

void hyinf_init(hyinf_stream *s, const uint8_t *in, size_t in_len, uint8_t *out, size_t out_cap)
{
    if (!fixed_ready) fixed_init();
    memset(s, 0, sizeof *s);
    s->size = in_len >= 4 ? hyinf_size(in) : 0;
    s->p = in + 4; s->end = in + in_len; s->start = in;
    s->out = out; s->cap = out_cap;
    s->left = s->size;
    if (in_len < 4) s->err = 1;
}

/* IWRAM 0x030005c8 Inflate::UncompressBlock(n). Returns the output offset where this request's
 * bytes start (the pointer the player hands to its blitter), or -1 on error. */
long hyinf_request(hyinf_stream *s, long n)
{
    int32_t k = sub32(s->left, s->rem);
    if ((int32_t)n < k) k = (int32_t)n;
    int go = k > 0;
    long ret = (long)s->o + s->rem;   /* where this request's bytes start */
    while (go && !s->err) {
        switch (s->state) {
        case 0: {
            unsigned t = getb(s, 2);
            s->blocks[t]++;
            s->state = t == 0 ? 5 : t == 1 ? 2 : t == 2 ? 1 : 7;
            break;
        }
        case 1:
            if (dyn_header(s) < 0) { if (!s->err) s->err = 1; break; }
            s->rem = 0; s->state = 3;
            /* fall through */
        case 3:
            k = add32(s->rem, k); s->rem = k; s->left = sub32(s->left, k);
            core(s, 0);
            k = s->rem; s->left = add32(s->left, k);
            if (s->state == 3) go = k > 0;
            break;
        case 2:
            s->rem = 0; s->state = 4;
            /* fall through */
        case 4:
            k = add32(s->rem, k); s->rem = k; s->left = sub32(s->left, k);
            core(s, 1);
            k = s->rem; s->left = add32(s->left, k);
            if (s->state == 4) go = k > 0;
            break;
        case 5:
            k = add32(s->rem, k); s->rem = k; s->left = sub32(s->left, k);
            stored(s);
            k = s->rem; s->left = add32(s->left, k);
            if (s->state == 5) go = k > 0;
            break;
        case 7:
            go = 0;            /* the cartridge asserts here that out == end and left == 0 */
            break;
        default:
            s->err = 1;
        }
    }
    return ret;            /* may be negative after the cartridge's own bookkeeping wraps; check s->err */
}

long hyinf_decode(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_cap, hyinf_stats *st)
{
    hyinf_stream s;
    hyinf_init(&s, in, in_len, out, out_cap);
    if (s.err || s.size > out_cap) return -1;
    /* one request for the whole size (the player decompresses a codebook this way) */
    hyinf_request(&s, (long)s.size);
    if (st) {
        memcpy(st->blocks, s.blocks, sizeof st->blocks);
        st->in_bytes = (size_t)(s.p - s.start);
    }
    if (s.err || s.o < s.size) return -1;
    return (long)s.size;
}

/* test helper (fuzz.py): requests of the given sizes; offs[i] = offset returned by request i.
 * Returns the number of bytes written, or -1 on error. */
long hyinf_run_chunks(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_cap,
                      const long *chunks, int n, long *offs)
{
    hyinf_stream s;
    hyinf_init(&s, in, in_len, out, out_cap);
    for (int i = 0; i < n; i++) {
        offs[i] = hyinf_request(&s, chunks[i]);
        if (s.err) return -s.err;
    }
    return (long)s.o;
}

/* after a whole blob has been produced: does the stream end the way the player expects
 * (end of the last Huffman block, then block type 3)? */
int hyinf_check_end(hyinf_stream *s)
{
    if (s->err || s->o != s->size) return 0;
    if (s->state == 3 || s->state == 4) {
        int c = huff_dec(s, s->state == 3 ? &s->lit : &FIXED_LIT);
        if (s->err || c != 256) return 0;
        s->state = 0;
    }
    if (s->state != 0) return s->state == 7;
    unsigned t = getb(s, 2);
    s->blocks[t]++;
    return t == 3;
}
