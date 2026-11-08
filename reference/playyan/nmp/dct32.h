/* The NMP DSP's DCT-32 (routine 2:899A), written by hand from its structure; see the Rust twin
 * crates/gbav-playyan/src/nmp/dct32.rs for the description.  k[] = 102 int32 constants from the ROM
 * (data 0x9400).  Products: hh = 2*hi(a)*hi(c), cross = 2*(lo(a)*hi(c) + lo(c)*hi(a)) (lo unsigned);
 * rotation term hh + (cross >> 16), output term (hh << 2) + (cross >> 14); stores wrap to 32 bits. */
#include <stdint.h>

typedef struct { int32_t a, c; } DctTerm;
static int64_t dct_hh, dct_cr;
static void dct_parts(const DctTerm *t, int n)
{
    dct_hh = dct_cr = 0;
    for (int i = 0; i < n; i++) {
        int64_t ah = t[i].a >> 16, al = (uint32_t)t[i].a & 0xFFFF, ch = t[i].c >> 16, cl = (uint32_t)t[i].c & 0xFFFF;
        dct_hh += 2 * ah * ch; dct_cr += 2 * (al * ch + cl * ah);
    }
}
static int32_t dct_w(int64_t v) { return (int32_t)(uint32_t)v; }
static int32_t dct_q2(int32_t a, int32_t ca, int32_t b, int32_t cb)
{
    DctTerm t[2] = {{a, ca}, {b, cb}}; dct_parts(t, 2);
    return dct_w(dct_hh * 4 + (dct_cr >> 14));
}
static int64_t dct_r1(int32_t a, int32_t c) { DctTerm t[1] = {{a, c}}; dct_parts(t, 1); return dct_hh + (dct_cr >> 16); }
/* (s, t) folded with C5 onto two partners */
static void dct_bfly(int32_t s, int32_t t, int32_t hi, int32_t lo, int32_t c5, int32_t o[4])
{
    int64_t md = dct_r1(dct_w((int64_t)s - t), c5), ms = dct_r1(dct_w((int64_t)s + t), c5);
    o[0] = dct_w(hi + md); o[1] = dct_w(hi - md); o[2] = dct_w(lo + ms); o[3] = dct_w(lo - ms);
}
/* base +- (m1 * k[j] + m2 * k[j+1]) */
static void dct_rot(int32_t m1, int32_t m2, const int32_t *k, int j, int32_t base, int32_t *plus, int32_t *minus)
{
    DctTerm t[2] = {{m1, k[j]}, {m2, k[j + 1]}}; dct_parts(t, 2);
    int64_t v = dct_hh + (dct_cr >> 16);
    *plus = dct_w(base + v); *minus = dct_w(base - v);
}

