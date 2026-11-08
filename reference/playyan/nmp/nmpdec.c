/* Nintendo MP3 Player (TI TMS320VC5416) MP3 decoder, bit-exact with its DSP firmware.
 * C99 reference of gbav-playyan::nmp; structure and DSP routine addresses in docs/playyan/decoders.md.
 * Tables (nmp_tables.h) are generated from your ROM by gen_tables.py. */
#include <stdlib.h>
#include <string.h>
#include "nmpdec.h"
#include "nmp_tables.h"
#include "dct32.h"
#include "huff.h"

static int64_t sx(int64_t v, int b) { return (int64_t)((uint64_t)v << (64 - b)) >> (64 - b); }
static int32_t s32(int64_t v) { return (int32_t)(uint32_t)v; }
static int32_t sat32(int64_t v) { return v > INT32_MAX ? INT32_MAX : v < INT32_MIN ? INT32_MIN : (int32_t)v; }
static int64_t shl40(int64_t a, int64_t t)
{
    t = sx(t, 6);
    return t >= 0 ? sx((int64_t)((uint64_t)a << t), 40) : sx(a, 40) >> -t;
}
static int64_t exp40(int64_t a)
{
    a = sx(a, 40);
    if (!a) return 0;
    int64_t s = (a >> 39) & 1, n = 0;
    for (int bit = 38; bit >= 0; bit--) { if (((a >> bit) & 1) == s) n++; else break; }
    return n - 8;
}
static void hl(int32_t x, int64_t *h, int64_t *l) { *h = x >> 16; *l = (uint32_t)x & 0xFFFF; }
static void mul_parts(int32_t x, int32_t y, int64_t *hh, int64_t *cr)
{
    int64_t xh, xl, yh, yl; hl(x, &xh, &xl); hl(y, &yh, &yl);
    *hh = xh * yh; *cr = xl * yh + yl * xh;
}
static int32_t d32(const uint16_t *t, int i) { return (int32_t)((uint32_t)t[2 * i] << 16 | t[2 * i + 1]); }

/* data memory reads of the requantiser region (0xB182..0xB4BB) */
static int64_t W(int64_t a)
{
    a &= 0xFFFF;
    if (a < NMP_RQ_BASE || a >= NMP_RQ_BASE + 826) abort();
    return nmp_rq[a - NMP_RQ_BASE];
}

/* 2:810E: one big-values line */
static int32_t requant(int32_t v, int64_t s)
{
    int neg = v < 0;
    int64_t av = v < 0 ? -(int64_t)v : v, e, mh, ml;
    if (av < 128) {
        e = sx(W(0xB286 + av), 16); mh = sx(W(0xB306 + 2 * av), 16); ml = W(0xB307 + 2 * av);
    } else {
        int64_t a = av << 16, t = exp40(a);
        a = shl40(a, t);
        int64_t x40 = a, x32 = sx(a, 32), tm = sx(a >> 16, 16);
        e = 15 - t;
        a = sx(W(0xB282), 16) * 65536;
        int64_t b = sx(W(0xB283), 16) * 65536;
        for (int64_t c = 0xB284; c <= 0xB286; c++) {
            a = sx(sx(sx(sx(a >> 16, 17) * tm * 2 + b, 40) + 0x8000, 40) & ~(int64_t)0xFFFF, 40);
            b = sx(W(c), 16) * 65536;
        }
        int64_t p = sx(a, 32);
        int64_t y0 = W((sx(sx(a >> 16, 16) >> 7, 16) + 0xB182) & 0xFFFF) & 0xFFFF;
        for (int it = 0; it < 3; it++) {
            int64_t ph = sx(p >> 16, 16), pl = p & 0xFFFF;
            int64_t q = 2 * pl * ph + 2 * pl * ph;
            q = (q >> 16) + 2 * ph * ph;
            q = sx(q, 32);
            int64_t qh = sx(q >> 16, 16), ql = q & 0xFFFF;
            int64_t r = 2 * ql * ph + 2 * pl * qh;
            r = (r >> 16) + 2 * ph * qh;
            r = sx(-sx(r - x32, 40), 40);
            int64_t d3 = sx(r >> 16, 16), d4 = r & 0xFFFF;
            r = d4 * y0;
            r = (r >> 16) + y0 * d3;
            p = sx(sx(r + p, 40), 32);
        }
        int64_t ph = sx(p >> 16, 16), pl = p & 0xFFFF, t2 = sx(x40 >> 16, 16);
        int64_t r = (t2 & 0xFFFF) * pl * 2;
        r = (r >> 16) + t2 * ph * 2;
        r = sx(r, 32);
        mh = sx(r >> 16, 16); ml = r & 0xFFFF;
    }
    int64_t shift = (sx(s, 16) >> 2) + sx(W(0xB406 + e), 16);
    int64_t g = 3 * (4 - (s & 3)) + sx(W(0xB416 + e), 16);
    int64_t ga = (2 * g + 0xB42F) & 0xFFFF;
    int64_t glo = W(ga), ghi = sx(W(ga - 1), 16);
    int64_t a = 2 * glo * mh + 2 * ml * ghi;
    a = (a >> 16) + 2 * ghi * mh;
    if (neg) a = sx(-a, 40);
    int64_t d = shift - 3;
    if (d + 16 >= 0) a = shl40(a, d);
    else { a = shl40(a >> 16, d + 16); if (d + 32 < 0) a >>= 16; }
    return s32(a);
}

/* 2:99C6: a count1 line */
static int32_t count1(int32_t v, int64_t s)
{
    int64_t k = 2 * (s & 3);
    int64_t t1 = sx(W(0xB426 + k) << 16 | W(0xB427 + k), 32);
    int64_t d = (sx(s, 16) >> 2) - 3, a = v < 0 ? -t1 : t1;
    if (d + 16 >= 0) a = shl40(a, d);
    else if (d + 32 >= 0) a = shl40(a >> 16, d + 16);
    else a >>= 32;
    return s32(a);
}

static int32_t ms_mul(int32_t x)
{
    const int32_t C = 0x5A82799A;
    int64_t xh, xl, ch, cl; hl(x, &xh, &xl); hl(C, &ch, &cl);
    return s32(((2 * cl * xh + 2 * xl * ch) >> 16) + 2 * xh * ch);
}

