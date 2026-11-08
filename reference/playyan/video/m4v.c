/* MPEG-4 Part 2 Simple Profile as the Play-Yan video firmware decodes it.
 * See crates/gbav-playyan/src/m4v.rs and docs/playyan/decoders.md for every firmware deviation. */
#include "pyvideo.h"
#include "fw_tables.h"
#include "m4v_tables.h"
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ bits */
typedef struct {
    const uint8_t *d;
    size_t n;
    size_t pos;
} bits;

static uint32_t peek32(const bits *b) {
    size_t by = b->pos >> 3;
    uint64_t w = 0;
    for (int i = 0; i < 5; i++) w = w << 8 | (by + i < b->n ? b->d[by + i] : 0);
    return (uint32_t)(w << (b->pos & 7) >> 8);
}
static uint32_t peek(const bits *b, int k) { return k ? peek32(b) >> (32 - k) : 0; }
static uint32_t get(bits *b, int k) {
    uint32_t v = peek(b, k);
    b->pos += (size_t)k;
    return v;
}
static int bit1(bits *b) { return (int)get(b, 1); }
static long bits_left(const bits *b) { return (long)(b->n * 8) - (long)b->pos; }

/* ------------------------------------------------------------------ VLC */
typedef struct {
    int nbits;
    uint16_t *sym;
    uint8_t *len;
} vlc;

static void vlc_init(vlc *v, const void *codes, int n, int wide, const int *skip, int nskip) {
    int mx = 1;
    for (int i = 0; i < n; i++) {
        int l = wide ? ((const uint16_t(*)[2])codes)[i][1] : ((const uint8_t(*)[2])codes)[i][1];
        if (l > mx) mx = l;
    }
    v->nbits = mx;
    v->sym = calloc((size_t)1 << mx, 2);
    v->len = calloc((size_t)1 << mx, 1);
    for (int i = 0; i < n; i++) {
        int c = wide ? ((const uint16_t(*)[2])codes)[i][0] : ((const uint8_t(*)[2])codes)[i][0];
        int l = wide ? ((const uint16_t(*)[2])codes)[i][1] : ((const uint8_t(*)[2])codes)[i][1];
        int sk = 0;
        for (int k = 0; k < nskip; k++) sk |= skip[k] == i;
        if (!l || sk) continue;
        int sh = mx - l;
        for (int k = 0; k < (1 << sh); k++) {
            v->sym[(c << sh) | k] = (uint16_t)i;
            v->len[(c << sh) | k] = (uint8_t)l;
        }
    }
}
static int vlc_read(const vlc *v, bits *b) {
    uint32_t i = peek(b, v->nbits);
    if (!v->len[i]) return -1;
    b->pos += v->len[i];
    return v->sym[i];
}

typedef struct {
    vlc v;
    const uint8_t *run, *level;
    int last_start;
    uint8_t max_level[2][64], max_run[2][64];
} rltab;

static void rl_init(rltab *r, const uint16_t codes[103][2], const uint8_t *run, const uint8_t *level, int last_start) {
    memset(r, 0, sizeof *r);
    vlc_init(&r->v, codes, 103, 1, NULL, 0);
    r->run = run;
    r->level = level;
    r->last_start = last_start;
    for (int i = 0; i < 102; i++) {
        int l = i >= last_start;
        if (level[i] > r->max_level[l][run[i]]) r->max_level[l][run[i]] = level[i];
        if (run[i] > r->max_run[l][level[i]]) r->max_run[l][level[i]] = run[i];
    }
}

/* ------------------------------------------------------------------ state */
typedef struct {
    uint32_t slice;
    int intra, qp;
    int mv[4][2];
} mbinfo;

typedef struct {
    int dc;
    int16_t row[8], col[8];
} blkpred;

typedef struct {
    uint32_t w, h;
    size_t stride, rows;
    uint8_t *y, *cb, *cr;
} picture;

typedef struct {
    int verid, quant_type, resync_disable, time_inc_bits;
    uint32_t w, h;
    uint8_t intra_matrix[64], inter_matrix[64];
} vol_t;

struct pyv_dec {
    vlc intra_mcbpc, inter_mcbpc, cbpy, mv, dc_lum, dc_chrom;
    rltab intra, inter;
    int have_vol, have_ref;
    vol_t vol;
    size_t mbw, mbh;
    picture cur, ref;
    mbinfo *mbs;
    blkpred *pred[3];
    int qp, dc_thr, rounding, fcode;
    uint32_t slice;
    int spill[6][64]; /* stored into later blocks of the MB by out-of-range scan indices */
    int fw_error;     /* the firmware's decode-error flag: the VOP stops after this MB */
};

#define NONE 0xffffffffu
static int s16(int x) { return (int16_t)x; }
static int sat16(int x) { return x < -32768 ? -32768 : x > 32767 ? 32767 : x; }
static int sgn(int x) { return (x > 0) - (x < 0); }
static int clampi(int x, int lo, int hi) { return x < lo ? lo : x > hi ? hi : x; }