static void nmp_dct32(int32_t x[32], const int32_t *k)
{
    int32_t a[16], b[16], e[8], f[8], g[4], h[4], y[32];
    for (int i = 0; i < 16; i++) { a[i] = dct_w(((int64_t)x[i] + x[31 - i]) >> 2); b[i] = dct_w(((int64_t)x[i] - x[31 - i]) >> 2); }
    for (int i = 0; i < 8; i++) { e[i] = dct_w((int64_t)a[i] + a[15 - i]); f[i] = dct_w((int64_t)a[i] - a[15 - i]); }
    for (int i = 0; i < 4; i++) { g[i] = dct_w((int64_t)e[i] + e[7 - i]); h[i] = dct_w((int64_t)e[i] - e[7 - i]); }
    int32_t p0 = dct_w((int64_t)g[0] + g[3]), p1 = dct_w((int64_t)g[0] - g[3]);
    int32_t q0 = dct_w((int64_t)g[1] + g[2]), q1 = dct_w((int64_t)g[1] - g[2]);
    y[0] = dct_w(((int64_t)q0 + p0) * 4);
    { DctTerm t[1] = {{dct_w((int64_t)p0 - q0), k[0]}}; dct_parts(t, 1); y[16] = dct_w(dct_hh * 4 + (dct_cr >> 14)); }
    y[8] = dct_q2(q1, k[1], p1, k[2]);
    y[24] = dct_q2(q1, k[3], p1, k[4]);

    int32_t H[4], A[4], C[4], B0[4], B1[4], B2[4], B3[4];
    dct_bfly(h[1], h[2], h[3], h[0], k[5], H);      /* hp hm lp lm */
    y[4] = dct_q2(H[0], k[46], H[2], k[47]); y[20] = dct_q2(H[1], k[48], H[3], k[49]);
    y[12] = dct_q2(H[3], k[50], H[1], k[51]); y[28] = dct_q2(H[2], k[52], H[0], k[53]);

    dct_bfly(f[2], f[5], f[6], f[1], k[5], A);      /* ap am bp bm */
    dct_bfly(f[3], f[4], f[7], f[0], k[5], C);      /* cp cm dp dm */
    int32_t r1, r2, r3, r4, r5, r6, r7, r8;
    dct_rot(A[0], A[2], k, 6, C[0], &r1, &r2); dct_rot(A[0], A[2], k, 8, C[2], &r3, &r4);
    dct_rot(A[1], A[3], k, 10, C[1], &r5, &r6); dct_rot(A[1], A[3], k, 12, C[3], &r7, &r8);
    y[2] = dct_q2(r1, k[54], r3, k[55]); y[18] = dct_q2(r2, k[56], r4, k[57]);
    y[10] = dct_q2(r6, k[58], r8, k[59]); y[26] = dct_q2(r5, k[60], r7, k[61]);
    y[6] = dct_q2(r7, k[62], r5, k[63]); y[22] = dct_q2(r8, k[64], r6, k[65]);
    y[14] = dct_q2(r4, k[66], r2, k[67]); y[30] = dct_q2(r3, k[68], r1, k[69]);

    dct_bfly(b[4], b[11], b[12], b[3], k[5], B0);   /* p m l n */
    dct_bfly(b[5], b[10], b[13], b[2], k[5], B1);
    dct_bfly(b[6], b[9], b[14], b[1], k[5], B2);
    dct_bfly(b[7], b[8], b[15], b[0], k[5], B3);
    int32_t s115, s116, s117, s118, s119, s120, s121, s122, s123, s124, s125, s126, s127, s128, s129, s130;
    dct_rot(B1[0], B1[2], k, 16, B2[2], &s116, &s115);
    dct_rot(B1[0], B1[2], k, 14, B2[0], &s117, &s118);
    dct_rot(B0[0], B0[2], k, 20, B3[2], &s120, &s119);
    dct_rot(B0[0], B0[2], k, 18, B3[0], &s121, &s122);
    dct_rot(B0[1], B0[3], k, 24, B3[3], &s124, &s123);
    dct_rot(B0[1], B0[3], k, 22, B3[1], &s125, &s126);
    dct_rot(B1[1], B1[3], k, 28, B2[3], &s128, &s127);
    dct_rot(B1[1], B1[3], k, 26, B2[1], &s129, &s130);
    int32_t t[16];
    dct_rot(s117, s116, k, 30, s121, &t[0], &t[1]);  dct_rot(s117, s116, k, 32, s120, &t[2], &t[3]);
    dct_rot(s118, s115, k, 34, s122, &t[4], &t[5]);  dct_rot(s118, s115, k, 36, s119, &t[6], &t[7]);
    dct_rot(s130, s127, k, 38, s126, &t[8], &t[9]);  dct_rot(s130, s127, k, 40, s123, &t[10], &t[11]);
    dct_rot(s129, s128, k, 42, s125, &t[12], &t[13]); dct_rot(s129, s128, k, 44, s124, &t[14], &t[15]);
    static const int8_t OP[8][5] = {{0, 2, 70, 1, 31}, {1, 3, 74, 17, 15}, {5, 7, 78, 9, 23}, {4, 6, 82, 25, 7},
                                    {8, 10, 86, 5, 27}, {9, 11, 90, 21, 11}, {13, 15, 94, 13, 19}, {12, 14, 98, 29, 3}};
    for (int i = 0; i < 8; i++) {
        int32_t u = t[OP[i][0]], v = t[OP[i][1]]; int j = OP[i][2];
        y[OP[i][3]] = dct_q2(u, k[j], v, k[j + 1]);
        y[OP[i][4]] = dct_q2(u, k[j + 2], v, k[j + 3]);
    }
    for (int i = 0; i < 32; i++) x[i] = y[i];
}
