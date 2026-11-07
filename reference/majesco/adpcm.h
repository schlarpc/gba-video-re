/* Majesco GBA Video ADPCM audio decoder (ADS and Hydrogen players), bit-exact with the
 * cartridges' own decoder. Spec: docs/majesco/adpcm.md. */
#ifndef MJ_ADPCM_H
#define MJ_ADPCM_H
#include <stdint.h>
#include <stddef.h>

#define MJ_ADPCM_BLOCK 16458          /* samples per coded block (0x404a) */
#define MJ_ADPCM_RATE  16384          /* playback rate, Hz (Timer0 reload 0xFC00) */

enum mj_ctab_coding {
    MJ_CTAB_VLC = 0,   /* ADS: 7-entry palette VLC with "11" escape */
    MJ_CTAB_FLAT = 1   /* escape form only (no palette), 5 bits per entry */
};

typedef struct {
    /* bit reader: MSB-first over little-endian 32-bit words */
    const uint8_t *buf;
    size_t nbits_total;   /* size of buf in bits */
    size_t pos;           /* bit position */
    int overrun;
    int coding;
    /* predictor state (names follow adpcm.md) */
    int32_t step, prev, h0, h1, f0, f1, f2;
    uint8_t s1, s2, ctx;
    uint8_t tab[64];
    uint32_t left;        /* samples left in the current block */
    uint32_t block;       /* number of block headers read */
} mj_adpcm;

/* payload = first audio word (after the resource header and block-size table), len in bytes */
void mj_adpcm_init(mj_adpcm *a, const uint8_t *payload, size_t len, int coding);
/* move to the start of a block at byte offset `off` in the payload (sum of earlier block sizes * 4);
 * the next block header must be a full (non-continuation) header for the result to be exact */
void mj_adpcm_seek(mj_adpcm *a, size_t off, uint32_t block_index);
/* decode n samples (signed 8-bit) into out; reads block headers as needed. returns n, or less on overrun */
size_t mj_adpcm_decode(mj_adpcm *a, int8_t *out, size_t n);

#endif
