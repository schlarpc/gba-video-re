#include "g726.h"
#include <stdlib.h>

static const int POWER2[15] = {1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000};
static int quan(int v, const int *t, int n) {
    int i = 0;
    while (i < n && v >= t[i]) i++;
    return i;
}
static int s16(int v) { return (int16_t)v; }
static int fmult(int an, int srn) {
    int anmag = an > 0 ? an : ((-an) & 0x1FFF);
    int anexp = quan(anmag, POWER2, 15) - 6;
    int anmant = anmag == 0 ? 32 : anexp >= 0 ? anmag >> anexp : anmag << -anexp;
    int wanexp = anexp + ((srn >> 6) & 0xF) - 13;
    int wanmant = (anmant * (srn & 077) + 0x30) >> 4;
    int r = wanexp >= 0 ? (wanmant << wanexp) & 0x7FFF : wanmant >> -wanexp;
    return (an ^ srn) < 0 ? -r : r;
}

static const int DQ16[4] = {116, 365, 365, 116}, WI16[4] = {-704, 14048, 14048, -704}, FI16[4] = {0, 0xE00, 0xE00, 0};
static const int DQ24[8] = {-2048, 135, 273, 373, 373, 273, 135, -2048}, WI24[8] = {-128, 960, 4384, 18624, 18624, 4384, 960, -128},
                 FI24[8] = {0, 0x200, 0x400, 0xE00, 0xE00, 0x400, 0x200, 0};
static const int DQ32[16] = {-2048, 4, 135, 213, 273, 323, 373, 425, 425, 373, 323, 273, 213, 135, 4, -2048},
                 WI32[16] = {-384, 576, 1312, 2048, 3584, 6336, 11360, 35904, 35904, 11360, 6336, 3584, 2048, 1312, 576, -384},
                 FI32[16] = {0, 0, 0, 0x200, 0x200, 0x200, 0x600, 0xE00, 0xE00, 0x600, 0x200, 0x200, 0x200, 0, 0, 0};
static const int DQ40[32] = {-2048, -66, 28, 104, 169, 224, 274, 318, 358, 395, 429, 459, 488, 514, 539, 566,
                             566, 539, 514, 488, 459, 429, 395, 358, 318, 274, 224, 169, 104, 28, -66, -2048},
                 WI40[32] = {448, 448, 768, 1248, 1280, 1312, 1856, 3200, 4512, 5728, 7008, 8960, 11456, 14080, 16928, 22272,
                             22272, 16928, 14080, 11456, 8960, 7008, 5728, 4512, 3200, 1856, 1312, 1280, 1248, 768, 448, 448},
                 FI40[32] = {0, 0, 0, 0, 0, 0x200, 0x200, 0x200, 0x200, 0x200, 0x400, 0x600, 0x800, 0xA00, 0xC00, 0xC00,
                             0xC00, 0xC00, 0xA00, 0x800, 0x600, 0x400, 0x200, 0x200, 0x200, 0x200, 0x200, 0, 0, 0, 0, 0};

void pyg726_init(pyg726 *s, int bits) {
    *s = (pyg726){34816, 544, 0, 0, 0, {0, 0}, {0}, {0, 0}, {32, 32, 32, 32, 32, 32}, {32, 32}, 0, bits};
}

static int float_of(int v) {
    int e = quan(v, POWER2, 15);
    return (e << 6) + ((v << 6) >> e);
}

