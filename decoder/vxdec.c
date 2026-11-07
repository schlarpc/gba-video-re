/* vxdec.c -- reference decoder for the VX++ video stream of the Shrek GBA Video cartridge.
 *
 * Exact model of the player's hand-written ARM decoder (IWRAM 0x03000520 and callees).
 * Spec: tmp/shrek/notes/video.md. Every function cites the ARM routine it models.
 *
 * Memory model: the decoder works inside a 256 KiB image of GBA EWRAM, at the same
 * addresses as the player (frame slot i at 0x020032c8 + i*0xF000, MV array 0x0203F2C8).
 * Reads of reference pixels are address-wrapped like EWRAM's mirroring, so any read
 * outside a frame (bad MVs, intra edge reads) returns exactly what the player would read.
 *
 *   vxdec ROM_OR_VXFILE [--start-frame N] [--count M] [--out DIR] [--y4m FILE] [--rgb]
 *                       [--md5]
 * Output per frame: DIR/NNNNN.yuv = the 0xF000-byte decoder slot (Y 256x160 stride 256,
 * then interleaved U,V 256x80), optional DIR/NNNNN.bgr555 = the player's mode-3 blit.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bits.h"
#include "residual.h"
#include "fill_tables.h"
#include "vxdec.h"
#include "vxgb_tables.h"

#define EWMASK 0x3FFFF
#define SLOT0 0x32C8u
#define SLOTSZ 0xF000u
#define MVARR 0x3F2C8u
#define MVSTART 0x3F314u   /* 0x0203F314: MV entry of MB (0,0) */
#define MVSTRIDE 0x24

/* ------------------------------------------------------------------ memory */
static inline uint8_t rd8(vx_dec *d, uint32_t a)
{
    return d->ew[a & EWMASK];
}
static inline uint8_t *px(vx_dec *d, uint32_t a)   /* in-frame pointer (output block) */
{
    return &d->ew[a & EWMASK];
}
static inline void wr8(vx_dec *d, uint32_t a, uint32_t v)
{
    d->ew[a & EWMASK] = (uint8_t)v;
}
static inline int16_t rd16s(vx_dec *d, uint32_t a)
{
    return (int16_t)(rd8(d, a) | (rd8(d, a + 1) << 8));
}
static inline void wr16(vx_dec *d, uint32_t a, uint32_t v)
{
    wr8(d, a, v);
    wr8(d, a + 1, v >> 8);
}

/* diagnostics: count motion vectors whose source block leaves the picture */
static void note_mv(vx_dec *d, int w, int h, int mx, int my)
{
    int bx = (int)(d->yoff & 0xff) + mx, by = (int)(d->yoff >> 8) + my;
    if (bx < 0 || by < 0 || bx + w > d->width || by + h > d->height) d->oob++;
}

/* ------------------------------------------------------------------ predictors */
/* MC copy, integer pel. 0x03001578 (16 wide), 0x03002910 (8), 0x03003b00 (4), 0x03004964 (2).
 * MV (mx,my) in luma pels. Luma source = ref + off + mx + my*256.
 * Chroma source = refUV + uvoff + (mx & ~1) + (my & ~1)*128 (= chroma pel offset floor(mv/2)).
 * Also stores the MV (mx + my*256, 16 bits) into the current MB's MV-array entry. */
static void mc_copy(vx_dec *d, int ref, int w, int h, int mx, int my)
{
    uint32_t rY = d->refY[ref], rC = d->refC[ref];
    int32_t lo = mx + my * 256;
    int32_t co = (mx & ~1) + (my & ~1) * 128;
    wr16(d, d->mvp, (uint32_t)lo);
    note_mv(d, w, h, mx, my);
    for (int y = 0; y < h; y++) {
        uint32_t s = rY + d->yoff + lo + y * 256, t = d->outY + d->yoff + y * 256;
        for (int x = 0; x < w; x++) wr8(d, t + x, rd8(d, s + x));
    }
    for (int y = 0; y < h / 2; y++) {
        uint32_t s = rC + d->uvoff + co + y * 256, t = d->outC + d->uvoff + y * 256;
        for (int x = 0; x < w; x++) wr8(d, t + x, rd8(d, s + x));
    }
}

static inline int clip255(int v) { return v < 0 ? 0 : v > 255 ? 255 : v; }

/* mode 5: absolute MV from ref0 + DC offsets. 0x03001854 / 0x03002b9c / 0x03003d58 / 0x03004aac.
 * Syntax: se mx, se my, se dY, se dU, se dV. pixel = clip(ref + 2*d). MV array NOT written. */
