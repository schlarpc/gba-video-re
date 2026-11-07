#ifndef SX2CODEC_H
#define SX2CODEC_H
#include <stdint.h>
extern const uint8_t SX2_BW[8], SX2_BH[8];
/* brightness coefficient (cart levels: 230,256,281,307,332,358,384,409; default 256) */
void sx2_build_tables(unsigned coef);
/* in-place unpredict of the inflated YUV codebook, then RGB555 entries into cb (256*16 halfwords) */
void sx2_build_codebook(uint8_t *yuvbuf, unsigned n, unsigned mode, uint16_t *cb);
/* VQ blit of one frame of byte indices into a 240x160 RGB555 frame buffer */
void sx2_blit(uint16_t *fb, const uint16_t *cb, const uint8_t *idx, unsigned mode);
#endif