pyv_dec *pyv_dec_new(void) {
    pyv_dec *d = calloc(1, sizeof *d);
    static const int skip_inter[] = {21, 22, 23, 24, 25, 26, 27};
    vlc_init(&d->intra_mcbpc, INTRA_MCBPC, 9, 0, NULL, 0);
    vlc_init(&d->inter_mcbpc, INTER_MCBPC, 28, 0, skip_inter, 7);
    vlc_init(&d->cbpy, CBPY, 16, 0, NULL, 0);
    vlc_init(&d->mv, MVTAB, 33, 0, NULL, 0);
    vlc_init(&d->dc_lum, DC_LUM, 13, 0, NULL, 0);
    vlc_init(&d->dc_chrom, DC_CHROM, 13, 0, NULL, 0);
    rl_init(&d->intra, INTRA_VLC, INTRA_RUN, INTRA_LEVEL, 67);
    rl_init(&d->inter, INTER_VLC, INTER_RUN, INTER_LEVEL, 58);
    return d;
}

static void vfree(vlc *v) {
    free(v->sym);
    free(v->len);
}
static void pic_free(picture *p) {
    free(p->y);
    free(p->cb);
    free(p->cr);
}
void pyv_dec_free(pyv_dec *d) {
    if (!d) return;
    vfree(&d->intra_mcbpc);
    vfree(&d->inter_mcbpc);
    vfree(&d->cbpy);
    vfree(&d->mv);
    vfree(&d->dc_lum);
    vfree(&d->dc_chrom);
    vfree(&d->intra.v);
    vfree(&d->inter.v);
    pic_free(&d->cur);
    pic_free(&d->ref);
    free(d->mbs);
    for (int i = 0; i < 3; i++) free(d->pred[i]);
    free(d);
}

static void pic_new(picture *p, uint32_t w, uint32_t h) {
    pic_free(p);
    p->w = w;
    p->h = h;
    p->stride = (w + 15) & ~15u;
    p->rows = (h + 15) & ~15u;
    p->y = calloc(p->stride * p->rows, 1);
    p->cb = malloc(p->stride * p->rows / 4);
    p->cr = malloc(p->stride * p->rows / 4);
    memset(p->cb, 128, p->stride * p->rows / 4);
    memset(p->cr, 128, p->stride * p->rows / 4);
}

/* ------------------------------------------------------------------ headers */
static void read_matrix(bits *b, uint8_t m[64]) {
    int i = 0;
    uint8_t last = 0;
    while (i < 64) {
        uint8_t v = (uint8_t)get(b, 8);
        if (!v) break;
        last = v;
        m[ZIGZAG[i++]] = v;
    }
    while (i < 64) m[ZIGZAG[i++]] = last;
}

static int parse_vol(bits *b, vol_t *v) {
    memset(v, 0, sizeof *v);
    get(b, 9);
    v->verid = 1;
    if (bit1(b)) {
        v->verid = (int)get(b, 4);
        get(b, 3);
    }
    if (get(b, 4) == 15) get(b, 16);
    if (bit1(b)) {
        get(b, 3);
        if (bit1(b)) b->pos += 79;
    }
    if (get(b, 2)) return -1;
    get(b, 1);
    uint32_t res = get(b, 16);
    get(b, 1);
    uint32_t r = res < 2 ? 1 : res - 1;
    int tib = 0;
    while (r) {
        tib++;
        r >>= 1;
    }
    v->time_inc_bits = tib ? tib : 1;
    if (bit1(b)) get(b, v->time_inc_bits);
    get(b, 1);
    v->w = get(b, 13);
    get(b, 1);
    v->h = get(b, 13);
    get(b, 1);
    if (bit1(b)) return -1; /* interlaced */
    get(b, 1);
    if (v->verid == 1 ? get(b, 1) : get(b, 2)) return -1;
    if (bit1(b)) return -1;
    v->quant_type = (int)get(b, 1);
    memcpy(v->intra_matrix, DEFAULT_INTRA_MATRIX, 64);
    memcpy(v->inter_matrix, DEFAULT_INTER_MATRIX, 64);
    if (v->quant_type) {
        if (bit1(b)) read_matrix(b, v->intra_matrix);
        if (bit1(b)) read_matrix(b, v->inter_matrix);
    }
    if (v->verid != 1 && bit1(b)) return -1;
    if (!bit1(b)) return -1;
    v->resync_disable = bit1(b);
    if (bit1(b)) return -1; /* data partitioning */
    if (v->verid != 1) {
        if (bit1(b)) return -1;
        get(b, 1);
    }
    if (bit1(b)) return -1;
    if (!v->w || !v->h || v->w > 1024 || v->h > 1024) return -1;
    return 0;
}

static void set_vol(pyv_dec *d, const vol_t *v) {
    if (!d->have_vol || d->vol.w != v->w || d->vol.h != v->h) {
        pic_new(&d->cur, v->w, v->h);
        pic_new(&d->ref, v->w, v->h);
        d->mbw = d->cur.stride / 16;
        d->mbh = d->cur.rows / 16;
        free(d->mbs);
        d->mbs = calloc(d->mbw * d->mbh, sizeof *d->mbs);
        for (int i = 0; i < 3; i++) {
            free(d->pred[i]);
            d->pred[i] = calloc((i ? 1 : 4) * d->mbw * d->mbh, sizeof(blkpred));
        }
        d->have_ref = 0;
    }
    d->vol = *v;
    d->have_vol = 1;
}