static void update(pyg726 *s, int code_size, int y, int wi, int fi, int dq, int sr, int dqsr) {
    int pk0 = dqsr < 0, mag = dq & 0x7FFF;
    int ylint = s->yl >> 15, ylfrac = (s->yl >> 10) & 0x1F;
    int thr1 = (32 + ylfrac) << ylint, thr2 = ylint > 9 ? 31 << 10 : thr1, dqthr = (thr2 + (thr2 >> 1)) >> 1;
    int tr = s->td && mag > dqthr, a2p;
    s->yu = s16(y + ((wi - y) >> 5));
    if (s->yu < 544) s->yu = 544;
    if (s->yu > 5120) s->yu = 5120;
    s->yl += s->yu + ((-s->yl) >> 6);
    if (tr) {
        s->a[0] = s->a[1] = 0;
        for (int i = 0; i < 6; i++) s->b[i] = 0;
        a2p = 0;
    } else {
        int pks1 = pk0 ^ s->pk[0];
        a2p = s->a[1] - (s->a[1] >> 7);
        if (dqsr) {
            int fa1 = pks1 ? s->a[0] : -s->a[0];
            if (fa1 < -8191) a2p -= 0x100;
            else if (fa1 > 8191) a2p += 0xFF;
            else a2p += fa1 >> 5;
            if (pk0 ^ s->pk[1]) {
                if (a2p <= -12160) a2p = -12288;
                else if (a2p >= 12416) a2p = 12288;
                else a2p -= 0x80;
            } else if (a2p <= -12416) a2p = -12288;
            else if (a2p >= 12160) a2p = 12288;
            else a2p += 0x80;
        }
        s->a[1] = s16(a2p);
        int a0 = s->a[0] - (s->a[0] >> 8);
        if (dqsr) a0 = pks1 == 0 ? a0 + 192 : a0 - 192;
        int a1ul = 15360 - a2p;
        if (a0 < -a1ul) a0 = -a1ul;
        else if (a0 > a1ul) a0 = a1ul;
        s->a[0] = s16(a0);
        for (int c = 0; c < 6; c++) {
            int b = s->b[c];
            b -= code_size == 5 ? b >> 9 : b >> 8;
            if (dq & 0x7FFF) b = (dq ^ s->dq[c]) >= 0 ? b + 128 : b - 128;
            s->b[c] = s16(b);
        }
    }
    for (int c = 5; c > 0; c--) s->dq[c] = s->dq[c - 1];
    if (mag == 0) s->dq[0] = dq >= 0 ? 0x20 : s16(0xFC20);
    else s->dq[0] = s16(dq >= 0 ? float_of(mag) : float_of(mag) - 0x400);
    s->sr[1] = s->sr[0];
    if (sr == 0) s->sr[0] = 0x20;
    else if (sr > 0) s->sr[0] = s16(float_of(sr));
    else if (sr > -32768) s->sr[0] = s16(float_of(-sr) - 0x400);
    else s->sr[0] = s16(0xFC20);
    s->pk[1] = s->pk[0];
    s->pk[0] = pk0;
    s->td = tr ? 0 : a2p < -11776;
    s->dms = s16(s->dms + ((fi - s->dms) >> 5));
    s->dml = s16(s->dml + (((fi << 2) - s->dml) >> 7));
    if (tr) s->ap = 256;
    else if (y < 1536 || s->td == 1 || abs((s->dms << 2) - s->dml) >= (s->dml >> 3)) s->ap += (0x200 - s->ap) >> 4;
    else s->ap += (-s->ap) >> 4;
    s->ap = s16(s->ap);
}

int16_t pyg726_decode(pyg726 *s, unsigned code) {
    const int *dqln, *wi, *fi;
    int srmask = 0x3FFF;
    switch (s->bits) {
    case 2: dqln = DQ16, wi = WI16, fi = FI16; break;
    case 3: dqln = DQ24, wi = WI24, fi = FI24; break;
    case 4: dqln = DQ32, wi = WI32, fi = FI32; break;
    default: dqln = DQ40, wi = WI40, fi = FI40, srmask = 0x7FFF; break;
    }
    int i = (int)(code & ((1u << s->bits) - 1));
    int sezi = 0;
    for (int k = 0; k < 6; k++) sezi += fmult(s->b[k] >> 2, s->dq[k]);
    sezi = s16(sezi);
    int sez = sezi >> 1;
    int sei = s16(sezi + fmult(s->a[1] >> 2, s->sr[1]) + fmult(s->a[0] >> 2, s->sr[0]));
    int se = sei >> 1;
    int y;
    if (s->ap >= 256) y = s->yu;
    else {
        y = s->yl >> 6;
        int dif = s->yu - y, al = s->ap >> 2;
        if (dif > 0) y += (dif * al) >> 6;
        else if (dif < 0) y += (dif * al + 0x3F) >> 6;
    }
    y = s16(y);
    int sign = i & (1 << (s->bits - 1)), dql = dqln[i] + (y >> 2), dq;
    if (dql < 0) dq = sign ? -0x8000 : 0;
    else {
        int dex = (dql >> 7) & 15, dqt = 128 + (dql & 127);
        dq = (dqt << 7) >> (14 - dex);
        if (sign) dq -= 0x8000;
    }
    dq = s16(dq);
    int sr = s16(dq < 0 ? se - (dq & srmask) : se + dq);
    int dqsr = s16(sr - se + sez);
    update(s, s->bits, y, wi[i], fi[i], dq, sr, dqsr);
    int out = sr * 4;
    return (int16_t)(out < -32768 ? -32768 : out > 32767 ? 32767 : out);
}

size_t pyg726_decode_payload(pyg726 *s, const uint8_t *in, size_t len, int16_t *out) {
    size_t n = len * 8 / (size_t)s->bits, pos = 0;
    for (size_t k = 0; k < n; k++) {
        unsigned c = 0;
        for (int j = 0; j < s->bits; j++, pos++) c = c << 1 | ((in[pos >> 3] >> (7 - (pos & 7))) & 1);
        out[k] = pyg726_decode(s, c);
    }
    return n;
}
