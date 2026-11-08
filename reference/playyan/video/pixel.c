/* Output stages of the video firmware (see crates/gbav-playyan/src/pixel.rs). */
#include "pyvideo.h"
#include "fw_tables.h"
#include <stdlib.h>
#include <string.h>

static int fl8(int a, int b) { return (a * b) >> 8; }
static int cl255(int v) { return v < 0 ? 0 : v > 255 ? 255 : v; }

static uint16_t yuv565(int y, int cb, int cr) {
    int yy = fl8(y - 16, 298), u = cb - 128, v = cr - 128;
    int g = fl8(u, -100) + fl8(v, -200);
    g = g < -32768 ? -32768 : g > 32767 ? 32767 : g;
    int lo = cl255(yy + fl8(u, 408)), hi = cl255(yy + fl8(v, 516)), gg = cl255(yy + g);
    return (uint16_t)((lo >> 3) | ((gg >> 2) << 5) | ((hi & 0xF8) << 8));
}

static uint16_t bright565(uint16_t p, uint32_t b) {
    uint32_t r = ((((uint32_t)p >> 11) << 3) * b) >> 8, g = (((((uint32_t)p >> 5) & 63) << 2) * b) >> 8,
             bl = ((((uint32_t)p & 31) << 3) * b) >> 8;
    r = (r > 255 ? 255 : r) & 0xf8;
    g = (g > 255 ? 255 : g) & 0xfc;
    bl = (bl > 255 ? 255 : bl) & 0xf8;
    return (uint16_t)((r << 8) | (g << 3) | (bl >> 3));
}

uint16_t pyv_rgb565_to_bgr555(uint16_t p) { return (uint16_t)((p >> 11) | ((p >> 6) & 31) << 5 | (p & 31) << 10); }

uint32_t pyv_garage_hash(const uint16_t *px, size_t n) {
    uint32_t h = 0;
    for (size_t i = 0; i < n; i++) {
        h += px[i];
        h = h << 1 | h >> 31;
    }
    return h;
}

