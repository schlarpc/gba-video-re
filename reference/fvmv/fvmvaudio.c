/* FVMV audio decoder -- see fvmvaudio.h and docs/format-fvmv.md (audio). C99. */
#include <string.h>
#include "fvmvaudio.h"

/* all arithmetic is 32-bit two's complement with wraparound, as on the ARM7 */
static inline int32_t wrap(uint32_t x) { return (int32_t)x; }
static inline int32_t asr(int32_t x, int s) { return x < 0 ? ~(~x >> s) : x >> s; }
static inline int32_t madd_q15(int32_t a, int32_t b) /* (a*b + 0x4000) >> 15, 32-bit wrap */
{
    return asr(wrap((uint32_t)a * (uint32_t)b + 0x4000u), 15);
}

/* GSM 06.10 tables (ETSI EN 300 961, sections 4.2.8, 5.2.8 and 5.2.15) */
static const int16_t lar_B[8] = { 0, 0, 2048, -2560, 94, -1792, -341, -1144 };
static const int16_t lar_MIC[8] = { -32, -32, -16, -16, -8, -8, -4, -4 };
static const int16_t lar_INVA[8] = { 13107, 13107, 13107, 13107, 19223, 17476, 31454, 29708 };
static const int16_t gsm_FAC[8] = { 18431, 20479, 22527, 24575, 26623, 28671, 30719, 32767 };

static int16_t sat16(int32_t x) { return (int16_t)(x > 32767 ? 32767 : x < -32768 ? -32768 : x); }
static int16_t mult_r(int16_t a, int16_t b) { return (int16_t)(((int32_t)a * b + 16384) >> 15); }

/* LARc -> reflection coefficient rp (Q15): GSM decoding of the coded LARs + LAR->rp */
static int32_t lar_to_rp(int i, int c)
{
    int16_t t = sat16((c + lar_MIC[i]) * 1024);
    t = sat16(t - lar_B[i] * 2);
    t = mult_r(lar_INVA[i], t);
    int16_t larp = sat16(t + t);
    int32_t a = larp < 0 ? -larp : larp;
    if (a < 11059) a <<= 1;
    else if (a < 20070) a += 11059;
    else a = (a >> 2) + 26112;
    return larp < 0 ? -a : a;
}
/* xmaxc (6 bit) and pulse code (3 bit) -> pulse amplitude: GSM APCM inverse quantisation */
static int32_t pulse(int xmaxc, int v)
{
    int exp = 0, mant;
    if (xmaxc > 15) exp = (xmaxc >> 3) - 1;
    mant = xmaxc - (exp << 3);
    if (mant == 0) { exp = -4; mant = 7; }
    else { while (mant <= 7) { mant = mant << 1 | 1; exp--; } mant -= 8; }
    int t2 = 6 - exp, t3 = t2 > 0 ? 1 << (t2 - 1) : 0;
    int32_t t = ((v << 1) - 7) * 4096;
    t = ((int32_t)gsm_FAC[mant] * t + 16384) >> 15;
    return (t + t3) >> t2;
}

static int32_t rp_tab[8][64], xm_tab[64][8];
static int tabs_ready;
static void tables(void)
{
    static const int nlev[8] = { 64, 64, 32, 32, 16, 16, 8, 8 };
    for (int i = 0; i < 8; i++)
        for (int c = 0; c < nlev[i]; c++) rp_tab[i][c] = lar_to_rp(i, c);
    for (int x = 0; x < 64; x++)
        for (int v = 0; v < 8; v++) xm_tab[x][v] = pulse(x, v);
    tabs_ready = 1;
}

void fvmv_audio_init(fvmv_audio *a) { memset(a, 0, sizeof *a); if (!tabs_ready) tables(); }

static uint32_t w32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

void fvmv_audio_synth(fvmv_audio *a, const uint8_t blk[40], int32_t out[256])
{
    uint32_t w[10];
    for (int i = 0; i < 10; i++) w[i] = w32(blk + 4 * i);
    int larc[8];
    larc[0] = w[0] >> 26; larc[1] = (w[0] >> 20) & 63; larc[2] = (w[0] >> 15) & 31; larc[3] = (w[0] >> 10) & 31;
    larc[4] = (w[0] >> 6) & 15; larc[6] = (w[0] >> 3) & 7; larc[7] = w[0] & 7;
    larc[5] = (int)((w[3] & 1) << 3 | (w[5] & 1) << 2 | (w[7] & 1) << 1 | (w[9] & 1));
    int32_t rp[8];
    for (int i = 0; i < 8; i++) rp[i] = rp_tab[i][larc[i]];
    /* excitation: 4 sub-blocks of 64 samples, 21 pulses at stride 3 from grid offset 0..3 */
    for (int k = 0; k < 4; k++) {
        int xmaxc = (int)((w[1] >> (8 + 6 * k)) & 63), grid = (int)((w[1] >> (2 * k)) & 3);
        uint32_t A = w[2 + 2 * k], B = w[3 + 2 * k];
        int p[21];
        for (int i = 0; i < 10; i++) { p[i] = (A >> (29 - 3 * i)) & 7; p[10 + i] = (B >> (29 - 3 * i)) & 7; }
        p[20] = (int)((A & 3) << 1 | ((B >> 1) & 1));
        int32_t *e = out + 64 * k;
        for (int n = 0; n < 64; n++) e[n] = 0;
        for (int i = 0; i < 21; i++) e[grid + 3 * i] = xm_tab[xmaxc][p[i]];
    }
    /* short-term synthesis lattice (GSM 4.3.2 without saturation) and de-emphasis */
    for (int n = 0; n < 256; n++) {
        int32_t s = out[n];
        for (int k = 7; k >= 0; k--) {
            s = wrap((uint32_t)s - (uint32_t)madd_q15(rp[k], a->v[k]));
            a->v[k + 1] = wrap((uint32_t)a->v[k] + (uint32_t)madd_q15(rp[k], s));
        }
        a->v[0] = s;
        a->msr = wrap((uint32_t)s + (uint32_t)madd_q15(a->msr, 28180));
        out[n] = wrap((uint32_t)a->msr * 2u);
    }
}

static int8_t quant(fvmv_audio *a, int32_t v)
{
    int32_t q = asr(wrap((uint32_t)v + 0x80u - (uint32_t)a->fb), 8);
    if (q < -128) q = -128;
    if (q > 127) q = 127;
    int32_t old = a->err;
    a->err = wrap((uint32_t)q * 256u - (uint32_t)v);
    int32_t f = wrap((uint32_t)old + 2u * (uint32_t)a->err);
    a->fb = wrap((uint32_t)f - (uint32_t)asr(f, 4));
    return (int8_t)q;
}

void fvmv_audio_output(fvmv_audio *a, const int32_t *in, int n, int8_t *out)
{
    for (int i = 0; i < n; i++) {
        int32_t x = in[i], p = a->prev;
        out[4 * i + 0] = quant(a, asr(wrap(3u * (uint32_t)p + (uint32_t)x), 2));
        out[4 * i + 1] = quant(a, asr(wrap((uint32_t)p + (uint32_t)x), 1));
        out[4 * i + 2] = quant(a, asr(wrap(3u * (uint32_t)x + (uint32_t)p), 2));
        out[4 * i + 3] = quant(a, x);
        a->prev = x;
    }
}

size_t fvmv_audio_size(void) { return sizeof(fvmv_audio); }