/* ------------------------------------------------------------------------------- bitstream */
static const unsigned BITRATE[15] = {0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320};
static const unsigned BITRATE_LSF[15] = {0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160};
static const unsigned RATES[9] = {44100, 48000, 32000, 22050, 24000, 16000, 11025, 12000, 8000};
static const uint16_t SFB_LONG[9][23] = {
    {0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 52, 62, 74, 90, 110, 134, 162, 196, 238, 288, 342, 418, 576},
    {0, 4, 8, 12, 16, 20, 24, 30, 36, 42, 50, 60, 72, 88, 106, 128, 156, 190, 230, 276, 330, 384, 576},
    {0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 54, 66, 82, 102, 126, 156, 194, 240, 296, 364, 448, 550, 576},
    {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576},
    {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 114, 136, 162, 194, 232, 278, 332, 394, 464, 540, 576},
    {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576},
    {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576},
    {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464, 522, 576},
    {0, 12, 24, 36, 48, 60, 72, 88, 108, 132, 160, 192, 232, 280, 336, 400, 476, 566, 568, 570, 572, 574, 576}};
static const uint16_t SFB_SHORT[9][14] = {
    {0, 4, 8, 12, 16, 22, 30, 40, 52, 66, 84, 106, 136, 192},
    {0, 4, 8, 12, 16, 22, 28, 38, 50, 64, 80, 100, 126, 192},
    {0, 4, 8, 12, 16, 22, 30, 42, 58, 78, 104, 138, 180, 192},
    {0, 4, 8, 12, 18, 24, 32, 42, 56, 74, 100, 132, 174, 192},
    {0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 136, 180, 192},
    {0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192},
    {0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192},
    {0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192},
    {0, 8, 16, 24, 36, 52, 72, 96, 124, 160, 162, 164, 166, 192}};
static const uint8_t LSF_NSF[6][3][4] = {   /* long, short, mixed */
    {{6, 5, 5, 5}, {9, 9, 9, 9}, {6, 9, 9, 9}}, {{6, 5, 7, 3}, {9, 9, 12, 6}, {6, 9, 12, 6}},
    {{11, 10, 0, 0}, {18, 18, 0, 0}, {15, 18, 0, 0}}, {{7, 7, 7, 0}, {12, 12, 12, 0}, {6, 15, 12, 0}},
    {{6, 6, 6, 3}, {12, 9, 9, 6}, {6, 12, 9, 6}}, {{8, 8, 5, 0}, {15, 12, 9, 0}, {6, 18, 9, 0}}};
static const uint8_t SLEN[16][2] = {{0,0},{0,1},{0,2},{0,3},{3,0},{1,1},{1,2},{1,3},{2,1},{2,2},{2,3},{3,1},{3,2},{3,3},{4,2},{4,3}};
static const int PRETAB[22] = {0,0,0,0,0,0,0,0,0,0,0,1,1,1,1,2,2,3,3,3,2,0};

typedef struct { const uint8_t *d; size_t n, p; } Bits;
static unsigned get(Bits *b, int n)
{
    unsigned v = 0;
    while (n--) {
        unsigned byte = (b->p >> 3) < b->n ? b->d[b->p >> 3] : 0;
        v = v << 1 | ((byte >> (7 - (b->p & 7))) & 1); b->p++;
    }
    return v;
}

typedef struct {
    unsigned part2_3_length, big_values, scalefac_compress, table_select[3], region0_count, region1_count;
    int global_gain, window_switching, block_type, mixed, subblock_gain[3], preflag, scalefac_scale, count1table;
} GC;

static int tree_walk(Bits *b, int off, int tlen, int *leaf)
{
    int point = 0, left = 32;
    for (;;) {
        unsigned e = nmp_huff_tree[off + point];
        if (!(e & 0xff00)) { *leaf = e; return 1; }
        if (get(b, 1)) { while ((nmp_huff_tree[off + point] & 0xff) >= 250) point += nmp_huff_tree[off + point] & 0xff;
                         point += nmp_huff_tree[off + point] & 0xff; }
        else { while ((nmp_huff_tree[off + point] >> 8) >= 250) point += nmp_huff_tree[off + point] >> 8;
               point += nmp_huff_tree[off + point] >> 8; }
        if (tlen >= 0 && (--left == 0 || point >= tlen)) { *leaf = 0; return 0; }
    }
}
static void huff_pair(Bits *b, int tab, int *x, int *y)
{
    int off = nmp_huff_main[tab].off, lin = nmp_huff_main[tab].linbits, leaf;
    if (off < 0) { *x = *y = 0; return; }
    tree_walk(b, off, nmp_huff_main[tab].len, &leaf);
    *x = (leaf >> 4) & 15; *y = leaf & 15;
    if (lin && *x == 15) *x += get(b, lin);
    if (*x && get(b, 1)) *x = -*x;
    if (lin && *y == 15) *y += get(b, lin);
    if (*y && get(b, 1)) *y = -*y;
}
static void count1_quad(Bits *b, int table_b, int q[4])
{
    int v;
    if (table_b) v = 15 - (int)get(b, 4);
    else { tree_walk(b, 2742, -1, &v); v &= 15; }
    for (int j = 0; j < 4; j++) { int k = 3 - j; q[j] = (v >> k) & 1; if (q[j] && get(b, 1)) q[j] = -q[j]; }
}


/* ------------------------------------------------------------------------------- stereo */
typedef struct {
    int64_t sfl[22], sfs[13][3], is_maxl[22], is_maxs[13][3]; int iscale;
} IsInfo;

/* band ranges in xr order: br[j] = {band, window (3 = long), lo, hi} -> here {window, lo, hi, band} */
static int bands_gc(int shortb, int mixed, int ri, int br[40][4])
{
    int n = 0;
    if (shortb) {
        const uint16_t *sfb = SFB_SHORT[ri]; int i = 0, b0 = 0;
        if (mixed) {   /* the firmware's list: long bands to line 36 (72 at 8 kHz), then short bands */
            int L = ri == 8 ? 72 : 36;
            for (int b = 0; SFB_LONG[ri][b] < L; b++) { br[n][0] = 3; br[n][1] = SFB_LONG[ri][b]; br[n][2] = SFB_LONG[ri][b + 1]; br[n][3] = b; n++; }
            i = L; while (sfb[b0] * 3 < L) b0++;
        }
        for (int b = b0; b < 13; b++)
            for (int w = 0; w < 3; w++) { int ww = sfb[b + 1] - sfb[b]; br[n][0] = w; br[n][1] = i; br[n][2] = i + ww; br[n][3] = b; n++; i += ww; }
    } else
        for (int b = 0; b < 22; b++) { br[n][0] = 3; br[n][1] = SFB_LONG[ri][b]; br[n][2] = SFB_LONG[ri][b + 1]; br[n][3] = b; n++; }
    return n;
}

static int32_t fm(int32_t x, int32_t k)
{
    int64_t xh, xl, kh, kl; hl(x, &xh, &xl); hl(k, &kh, &kl);
    return s32(2 * xh * kh + ((2 * (xl * kh + kl * xh)) >> 16));
}