/* ---- the general fit (0x2749a mode 2 -> 0x27980 -> 0x2788e -> 0x276de); see pixel.rs Fitter */
#define SCRATCH 24576
typedef struct {
    uint8_t *m;
    long n;
} mem_t;
static long rd(const mem_t *M, long a) { return a >= 0 && a < M->n ? M->m[a] : 0; }
static void wr(mem_t *M, long a, long v) {
    if (a >= 0 && a < M->n) M->m[a] = (uint8_t)v;
}
static void f_memcpy(mem_t *M, long d, long s, long n) {
    for (long i = 0; i < n; i++) wr(M, d + i, rd(M, s + i));
}
static long rem16(long x) { return ((x % 16) + 16) % 16; }
static void lerp_rows(mem_t *M, long d, long a, long n, long st, long w) {
    w &= 0xffffffffL;
    if (w <= 63) {
        f_memcpy(M, d, a, n);
        return;
    }
    w &= 0xffff;
    int fast = rem16(d + n) == 0; /* one byte too many unless the row ends 16-aligned */
    long len = n + (fast ? 0 : 1);
    uint8_t *v = malloc((size_t)len + 1);
    for (long i = 0; i < len; i++) {
        long x = rd(M, a + i), y = rd(M, a + st + i);
        v[i] = (uint8_t)(x + (((y - x) * w + 0x8000) >> 16));
    }
    for (long i = 0; i < len; i++) wr(M, d + i, v[i]);
    free(v);
}
static void vbox(mem_t *M, long d, long s, long nb, long st, long n) {
    if (n - 1 <= 1) {
        lerp_rows(M, d, s, nb, st, (n - 1) << 15);
        return;
    }
    while (n > 16) st <<= 1, n >>= 1;
    long r = PYV_BOXRECIP[n] & 0xffff;
    uint8_t *v = malloc((size_t)nb + 1);
    for (long i = 0; i < nb; i++) {
        long sm = 0;
        for (long k = 0; k < n; k++) sm += rd(M, s + k * st + i);
        v[i] = (uint8_t)((sm * r + 0x8000) >> 16);
    }
    for (long i = 0; i < nb; i++) wr(M, d + i, v[i]);
    free(v);
}
static void hbox(mem_t *M, long d, long s, long nout, long step) {
    long n = (step + 0xffff) >> 16;
    if (n > 16) n = 16;
    long r = PYV_BOXRECIP[n] & 0xffff, pos = 0;
    for (long j = 0; j < nout; j++) {
        long base = s + (pos >> 16), sm = 0;
        for (long k = 0; k < n; k++) sm += (rd(M, base + k) * r + 0x8000) >> 16;
        wr(M, d + j, sm < 255 ? sm : 255);
        pos += step;
    }
}
static void hres_rows(mem_t *M, long d, long s, long n, long x0, long step, long dp, long sp, long rows) {
    uint8_t *v = malloc((size_t)n + 2);
    for (long r = 0; r < rows; r++) {
        /* one byte too many unless the row ends 16-aligned */
        long nl = n + (rem16(d + r * dp + n) ? 1 : 0);
        for (long j = 0; j < nl; j++) {
            long xx = x0 + j * step, x = xx >> 16, f = xx & 0xffff;
            long p0 = rd(M, s + r * sp + x), p1 = rd(M, s + r * sp + x + 1);
            v[j] = (uint8_t)(p0 + (((p1 - p0) * f + 0x8000) >> 16));
        }
        for (long j = 0; j < nl; j++) wr(M, d + r * dp + j, v[j]);
    }
    free(v);
}
static void hres_row1(mem_t *M, long d, long s, long n, long x0, long step) {
    long xx = x0;
    for (long j = 0; j < n; j++) {
        long x = xx >> 16, f = xx & 0xffff, p0 = rd(M, s + x), p1 = rd(M, s + x + 1);
        wr(M, d + j, p0 + (((p1 - p0) * f) >> 16));
        xx += step;
    }
}
static void edge_fill(mem_t *M, long d, long s, long n, long rows, long dp, long sp) {
    for (long r = 0; r < rows; r++) {
        long v = rd(M, s + r * sp);
        for (long j = 0; j < n; j++) wr(M, d + r * dp + j, v);
    }
}
static void hpass(mem_t *M, long dst, long dw, long rows, long dp, long src, long sw, long sp) {
    long step = (sw << 16) / dw;
    if (step > (2L << 16)) {
        for (long r = 0; r < rows; r++) hbox(M, dst + r * dp, src + r * sp, dw, step);
        return;
    }
    long x0 = ((sw - dw) << 15) / dw;
    if (dw > sw) {
        long e = (sw + dw - 1) / (2 * sw);
        edge_fill(M, dst, src, e, rows, dp, sp);
        edge_fill(M, dst + dw - e, src + sw - 1, e, rows, dp, sp);
        dst += e;
        x0 += e * step;
        dw -= 2 * e;
    }
    long r = rows;
    if (!((dp | sp) & 8)) {
        while (r >= 16) {
            hres_rows(M, dst, src, dw, x0, step, dp, sp, 16);
            dst += 16 * dp, src += 16 * sp, r -= 16;
        }
        if (r >= 8) {
            long r15 = r & 7;
            if (r15 > 1) {
                hres_rows(M, dst, src, dw, x0, step, dp, sp, 8);
                dst += r15 * dp, src += r15 * sp, r -= r15;
            }
            hres_rows(M, dst, src, dw, x0, step, dp, sp, 8);
            dst += 8 * dp, src += 8 * sp, r -= 8;
        }
    } else {
        while (r >= 32) {
            for (int par = 0; par < 2; par++) hres_rows(M, dst + par * dp, src + par * sp, dw, x0, step, 2 * dp, 2 * sp, 16);
            dst += 32 * dp, src += 32 * sp, r -= 32;
        }
        if (r >= 16) {
            for (int par = 0; par < 2; par++) hres_rows(M, dst + par * dp, src + par * sp, dw, x0, step, 2 * dp, 2 * sp, 8);
            dst += 16 * dp, src += 16 * sp, r -= 16;
        }
        if (r >= 8) {
            for (int par = 0; par < 2; par++) hres_rows(M, dst + par * dp, src + par * sp, dw, x0, step, 2 * dp, 2 * sp, 4);
            dst += 8 * dp, src += 8 * sp, r -= 8;
        }
    }
    while (r > 0) {
        hres_row1(M, dst, src, dw, x0, step);
        dst += dp, src += sp, r--;
    }
}
static void vpass(mem_t *M, long dst, long n, long rows, long dp, long src, long sp, long y0, long step, long last) {
    if (step >= (2L << 16)) {
        long y = y0;
        for (long i = 0; i < rows; i++) {
            long a = y >> 16;
            y += step;
            vbox(M, dst, src + a * sp, n, sp, (y >> 16) - a);
            dst += dp;
        }
        return;
    }
    long y = y0 + (step >> 1) - 0x8000;
    for (long i = 0; i < rows; i++) {
        long r3 = y >> 16;
        if (y <= 0) f_memcpy(M, dst, src, n);
        else if (r3 >= last) f_memcpy(M, dst, src + r3 * sp, n);
        else lerp_rows(M, dst, src + r3 * sp, n, sp, y);
        dst += dp;
        y += step;
    }
}
static int plane(mem_t *M, long dst, long dw, long dh, long dp, long src, long sw, long sh, long sp, long scratch) {
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0) return 0;
    if (dw == sw && dh == sh) {
        for (long r = 0; r < dh; r++) f_memcpy(M, dst + r * dp, src + r * sp, sw);
        return 0;
    }
    if (dh >= sh) {
        long off = (dh - sh) * dp;
        hpass(M, dst + off, dw, sh, dp, src, sw, sp);
        vpass(M, dst, dw, dh, dp, dst + off, dp, 0, (sh << 16) / dh, sh - 1);
        return 0;
    }
    if (sh == 2 * dh && sw == 2 * dw && !((dh | sw | dp | sp | dst | src) & 15)) {
        for (long r = 0; r < dh; r++)
            for (long x = 0; x < dw; x++) {
                long a = src + 2 * r * sp + 2 * x;
                wr(M, dst + r * dp + x, (rd(M, a) + rd(M, a + 1) + rd(M, a + sp) + rd(M, a + sp + 1) + 2) >> 2);
            }
        return 0;
    }
    long pitch = (sw + 15) & ~15L;
    long chunk = SCRATCH >= pitch * 64 ? 64 : SCRATCH >= pitch * 32 ? 32 : (SCRATCH / pitch < 16 ? SCRATCH / pitch : 16);
    long step = (sh << 16) / dh, y0 = 0, done = 0;
    while (done < dh) {
        long c = chunk < dh - done ? chunk : dh - done;
        vpass(M, scratch, sw, c, pitch, src, sp, y0, step, sh - 1);
        hpass(M, dst, dw, c, dp, scratch, sw, pitch);
        y0 += step * c, dst += c * dp, done += c;
    }
    return 0;
}