static void mc_dcoff(vx_dec *d, int w, int h)
{
    int mx = vx_se(&d->bs);
    int my = vx_se(&d->bs);
    int dy = vx_se(&d->bs), du = vx_se(&d->bs), dv = vx_se(&d->bs);
    uint32_t rY = d->refY[0], rC = d->refC[0];
    int32_t lo = mx + my * 256;
    int32_t co = (mx & ~1) + (my & ~1) * 128;
    note_mv(d, w, h, mx, my);
    for (int y = 0; y < h; y++) {
        uint32_t s = rY + d->yoff + lo + y * 256, t = d->outY + d->yoff + y * 256;
        for (int x = 0; x < w; x++) wr8(d, t + x, clip255(rd8(d, s + x) + 2 * dy));
    }
    for (int y = 0; y < h / 2; y++) {
        uint32_t s = rC + d->uvoff + co + y * 256, t = d->outC + d->uvoff + y * 256;
        for (int x = 0; x < w; x += 2) {
            wr8(d, t + x, clip255(rd8(d, s + x) + 2 * du));
            wr8(d, t + x + 1, clip255(rd8(d, s + x + 1) + 2 * dv));
        }
    }
}

/* Execute a generated fill op list (see gen_fill.py). base[0] = luma block address,
 * base[1] = chroma block address. v = corner value for the inner (plane-mode) fills. */
static void run_fill(vx_dec *d, const vx_fillop *ops, int n, uint32_t yb, uint32_t cb, int v)
{
    int32_t val[400];
    for (int i = 0; i < n; i++) {
        const vx_fillop *o = &ops[i];
        uint32_t b = o->plane ? cb : yb;
        int32_t a = o->aref == 2 ? v : o->aref ? val[o->a] : rd8(d, b + o->a);
        int32_t c = o->bref == 2 ? v : o->bref ? val[o->b] : rd8(d, b + o->b);
        int32_t r;
        if (o->kind == 1) {
            r = ((a + c + 1) >> 1) + 2 * vx_se(&d->bs);
            if (r < 0 || r > 255) d->fill_ovf++;
        }
        else
            r = (a + c) >> 1;
        val[i] = r;
        wr8(d, b + o->dst, (uint32_t)r);
    }
}

/* mode 4 "bilinear" intra (Mobiclip-style recursive bisection). Per plane: corner
 * pixel (w-1,h-1) = ((T[w-1] + L[h-1] + 1) >> 1) + 2*se, then the block is filled by
 * recursive midpoint averaging. Luma first, then U, then V (one se each). */
static void intra_bilinear(vx_dec *d, int w, int h)
{
    run_fill(d, fill_m4[w][h], fill_m4_n[w][h], d->outY + d->yoff, d->outC + d->uvoff, 0);
}

/* --- intra 16x16-style whole-block predictors, generic WxH (0x03000884) */
static void ipred_luma(vx_dec *d, int mode, int w, int h)
{
    uint32_t b = d->outY + d->yoff;
    uint32_t off = d->yoff;
    switch (mode) {
    case 0: /* V 0x030009b8 */
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) wr8(d, b + y * 256 + x, rd8(d, b - 256 + x));
        break;
    case 1: /* H 0x03000a04 */
        for (int y = 0; y < h; y++) {
            uint8_t l = rd8(d, b + y * 256 - 1);
            for (int x = 0; x < w; x++) wr8(d, b + y * 256 + x, l);
        }
        break;
    case 2: { /* DC 0x03000a54; availability from the frame position only */
        static const uint8_t lg[5] = {0, 2, 3, 0, 4};
        int dc, t = 0, l = 0;
        if (off == 0) dc = 0x80;
        else {
            if (off & 0xff00) {
                for (int x = 0; x < w; x++) t += rd8(d, b - 256 + x);
                t = (t + (w >> 1)) >> lg[w >> 2];
            }
            if (off & 0xff) {
                for (int y = 0; y < h; y++) l += rd8(d, b + y * 256 - 1);
                l = (l + (h >> 1)) >> lg[h >> 2];
            }
            if (!(off & 0xff)) dc = t;            /* left column of the frame: top only */
            else if (!(off & 0xff00)) dc = l;     /* top row of the frame: left only */
            else dc = (t + l + 1) >> 1;
        }
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) wr8(d, b + y * 256 + x, dc);
        break;
    }
    case 3: { /* "plane" 0x03000ba4: corner = (T[w-1]+L[h-1]+1)>>1, then bisection fill */
        int c = (rd8(d, b + w - 1 - 256) + rd8(d, b + (h - 1) * 256 - 1) + 1) >> 1;
        wr8(d, b + (h - 1) * 256 + w - 1, c);
        run_fill(d, fill_pl[w][h], fill_pl_n[w][h], b, 0, c);
        break;
    }
    default:
        d->err++;
    }
}