/* 2:A214 positions, 2:A404 M/S, 2:A441 -> 2:9AD1 (MPEG-1) / 2:9AF8 (MPEG-2) */
static void intensity_x(int32_t *l, int32_t *r, const IsInfo *ri_, int shortb, int mixed, int ri, int lsf, int ms)
{
    int br[40][4], n = bands_gc(shortb, mixed, ri, br), is_band[40] = {0};
    for (int w = 0; w < 4; w++) {
        int last = -1;
        for (int j = 0; j < n; j++) if (br[j][0] == w) for (int q = br[j][1]; q < br[j][2]; q++) if (r[q]) { last = j; break; }
        for (int j = 0; j < n; j++) if (br[j][0] == w && j > last) is_band[j] = 1;
    }
    const uint16_t *T = nmp_rq;   /* region 0xB182.. */
    for (int j = 0; j < n; j++) {
        int w = br[j][0], b = br[j][3], lo = br[j][1], hi = br[j][2];
        int64_t pos = -1;
        if (is_band[j]) {
            int64_t p, mx;
            if (w == 3) { p = b < 21 ? ri_->sfl[b] : 0; mx = lsf ? ri_->is_maxl[b] : 7; }
            else { p = b < 12 ? ri_->sfs[b][w] : 0; mx = lsf ? ri_->is_maxs[b][w] : 7; }
            if (p != mx) pos = p;
        }
        if (pos < 0) {
            if (ms) for (int q = lo; q < hi; q++) {
                int32_t m = l[q], sd = r[q];
                l[q] = ms_mul((int32_t)((uint32_t)m + (uint32_t)sd)); r[q] = ms_mul((int32_t)((uint32_t)m - (uint32_t)sd));
            }
            continue;
        }
        for (int q = lo; q < hi; q++) {
            int32_t x = l[q];
            if (!lsf) { l[q] = fm(x, d32(T + (0xB46E - NMP_RQ_BASE), (int)pos)); r[q] = fm(x, d32(T + (0xB460 - NMP_RQ_BASE), (int)pos)); }
            else {
                const uint16_t *tk = T + (0xB47C - NMP_RQ_BASE) + 32 * ri_->iscale;
                if (pos & 1) { l[q] = fm(x, d32(tk, (int)((pos + 1) >> 1))); r[q] = x; }
                else r[q] = fm(x, d32(tk, (int)(pos >> 1)));
            }
        }
    }
}

/* ------------------------------------------------------------------------------- hybrid */
typedef struct { int32_t ov[32][9]; uint16_t ring[1024]; int64_t prev_sf[22]; } Chan;
/* lsf_area (0x8061): the MPEG-2 / 2.5 scalefactor areas, 62 words per channel (long[i] at i, short
 * band b window w at 23 + b + 13w), kept across granules. Word 124 (0x80DD) is the frame's
 * main_data_begin; channel 1's mixed reader always overwrites it (band 13, window 2) before the
 * 8 kHz builder reads it, and its writes beyond (channel 0's side info) are dead by then. */
struct NmpDec { Chan ch[2]; uint8_t res[8192]; size_t nres; int64_t lsf_area[125]; };

/* 2:9143.. the MPEG-2 / 2.5 mixed-block scalefactor reader as the firmware runs it (see the Rust
 * lsf_mixed): long 0..5 and (nr0 - 6) / 3 short bands from band 6 at slen0; groups 1..3 at slen[k]
 * until the first slen[k] = 0, which zeroes (nr1 + nr2 + nr3) / 3 bands from the current band and
 * stops reading (the writes run past band 12); then band 12 is zeroed. The builder takes long 0..5
 * from the area and short band b window w from area[23 + b + 13w] (b + 1 at 8 kHz, where the
 * return value says so and pseudo[] gets the 12-wide band's area band 3). */
static int lsf_mixed(Bits *m, const int slen[4], const uint8_t nr[4], int64_t *area, int c, int k8,
                     int64_t sfl[22], int64_t sfs[13][3], int64_t pseudo[3])
{
    int base = 62 * c, band = 6;
#define PUT(i, v) do { int64_t v_ = (v); if (base + (i) < 125) area[base + (i)] = v_; } while (0)
    for (int j = 0; j < 6; j++) PUT(j, slen[0] ? get(m, slen[0]) : 0);
    for (int q = 0; q < (nr[0] > 6 ? (nr[0] - 6) / 3 : 0); q++, band++)
        for (int w = 0; w < 3; w++) PUT(23 + band + 13 * w, slen[0] ? get(m, slen[0]) : 0);
    for (int k = 1; k < 4; k++) {
        if (!slen[k]) {
            for (int q = 0; q < nr[1] / 3 + nr[2] / 3 + nr[3] / 3; q++, band++)
                for (int w = 0; w < 3; w++) PUT(23 + band + 13 * w, 0);
            break;
        }
        for (int q = 0; q < nr[k] / 3; q++, band++)
            for (int w = 0; w < 3; w++) PUT(23 + band + 13 * w, get(m, slen[k]));
    }
    for (int w = 0; w < 3; w++) PUT(35 + 13 * w, 0);
#undef PUT
#define AT(i) (base + (i) < 125 ? area[base + (i)] : 0)
    for (int j = 0; j < 6; j++) sfl[j] = area[base + j];
    for (int b = k8 ? 0 : 3; b < 13; b++)
        for (int w = 0; w < 3; w++) sfs[b][w] = AT(23 + b + k8 + 13 * w);
    if (k8) for (int w = 0; w < 3; w++) pseudo[w] = AT(26 + 13 * w);
#undef AT
    return k8;
}

