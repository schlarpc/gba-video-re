/* FVMV audio decoder (GSM 06.10-style RPE-LPC without long-term prediction), bit-exact
 * model of the player's IWRAM routines 0x03001b88 (block decode) and 0x03001d18 (4x
 * interpolation + 8-bit noise-shaped quantisation). C99. */
#ifndef FVMVAUDIO_H
#define FVMVAUDIO_H
#include <stdint.h>

#define FVMV_ABLOCK_BYTES 40      /* one coded block */
#define FVMV_ABLOCK_SAMPLES 256   /* at 16384 Hz */
#define FVMV_AOUT_BYTES 1024      /* 8-bit PCM at 65536 Hz per block */

typedef struct {
    int32_t v[9];       /* lattice state (IWRAM 0x03001b60) */
    int32_t msr;        /* de-emphasis state (0x03001b5c) */
    int32_t err, fb;    /* noise shaper: last quantisation error, feedback (0x03001d0c/10) */
    int32_t prev;       /* previous 16 kHz sample for the interpolator (0x03001d14) */
} fvmv_audio;

void fvmv_audio_init(fvmv_audio *a);
/* stage 1: decode a 40-byte block into 256 int32 samples (2 x de-emphasised output) */
void fvmv_audio_synth(fvmv_audio *a, const uint8_t blk[40], int32_t out[256]);
/* stage 2: 4x interpolation + 8-bit quantisation with error feedback; n input samples
 * -> 4n signed 8-bit output samples at 65536 Hz */
void fvmv_audio_output(fvmv_audio *a, const int32_t *in, int n, int8_t *out);
#include <stddef.h>
size_t fvmv_audio_size(void);
#endif