static void ipred_chroma(vx_dec *d, int mode, int w, int h)
{
    uint32_t b = d->outC + d->uvoff;
    uint32_t off = d->uvoff;
    int cw = w >> 1, ch = h >> 1;  /* chroma pairs / rows */
    switch (mode) {
    case 0: { /* DC 0x03000c08 */
        static const uint8_t lg[5] = {0, 1, 2, 0, 3};
        int du, dv, tu = 0, tv = 0, lu = 0, lv = 0;
        if (off == 0) du = dv = 0x80;
        else {
            if (off & 0xff00) {
                for (int x = 0; x < cw; x++) { tu += rd8(d, b - 256 + 2 * x); tv += rd8(d, b - 255 + 2 * x); }
                tu = (tu + (w >> 2)) >> lg[w >> 2];
                tv = (tv + (w >> 2)) >> lg[w >> 2];
            }
            if (off & 0xff) {
                for (int y = 0; y < ch; y++) { lu += rd8(d, b + y * 256 - 2); lv += rd8(d, b + y * 256 - 1); }
                lu = (lu + (h >> 2)) >> lg[h >> 2];
                lv = (lv + (h >> 2)) >> lg[h >> 2];
            }
            if (!(off & 0xff)) { du = tu; dv = tv; }
            else if (!(off & 0xff00)) { du = lu; dv = lv; }
            else { du = (tu + lu + 1) >> 1; dv = (tv + lv + 1) >> 1; }
        }
        for (int y = 0; y < ch; y++)
            for (int x = 0; x < cw; x++) { wr8(d, b + y * 256 + 2 * x, du); wr8(d, b + y * 256 + 2 * x + 1, dv); }
        break;
    }
    case 1: /* H 0x03000d90 */
        for (int y = 0; y < ch; y++) {
            uint8_t u = rd8(d, b + y * 256 - 2), v = rd8(d, b + y * 256 - 1);
            for (int x = 0; x < cw; x++) { wr8(d, b + y * 256 + 2 * x, u); wr8(d, b + y * 256 + 2 * x + 1, v); }
        }
        break;
    case 2: /* V 0x03000dd4 */
        for (int y = 0; y < ch; y++)
            for (int x = 0; x < w; x++) wr8(d, b + y * 256 + x, rd8(d, b - 256 + x));
        break;
    case 3: { /* plane 0x03000e44 */
        uint32_t tr = b + w - 256, bl = b + ch * 256;     /* r8 = base+w ; base + h*128 */
        int cu = (rd8(d, tr - 2) + rd8(d, bl - 258) + 1) >> 1;
        int cv = (rd8(d, tr - 1) + rd8(d, bl - 257) + 1) >> 1;
        uint32_t br = b + w + ch * 256 - 258;
        wr8(d, br, cu);
        wr8(d, br + 1, cv);
        run_fill(d, fill_pc[w][h], fill_pc_n[w][h], 0, b, cu);
        run_fill(d, fill_pc[w][h], fill_pc_n[w][h], 0, b + 1, cv);
        break;
    }
    default:
        d->err++;
    }
}

/* mode 6: 0x03000884. ue luma mode, ue chroma mode, then predict. */
static void intra_16(vx_dec *d, int w, int h)
{
    int lm = vx_ue(&d->bs);
    int cm = vx_ue(&d->bs);
    ipred_luma(d, lm, w, h);
    ipred_chroma(d, cm, w, h);
}

