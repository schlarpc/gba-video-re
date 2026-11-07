/* Majesco GBA Video VQ layer (shared by the Hydrogen and ADS families).
 *
 * A segment carries a codebook of up to 256 entries in a YCbCr-like space, coded as byte deltas.
 * mvq_unpredict() undoes the delta coding, mvq_convert() turns each entry into dithered RGB555
 * pixels laid out exactly like the cartridge's VRAM codebook at 0x06012C00, and mvq_blit()
 * rasterises one frame of 1-byte indices into a 240x160 mode-3 framebuffer.
 *
 * Spec: docs/majesco/vq.md
 */
#ifndef MAJESCO_VQ_H
#define MAJESCO_VQ_H
#include <stdint.h>
#include <stddef.h>

#define MVQ_W 240
#define MVQ_H 160
#define MVQ_ENTRY_HW 16          /* VRAM codebook entry stride in halfwords for 4x4 and 3x3 */

typedef struct {
    uint8_t dither[16];          /* ordered-dither thresholds, index = row*4+col inside the block */
    int brightness;              /* LUT gain: 256 = identity (player default, level 5 of 11) */
    int gain_bias;               /* added to brightness; the players keep it 0 */
    /* derived by mvq_init() */
    uint8_t lut[256];            /* lut[v] = min(255, v*(brightness+gain_bias) >> 8) */
    uint8_t five[16][256];       /* five[k][v] = dithered 5-bit value of 8-bit v at position k */
} mvq_tables;

/* geometry of a mode (0..7) */
int mvq_block_w(int mode);
int mvq_block_h(int mode);
int mvq_grid_w(int mode);        /* blocks per row: 60 or 80 */
int mvq_grid_h(int mode);        /* block rows: 40, 53 or 80 */
int mvq_blocks(int mode);        /* grid_w*grid_h = index bytes per frame */
int mvq_chroma_per_entry(int mode); /* chroma pairs per entry used by the converter (odd modes) */
size_t mvq_codebook_bytes(int mode, int ncb); /* inflated codebook size the converter reads */
int mvq_entry_stride(int mode);  /* VRAM entry stride in halfwords: 16, 12, 16 or 8 */

void mvq_init(mvq_tables *t);

/* in place: running byte sum over the luma plane, then (separately) over both chroma planes */
void mvq_unpredict(uint8_t *cb, int mode, int ncb);

/* cb = unpredicted codebook; out = 256 entries x 16 halfwords (only the first ncb entries and
 * mvq_entry_stride(mode) halfwords of each are written, exactly as the cartridge writes VRAM) */
void mvq_convert(const uint8_t *cb, int mode, int ncb, const mvq_tables *t, uint16_t out[256][16]);

/* idx = mvq_blocks(mode) bytes; pal24 = BG palette halfwords 0..23 (row 159 filler for the
 * 3-row-block modes; all zero on every observed cartridge) */
void mvq_blit(const uint8_t *idx, int mode, const uint16_t cb[256][16], const uint16_t pal24[24],
              uint16_t fb[MVQ_W * MVQ_H]);

#endif