/* ------------------------------------------------------------------ IDCT (firmware 0x26002) */
static int mr(int x, int c) { return s16((x * c + 0x8000) >> 16); }
static void bfly(int x[8]) {
    enum { T1 = 0x32EC, T2 = 0x6A0A, T3 = 0xAB0E, S2 = 0xB505 };
#define A(p, q) s16((p) + (q))
#define D(p, q) s16((p) - (q))
    int t8 = mr(x[1], T1), t9 = mr(x[7], T1);
    t9 = A(t9, x[1]);
    x[1] = D(t8, x[7]);
    t8 = mr(x[3], T3);
    x[7] = mr(x[5], T3);
    x[3] = A(x[3], x[7]);
    t8 = D(x[5], t8);
    x[5] = D(t9, x[3]);
    t9 = A(t9, x[3]);
    x[3] = A(x[1], t8);
    x[1] = D(x[1], t8);
    t8 = mr(x[1], S2);
    x[7] = mr(x[5], S2);
    x[1] = A(x[7], t8);
    x[5] = D(x[7], t8);
    x[7] = mr(x[6], T2);
    t8 = mr(x[2], T2);
    x[2] = A(x[2], x[7]);
    x[6] = D(t8, x[6]);
    t8 = D(x[0], x[4]);
    x[4] = A(x[0], x[4]);
    x[0] = A(t8, x[6]);
    x[6] = D(t8, x[6]);
    t8 = A(x[4], x[2]);
    x[2] = D(x[4], x[2]);
    x[4] = D(x[2], x[3]);
    x[3] = A(x[2], x[3]);
    x[2] = A(x[6], x[5]);
    x[5] = D(x[6], x[5]);
    x[6] = D(x[0], x[1]);
    x[1] = A(x[0], x[1]);
    x[0] = A(t8, t9);
    x[7] = D(t8, t9);
#undef A
#undef D
}
static void idct(int16_t blk[64]) {
    int rows[8][8];
    for (int v = 0; v < 8; v++) {
        int x[8];
        for (int u = 0; u < 8; u++) {
            int c = blk[8 * v + u] < 2047 ? blk[8 * v + u] : 2047;
            x[u] = mr(s16(c * 16), PYV_IDCT_PRESCALE[v * 16 + u]);
        }
        bfly(x);
        memcpy(rows[v], x, sizeof x);
    }
    for (int u = 0; u < 8; u++) {
        int y[8];
        for (int v = 0; v < 8; v++) y[v] = rows[v][u];
        bfly(y);
        for (int v = 0; v < 8; v++) blk[8 * v + u] = (int16_t)mr(y[v], 1024);
    }
}

/* ------------------------------------------------------------------ intra */
static int fw_div(int a, int b) { /* 0x26f0e */
    int q = (a * (int)PYV_RECIP[b]) >> 16;
    if (b * q + b <= a) q++;
    return q;
}
static int fw_ac_rescale(int p, int qp, int qc) { /* 0x2589e / 0x25926 */
    int a = s16(p * qp);
    int64_t prod = (int64_t)a * PYV_RECIP[qc];
    int q = (int)(prod < 0 ? -((-prod) >> 16) : prod >> 16);
    q = s16(q);
    int rem = s16(a - s16(q * qc));
    if (abs(rem) >= qc - (qc >> 1)) q += sgn(q);
    return s16(q);
}

static int pred_at(const pyv_dec *d, int comp, long bx, long by, blkpred *out) {
    long w = comp ? (long)d->mbw : 2 * (long)d->mbw, h = comp ? (long)d->mbh : 2 * (long)d->mbh;
    if (bx < 0 || by < 0 || bx >= w || by >= h) return 0;
    size_t mb = comp ? (size_t)(by * (long)d->mbw + bx) : (size_t)((by / 2) * (long)d->mbw + bx / 2);
    if (d->mbs[mb].slice != d->slice || !d->mbs[mb].intra) return 0;
    *out = d->pred[comp][by * w + bx];
    return 1;
}
static int mb_qp_at(const pyv_dec *d, int comp, long bx, long by) {
    size_t mb = comp ? (size_t)(by * (long)d->mbw + bx) : (size_t)((by / 2) * (long)d->mbw + bx / 2);
    return d->mbs[mb].qp;
}

/* TCOEF run/level decoding into lv (raster positions via scan sk: 0 zigzag, 1 alternate-
 * horizontal, 2 alternate-vertical) from scan index start, for block n. As the firmware
 * (py_fw0 0x20484 / 0x54b4): a table event at index >= 64 is stored at the raster position
 * given by the bytes after the scan table (PYV_SCAN_EXT), possibly in a later block of the MB;
 * a non-last table event at index >= 63, or an escape past 63 (not stored), sets the error
 * flag and ends the block. Inter blocks past 63 are refused. */