/* --- intra 4x4 predictors (table 0x030008d8), p = block address, off = frame offset */
#define P(x, y) rd8(d, p + (y) * 256 + (x))
#define S(x, y, v) wr8(d, p + (y) * 256 + (x), (v))
static void i4x4(vx_dec *d, int mode, uint32_t p, uint32_t off)
{
    int T[8], L[4], M = P(-1, -1);
    for (int i = 0; i < 8; i++) T[i] = P(i, -1);
    for (int i = 0; i < 4; i++) L[i] = P(-1, i);
    switch (mode) {
    case 0: /* V 0x03000ecc */
        for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) S(x, y, T[x]);
        break;
    case 1: /* H 0x03000eec */
        for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) S(x, y, L[y]);
        break;
    case 2: { /* DC 0x03000f90 */
        int s = 0, n = 1, dc;
        if (off == 0) dc = 0x80;
        else {
            if (off & 0xff) { s += L[0] + L[1] + L[2] + L[3] + 2; n++; }
            if (off & 0xff00) { s += T[0] + T[1] + T[2] + T[3] + 2; n++; }
            dc = s >> n;
        }
        for (int y = 0; y < 4; y++) for (int x = 0; x < 4; x++) S(x, y, dc);
        break;
    }
    case 3: /* diagonal down-left 0x03000fe0 (uses T[4..7] unconditionally) */
        for (int y = 0; y < 4; y++)
            for (int x = 0; x < 4; x++) {
                int k = x + y;
                int v = k == 6 ? (T[6] + 3 * T[7] + 2) >> 2 : (T[k] + 2 * T[k + 1] + T[k + 2] + 2) >> 2;
                S(x, y, v);
            }
        break;
    case 4: /* diagonal down-right 0x030010c4 */
        for (int y = 0; y < 4; y++)
            for (int x = 0; x < 4; x++) {
                int v;
                if (x > y) {
                    int k = x - y;  /* uses T[k-2], T[k-1], T[k] with T[-1] = M */
                    int a = k >= 2 ? T[k - 2] : M;
                    v = (a + 2 * T[k - 1] + T[k] + 2) >> 2;
                } else if (x < y) {
                    int k = y - x;
                    int a = k >= 2 ? L[k - 2] : M;
                    v = (a + 2 * L[k - 1] + L[k] + 2) >> 2;
                } else
                    v = (L[0] + 2 * M + T[0] + 2) >> 2;
                S(x, y, v);
            }
        break;
    case 5: { /* vertical-right 0x030011ac */
        int a = (L[2] + L[0] + 2 * L[1] + 2) >> 2;
        int b = (L[1] + M + 2 * L[0] + 2) >> 2;
        int c = (L[0] + T[0] + 2 * M + 2) >> 2;
        int e = (M + T[0] + 1) >> 1;
        int f = (M + T[1] + 2 * T[0] + 2) >> 2;
        int g = (T[0] + T[1] + 1) >> 1;
        int hh = (T[0] + T[2] + 2 * T[1] + 2) >> 2;
        int i = (T[2] + T[1] + 1) >> 1;
        int j = (T[1] + T[3] + 2 * T[2] + 2) >> 2;
        int k = (T[2] + T[3] + 1) >> 1;
        S(0, 3, a); S(0, 2, b); S(0, 1, c); S(1, 3, c); S(0, 0, e); S(1, 2, e);
        S(1, 1, f); S(2, 3, f); S(1, 0, g); S(2, 2, g); S(2, 1, hh); S(3, 3, hh);
        S(2, 0, i); S(3, 2, i); S(3, 1, j); S(3, 0, k);
        break;
    }
    case 6: { /* horizontal-down 0x030012b0 */
        int a = (L[3] + L[2] + 1) >> 1;
        int b = (L[3] + L[1] + 2 * L[2] + 2) >> 2;
        int c = (L[2] + L[1] + 1) >> 1;
        int e = (L[2] + L[0] + 2 * L[1] + 2) >> 2;
        int f = (L[1] + L[0] + 1) >> 1;
        int g = (L[1] + M + 2 * L[0] + 2) >> 2;
        int hh = (L[0] + M + 1) >> 1;
        int i = (L[0] + T[0] + 2 * M + 2) >> 2;
        int j = (M + T[1] + 2 * T[0] + 2) >> 2;
        int k = (T[0] + T[2] + 2 * T[1] + 2) >> 2;
        S(0, 3, a); S(1, 3, b); S(0, 2, c); S(2, 3, c); S(1, 2, e); S(3, 3, e);
        S(0, 1, f); S(2, 2, f); S(1, 1, g); S(3, 2, g); S(0, 0, hh); S(2, 1, hh);
        S(1, 0, i); S(3, 1, i); S(2, 0, j); S(3, 0, k);
        break;
    }
    case 7: { /* vertical-left 0x030013b4 */
        {
            int q33 = (T[6] + T[4] + 2 * T[5] + 2) >> 2;
            int q32 = (T[5] + T[4] + 1) >> 1;
            int t3 = T[3];
            int q13_32 = (T[5] + t3 + 2 * T[4] + 2) >> 2;  /* (3,1),(2,3) */
            int q03_22 = (t3 + T[4] + 1) >> 1;             /* (3,0),(2,2) */
            int q12_31 = (T[4] + T[2] + 2 * t3 + 2) >> 2;  /* (2,1),(1,3) */
            int q02_21 = (T[2] + t3 + 1) >> 1;             /* (2,0),(1,2) */
            int q11_30 = (t3 + T[1] + 2 * T[2] + 2) >> 2;  /* (1,1),(0,3) */
            int q01_20 = (T[2] + T[1] + 1) >> 1;           /* (1,0),(0,2) */
            int q10 = (T[2] + T[0] + 2 * T[1] + 2) >> 2;   /* (0,1) */
            int q00 = (T[1] + T[0] + 1) >> 1;              /* (0,0) */
            S(3, 3, q33); S(3, 2, q32);
            S(3, 1, q13_32); S(2, 3, q13_32);
            S(3, 0, q03_22); S(2, 2, q03_22);
            S(2, 1, q12_31); S(1, 3, q12_31);
            S(2, 0, q02_21); S(1, 2, q02_21);
            S(1, 1, q11_30); S(0, 3, q11_30);
            S(1, 0, q01_20); S(0, 2, q01_20);
            S(0, 1, q10); S(0, 0, q00);
        }
        break;
    }
    case 8: { /* horizontal-up 0x030014b0 */
        S(0, 0, (L[0] + L[1] + 1) >> 1);
        S(1, 0, (L[0] + L[2] + 2 * L[1] + 2) >> 2);
        S(2, 0, (L[1] + L[2] + 1) >> 1); S(0, 1, (L[1] + L[2] + 1) >> 1);
        S(3, 0, (L[1] + L[3] + 2 * L[2] + 2) >> 2); S(1, 1, (L[1] + L[3] + 2 * L[2] + 2) >> 2);
        S(2, 1, (L[2] + L[3] + 1) >> 1); S(0, 2, (L[2] + L[3] + 1) >> 1);
        S(3, 1, (L[3] + L[2] + 2 * L[3] + 2) >> 2); S(1, 2, (L[3] + L[2] + 2 * L[3] + 2) >> 2);
        S(0, 3, L[3]); S(2, 2, L[3]); S(3, 2, L[3]); S(1, 3, L[3]); S(2, 3, L[3]); S(3, 3, L[3]);
        break;
    }
    default:
        d->err++;
    }
}
#undef P
#undef S