/* persistent temp image and scratch of the firmware (one set per output size) */
typedef struct {
    long dw, dh;
    uint8_t *temp, *scratch;
    long tsz;
} fitstate;
static fitstate g_fit[2];

static int fit(const pyv_dec *d, long dw, long dh, uint16_t *out) {
    uint32_t w32, h32;
    size_t stride;
    const uint8_t *y, *cb, *cr;
    if (pyv_dec_picture(d, &w32, &h32, &stride, &y, &cb, &cr) < 0) return -1;
    long w = w32, h = h32, sp = (long)stride, hp = (h + 15) & ~15L;
    long tp = (dw + 15) & ~15L, big = dw * dh * 2 >= 28800, th = big ? dh / 2 : dh;
    long tsz = tp * ((th + 15) & ~15L) * 3 / 2, srcsz = sp * hp * 3 / 2;
    fitstate *st = &g_fit[dw == 60 ? 1 : 0];
    if (st->tsz != tsz) {
        free(st->temp);
        st->temp = calloc((size_t)tsz, 1);
        st->tsz = tsz;
    }
    if (!st->scratch) st->scratch = calloc(SCRATCH, 1);
    /* [source][temp][16 zero bytes: gp+0x1dbb4..][scratch] (see pixel.rs) */
    enum { GUARD = 16 };
    mem_t M = {calloc((size_t)(srcsz + tsz + GUARD + SCRATCH), 1), srcsz + tsz + GUARD + SCRATCH};
    memcpy(M.m, y, (size_t)(sp * hp));
    memcpy(M.m + sp * hp, cb, (size_t)(sp * hp / 4));
    memcpy(M.m + sp * hp * 5 / 4, cr, (size_t)(sp * hp / 4));
    memcpy(M.m + srcsz, st->temp, (size_t)tsz);
    memcpy(M.m + srcsz + tsz + GUARD, st->scratch, SCRATCH);
    long src = 0, temp = srcsz, scratch = srcsz + tsz + GUARD;
    long dx = 0, dy = 0, fh = dh, sx = 0, sy = 0, sw = w, sh = h;
    long h2 = dw * h / w;
    if (h2 < dh) dy = (dh - h2) / 2, fh = h2;
    else if (h2 > dh) {
        long used = dh * h / h2;
        sy = (h - used) / 2;
        sh = used;
    }
    long strips = big ? 2 : 1, ty = ((th + 15) >> 4) * tp * 16, tv = ty + ((th + 15) >> 4) * tp * 4;
    long syo = ((h + 15) >> 4) * sp * 16, svo = syo + ((h + 15) >> 4) * sp * 4, ct = tp >> 1, cs = sp >> 1;
    int err = 0;
    for (long k = 0; k < strips && !err; k++) {
        long fdh = fh / strips, ssh = sh / strips, ssy = sy + k * (sh / strips);
        err |= plane(&M, temp + dy * tp + dx, dw, fdh, tp, src + ssy * sp + sx, sw, ssh, sp, scratch);
        err |= plane(&M, temp + ty + (dy >> 1) * ct + (dx >> 1), dw >> 1, fdh >> 1, ct, src + syo + (ssy >> 1) * cs + (sx >> 1), sw >> 1, ssh >> 1, cs, scratch);
        err |= plane(&M, temp + tv + (dy >> 1) * ct + (dx >> 1), dw >> 1, fdh >> 1, ct, src + svo + (ssy >> 1) * cs + (sx >> 1), sw >> 1, ssh >> 1, cs, scratch);
        for (long r = 0; r < th; r++)
            for (long x = 0; x < dw; x++) {
                long c = (r >> 1) * ct + (x >> 1);
                out[(k * th + r) * dw + x] = yuv565((int)rd(&M, temp + r * tp + x), (int)rd(&M, temp + ty + c), (int)rd(&M, temp + tv + c));
            }
    }
    memcpy(st->temp, M.m + temp, (size_t)tsz);
    memcpy(st->scratch, M.m + scratch, SCRATCH);
    free(M.m);
    return err ? -1 : 0;
}

int pyv_frame_rgb565(const pyv_dec *d, uint32_t brightness, uint16_t out[240 * 160]) {
    if (fit(d, 240, 160, out) < 0) return -1;
    for (int i = 0; i < 240 * 160; i++) out[i] = bright565(out[i], brightness);
    return 0;
}

int pyv_thumb_rgb565(const pyv_dec *d, uint16_t out[60 * 40]) { return fit(d, 60, 40, out); }

void pyv_fit_reset(void) {
    for (int i = 0; i < 2; i++) {
        free(g_fit[i].temp);
        free(g_fit[i].scratch);
        g_fit[i] = (fitstate){0, 0, NULL, NULL, 0};
    }
}