static int read_coeffs(pyv_dec *d, bits *b, int intra, int start, int sk, int n, int lv[64]) {
    static const uint8_t *const scans[3] = {ZIGZAG, ALT_HORIZ, ALT_VERT};
    const uint8_t *scan = scans[sk];
    const rltab *rl = intra ? &d->intra : &d->inter;
    int i = start - 1;
    for (;;) {
        int idx = vlc_read(&rl->v, b);
        int last, run, level, esc = idx == 102;
        if (idx < 0) return -1;
        if (idx == 102) {
            if (!bit1(b)) {
                int k = vlc_read(&rl->v, b);
                if (k < 0 || k == 102) return -1;
                int l = k >= rl->last_start;
                run = rl->run[k];
                int l0 = rl->level[k] + rl->max_level[l][run];
                last = l;
                level = bit1(b) ? -l0 : l0;
            } else if (!bit1(b)) {
                int k = vlc_read(&rl->v, b);
                if (k < 0 || k == 102) return -1;
                int l = k >= rl->last_start;
                int l0 = rl->level[k];
                run = rl->run[k] + rl->max_run[l][l0] + 1;
                last = l;
                level = bit1(b) ? -l0 : l0;
            } else {
                last = bit1(b);
                run = (int)get(b, 6);
                get(b, 1);
                int v = (int)get(b, 12);
                level = v & 0x800 ? v - 4096 : v;
                get(b, 1);
            }
        } else {
            last = idx >= rl->last_start;
            run = rl->run[idx];
            int l0 = rl->level[idx];
            level = bit1(b) ? -l0 : l0;
        }
        i += run + 1;
        if (i > 63) {
            if (!intra) return -1;
            if (esc) {
                d->fw_error = 1;
                return 0;
            }
            int pos = PYV_SCAN_EXT[sk][i - 64], m = n + pos / 64;
            if (m >= 6) {
                if (!PYV_SPILL_PAST_HARMLESS) return -1;
            } else if (m == n)
                lv[pos] = s16(level);
            else
                d->spill[m][pos % 64] = s16(level);
        } else
            lv[scan[i]] = s16(level);
        if (last) return 0;
        if (i >= 63 && !esc) {
            d->fw_error = 1;
            return 0;
        }
    }
}

static void put_block(pyv_dec *d, size_t mx, size_t my, int n, const int16_t res[64], const uint8_t *pred) {
    uint8_t *plane;
    size_t stride, x0, y0;
    if (n < 4) {
        plane = d->cur.y;
        stride = d->cur.stride;
        x0 = mx * 16 + (size_t)(n & 1) * 8;
        y0 = my * 16 + (size_t)(n >> 1) * 8;
    } else {
        plane = n == 4 ? d->cur.cb : d->cur.cr;
        stride = d->cur.stride / 2;
        x0 = mx * 8;
        y0 = my * 8;
    }
    for (int y = 0; y < 8; y++)
        for (int x = 0; x < 8; x++) {
            int v = res[8 * y + x];
            if (pred) v = s16(v + pred[8 * y + x]);
            plane[(y0 + y) * stride + x0 + x] = (uint8_t)clampi(v, 0, 255);
        }
}