/* mode 7: 0x030008fc. Per 4x4 in raster order: pred = min(top, left) of the mode
 * context (5-wide byte array 0x030008bc, borders fixed at 9 => only modes inside this
 * block are used); pred 9 -> 2. Bit 1: mode = pred. Bit 0: 3 bits rem, mode = rem + (rem >= pred).
 * Register-exact bit handling (lets cnt go to -12 before refilling, like the ARM). */
static void intra_4x4(vx_dec *d, int w, int h)
{
    uint8_t ctx[5 * 5];
    vx_bits *bs = &d->bs;
    memset(ctx, 9, sizeof ctx);
    for (int by = 0; by < h / 4; by++)
        for (int bx = 0; bx < w / 4; bx++) {
            uint8_t *c = &ctx[(by + 1) * 5 + bx + 1];
            int pred = c[-5] < c[-1] ? c[-5] : c[-1];
            int mode;
            uint32_t flag;
            if (pred == 9) pred = 2;
            flag = bs->buf >> 31;
            bs->buf <<= 1;
            if (flag) {
                bs->cnt -= 1;
                mode = pred;
            } else {
                int rem = bs->buf >> 29;
                bs->buf <<= 3;
                bs->cnt -= 4;
                mode = rem >= pred ? rem + 1 : rem;
            }
            *c = (uint8_t)mode;
            {
                uint32_t off = d->yoff + by * 4 * 256 + bx * 4;
                i4x4(d, mode, d->outY + off, off);
            }
            if (bs->cnt < -12) vx_refill(bs);
        }
    if (bs->cnt < 0) vx_refill(bs);
    ipred_chroma(d, vx_ue(bs), w, h);
}

/* ------------------------------------------------------------------ VXGB nonzero-count grids */
/* The Rev 5 player keeps the CAVLC nC context in two persistent byte grids (zeroed once by
 * player_run, never per frame or on seek): luma, one byte per 4x4, stride 0x41, MB (0,0) at
 * 0x0203eb8a; chroma, one byte per 4x4 chroma block (U and V share it), stride 0x21, MB (0,0)
 * at 0x0203f5d3. The 16x16/16x8/8x16/8x8 dispatchers zero the block's entries after every
 * mode without residual (0x03001b20, 0x03002040, 0x03003190, 0x03003720), except a split
 * into 8x8-or-larger halves, which zero (or code) their own entries; blocks smaller than
 * 8x8 never touch the grids, and split pointers only move for 8-row / 8-column halves. */
#define VXGB_NZY_START 0x3EB8Au
#define VXGB_NZY_STRIDE 0x41
#define VXGB_NZC_START 0x3F5D3u
#define VXGB_NZC_STRIDE 0x21

static void nz_clear(vx_dec *d, int w, int h)
{
    for (int y = 0; y < h / 4; y++)
        for (int x = 0; x < w / 4; x++) wr8(d, d->nzy + y * VXGB_NZY_STRIDE + x, 0);
    for (int y = 0; y < h / 8; y++)
        for (int x = 0; x < w / 8; x++) wr8(d, d->nzc + y * VXGB_NZC_STRIDE + x, 0);
}

/* ------------------------------------------------------------------ partition tree */
/* Dispatchers 0x03001dac (16x16), 0x03002184 (16x8), 0x030030f4 (8x16), 0x03003584 (8x8):
 * 24 modes; all smaller sizes: 12 modes (no residual variants). mode = ue(v).
 *  m%12: 0 copy ref0 pred-MV | 1 split top/bottom | 2 split left/right | 3 ref0 MV delta
 *        4 bilinear intra | 5 abs MV + DC offsets (ref0) | 6 intra "16x16" | 7 intra 4x4
 *        8 copy ref1 | 9 ref1 MV delta | 10 copy ref2 | 11 ref2 MV delta
 *  m >= 12: same, followed by residual 0x03005650(w, h).
 * The MV predictor (pmx,pmy) is the MB's median predictor, unchanged for all sub-blocks. */