static void imdct_matrix(const int32_t *x, const uint16_t *T, int n, int32_t *y)
{
    for (int k = 0; k < n; k++) {
        int64_t b = 0, a = 0;
        for (int i = 0; i < n; i++) {
            int64_t th, tl, xh, xl; hl(d32(T, k * n + i), &th, &tl); hl(x[i], &xh, &xl);
            b += th * xh; a += tl * xh + xl * th;
        }
        b += a >> 16;
        y[k] = s32(b * 2);
    }
}
static void win_rot(const int32_t *y5, const int32_t *y4, const uint16_t *w, int n, int32_t *fw, int32_t *bw)
{
    for (int j = 0; j < n; j++) {
        int32_t w1 = d32(w, 2 * j), w2 = d32(w, 2 * j + 1);
        int64_t h1, c1, h2, c2;
        mul_parts(y5[j], w1, &h1, &c1); mul_parts(y4[j], w2, &h2, &c2);
        bw[j] = sat32((h1 + h2 + ((c1 + c2) >> 16)) * 2);
        mul_parts(y4[j], w1, &h1, &c1); mul_parts(y5[j], w2, &h2, &c2);
        fw[j] = sat32((-h1 + h2 + ((-c1 + c2) >> 16)) * 2);
    }
}
static void imdct_sb(const int32_t x[18], int32_t ov[9], int bt, int32_t out[18])
{
    int32_t c[18], old[9], fw[9], bw[9], y5[9], y4[9];
    if (bt != 2) imdct_matrix(x, nmp_imdct18, 18, c);
    else for (int w = 0; w < 3; w++) imdct_matrix(x + 6 * w, nmp_imdct6, 6, c + 6 * w);
    for (int i = 0; i < 18; i++) c[i] = (int32_t)(0u - (uint32_t)c[i]);
    memcpy(old, ov, sizeof old);
    if (bt == 0 || bt == 1) {
        for (int r = 0; r < 9; r++) { ov[8 - r] = c[r]; c[r] = old[8 - r]; }
        for (int j = 0; j < 9; j++) { y5[j] = c[8 - j]; y4[j] = c[9 + j]; }
        win_rot(y5, y4, nmp_win_l, 9, fw, bw);
        for (int j = 0; j < 9; j++) { out[j] = fw[j]; out[17 - j] = bw[j]; }
    } else if (bt == 2) {
        int32_t c0[18]; memcpy(c0, c, sizeof c0);
        for (int i = 0; i < 3; i++) { c[i] = old[8 - i]; c[6 + i] = c0[i]; c[12 + i] = c0[6 + i]; ov[8 - i] = c0[12 + i]; }
        for (int i = 0; i < 6; i++) out[i] = old[i];
        for (int w = 0; w < 3; w++) {
            int b = 6 * w; int32_t s[6];
            for (int j = 0; j < 3; j++) { y5[j] = c[b + 2 - j]; y4[j] = c[b + 3 + j]; }
            win_rot(y5, y4, nmp_win_s, 3, fw, bw);
            for (int j = 0; j < 3; j++) { s[j] = fw[j]; s[5 - j] = bw[j]; }
            if (w < 2) memcpy(out + 6 + 6 * w, s, sizeof s); else memcpy(ov, s, sizeof s);
        }
    } else {
        int32_t t;
        t = c[12]; c[12] = c[14]; c[14] = t;
        for (int i = 0; i < 3; i++) { t = c[9 + i]; c[9 + i] = c[17 - i]; c[17 - i] = t; }
        t = c[9]; c[9] = c[11]; c[11] = t;
        for (int j = 0; j < 9; j++) ov[j] = c[8 - j];
        for (int i = 0; i < 6; i++) c[i] = old[i];
        c[6] = old[8]; c[7] = old[7]; c[8] = old[6];
        for (int j = 0; j < 3; j++) { y5[j] = c[8 - j]; y4[j] = c[9 + j]; }
        win_rot(y5, y4, nmp_win_s, 3, fw, bw);
        for (int i = 0; i < 6; i++) out[i] = c[i];
        for (int j = 0; j < 3; j++) { out[6 + j] = fw[j]; out[11 - j] = bw[j]; }
        for (int i = 12; i < 18; i++) out[i] = c[i];
    }
}

/* 2:81B0 */
static int16_t syn_store(int64_t a)
{
    int64_t v = a * 64;
    if (v > INT32_MAX) v = INT32_MAX;
    if (v < INT32_MIN) v = INT32_MIN;
    return (int16_t)(v >> 16);
}
#define CIRC(p, d) ((unsigned)((int)(p) + (d)) & 0x3FF)
static void tap(int64_t *A, int64_t *B, const uint16_t *win, unsigned w, const uint16_t *ring, unsigned r, int mas)
{
    int64_t xh = (int16_t)win[w], xl = win[w + 1], yh = (int16_t)ring[r], yl = ring[r + 1];
    if (mas) *A -= xh * yh; else *A += xh * yh;
    *B += yl * xh + xl * yh;
}
static void synth(const uint16_t *words, uint16_t *ring, int16_t *out)
{
    const uint16_t *win = nmp_synth;
    unsigned p8 = 0; int o = 0;
    for (int slot = 0; slot < 18; slot++) {
        p8 = CIRC(p8, -0x40);
        for (int k = 0; k < 64; k++) ring[CIRC(p8, k)] = words[64 * slot + k];
        unsigned w2 = 0, w4 = 0x200, r3 = CIRC(p8, 0x20), r5 = CIRC(p8, 0x60);
        for (int it = 0; it < 16; it++) {
            int64_t A = 0, B = 0;
            for (int t = 0; t < 8; t++) { tap(&A, &B, win, w2, ring, r3, 0); w2 += 2; r3 = CIRC(r3, 128); }
            B = -B;
            for (int t = 0; t < 8; t++) { tap(&A, &B, win, w4, ring, r5, 1); w4 += 2; r5 = CIRC(r5, 128); }
            out[o++] = syn_store(A - (B >> 16) + 512);
            r3 = CIRC(r3, -0x3fe); r5 = CIRC(r5, -0x402);
        }
        {
            int64_t A = 0, B = 0;
            for (int t = 0; t < 8; t++) { tap(&A, &B, win, w4, ring, r5, 1); w4 += 2; r5 = CIRC(r5, 128); }
            out[o++] = syn_store(A - (B >> 16) + 512);
        }
        w2 += 0x10; r3 = CIRC(r3, -2); r5 = CIRC(r5, -0x3fe);
        for (int it = 0; it < 15; it++) {
            int64_t A = 0, B = 0;
            for (int t = 0; t < 8; t++) {
                tap(&A, &B, win, w2, ring, r3, 1); w2 += 2; r3 = CIRC(r3, 128);
                tap(&A, &B, win, w4, ring, r5, 1); w4 += 2; r5 = CIRC(r5, 128);
            }
            out[o++] = syn_store(A - (B >> 16) + 512);
            r3 = CIRC(r3, -0x402); r5 = CIRC(r5, -0x3fe);
        }
    }
    uint16_t old[1024]; memcpy(old, ring, sizeof old);
    for (int i = 0; i < 1024; i++) ring[(i + 0x80) & 0x3FF] = old[i];
}