static int intra_block(pyv_dec *d, bits *b, size_t mx, size_t my, int n, int coded, int ac_pred, int use_dc_vlc, int16_t blk[64]) {
    int comp;
    long bx, by;
    if (n < 4) {
        comp = 0;
        bx = (long)(2 * mx + (n & 1));
        by = (long)(2 * my + (n >> 1));
    } else {
        comp = n - 3;
        bx = (long)mx;
        by = (long)my;
    }
    blkpred pa, pb, pc;
    int ha = pred_at(d, comp, bx - 1, by, &pa), hb = pred_at(d, comp, bx - 1, by - 1, &pb), hc = pred_at(d, comp, bx, by - 1, &pc);
    int a = ha ? pa.dc : 1024, bb = hb ? pb.dc : 1024, c = hc ? pc.dc : 1024;
    int top = abs(a - bb) < abs(bb - c);
    int pred_dc = top ? c : a;
    int scale = comp ? C_DC_SCALE[d->qp] : Y_DC_SCALE[d->qp];
    int lv[64];
    memcpy(lv, d->spill[n], sizeof lv); /* zero unless an earlier block spilled into it */
    if (use_dc_vlc) {
        int size = vlc_read(comp ? &d->dc_chrom : &d->dc_lum, b);
        if (size < 0) return -1;
        int dd = 0;
        if (size) {
            int v = (int)get(b, size);
            dd = v >> (size - 1) ? v : v - (1 << size) + 1;
            if (size > 8) get(b, 1);
        }
        lv[0] = dd;
    }
    /* firmware deviation: with the DC coded through the TCOEF table (QP >= threshold) the
     * events are still read from scan index 1, and the DC level stays what the buffer held */
    int sk = ac_pred ? (top ? 1 : 2) : 0;
    if (coded && read_coeffs(d, b, 1, 1, sk, n, lv) < 0) return -1;
    if (ac_pred) {
        int qc = d->qp;
        if (top && hc) {
            int qpp = mb_qp_at(d, comp, bx, by - 1), sc = qpp && qpp != qc;
            for (int j = 1; j < 8; j++) lv[j] = s16(lv[j] + (sc ? fw_ac_rescale(pc.row[j], qpp, qc) : pc.row[j]));
        } else if (!top && ha) {
            int qpp = mb_qp_at(d, comp, bx - 1, by), sc = qpp && qpp != qc;
            for (int j = 1; j < 8; j++) lv[8 * j] = s16(lv[8 * j] + (sc ? fw_ac_rescale(pa.col[j], qpp, qc) : pa.col[j]));
        }
    }
    lv[0] = s16(lv[0] + fw_div(pred_dc + (scale >> 1), scale));
    int cl[64], f[64];
    for (int j = 0; j < 64; j++) cl[j] = clampi(lv[j], -2048, 2047);
    int q = d->qp;
    f[0] = s16(cl[0] * scale);
    if (!d->vol.quant_type) {
        int qadd = (q - 1) | 1;
        for (int j = 1; j < 64; j++) f[j] = s16(cl[j] * 2 * q + sgn(cl[j]) * qadd);
    } else {
        /* firmware: intra blocks use the non-intra matrix */
        for (int j = 1; j < 64; j++) f[j] = sat16(sat16(cl[j] * q) * d->vol.inter_matrix[j]) / 8;
        int sum = 0;
        for (int j = 0; j < 64; j++) sum += f[j];
        if (!(sum & 1)) f[63] = s16(f[63] + ((f[63] & 1) ? -1 : 1));
    }
    long w = comp ? (long)d->mbw : 2 * (long)d->mbw;
    blkpred st;
    st.dc = cl[0] * scale; /* not clipped: the firmware keeps level and scale */
    st.row[0] = st.col[0] = 0;
    for (int j = 1; j < 8; j++) {
        st.row[j] = (int16_t)cl[j];
        st.col[j] = (int16_t)cl[8 * j];
    }
    d->pred[comp][by * w + bx] = st;
    for (int j = 0; j < 64; j++) blk[j] = (int16_t)f[j];
    return 0;
}

/* the firmware does not clamp (0x64f2): out of 1..31 is an error with tables read out of range */
static int update_qp(pyv_dec *d, bits *b) {
    static const int dq[4] = {-1, -2, 1, 2};
    d->qp += dq[get(b, 2)];
    return d->qp >= 1 && d->qp <= 31 ? 0 : -1;
}

static int intra_mb(pyv_dec *d, bits *b, size_t mx, size_t my, int cbpc, int dq) {
    int ac_pred = bit1(b);
    int cbpy = vlc_read(&d->cbpy, b);
    if (cbpy < 0) return -1;
    if (dq && update_qp(d, b) < 0) return -1;
    int cbp = cbpy << 2 | cbpc;
    mbinfo *m = &d->mbs[my * d->mbw + mx];
    memset(m, 0, sizeof *m);
    m->slice = d->slice;
    m->intra = 1;
    m->qp = d->qp;
    int use_dc_vlc = d->qp < d->dc_thr; /* QP after dquant (0x4b04) */
    memset(d->spill, 0, sizeof d->spill);
    for (int n = 0; n < 6; n++) {
        int16_t blk[64];
        if (intra_block(d, b, mx, my, n, (cbp >> (5 - n)) & 1, ac_pred, use_dc_vlc, blk) < 0) return -1;
        idct(blk);
        put_block(d, mx, my, n, blk, NULL);
    }
    return 0;
}

/* ------------------------------------------------------------------ motion */
static int read_mv(pyv_dec *d, bits *b, int pred, int *out) {
    int code = vlc_read(&d->mv, b);
    if (code < 0) return -1;
    if (!code) {
        *out = pred;
        return 0;
    }
    int neg = bit1(b), shift = d->fcode - 1, val = code;
    if (shift) val = (((val - 1) << shift) | (int)get(b, shift)) + 1;
    if (neg) val = -val;
    val += pred;
    int k = 32 - (5 + d->fcode);
    *out = (int)((uint32_t)val << k) >> k;
    return 0;
}

