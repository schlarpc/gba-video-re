/* Sonic X Vol 2 (proto) / Majesco Hydrogen VLC-ADPCM audio decoder. */
#ifndef SX2_ADPCM_H
#define SX2_ADPCM_H
#include <stdint.h>
#include <stddef.h>

#define SX2A_BLOCK_SAMPLES 0x404a     /* 16458 samples = 12 video frames */
#define SX2A_FRAME_SAMPLES 0x55b      /* 1371 (+1 every other frame) */

/* Mirrors the player's 128-byte context (IWRAM 0x03004a14 in the proto). */
typedef struct {
    int32_t x;        /* +0x00 log-domain step state            */
    int32_t dprev;    /* +0x04 previous quantised difference    */
    int32_t p1, p2;   /* +0x08,+0x0c last two reconstructions   */
    int32_t a1, a2;   /* +0x10,+0x14 pole coefficients          */
    int32_t b;        /* +0x18 zero coefficient                 */
    uint32_t word;    /* +0x1c bit buffer, MSB first            */
    uint32_t bits;    /* +0x20 valid bits in word               */
    uint32_t next;    /* +0x24 look-ahead word                  */
    uint32_t nbits;   /* +0x28 valid bits in next               */
    const uint8_t *ptr; /* +0x2c next 32-bit LE word to load    */
    const uint8_t *end;
    uint32_t rem;     /* +0x34 samples left in current block    */
    uint32_t st;      /* +0x38 6-bit context (last 3 symbols)   */
    uint8_t perm[64]; /* +0x3c symbol permutation per context   */
    uint8_t s1, s2;   /* +0x7c,+0x7d last two update signs      */
    int clip;         /* 0 = proto (store low 8 bits), 1 = retail (clip to [-128,127]) */
} sx2a_ctx;

typedef struct {
    uint32_t w0, w1, w2;
    unsigned codec;         /* (w1>>6)&15, must be 3 */
    unsigned last_frames;   /* video frames in the last block */
    unsigned nblocks;
    const uint8_t *sizes;   /* nblocks x u16 LE, block size in 32-bit words */
    const uint8_t *data;
    size_t frames, samples;
} sx2a_info;

/* Parse a type-2 resource (pointer to its first byte = after the resource header word). */
int sx2a_parse(const uint8_t *res, size_t len, sx2a_info *info);
/* Initialise exactly as SoundPlayer::SetSoundFile (bit reader at data start, predictor reset). */
void sx2a_init(sx2a_ctx *c, const sx2a_info *info, int clip);
/* Seek to the start of block k (as the player's seek does). */
void sx2a_seek_block(sx2a_ctx *c, const sx2a_info *info, unsigned k);
/* The inner loop (IWRAM 0x03002a4c): decode n samples with the current block's tables. */
void sx2a_samples(sx2a_ctx *c, int8_t *out, uint32_t n);
/* Block-level driver (ROM 0x08005ea0): reads block headers as needed. */
void sx2a_decode(sx2a_ctx *c, int8_t *out, uint32_t n);
/* Whole stream convenience: returns samples written. */
int sx2_adpcm_decode(const uint8_t *res, size_t len, int8_t *out, size_t max_samples);
/* diagnostics: number of block headers seen of each kind */
extern unsigned long sx2a_hdr_resync, sx2a_hdr_cont;
#endif