static void backend(Chan *ch, const int32_t *xr, int bt, int mixed, int ri, int16_t *pcm)
{
    static int32_t buf[576];
    int long_lines = bt != 2 ? 576 : mixed ? 36 : 0, nb = bt != 2 ? 31 : mixed ? 1 : 0;
    for (int i = 0; i < long_lines; i++) buf[(i % 18) * 32 + i / 18] = xr[i];
    if (bt == 2) {
        const uint16_t *sfb = SFB_SHORT[ri]; int i = long_lines;
        if (mixed && ri == 8)   /* 8 kHz: lines 36..71 are one 12-wide short band at f = 12..23 */
            for (int win = 0; win < 3; win++)
                for (int f = 12; f < 24; f++) buf[(win * 6 + f % 6) * 32 + f / 6] = xr[i++];
        for (int band = mixed ? 3 : 0; band < 13; band++)
            for (int win = 0; win < 3; win++)
                for (int k = 0; k < sfb[band + 1] - sfb[band]; k++) {
                    int f = sfb[band] + k;
                    buf[(win * 6 + f % 6) * 32 + f / 6] = xr[i++];
                }
    }
    {
        for (int sb = 1; sb < 1 + nb; sb++)
            for (int i = 0; i < 8; i++) {
                int32_t ca = d32(nmp_aa, 2 * i), cs = d32(nmp_aa, 2 * i + 1);
                int32_t up = buf[(17 - i) * 32 + sb - 1], dn = buf[i * 32 + sb];
                int64_t h1, c1, h2, c2;
                mul_parts(up, ca, &h1, &c1); mul_parts(dn, cs, &h2, &c2);
                int32_t nd = s32((h1 + h2 + ((c1 + c2) >> 16)) * 2);
                mul_parts(dn, ca, &h1, &c1); mul_parts(up, cs, &h2, &c2);
                int32_t nu = s32((-h1 + h2 + ((-c1 + c2) >> 16)) * 2);
                buf[i * 32 + sb] = nd; buf[(17 - i) * 32 + sb - 1] = nu;
            }
    }
    for (int sb = 0; sb < 32; sb++) {
        int32_t x[18], out[18];
        for (int r = 0; r < 18; r++) x[r] = buf[r * 32 + sb];
        imdct_sb(x, ch->ov[sb], mixed && sb < 2 ? 0 : bt, out);   /* a mixed block's long subbands */
        for (int r = 0; r < 18; r++) buf[r * 32 + sb] = out[r];
    }
    for (int r = 1; r < 18; r += 2)
        for (int sb = 1; sb < 32; sb += 2) buf[r * 32 + sb] = (int32_t)(0u - (uint32_t)buf[r * 32 + sb]);
    static uint16_t words[18 * 64];
    static int32_t dctk[102]; static int dctk_ok;
    if (!dctk_ok) { for (int i = 0; i < 102; i++) dctk[i] = d32(nmp_dct, i); dctk_ok = 1; }
    for (int r = 0; r < 18; r++) {
        nmp_dct32(buf + r * 32, dctk);
        for (int k = 0; k < 32; k++) { words[r * 64 + 2 * k] = (uint32_t)buf[r * 32 + k] >> 16; words[r * 64 + 2 * k + 1] = (uint16_t)buf[r * 32 + k]; }
    }
    synth(words, ch->ring, pcm);
}

/* ------------------------------------------------------------------------------- frames */
NmpDec *nmp_new(void) { return calloc(1, sizeof(NmpDec)); }
void nmp_free(NmpDec *d) { free(d); }

size_t nmp_frame_size(const uint8_t *p, size_t n)
{
    if (n < 4) return 0;
    uint32_t h = (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3];
    unsigned ver = (h >> 19) & 3;
    if (((h >> 21) & 0x7FF) != 0x7FF || ver == 1 || ((h >> 17) & 3) != 1) return 0;
    unsigned bri = (h >> 12) & 15, sri = (h >> 10) & 3;
    if (bri == 0 || bri == 15 || sri == 3) return 0;
    unsigned ri = sri + (ver == 3 ? 0 : ver == 2 ? 3 : 6);
    return (ver == 3 ? 144000 * BITRATE[bri] : 72000 * BITRATE_LSF[bri]) / RATES[ri] + ((h >> 9) & 1);
}