static int mv_cand(const pyv_dec *d, size_t mx, size_t my, long bx, long by, int *v) {
    if (bx < 0 || by < 0 || bx >= 2 * (long)d->mbw || by >= 2 * (long)d->mbh) return 0;
    size_t mb = (size_t)(by / 2) * d->mbw + (size_t)bx / 2, cur = my * d->mbw + mx;
    const mbinfo *m = &d->mbs[mb];
    if (mb > cur || m->slice != d->slice) return 0;
    v[0] = m->mv[(by & 1) * 2 + (bx & 1)][0];
    v[1] = m->mv[(by & 1) * 2 + (bx & 1)][1];
    return 1;
}
static int med(int a, int b, int c) {
    int mx = a > b ? a : b, mn = a < b ? a : b;
    return mx < c ? mx : (mn > c ? mn : c);
}
static void mv_pred(const pyv_dec *d, size_t mx, size_t my, int k, int *px, int *py) {
    static const int off[4] = {2, 1, 1, -1};
    long bx = (long)(2 * mx + (k & 1)), by = (long)(2 * my + (k >> 1));
    int c[3][2] = {{0}};
    int h[3] = {mv_cand(d, mx, my, bx - 1, by, c[0]), mv_cand(d, mx, my, bx, by - 1, c[1]), mv_cand(d, mx, my, bx + off[k], by - 1, c[2])};
    int nv = h[0] + h[1] + h[2];
    if (nv == 0) {
        *px = *py = 0;
    } else if (nv == 1) {
        int i = h[0] ? 0 : h[1] ? 1 : 2;
        *px = c[i][0];
        *py = c[i][1];
    } else {
        *px = med(c[0][0], c[1][0], c[2][0]);
        *py = med(c[0][1], c[1][1], c[2][1]);
    }
}

/* firmware MC wrapper 0x38a8 (window model; see m4v.rs fw_mc16) */
static const picture *g_ref;
static int g_rc;
static int rdmem(long a) {
    const picture *p = g_ref;
    long ysz = (long)(p->stride * p->rows), csz = ysz / 4;
    if (a < 0) return 0;
    if (a < ysz) return p->y[a];
    if (a < ysz + csz) return p->cb[a - ysz];
    if (a < ysz + 2 * csz) return p->cr[a - ysz - csz];
    return 0;
}
typedef struct {
    long org;
    long size;
    int clamp;
} axis_t;
static long ax_src(axis_t a, long k) {
    long c = a.org + k;
    return a.clamp ? (c < 0 ? 0 : c > a.size - 1 ? a.size - 1 : c) : c;
}
static axis_t axis(long p, long org, int mv, long size, int n, long lim) {
    axis_t a;
    long hh = n / 2;
    a.size = size;
    if (p < 0) {
        a.org = -hh;
        a.clamp = 1;
    } else if (p > lim) {
        a.org = size - hh;
        a.clamp = 1;
    } else {
        long o = n == 32 ? org + (mv >> 1) : (org + (mv >> 1)) >> 1;
        a.org = o & ~(hh - 1);
        a.clamp = 0;
    }
    return a;
}
/* sample from plane base `base` with row width `w` through two axes (direct: identity) */
static int samp(long base, long w, axis_t ax, axis_t ay, int direct, long i, long j) {
    return direct ? rdmem(base + j * w + i) : rdmem(base + ax_src(ay, j) * w + ax_src(ax, i));
}
static void interp(long base, long w, axis_t ax, axis_t ay, int direct, long ox, long oy, int fx, int fy, int n, uint8_t *out) {
    int rc = g_rc;
    for (long j = 0; j < n; j++)
        for (long i = 0; i < n; i++) {
            long x = ox + i, y = oy + j;
            int a = samp(base, w, ax, ay, direct, x, y), v;
            if (fx && fy)
                v = (a + samp(base, w, ax, ay, direct, x + 1, y) + samp(base, w, ax, ay, direct, x, y + 1) + samp(base, w, ax, ay, direct, x + 1, y + 1) + 2 - rc) >> 2;
            else if (fx)
                v = (a + samp(base, w, ax, ay, direct, x + 1, y) + 1 - rc) >> 1;
            else if (fy)
                v = (a + samp(base, w, ax, ay, direct, x, y + 1) + 1 - rc) >> 1;
            else
                v = a;
            out[j * n + i] = (uint8_t)v;
        }
}
static int cmv(int m) { return (m >> 1) | (m & 1); }
static void fw_mc16(const picture *ref, int x, int y, int mvx, int mvy, int rc, uint8_t ly[256], uint8_t cb[64], uint8_t cr[64]) {
    g_ref = ref;
    g_rc = rc;
    long w = (long)ref->stride, h = (long)ref->rows, cw = w / 2, ch = h / 2;
    long ub = w * h, vb = ub + ub / 4;
    long px = 2L * x + mvx, py = 2L * y + mvy;
    axis_t none = {0, 0, 0};
    if (px >= 0 && px <= 2 * w - 32 && py >= 0 && py <= 2 * h - 32) {
        interp(0, w, none, none, 1, x + (mvx >> 1), y + (mvy >> 1), mvx & 1, mvy & 1, 16, ly);
        int cx = cmv(mvx), cy = cmv(mvy);
        interp(ub, cw, none, none, 1, x / 2 + (cx >> 1), y / 2 + (cy >> 1), cx & 1, cy & 1, 8, cb);
        interp(vb, cw, none, none, 1, x / 2 + (cx >> 1), y / 2 + (cy >> 1), cx & 1, cy & 1, 8, cr);
        return;
    }
    axis_t axl = axis(px, x, mvx, w, 32, 2 * w - 32), ayl = axis(py, y, mvy, h, 32, 2 * h - 32);
    int m5x = mvx & 31, m5y = mvy & 31;
    interp(0, w, axl, ayl, 0, m5x >> 1, m5y >> 1, m5x & 1, m5y & 1, 16, ly);
    axis_t axc = axis(px, x, mvx, cw, 16, 2 * w - 32), ayc = axis(py, y, mvy, ch, 16, 2 * h - 32);
    int cx = cmv(m5x), cy = cmv(m5y);
    interp(ub, cw, axc, ayc, 0, cx >> 1, cy >> 1, cx & 1, cy & 1, 8, cb);
    interp(vb, cw, axc, ayc, 0, cx >> 1, cy >> 1, cx & 1, cy & 1, 8, cr);
}

