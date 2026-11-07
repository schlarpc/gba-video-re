/* vxgb_residual.c -- residual decoder of the VXGB codec (GBA Video Rev 5: Shark Tale, Shrek 2).
 *
 * Exact model of the Rev 5 player's IWRAM 0x03005eb0 residual_block and its callee 0x030062e0
 * (4x4 coefficient decode + dequant + IDCT + add). Spec: docs/vxgb.md section 5.
 *
 * Coefficients use the H.264 CAVLC code tables (coeff_token, total_zeros, run_before) with a
 * VXGB-specific syntax order and level code; the tables are copied from the ROM at build time
 * by gen_vxgb_tables.py (vxgb_tables.h), in the player's own layout, and walked exactly as the
 * ARM walks them. Every bit-reader operation below mirrors one ARM instruction group so that
 * the refill phase (bits.h) is register-exact.
 */
#include <stdint.h>
#include <string.h>
#include "residual.h"
#include "vxgb_tables.h"

#define NZY_STRIDE 0x41
#define NZC_STRIDE 0x21

static const uint8_t zz[16] = { 0, 1, 4, 8, 5, 2, 3, 6, 9, 12, 13, 10, 7, 11, 14, 15 };

static inline uint8_t clip8(int32_t v) { return v < 0 ? 0 : v > 255 ? 255 : (uint8_t)v; }

/* consume n bits (register shift: n >= 32 clears buf) with one refill, like
 * "lsl r3,r3,rN; subs r2,r2,rN; bllt refill" */
static inline void skip(vx_bits *bs, unsigned n)
{
    bs->buf = n >= 32 ? 0 : bs->buf << n;
    bs->cnt -= (int)n;
    if (bs->cnt < 0) vx_refill(bs);
}

/* table walk "len[16] + lut[1 << width]" (total_zeros 0x03006390, run_before 0x03006464) */
static inline unsigned vlc_tab(vx_bits *bs, const uint8_t *t, int width)
{
    unsigned v = t[16 + (bs->buf >> (32 - width))];
    skip(bs, t[v]);
    return v;
}

/* 0x030062e0: one 4x4 block. nC = context, *nz receives TotalCoeff. step 1 = luma, 2 = U/V.
 * Returns 0, or <0 for a code the ARM cannot survive (out-of-range writes / endless loops). */