int nmp_decode_frame(NmpDec *d, const uint8_t *fr, size_t n, int16_t calls[4][576], int16_t pcm[2304])
{
    size_t size = nmp_frame_size(fr, n);
    if (!size || size > n) return -1;
    uint32_t h = (uint32_t)fr[0] << 24 | fr[1] << 16 | fr[2] << 8 | fr[3];
    int ver = (h >> 19) & 3, lsf = ver != 3;
    int ri = ((h >> 10) & 3) + (ver == 3 ? 0 : ver == 2 ? 3 : 6), mode = (h >> 6) & 3, ext = (h >> 4) & 3, crc = !((h >> 16) & 1);
    int nch = mode == 3 ? 1 : 2, ngr = lsf ? 1 : 2;
    Bits b = {fr, size, 32 + (crc ? 16 : 0)};
    int mdb = get(&b, lsf ? 8 : 9);
    get(&b, lsf ? (nch == 1 ? 1 : 2) : (nch == 1 ? 5 : 3));
    int scfsi[2][4] = {{0}};
    if (!lsf) for (int c = 0; c < nch; c++) for (int k = 0; k < 4; k++) scfsi[c][k] = get(&b, 1);
    GC gcs[2][2];
    memset(gcs, 0, sizeof gcs);
    for (int g = 0; g < ngr; g++)
        for (int c = 0; c < nch; c++) {
            GC *gc = &gcs[g][c];
            gc->part2_3_length = get(&b, 12); gc->big_values = get(&b, 9); gc->global_gain = get(&b, 8);
            gc->scalefac_compress = get(&b, lsf ? 9 : 4); gc->window_switching = get(&b, 1);
            if (gc->window_switching) {
                gc->block_type = get(&b, 2); gc->mixed = get(&b, 1);
                gc->table_select[0] = get(&b, 5); gc->table_select[1] = get(&b, 5);
                for (int k = 0; k < 3; k++) gc->subblock_gain[k] = get(&b, 3);
                gc->region0_count = (gc->block_type == 2 && !gc->mixed) ? 8 : 7; gc->region1_count = 36;
            } else {
                for (int k = 0; k < 3; k++) gc->table_select[k] = get(&b, 5);
                gc->region0_count = get(&b, 4); gc->region1_count = get(&b, 3);
            }
            gc->preflag = lsf ? 0 : get(&b, 1); gc->scalefac_scale = get(&b, 1); gc->count1table = get(&b, 1);
        }
    /* MPEG-2/2.5 mixed block on the intensity-coded channel: not modelled */
    if (lsf && mode == 1 && (ext & 1) && gcs[0][1].window_switching && gcs[0][1].block_type == 2 && gcs[0][1].mixed)
        return -2;
    size_t hdr = b.p / 8;
    long start = (long)d->nres - mdb;
    if (d->nres + (size - hdr) > sizeof d->res) {   /* keep the last 4096 bytes */
        size_t keep = d->nres > 4096 ? 4096 : d->nres;
        memmove(d->res, d->res + d->nres - keep, keep); start -= (long)(d->nres - keep); d->nres = keep;
    }
    memcpy(d->res + d->nres, fr + hdr, size - hdr); d->nres += size - hdr;
    if (start < 0) return 0;
    Bits m = {d->res, d->nres, (size_t)start * 8};
    int ncalls = 0;
    for (int g = 0; g < ngr; g++) {
        static int32_t xs[2][576];
        IsInfo isr; memset(&isr, 0, sizeof isr); int isr_short = 0, isr_mixed = 0;
        for (int c = 0; c < nch; c++) {
            GC *gc = &gcs[g][c];
            size_t p0 = m.p;
            int is_right = c == 1 && mode == 1 && (ext & 1);
            int64_t is_maxl[22], is_maxs[13][3]; int iscale = 0;
            for (int q = 0; q < 22; q++) is_maxl[q] = -1;
            for (int q = 0; q < 13; q++) is_maxs[q][0] = is_maxs[q][1] = is_maxs[q][2] = -1;
            int sl1 = SLEN[gc->scalefac_compress & 15][0], sl2 = SLEN[gc->scalefac_compress & 15][1];
            int64_t sfl[22] = {0}, sfs[13][3] = {{0}}, pseudo[3]; int k8 = 0;
            int shortb = gc->window_switching && gc->block_type == 2;
            if (lsf) {   /* ISO 13818-3 2.4.3.4; the intensity-coded right channel uses the firmware's
                          * own slen formulas for isfc >= 180 (no -180/-244, % 63 / % 15 / % 3) */
                int sf = gc->scalefac_compress, slen[4], t2, j = 0;
                if (is_right) {
                    int i = sf >> 1; iscale = sf & 1;
                    if (i < 180) { slen[0] = i / 36; slen[1] = (i % 36) / 6; slen[2] = (i % 36) % 6; slen[3] = 0; t2 = 3; }
                    else if (i < 244) { slen[0] = (i % 63) >> 4; slen[1] = (i % 15) >> 2; slen[2] = i % 3; slen[3] = 0; t2 = 4; }
                    else { slen[0] = i / 3; slen[1] = i % 3; slen[2] = slen[3] = 0; t2 = 5; }
                } else if (sf < 400) { slen[0] = (sf >> 4) / 5; slen[1] = (sf >> 4) % 5; slen[2] = (sf % 16) >> 2; slen[3] = sf % 4; t2 = 0; }
                else if (sf < 500) { sf -= 400; slen[0] = (sf >> 2) / 5; slen[1] = (sf >> 2) % 5; slen[2] = sf % 4; slen[3] = 0; t2 = 1; }
                else { sf -= 500; slen[0] = sf / 3; slen[1] = sf % 3; slen[2] = slen[3] = 0; t2 = 2; }
                gc->preflag = t2 == 2;
                if (shortb && gc->mixed) k8 = lsf_mixed(&m, slen, LSF_NSF[t2][2], d->lsf_area, c, ri == 8, sfl, sfs, pseudo);
                for (int k = 0; k < (shortb && gc->mixed ? 0 : 4); k++) {
                    int64_t mx = slen[k] ? ((int64_t)1 << slen[k]) - 1 : -1;
                    for (int q = 0; q < LSF_NSF[t2][shortb][k]; q++, j++) {
                        int64_t v = get(&m, slen[k]);
                        if (shortb) { if (j / 3 < 13) { sfs[j / 3][j % 3] = v; is_maxs[j / 3][j % 3] = mx; } }
                        else if (j < 22) { sfl[j] = v; is_maxl[j] = mx; }
                    }
                }
                /* the plain long / short readers store into the same area */
                int64_t *a = d->lsf_area + 62 * c;
                if (shortb && !gc->mixed)
                    for (int w = 0; w < 3; w++) { for (int q = 0; q < 12; q++) a[23 + q + 13 * w] = sfs[q][w]; a[35 + 13 * w] = 0; }
                else if (!shortb)
                    for (int q = 0; q < j && q < 22; q++) a[q] = sfl[q];
            } else if (shortb && gc->mixed) {
                for (int s = 0; s < 8; s++) sfl[s] = get(&m, sl1);
                for (int s = 3; s < 12; s++) for (int w = 0; w < 3; w++) sfs[s][w] = get(&m, s < 6 ? sl1 : sl2);
            } else if (shortb) {
                for (int s = 0; s < 12; s++) for (int w = 0; w < 3; w++) sfs[s][w] = get(&m, s < 6 ? sl1 : sl2);
            } else {
                static const int lo[4] = {0, 6, 11, 16}, hi[4] = {6, 11, 16, 21};
                for (int k = 0; k < 4; k++)
                    for (int s = lo[k]; s < hi[k]; s++)
                        sfl[s] = (g == 1 && scfsi[c][k]) ? d->ch[c].prev_sf[s] : get(&m, k < 2 ? sl1 : sl2);
                memcpy(d->ch[c].prev_sf, sfl, sizeof sfl);
            }
            size_t end = p0 + gc->part2_3_length;
            int isv[576] = {0};
            int bv = 2 * gc->big_values > 576 ? 576 : 2 * gc->big_values, r1, r2;
            if (gc->window_switching) {
                r1 = gc->block_type == 2 && gc->mixed && lsf ? (ri == 8 ? 96 : 48)
                   : gc->block_type == 2 ? (ri == 8 ? 72 : 36) : lsf ? (ri == 8 ? 108 : 54) : 36; r2 = 576;
            }
            else {
                int e2 = gc->region0_count + gc->region1_count + 2; if (e2 > 22) e2 = 22;
                r1 = SFB_LONG[ri][gc->region0_count + 1]; r2 = SFB_LONG[ri][e2];
            }
            int i = 0;
            while (i < bv) { int tab = gc->table_select[i < r1 ? 0 : i < r2 ? 1 : 2]; huff_pair(&m, tab, &isv[i], &isv[i + 1]); i += 2; }
            /* a quad starting at line 574 is decoded and its first two values kept */
            while (i < 576 && m.p < end) {
                int q[4]; count1_quad(&m, gc->count1table, q);
                for (int k = 0; k < 4; k++) if (i + k < 576) isv[i + k] = q[k];
                i += 4;
            }
            if (m.p > end) { i -= 4; for (int k = 0; k < 4 && i + k < 576; k++) isv[i + k] = 0; }
            int nz = i < 576 ? i : 576;
            m.p = end;
            int64_t s[576], mult = 2 * (1 + gc->scalefac_scale), gg = gc->global_gain - 210;
            if (shortb) {
                const uint16_t *sfb = SFB_SHORT[ri]; int k = 0, b0 = 0;
                if (k8) {   /* 8 kHz MPEG-2.5 mixed: long 0..2, lines 36..71 as a 12-wide short band,
                             * short bands 3..12 (scalefactors from area band b + 1) */
                    for (int band = 0; band < 3; band++)
                        for (int q = SFB_LONG[ri][band]; q < SFB_LONG[ri][band + 1]; q++) s[q] = gg - mult * sfl[band];
                    k = 36;
                    for (int w = 0; w < 3; w++) for (int q = 0; q < 12; q++) s[k++] = gg - 8 * gc->subblock_gain[w] - mult * pseudo[w];
                    for (int band = 3; band < 13; band++)
                        for (int w = 0; w < 3; w++)
                            for (int q = 0; q < sfb[band + 1] - sfb[band]; q++) s[k++] = gg - 8 * gc->subblock_gain[w] - mult * sfs[band][w];
                    b0 = 13;
                } else if (gc->mixed) {   /* long bands to line 36, short bands from 3 */
                    for (int band = 0; SFB_LONG[ri][band] < 36; band++)
                        for (int q = SFB_LONG[ri][band]; q < SFB_LONG[ri][band + 1]; q++)
                            s[q] = gg - mult * (sfl[band] + gc->preflag * PRETAB[band]);
                    k = 36; b0 = 3;
                }
                for (int band = b0; band < 13; band++)
                    for (int w = 0; w < 3; w++)
                        for (int q = 0; q < sfb[band + 1] - sfb[band]; q++)
                            s[k++] = gg - 8 * gc->subblock_gain[w] - mult * (band < 12 ? sfs[band][w] : 0);
            } else {
                const uint16_t *sfb = SFB_LONG[ri];
                for (int band = 0; band < 22; band++) {
                    int64_t sf = band < 21 ? sfl[band] + gc->preflag * PRETAB[band] : 0;
                    for (int q = sfb[band]; q < sfb[band + 1]; q++) s[q] = gg - mult * sf;
                }
            }
            for (int q = 0; q < 576; q++) {
                int v = isv[q];
                xs[c][q] = (q >= nz || !v) ? 0 : q < bv ? requant(v, s[q]) : count1(v, s[q]);
            }
            /* 2:8720: a band whose scale index is positive (or has low byte 0x80) is zeroed */
            int br[40][4], nbd = bands_gc(shortb, gc->mixed, ri, br);
            for (int j = 0; j < nbd; j++) {
                int64_t sv = s[br[j][1]];
                if (sv > 0 || (sv & 0xFF) == 0x80) for (int q = br[j][1]; q < br[j][2]; q++) xs[c][q] = 0;
            }
            if (is_right) {
                memcpy(isr.sfl, sfl, sizeof sfl); memcpy(isr.sfs, sfs, sizeof sfs);
                memcpy(isr.is_maxl, is_maxl, sizeof is_maxl); memcpy(isr.is_maxs, is_maxs, sizeof is_maxs);
                isr.iscale = iscale; isr_short = shortb; isr_mixed = gc->mixed;
            }
        }
        if (mode == 1 && (ext & 1)) intensity_x(xs[0], xs[1], &isr, isr_short, isr_mixed, ri, lsf, ext & 2);
        else
        if (mode == 1 && (ext & 2))
            for (int q = 0; q < 576; q++) {
                int32_t mm = xs[0][q], sd = xs[1][q];
                xs[0][q] = ms_mul((int32_t)((uint32_t)mm + (uint32_t)sd));
                xs[1][q] = ms_mul((int32_t)((uint32_t)mm - (uint32_t)sd));
            }
        for (int c = 0; c < nch; c++)
            backend(&d->ch[c], xs[c], gcs[g][c].block_type, gcs[g][c].block_type == 2 && gcs[g][c].mixed, ri, calls[ncalls + c]);
        for (int q = 0; q < 576; q++) {
            pcm[g * 1152 + 2 * q] = calls[ncalls][q];
            pcm[g * 1152 + 2 * q + 1] = calls[ncalls + (nch == 2)][q];
        }
        ncalls += nch;
    }
    return ncalls;
}