static void block(vx_dec *d, int w, int h, int pmx, int pmy)
{
    uint32_t m = vx_ue(&d->bs);
    int big = w >= 8 && h >= 8;
    int res = 0;
    if (d->trace) fprintf(d->trace, "  blk %dx%d @%05x mode %u bit %u\n", w, h, d->yoff, m, vx_bits_pos(&d->bs));
    if (m >= (big ? 24u : 12u)) { d->err++; return; }
    if (m >= 12) { res = 1; m -= 12; }
    d->stat_modes[w == 16 ? 0 : w == 8 ? 1 : w == 4 ? 2 : 3][m]++;
    if ((m == 4 || ((m == 6 || m == 7) && w >= 4 && h >= 4)) && d->yoff < 256)
        d->edge_intra++;   /* intra block in picture row 0: may read row -1 (outside the picture) */
    switch (m) {
    case 0: mc_copy(d, 0, w, h, pmx, pmy); break;
    case 8: mc_copy(d, 1, w, h, pmx, pmy); break;
    case 10: mc_copy(d, 2, w, h, pmx, pmy); break;
    case 3: case 9: case 11: {
        int dx = vx_se(&d->bs);
        int dy = vx_se(&d->bs);
        mc_copy(d, m == 3 ? 0 : m == 9 ? 1 : 2, w, h, pmx + dx, pmy + dy);
        break;
    }
    case 1:
        if (h == 2) { d->err++; return; }
        block(d, w, h / 2, pmx, pmy);
        d->yoff += (h / 2) * 256; d->uvoff += (h / 4) * 256;
        if (h == 16) { d->nzy += 2 * VXGB_NZY_STRIDE; d->nzc += VXGB_NZC_STRIDE; }
        block(d, w, h / 2, pmx, pmy);
        d->yoff -= (h / 2) * 256; d->uvoff -= (h / 4) * 256;
        if (h == 16) { d->nzy -= 2 * VXGB_NZY_STRIDE; d->nzc -= VXGB_NZC_STRIDE; }
        break;
    case 2:
        if (w == 2) { d->err++; return; }
        block(d, w / 2, h, pmx, pmy);
        d->yoff += w / 2; d->uvoff += w / 2;
        if (w == 16) { d->nzy += 2; d->nzc += 1; }
        block(d, w / 2, h, pmx, pmy);
        d->yoff -= w / 2; d->uvoff -= w / 2;
        if (w == 16) { d->nzy -= 2; d->nzc -= 1; }
        break;
    case 4: intra_bilinear(d, w, h); break;
    case 5: mc_dcoff(d, w, h); break;
    case 6: if (w >= 4 && h >= 4) intra_16(d, w, h); break;   /* no-op (no bits) for 2-wide/2-high */
    case 7: if (w >= 4 && h >= 4) intra_4x4(d, w, h); break;
    }
    if (d->vxgb) {
        if (res) {
            if (vxgb_residual(&d->bs, d->gscale, px(d, d->outY + d->yoff), px(d, d->outC + d->uvoff),
                              px(d, d->nzy), px(d, d->nzc), w, h) < 0)
                d->err++;
        } else if (big && !(m == 1 && h == 16) && !(m == 2 && w == 16))
            nz_clear(d, w, h);   /* every leaf mode, and splits into blocks smaller than 8x8 */
    } else if (res) {
        if (vx_residual(&d->bs, &d->q, px(d, d->outY + d->yoff), px(d, d->outC + d->uvoff), w, h) < 0)
            d->err++;
    }
}

/* ------------------------------------------------------------------ frame */
static const uint8_t level_scale[6][3] = {   /* 0x03000680 */
    {10, 13, 16}, {11, 14, 18}, {13, 16, 20}, {14, 18, 23}, {16, 20, 25}, {18, 23, 29}};

static void set_qp(vx_dec *d, uint32_t qp)
{
    /* 0x03000730: tables 0x030006c8 (QP%6 * 12) and 0x030006f8 (QP/6), 48 entries each */
    d->q.qp = (int)qp;
    for (int i = 0; i < 3; i++) d->q.scale[i] = (int32_t)level_scale[qp % 6][i] << (qp / 6);
}

/* player_run: ctx+0x100 + 4i = deq[qp%6][i] << qp/6 (deq: EWRAM 0x02002a14, i = (y&1)*4 + x) */
static void vxgb_set_qp(vx_dec *d, int qp)
{
    for (int i = 0; i < 8; i++)
        d->gscale[i] = (int32_t)((uint32_t)vxgb_deq[(qp % 6) * 8 + i] << (qp / 6));
}

static inline void mv_unpack(int16_t v, int *x, int *y)
{
    int8_t lx = (int8_t)(v & 0xff);
    *x = lx;
    *y = (v >> 8) + (lx < 0);
}

