/* sx2codec: Hydrogen VQ codebook + blit as in the Sonic X Vol 2 prototype (see sx2dec.c). */
#include <stdint.h>
#include <string.h>
#include "sx2codec.h"

/* ---- codebook ----------------------------------------------------------------------------- */
const uint8_t SX2_BW[8] = {4,4,4,4,3,3,4,4}, SX2_BH[8] = {4,4,3,3,3,3,2,2};
#define BW SX2_BW
#define BH SX2_BH
static const uint8_t CHROMA_PER_ENTRY[8] = {1,4,1,3,1,2,1,2};    /* UnPredictChrominance 0x03001ce4 */
static const uint8_t MAP4[16] = {0,1,4,5,2,3,6,7,8,9,12,13,10,11,14,15};  /* ROM table @0x08016dd8 */
static const uint8_t MAP43[12] = {0,1,4,5,2,3,6,7,8,9,10,11};             /* ROM table @0x08016de8 */
static const uint8_t DITHER[16] = {7,3,6,2,1,5,0,4,6,2,7,3,0,4,1,5};      /* tables at ctx+0x2c */

static uint8_t clampt[0x500];           /* ctx+0x10dd: 0x200 zeros, b[0..255], 0x200 x b[255] */
static uint8_t dith[16][256];           /* ctx+0x2c */

void sx2_build_tables(unsigned coef)
{
    memset(clampt, 0, 0x200);
    for (unsigned i = 0; i < 256; i++) { unsigned v = coef * i >> 8; clampt[0x200 + i] = v > 255 ? 255 : (uint8_t)v; }
    memset(clampt + 0x300, clampt[0x2ff], 0x200);
    for (int p = 0; p < 16; p++) for (int v = 0; v < 256; v++) { int t = (v + DITHER[p]) >> 3; dith[p][v] = t > 31 ? 31 : (uint8_t)t; }
}
static inline uint16_t yuv(int y, int u, int v, int pos)
{
    const uint8_t *c = clampt + 0x200;
    int r = c[y + 2 * v], g = c[y - (u >> 1) - v], b = c[y + 2 * u];
    return (uint16_t)(dith[pos][r] | dith[pos][g] << 5 | dith[pos][b] << 10);
}

/* codebook bytes (YUV, after inflate) -> RGB555 entries laid out as the blit expects
 * (stride 32 B for 4x4/3x3, 24 B for 4x3, 16 B for 4x2) */
void sx2_build_codebook(uint8_t *yuvbuf, unsigned n, unsigned mode, uint16_t *cb /* 256*16 */)
{
    unsigned bw = BW[mode], bh = BH[mode], np = bw * bh, lum = n * np;
    /* UnPredictLuminance 0x03001be0: running byte sum over the whole luma plane */
    uint8_t acc = 0;
    for (unsigned i = 0; i < lum; i++) { acc = (uint8_t)(acc + yuvbuf[i]); yuvbuf[i] = acc; }
    /* UnPredictChrominance 0x03001ce4: running byte sum over both chroma planes */
    unsigned cl = n * CHROMA_PER_ENTRY[mode] * 2;
    acc = 0;
    for (unsigned i = 0; i < cl; i++) { acc = (uint8_t)(acc + yuvbuf[lum + i]); yuvbuf[lum + i] = acc; }
    /* YUV -> RGB555, 0x03001470 */
    const uint8_t *Y = yuvbuf;
    if (mode == 4 || mode == 5) {
        const int8_t *U = (const int8_t *)(yuvbuf + lum), *V = U + n * 2;
        static const uint8_t dpos[9] = {0,1,2,4,5,6,8,9,10};
        for (unsigned e = 0; e < n; e++) {
            uint16_t *o = cb + e * 16;
            for (int k = 0; k < 9; k++) { int c = k < 6 ? 0 : 1; o[k] = yuv(Y[k], U[c], V[c], dpos[k]); }
            Y += 9; U += 2; V += 2;
        }
    } else {
        const uint8_t *map = (mode == 2 || mode == 3) ? MAP43 : MAP4;
        const int8_t *U = (const int8_t *)(yuvbuf + lum), *V = U + lum / 4;
        unsigned stride = bh * 4;          /* halfwords per entry */
        for (unsigned e = 0; e < n; e++) {
            uint16_t *o = cb + e * stride;
            for (unsigned r = 0; r < bh; r++) {      /* 4 luma per chroma pair */
                for (int k = 0; k < 4; k++) { unsigned pos = map[r * 4 + k]; o[pos] = yuv(Y[k], *U, *V, pos); }
                Y += 4; U++; V++;
            }
        }
    }
}

/* ---- blit (0x0300202c) -------------------------------------------------------------------- */
void sx2_blit(uint16_t *fb, const uint16_t *cb, const uint8_t *idx, unsigned mode)
{
    unsigned bw = BW[mode], bh = BH[mode], cols = 240 / bw, rows = 160 / bh;
    unsigned stride = (mode == 2 || mode == 3) ? 12 : (mode >= 6 ? 8 : 16);
    for (unsigned by = 0; by < rows; by++)
        for (unsigned bx = 0; bx < cols; bx++) {
            const uint16_t *e = cb + (*idx++) * stride;
            for (unsigned y = 0; y < bh; y++)
                for (unsigned x = 0; x < bw; x++)
                    fb[(by * bh + y) * 240 + bx * bw + x] = e[y * bw + x];
        }
}