/* ------------------------------------------------------------------------------- ID3 / length */
static size_t syncsafe(const uint8_t *b) { return (size_t)(b[0] & 127) << 21 | (b[1] & 127) << 14 | (b[2] & 127) << 7 | (b[3] & 127); }
static int text_units(const uint8_t *p, size_t n, uint16_t *out, int *enc)
{
    int k = 0;
    if (!n) { *enc = 0; return 0; }
    *enc = p[0]; p++; n--;
    if (*enc == 0) { for (size_t i = 0; i < n && p[i] && k < 64; i++) out[k++] = p[i]; }
    else {
        if (*enc == 1) { if (n < 2) return 0; p += 2; n -= 2; }
        for (size_t i = 0; i + 1 < n && k < 64; i += 2) { uint16_t v = p[i] | p[i + 1] << 8; if (!v) break; out[k++] = v; }
    }
    return k;
}
void nmp_id3(const uint8_t *d, size_t len, NmpId3 *r)
{
    memset(r, 0, sizeof *r);
    size_t v2len = 0; int have_v2 = 0;
    if (len >= 10 && !memcmp(d, "ID3", 3)) {
        have_v2 = 1; int ver = d[3]; size_t size = syncsafe(d + 6);
        v2len = 10 + size + ((d[5] & 0x10) ? 10 : 0);
        size_t end = 10 + size < len ? 10 + size : len, p = 10, hdr = ver == 2 ? 6 : 10;
        const char *tt = ver == 2 ? "TT2" : "TIT2", *ta = ver == 2 ? "TP1" : "TPE1"; size_t idl = ver == 2 ? 3 : 4;
        while ((ver >= 2 && ver <= 4) && p + hdr <= end) {
            size_t fs = ver == 2 ? (size_t)d[p + 3] << 16 | d[p + 4] << 8 | d[p + 5]
                      : ver == 4 ? syncsafe(d + p + 4) : (size_t)d[p + 4] << 24 | d[p + 5] << 16 | d[p + 6] << 8 | d[p + 7];
            if (!d[p]) break;
            size_t a = p + hdr < len ? p + hdr : len, b = p + hdr + fs < len ? p + hdr + fs : len;
            int enc;
            if (!memcmp(d + p, tt, idl)) { r->ntitle = text_units(d + a, b - a, r->title, &enc); r->encoding = enc; }
            else if (!memcmp(d + p, ta, idl)) r->nartist = text_units(d + a, b - a, r->artist, &enc);
            p += hdr + fs;
        }
    }
    int v1 = len >= 128 && !memcmp(d + len - 128, "TAG", 3);
    if (!have_v2 && v1) {
        const uint8_t *t = d + len - 128;
        for (int i = 0; i < 30 && t[3 + i]; i++) r->title[r->ntitle++] = t[3 + i];
        for (int i = 0; i < 30 && t[33 + i]; i++) r->artist[r->nartist++] = t[33 + i];
        r->encoding = 0xFFFF;
    }
    for (size_t p = v2len; p + 4 <= len; p++) {
        size_t fsz = nmp_frame_size(d + p, len - p);
        if (!fsz || p + fsz > len) continue;
        const uint8_t *fr = d + p; uint32_t h = (uint32_t)fr[0] << 24 | fr[1] << 16 | fr[2] << 8 | fr[3];
        int ver = (h >> 19) & 3, lsf = ver != 3, mode = (h >> 6) & 3, crc = !((h >> 16) & 1), bri = (h >> 12) & 15;
        unsigned rate = RATES[((h >> 10) & 3) + (ver == 3 ? 0 : ver == 2 ? 3 : 6)];
        size_t off = 4 + (crc ? 2 : 0) + (lsf ? (mode == 3 ? 9 : 17) : (mode == 3 ? 17 : 32));
        if (off + 12 <= fsz && !memcmp(fr + off, "Xing", 4) && (fr[off + 7] & 1)) {
            uint64_t n = (uint64_t)fr[off + 8] << 24 | fr[off + 9] << 16 | fr[off + 10] << 8 | fr[off + 11];
            r->seconds = (uint32_t)(n * (lsf ? 576 : 1152) / rate);
        } else {
            uint64_t bytes = len - v2len - (v1 ? 128 : 0);
            r->seconds = (uint32_t)(bytes * 8 / ((uint64_t)(lsf ? BITRATE_LSF[bri] : BITRATE[bri]) * 1000));
        }
        break;
    }
}