static inline int median3(int a, int b, int c)
{
    int mn = a < b ? a : b, mx = a < b ? b : a;
    if (mn > c) mn = c;
    if (mx < c) mx = c;
    return a + b + c - mn - mx;
}

/* 0x03006d30 + 0x03000520: decode one frame into slot (dec_index & 3). */
void vx_decode_frame(vx_dec *d)
{
    uint32_t n = d->dec_index;
    for (int k = 0; k < 3; k++) {
        uint32_t s = (n - 1 - k) & 3;
        d->refY[k] = d->slot0 + s * SLOTSZ;
        d->refC[k] = d->slot0 + s * SLOTSZ + 0xA000;
    }
    d->outY = d->slot0 + (n & 3) * SLOTSZ;
    d->outC = d->outY + 0xA000;
    d->frame_bit = vx_bits_pos(&d->bs);
    if (!d->vxgb) {
        uint32_t q = vx_ue(&d->bs);   /* 0 = keep QP; else new QP */
        d->hdr_q = q;
        if (q) {
            if (q >= 48) d->err++;
            else set_qp(d, q);
        }
    }
    d->yoff = 0;
    d->uvoff = 0;
    for (int my = 0; my < d->height / 16; my++) {
        for (int mx = 0; mx < d->width / 16; mx++) {
            int ax, ay, bx, by, cx, cy;
            d->mvp = d->mvstart + my * MVSTRIDE + mx * 2;
            d->nzy = VXGB_NZY_START + my * 4 * VXGB_NZY_STRIDE + mx * 4;
            d->nzc = VXGB_NZC_START + my * 2 * VXGB_NZC_STRIDE + mx * 2;
            wr16(d, d->mvp, 0);
            mv_unpack(rd16s(d, d->mvp - 2), &ax, &ay);            /* left */
            mv_unpack(rd16s(d, d->mvp - MVSTRIDE), &bx, &by);     /* top */
            mv_unpack(rd16s(d, d->mvp - MVSTRIDE + 2), &cx, &cy); /* top-right */
            if (d->trace) fprintf(d->trace, " MB %d,%d bit %u\n", mx, my, vx_bits_pos(&d->bs));
            if (d->mb_bits) d->mb_bits[my * (d->width / 16) + mx] = vx_bits_pos(&d->bs);
            block(d, 16, 16, median3(ax, bx, cx), median3(ay, by, cy));
            d->yoff += 16;
            d->uvoff += 16;
        }
        d->yoff += 0x1000 - d->width;
        d->uvoff += 0x800 - d->width;
    }
    d->dec_index++;
}

const uint8_t *vx_frame_slot(vx_dec *d, uint32_t dec_index)
{
    return &d->ew[d->slot0 + (dec_index & 3) * SLOTSZ];
}

/* Player state after player_run (0x03006df4): Y planes 0, chroma 0x80, MV array 0,
 * decode index 3 (VX frame 0 goes to slot 3). ewram_image: optional EWRAM image
 * (ROM 0x8a00, 0x32c8 bytes) so reads before slot 0 return the player's bytes. */
void vx_dec_init(vx_dec *d, const uint8_t *stream, size_t size, int width, int height,
                 const uint8_t *ewram_image, size_t ewram_len)
{
    memset(d, 0, sizeof *d);
    if (ewram_image) memcpy(d->ew, ewram_image, ewram_len < 0x32c8 ? ewram_len : 0x32c8);
    for (int i = 0; i < 4; i++) {
        memset(&d->ew[SLOT0 + i * SLOTSZ], 0, 0xA000);
        memset(&d->ew[SLOT0 + i * SLOTSZ + 0xA000], 0x80, 0x5000);
    }
    memset(&d->ew[MVARR], 0, 0x318);
    d->slot0 = SLOT0;
    d->mvstart = MVSTART;
    d->width = width;
    d->height = height;
    d->dec_index = 3;
    d->stream = stream;
    d->size = size;
    vx_bits_init_start(&d->bs, stream, size);
}

/* VXGB player state after player_run (EWRAM 0x02000a00): frame slots at 0x02002b48 + i*0xF000
 * (Y 0, chroma 0x80), nnz grids 0x0203eb48 (0xa69 bytes) and 0x0203f5b1 (0x2b5), MV array
 * 0x0203f866 (0x318) all zero, decode index 3; QP = VX header +0x14 for the whole stream.
 * ewram_image: optional Rev 5 EWRAM image (ROM 0x8a00, 0x2b48 bytes). */