static int chroma4(int s) {
    static const int RT[16] = {0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2};
    int a = abs(s);
    return (s < 0 ? -1 : 1) * (RT[a & 15] + 2 * (a >> 4));
}

static int inter_recon(pyv_dec *d, size_t mx, size_t my, int mv[4][2], int four, int cbp, bits *b) {
    int rc = d->rounding, x = (int)mx * 16, y = (int)my * 16;
    uint8_t preds[6][64], ly[256], cb[64], cr[64];
    if (!four) {
        fw_mc16(&d->ref, x, y, mv[0][0], mv[0][1], rc, ly, cb, cr);
        for (int k = 0; k < 4; k++)
            for (int j = 0; j < 8; j++) memcpy(preds[k] + j * 8, ly + ((k >> 1) * 8 + j) * 16 + (k & 1) * 8, 8);
    } else {
        int w2 = 2 * (int)d->ref.stride - 2, h2 = 2 * (int)d->ref.rows - 2;
        for (int k = 0; k < 4; k++) {
            int vx = mv[k][0], vy = mv[k][1];
            if (2 * x + vx < -30)
                vx = -30 - 2 * x;
            else if (2 * x + vx > w2)
                vx = w2 - 2 * x;
            if (2 * y + vy < -30)
                vy = -30 - 2 * y;
            else if (2 * y + vy > h2)
                vy = h2 - 2 * y;
            fw_mc16(&d->ref, x, y, vx, vy, rc, ly, cb, cr);
            for (int j = 0; j < 8; j++) memcpy(preds[k] + j * 8, ly + ((k >> 1) * 8 + j) * 16 + (k & 1) * 8, 8);
        }
        int cx = chroma4(mv[0][0] + mv[1][0] + mv[2][0] + mv[3][0]), cy = chroma4(mv[0][1] + mv[1][1] + mv[2][1] + mv[3][1]);
        fw_mc16(&d->ref, x, y, 4 * (cx >> 1) + (cx & 1), 4 * (cy >> 1) + (cy & 1), rc, ly, cb, cr);
    }
    memcpy(preds[4], cb, 64);
    memcpy(preds[5], cr, 64);
    for (int n = 0; n < 6; n++) {
        int16_t res[64] = {0};
        if ((cbp >> (5 - n)) & 1) {
            int lv[64] = {0}, f[64];
            if (!b || read_coeffs(d, b, 0, 0, 0, 0, lv) < 0) return -1;
            int q = d->qp;
            if (!d->vol.quant_type) {
                int qadd = (q - 1) | 1;
                for (int j = 0; j < 64; j++) {
                    int l = s16(lv[j]);
                    f[j] = s16(l * 2 * q + sgn(l) * qadd);
                }
            } else {
                int sum = 0;
                for (int j = 0; j < 64; j++) {
                    int l = s16(lv[j]);
                    int xx = sat16(l * 2) + sgn(l);
                    xx = sat16(sat16(xx * q) * d->vol.inter_matrix[j]);
                    f[j] = clampi(xx / 16, -2048, 2047);
                    sum += f[j];
                }
                if (!(sum & 1)) f[63] += (f[63] & 1) ? -1 : 1;
            }
            for (int j = 0; j < 64; j++) res[j] = (int16_t)f[j];
            idct(res);
        }
        put_block(d, mx, my, n, res, preds[n]);
    }
    return 0;
}

static int mb_p(pyv_dec *d, bits *b, size_t mx, size_t my) {
    mbinfo *m = &d->mbs[my * d->mbw + mx];
    int c;
    for (;;) {
        if (bit1(b)) {
            int zero[4][2] = {{0}};
            memset(m, 0, sizeof *m);
            m->slice = d->slice;
            m->qp = d->qp;
            return inter_recon(d, mx, my, zero, 0, 0, NULL);
        }
        c = vlc_read(&d->inter_mcbpc, b);
        if (c < 0) return -1;
        if (c != 20) break;
    }
    int cbpc = c & 3, intra = c & 4, dq = c & 8, four = c & 16;
    if (intra) return intra_mb(d, b, mx, my, cbpc, dq != 0);
    int cbpy = vlc_read(&d->cbpy, b);
    if (cbpy < 0) return -1;
    cbpy ^= 15;
    if (dq && update_qp(d, b) < 0) return -1;
    int cbp = cbpy << 2 | cbpc;
    memset(m, 0, sizeof *m);
    m->slice = d->slice;
    m->qp = d->qp;
    int mv[4][2];
    if (four) {
        for (int k = 0; k < 4; k++) {
            int px, py;
            mv_pred(d, mx, my, k, &px, &py);
            if (read_mv(d, b, px, &mv[k][0]) < 0 || read_mv(d, b, py, &mv[k][1]) < 0) return -1;
            m->mv[k][0] = mv[k][0];
            m->mv[k][1] = mv[k][1];
        }
    } else {
        int px, py, vx, vy;
        mv_pred(d, mx, my, 0, &px, &py);
        if (read_mv(d, b, px, &vx) < 0 || read_mv(d, b, py, &vy) < 0) return -1;
        for (int k = 0; k < 4; k++) {
            mv[k][0] = m->mv[k][0] = vx;
            mv[k][1] = m->mv[k][1] = vy;
        }
    }
    return inter_recon(d, mx, my, mv, four != 0, cbp, b);
}