/* ------------------------------------------------------------------------------- speaker stream */
#define SRC_W(a) ((int64_t)nmp_src[((a) & 0xFFFF) - NMP_SRC_BASE])
static const unsigned SRC_RATES[9] = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000};
static int16_t sth_sat(int64_t a, int sh)
{
    int64_t v = a * ((int64_t)1 << sh);
    if (v > INT32_MAX) v = INT32_MAX;
    if (v < INT32_MIN) v = INT32_MIN;
    return (int16_t)(v >> 16);
}
typedef struct {   /* one channel of 2:E309 (see the Rust Src) */
    int path_a, per_out, pos, n1d, started, p, q;
    int64_t step, mod, c[5], f, ring[17], st[4];
} Src;
static void src_init(Src *s, int i)
{
    memset(s, 0, sizeof *s);
    s->path_a = SRC_RATES[i] != 48000;
    s->step = SRC_W((s->path_a ? 0xE013 : 0xE01C) + i);
    s->mod = s->path_a ? 3528 : 3520;
    s->per_out = s->path_a ? 0 : (int)SRC_W(0xE025 + i);
    int64_t cp = SRC_W(0xE5E1 + i);
    for (int k = 0; k < 5; k++) s->c[k] = (int16_t)SRC_W(cp + k);
}
static int64_t src_iir(Src *s, int64_t x)
{
    int64_t b = x * s->c[4];
    for (int k = 0; k < 4; k++) b += s->c[k] * s->st[(s->q + k) % 4];
    int64_t y = sth_sat(b, 2);
    s->st[(s->q + 3) % 4] = x; s->st[(s->q + 2) % 4] = y; s->q = (s->q + 2) % 4;
    return y;
}
static int64_t src_fir(Src *s)
{
    int64_t ptr = SRC_W(0xE5B4 + ((s->f * 26215) >> 21)), a = 0;
    for (int k = 0; k < 17; k++) a += s->ring[(s->p + 1 + k) % 17] * (int16_t)SRC_W(ptr + k);
    return sth_sat(a, 1);
}
static void src_push(Src *s, int64_t x) { s->p = (s->p + 1) % 17; s->ring[s->p] = x; }
static int src_feed(Src *s, const int16_t *x, int n, int16_t *out)
{
    int i = 0, o = 0;
    if (!s->started) { s->started = 1; s->pos = s->path_a ? 0 : 1; s->n1d = 0; }
    for (;;) switch (s->pos) {
    case 0: { int64_t y = src_fir(s); if (s->path_a) y = src_iir(s, y); out[o++] = (int16_t)y;
              s->n1d = 0; s->pos = s->path_a ? 2 : 1; break; }
    case 1: if (i >= n) return o;
            src_push(s, src_iir(s, x[i++])); if (++s->n1d >= s->per_out) s->pos = 2; break;
    case 2: s->f += s->step; if (s->f - s->mod >= 0) { s->f -= s->mod; s->pos = 3; } else s->pos = 0; break;
    default: if (i >= n) return o;
             { int64_t v = x[i++]; src_push(s, s->path_a ? v : src_iir(s, v)); } s->pos = 0; break;
    }
}
struct NmpSpeaker { int has_src; Src src[2]; uint32_t c; int64_t last; int pend; uint16_t pl, pr; int nw; uint16_t ch1[288], ch2[288]; };
NmpSpeaker *nmp_speaker_new(unsigned rate)
{
    int i = -1;
    for (int k = 0; k < 9; k++) if (SRC_RATES[k] == rate) i = k;
    if (i < 0) return NULL;
    NmpSpeaker *s = calloc(1, sizeof *s);
    s->has_src = rate != 44100; s->last = -1;
    if (s->has_src) { src_init(&s->src[0], i); src_init(&s->src[1], i); }
    return s;
}
void nmp_speaker_free(NmpSpeaker *s) { free(s); }
int nmp_speaker_push(NmpSpeaker *s, const int16_t *l, const int16_t *r, int n, uint16_t blocks[8][576])
{
    static int16_t bl[4096], br[4096];   /* 576 inputs at 8 kHz -> 3176 outputs */
    if (s->has_src) { int a = src_feed(&s->src[0], l, n, bl); src_feed(&s->src[1], r, n, br); l = bl; r = br; n = a; }
    int nb = 0;
    for (int k = 0; k < n; k++) {
        int64_t q = ((int64_t)s->c << 14) / 44100;
        if (++s->c > 44100) s->c = 0;
        if (q == s->last) continue;
        s->last = q;
        uint16_t hl = (uint16_t)l[k] >> 8, hr = (uint16_t)r[k] >> 8;
        if (!s->pend) { s->pend = 1; s->pl = hl; s->pr = hr; continue; }
        s->pend = 0;
        s->ch2[s->nw] = hl << 8 | s->pl; s->ch1[s->nw] = hr << 8 | s->pr;
        if (++s->nw == 288) { memcpy(blocks[nb], s->ch1, 576); memcpy(blocks[nb] + 288, s->ch2, 576); nb++; s->nw = 0; }
    }
    return nb;
}