void vxgb_dec_init(vx_dec *d, const uint8_t *stream, size_t size, int width, int height, int qp,
                   const uint8_t *ewram_image, size_t ewram_len)
{
    memset(d, 0, sizeof *d);
    d->vxgb = 1;
    d->slot0 = 0x2B48;
    d->mvstart = 0x3F8B2;
    if (ewram_image) memcpy(d->ew, ewram_image, ewram_len < 0x2b48 ? ewram_len : 0x2b48);
    for (int i = 0; i < 4; i++) {
        memset(&d->ew[d->slot0 + i * SLOTSZ], 0, 0xA000);
        memset(&d->ew[d->slot0 + i * SLOTSZ + 0xA000], 0x80, 0x5000);
    }
    memset(&d->ew[0x3EB48], 0, 0xA69);
    memset(&d->ew[0x3F5B1], 0, 0x2B5);
    memset(&d->ew[0x3F866], 0, 0x318);
    d->width = width;
    d->height = height;
    d->dec_index = 3;
    d->stream = stream;
    d->size = size;
    d->q.qp = qp;
    vxgb_set_qp(d, qp);
    vx_bits_init_start(&d->bs, stream, size);
}

void vx_dec_seek(vx_dec *d, uint32_t bitoff)
{
    vx_bits_init(&d->bs, d->stream, d->size, bitoff);
}

/* ------------------------------------------------------------------ YUV -> BGR555 (0x03000370) */
/* Runtime IWRAM bytes 0x03000000..0x030003ff. The clamp table T is at 0x1f0; indices
 * -260..509 are used, so T reaches into the IRQ code before it and the blitter code after
 * it. That code includes the two immediates player_run patches for the stream's size
 * (0x38c `mov sl,#height`, 0x394 `mov sl,#width`), so T depends on the picture size. */
#include "iwram_lut.h"

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

/* ARM `mov sl,#v` as player_run encodes it (0x03006df4): rotated 8-bit immediate. */
static uint32_t mov_sl_imm(uint32_t v)
{
    uint32_t rot = 0x10;
    while (v > 0xff && !(v & 3)) { v >>= 2; rot--; }
    return 0xe3a0a000u | v | (rot & 0xf) << 8;
}

/* The blitter builds each horizontal pixel pair in one register by ADDING the six table
 * values, shifted (R | G<<5 | B<<10, then the odd pixel's fields << 16), and stores it as
 * one word. Table bytes past k = 384 are the blitter's own code and can exceed 31, so a
 * field can carry into the next one and into the odd pixel; modelled here exactly. */
static void blit(const uint8_t *iwram_lo, const uint8_t *slot, uint16_t *out, int width, int height)
{
    uint8_t iw[0x400];
    memcpy(iw, iwram_lo, sizeof iw);
    put32(iw + 0x38c, mov_sl_imm((uint32_t)height));
    put32(iw + 0x394, mov_sl_imm((uint32_t)width));
    const uint8_t *T = iw + 0x1f0;
    for (int y = 0; y < height; y++)
        for (int x = 0; x < width; x += 2) {
            int u = slot[0xA000 + (y >> 1) * 256 + x] - 128;
            int v = slot[0xA000 + (y >> 1) * 256 + x + 1] - 128;
            uint32_t w = 0;
            for (int k = 0; k < 2; k++) {
                int Y = slot[y * 256 + x + k] - 4 * ((x + k + y) & 1);
                w += ((uint32_t)T[Y + 2 * v] + ((uint32_t)T[Y - (v + (u >> 1))] << 5) +
                      ((uint32_t)T[Y + 2 * u] << 10)) << (16 * k);
            }
            out[y * width + x] = (uint16_t)w;
            out[y * width + x + 1] = (uint16_t)(w >> 16);
        }
}

void vx_yuv_to_bgr555(const uint8_t *slot, uint16_t *out, int width, int height)
{
    blit(vx_iwram_lo, slot, out, width, height);
}

/* Rev 5 player: same blitter, but its literal pool moved, so one byte inside the table's
 * index range (IWRAM 0x380 = T[400]) differs from the VX++ player. */
void vxgb_yuv_to_bgr555(const uint8_t *slot, uint16_t *out, int width, int height)
{
    blit(vxgb_iwram_lo, slot, out, width, height);
}

/* ------------------------------------------------------------------ test API (fuzz.py, ctypes) */
vx_dec *vx_new(void) { return calloc(1, sizeof(vx_dec)); }
void vx_free(vx_dec *d) { free(d); }
uint8_t *vx_ewram(vx_dec *d) { return d->ew; }
unsigned long vx_errors(vx_dec *d) { return d->err; }
uint32_t vx_bitpos(vx_dec *d) { return vx_bits_pos(&d->bs); }
void vx_set_mbbits(vx_dec *d, uint32_t *p) { d->mb_bits = p; }
void vx_yuv_to_bgr555_any(vx_dec *d, const uint8_t *slot, uint16_t *out)
{
    (d->vxgb ? vxgb_yuv_to_bgr555 : vx_yuv_to_bgr555)(slot, out, d->width, d->height);
}
int vxgb_supported(void) { return VXGB_HAVE_TABLES; }