static int mb_i(pyv_dec *d, bits *b, size_t mx, size_t my) {
    int c;
    do {
        c = vlc_read(&d->intra_mcbpc, b);
        if (c < 0) return -1;
    } while (c == 8);
    return intra_mb(d, b, mx, my, c & 3, (3 + (c >> 2)) == 4);
}

static int resync_next(const pyv_dec *d, const bits *b, int is_i) {
    int n = 8 - (int)(b->pos & 7);
    if (peek(b, n) != (1u << (n - 1)) - 1) return 0;
    bits q = *b;
    q.pos += (size_t)n;
    int len = is_i ? 17 : 16 + d->fcode;
    return peek(&q, len) == 1;
}

static int vop(pyv_dec *d, bits *b) {
    if (!d->have_vol) return -1;
    uint32_t t = get(b, 2);
    if (t > 1) return -1;
    int is_i = t == 0;
    while (bit1(b)) {
    }
    get(b, 1);
    get(b, d->vol.time_inc_bits);
    get(b, 1);
    if (!bit1(b)) return PYV_NOT_CODED;
    if (!is_i) {
        if (!d->have_ref) return -1;
        d->rounding = (int)get(b, 1);
    } else
        d->rounding = 0;
    d->dc_thr = DC_THRESHOLD[get(b, 3)];
    d->qp = (int)get(b, 5);
    if (!d->qp) return -1;
    if (!is_i) {
        d->fcode = (int)get(b, 3);
        if (!d->fcode) return -1;
    }
    size_t nmb = d->mbw * d->mbh;
    for (size_t i = 0; i < nmb; i++) d->mbs[i].slice = NONE;
    d->slice = 0;
    int nbits = 0;
    while ((1ul << nbits) < (nmb > 1 ? nmb : 2)) nbits++;
    for (size_t mb = 0; mb < nmb; mb++) {
        if (!d->vol.resync_disable && mb > 0 && resync_next(d, b, is_i)) {
            b->pos += (size_t)(8 - (b->pos & 7));
            b->pos += (size_t)(is_i ? 17 : 16 + d->fcode);
            size_t n = get(b, nbits);
            if (n >= nmb || n < mb) return -1;
            mb = n;
            d->qp = (int)get(b, 5);
            if (!d->qp) return -1;
            if (bit1(b)) {
                while (bit1(b)) {
                }
                get(b, 1);
                get(b, d->vol.time_inc_bits);
                get(b, 1);
                get(b, 5);
                if (!is_i) get(b, 3);
            }
            d->slice++;
        }
        size_t mx = mb % d->mbw, my = mb / d->mbw;
        if ((is_i ? mb_i(d, b, mx, my) : mb_p(d, b, mx, my)) < 0) return -1;
        if (bits_left(b) < 0) return -1;
        if (d->fw_error) { /* 0x4a20: VOP abandoned, the rest keeps the buffer's old content */
            d->fw_error = 0;
            break;
        }
    }
    picture tmp = d->cur;
    d->cur = d->ref;
    d->ref = tmp;
    d->have_ref = 1;
    return is_i ? PYV_FRAME_I : PYV_FRAME_P;
}

int pyv_dec_decode(pyv_dec *d, const uint8_t *data, size_t n) {
    int res = PYV_HEADERS;
    for (size_t i = 0; i + 4 <= n; i++) {
        if (data[i] || data[i + 1] || data[i + 2] != 1) continue;
        uint8_t code = data[i + 3];
        bits b = {data, n, (i + 4) * 8};
        if (code >= 0x20 && code <= 0x2f) {
            vol_t v;
            if (parse_vol(&b, &v) < 0) return -1;
            set_vol(d, &v);
        } else if (code == 0xb6)
            return vop(d, &b);
        i += 3;
    }
    return res;
}

int pyv_dec_picture(const pyv_dec *d, uint32_t *w, uint32_t *h, size_t *stride, const uint8_t **y, const uint8_t **cb, const uint8_t **cr) {
    if (!d->have_ref) return -1;
    *w = d->ref.w;
    *h = d->ref.h;
    *stride = d->ref.stride;
    *y = d->ref.y;
    *cb = d->ref.cb;
    *cr = d->ref.cr;
    return 0;
}
