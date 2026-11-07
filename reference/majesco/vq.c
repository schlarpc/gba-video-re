/* Majesco GBA Video VQ layer: codebook unprediction, YCbCr->RGB555 conversion with ordered
 * dither, and the frame blit. Bit-exact with the Hydrogen players (IWRAM StreamBase::UnPredict*,
 * the codebook converter and the 4-way blit) and with the ADS players.
 * See docs/majesco/vq.md for the specification. */
#include "vq.h"
#include <string.h>

static const uint8_t BW[8] = {4, 4, 4, 4, 3, 3, 4, 4};
static const uint8_t BH[8] = {4, 4, 3, 3, 3, 3, 2, 2};
/* chroma pairs per entry as the unpredict step counts them (StreamBase::UnPredictChrominance) */
static const uint8_t CPE_UNPRED[8] = {1, 4, 1, 3, 1, 2, 1, 2};
/* luma byte j of a 4-wide entry -> pixel slot (row*4+col). Luma is stored as 2x2 quads:
 * quad 0 = rows 0-1 cols 0-1, quad 1 = rows 0-1 cols 2-3, quad 2 = rows 2-3 cols 0-1, ...
 * For 4x3 the third group is the whole of row 2. Derivable from the geometry. */
static const uint8_t POS44[16] = {0, 1, 4, 5, 2, 3, 6, 7, 8, 9, 12, 13, 10, 11, 14, 15};
static const uint8_t POS43[12] = {0, 1, 4, 5, 2, 3, 6, 7, 8, 9, 10, 11};

int mvq_block_w(int m) { return BW[m & 7]; }
int mvq_block_h(int m) { return BH[m & 7]; }
int mvq_grid_w(int m) { return MVQ_W / BW[m & 7]; }
int mvq_grid_h(int m) { return MVQ_H / BH[m & 7]; }
int mvq_blocks(int m) { return mvq_grid_w(m) * mvq_grid_h(m); }
int mvq_chroma_per_entry(int m) { m &= 7; return (m == 4 || m == 5) ? 2 : BH[m]; }
int mvq_entry_stride(int m) { m &= 7; return (m == 4 || m == 5) ? 16 : BW[m] * BH[m]; }
size_t mvq_codebook_bytes(int m, int ncb)
{
    return (size_t)ncb * (BW[m & 7] * BH[m & 7] + 2 * mvq_chroma_per_entry(m));
}

void mvq_init(mvq_tables *t)
{
    int g = t->brightness + t->gain_bias;
    for (int v = 0; v < 256; v++) {
        unsigned p = (unsigned)(v * g) >> 8;
        t->lut[v] = p > 255 ? 255 : (uint8_t)p;
    }
    for (int k = 0; k < 16; k++)
        for (int v = 0; v < 256; v++)
            t->five[k][v] = (v >> 3) >= 31 ? 31 : (uint8_t)((v >> 3) + (t->dither[k] < (v & 7)));
}

void mvq_unpredict(uint8_t *cb, int mode, int ncb)
{
    size_t nl = (size_t)ncb * BW[mode & 7] * BH[mode & 7];
    size_t nc = (size_t)ncb * CPE_UNPRED[mode & 7] * 2;
    uint8_t acc = 0;
    for (size_t i = 0; i < nl; i++) cb[i] = acc = (uint8_t)(acc + cb[i]);
    acc = 0;
    for (size_t i = nl; i < nl + nc; i++) cb[i] = acc = (uint8_t)(acc + cb[i]);
}

static inline int clampv(const mvq_tables *t, int x)
{
    return x < 0 ? 0 : x > 255 ? t->lut[255] : t->lut[x];
}

static inline uint16_t pix(const mvq_tables *t, int k, int y, int u, int v)
{
    int r = t->five[k][clampv(t, y + 2 * v)];
    int g = t->five[k][clampv(t, y - (u >> 1) - v)];
    int b = t->five[k][clampv(t, y + 2 * u)];
    return (uint16_t)(r | g << 5 | b << 10);
}

void mvq_convert(const uint8_t *cb, int mode, int ncb, const mvq_tables *t, uint16_t out[256][16])
{
    mode &= 7;
    int bw = BW[mode], bh = BH[mode];
    size_t nl = (size_t)ncb * bw * bh;
    if (mode == 4 || mode == 5) {
        const int8_t *U = (const int8_t *)cb + nl, *V = U + 2 * ncb;
        for (int e = 0; e < ncb; e++) {
            for (int s = 0; s < 9; s++) {
                int c = s < 6 ? 0 : 1, k = (s / 3) * 4 + s % 3;
                out[e][s] = pix(t, k, cb[e * 9 + s], U[2 * e + c], V[2 * e + c]);
            }
            out[e][9] = 0;
        }
        return;
    }
    /* generic 4-wide path; used for even modes too (the cartridge does the same) */
    const uint8_t *pos = (bh == 3) ? POS43 : POS44;
    const int8_t *U = (const int8_t *)cb + nl, *V = U + (nl >> 2);
    for (int e = 0; e < ncb; e++)
        for (int g = 0; g < bh; g++) {
            int u = U[e * bh + g], v = V[e * bh + g];
            for (int j = 0; j < 4; j++) {
                int k = pos[g * 4 + j];
                out[e][k] = pix(t, k, cb[(e * bh + g) * 4 + j], u, v);
            }
        }
}

void mvq_blit(const uint8_t *idx, int mode, const uint16_t cb[256][16], const uint16_t pal24[24],
              uint16_t fb[MVQ_W * MVQ_H])
{
    mode &= 7;
    int bw = BW[mode], bh = BH[mode], gw = MVQ_W / bw, gh = MVQ_H / bh;
    for (int by = 0; by < gh; by++)
        for (int bx = 0; bx < gw; bx++) {
            const uint16_t *e = cb[idx[by * gw + bx]];
            for (int r = 0; r < bh; r++)
                memcpy(&fb[(by * bh + r) * MVQ_W + bx * bw], &e[r * bw], bw * 2);
        }
    for (int y = gh * bh; y < MVQ_H; y++)       /* only row 159 in the 53-row modes */
        for (int x = 0; x < MVQ_W; x++) fb[y * MVQ_W + x] = pal24[x % 24];
}