static int coef4x4(vx_bits *bs, const int32_t s[8], uint8_t *dst, int step, unsigned nC, uint8_t *nz)
{
    int32_t c[16];
    int p = 16;                     /* write pointer: c[--p], highest scan position first */
    int t1, tc, zl, sl = 0;

    /* ---- coeff_token */
    if (nC >= 8) {                  /* 0x03006270: 6-bit FLC */
        unsigned v = bs->buf >> 26;
        skip(bs, 6);
        t1 = v & 3;
        tc = (int)(v >> 2) + 1;
        if (t1 > tc) tc = 0;
    } else {                        /* nibble tree, up to 4 levels */
        uint32_t peek = bs->buf;
        int node = vxgb_ct_node[nC], sym = 0;
        for (int lvl = 0; lvl < 4; lvl++) {
            sym = vxgb_ct_tree[node * 16 + (peek >> 28)];
            if (sym >= 0 || lvl == 3) break;
            peek <<= 4;
            node = -sym;
            if (node >= 49) return -3;   /* the tree has no such node (never in valid codes) */
        }
        if (sym < 0) return -3;     /* no codeword: ARM reads lengths before the table */
        skip(bs, vxgb_ct_len[vxgb_ct_lenoff[nC] + sym]);
        t1 = sym & 3;
        tc = sym >> 2;
    }
    *nz = (uint8_t)tc;
    if (tc == 0) return 0;

    /* ---- total_zeros (0x0300637c) */
    if (tc == 16) zl = 0;
    else {
        zl = (int)vlc_tab(bs, vxgb_tz_tab[tc - 1], vxgb_tz_width[tc - 1]);
        int pad = 16 - (tc + zl);
        if (pad < 0) return -4;     /* ARM: zero-fill loop runs ~2^32 times */
        while (pad--) c[--p] = 0;
    }

    /* ---- levels interleaved with run_before (0x030063e0 .. 0x030064cc) */
    for (;;) {
        int32_t v;
        if (--t1 >= 0) {            /* trailing one: sign bit only */
            v = (bs->buf >> 31) ? -1 : 1;
            skip(bs, 1);
        } else {
            unsigned z = 0;
            uint32_t lc;
            int neg;
            while (!(bs->buf & 0x80000000u)) {
                if (++z >= 32) return -5;   /* ARM loops forever on an all-zero buffer */
                bs->buf <<= 1;
            }
            bs->buf <<= 1;          /* the terminating 1 */
            bs->cnt -= (int)z + 1;
            if (bs->cnt < 0) vx_refill(bs);
            if (z == 15) {          /* escape 0x030064d0: 11-bit suffix + sign */
                lc = (bs->buf >> 21) + (15u << sl);
                neg = (bs->buf >> 20) & 1;
                if ((int32_t)lc > (int32_t)vxgb_level_thr[sl]) sl++;
                skip(bs, 12);
            } else {
                unsigned n = (unsigned)sl + 1;
                lc = (sl ? bs->buf >> (32 - sl) : 0) + (z << sl);
                neg = (bs->buf >> (31 - sl)) & 1;
                if ((int32_t)lc > (int32_t)vxgb_level_thr[sl]) sl++;
                skip(bs, n);
            }
            v = (int32_t)(lc + 1);
            if (neg) v = -v;
        }
        if (p <= 0) return -6;
        c[--p] = v;
        if (--tc == 0) break;
        if (zl == 0) continue;
        {
            unsigned run;
            if (zl > 6) {           /* 0x030062a0 */
                unsigned n = 1;
                uint32_t t = bs->buf;
                while (!(t & 0x80000000u)) {
                    if (++n > 32) return -5;
                    t <<= 1;
                }
                if (n >= 4) { run = n + 3; skip(bs, n); }
                else { run = 7 - (bs->buf >> 29); skip(bs, 3); }
            } else
                run = vlc_tab(bs, vxgb_rb_tab[zl - 1], vxgb_rb_width[zl - 1]);
            if (run == 0) continue;
            zl -= (int)run;
            if (p - (int)run < 0) return -6;
            while (run--) c[--p] = 0;
        }
    }
    while (zl-- > 0) {
        if (p <= 0) return -6;
        c[--p] = 0;
    }

    /* ---- dequant (per position: s[(y&1)*4 + x]) + H.264 4x4 IDCT + add (0x03006524..) */
    {
        int32_t m[16], t[16];
        for (int i = 0; i < 16; i++) {
            int r = zz[i];
            m[r] = (int32_t)((uint32_t)c[i] * (uint32_t)s[((r >> 2) & 1) * 4 + (r & 3)]);
        }
        for (int x = 0; x < 4; x++) {
            uint32_t r0 = m[x], r1 = m[4 + x], r2 = m[8 + x], r3 = m[12 + x];
            uint32_t e0 = r0 + r2, e1 = r0 - r2;
            uint32_t e2 = (uint32_t)((int32_t)r1 >> 1) - r3, e3 = r1 + (uint32_t)((int32_t)r3 >> 1);
            t[x] = (int32_t)(e0 + e3);
            t[4 + x] = (int32_t)(e1 + e2);
            t[8 + x] = (int32_t)(e1 - e2);
            t[12 + x] = (int32_t)(e0 - e3);
        }
        for (int y = 0; y < 4; y++) {
            uint32_t r0 = t[4 * y], r1 = t[4 * y + 1], r2 = t[4 * y + 2], r3 = t[4 * y + 3];
            uint32_t e0 = r0 + r2, e1 = r0 - r2;
            uint32_t e2 = (uint32_t)((int32_t)r1 >> 1) - r3, e3 = r1 + (uint32_t)((int32_t)r3 >> 1);
            uint32_t o[4] = { e0 + e3, e1 + e2, e1 - e2, e0 - e3 };
            for (int x = 0; x < 4; x++) {
                uint8_t *q = dst + y * VX_STRIDE + x * step;
                *q = clip8(*q + ((int32_t)(o[x] + 32u) >> 6));
            }
        }
    }
    return 0;
}

static inline unsigned nc_of(const uint8_t *nz, int stride)
{
    return ((unsigned)nz[-1] + nz[-stride] + 1) >> 1;
}

int vxgb_residual(vx_bits *bs, const int32_t s[8], uint8_t *Y, uint8_t *UV, uint8_t *nzy,
                  uint8_t *nzc, int w, int h)
{
    for (int by = 0; by < h; by += 8) {
        for (int bx = 0; bx < w; bx += 8) {
            uint8_t *y = Y + by * VX_STRIDE + bx;
            uint8_t *uv = UV + (by >> 1) * VX_STRIDE + bx;
            uint8_t *ny = nzy + (by >> 2) * NZY_STRIDE + (bx >> 2);
            uint8_t *nc = nzc + (by >> 3) * NZC_STRIDE + (bx >> 3);
            uint32_t idx = vx_ue(bs);
            unsigned cbp;
            int r;
            if (idx >= 32) return -2;   /* ARM indexes past the table into code bytes */
            cbp = vxgb_cbp[idx];
            for (int k = 0; k < 4; k++) {
                uint8_t *yy = y + (k >> 1) * 4 * VX_STRIDE + (k & 1) * 4;
                uint8_t *nn = ny + (k >> 1) * NZY_STRIDE + (k & 1);
                if (!(cbp & (1u << k))) { *nn = 0; continue; }
                r = coef4x4(bs, s, yy, 1, nc_of(nn, NZY_STRIDE), nn);
                if (r < 0) return r;
            }
            if (!(cbp & 0x10)) *nc = 0;
            else {
                unsigned ncx = nc_of(nc, NZC_STRIDE), tu;
                r = coef4x4(bs, s, uv, 2, ncx, nc);       /* U, then V with the same nC */
                if (r < 0) return r;
                tu = *nc;
                r = coef4x4(bs, s, uv + 1, 2, ncx, nc);
                if (r < 0) return r;
                *nc = (uint8_t)((tu + *nc + 1) >> 1);
            }
        }
    }
    return 0;
}
