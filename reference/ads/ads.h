/* ADS (Majesco GBA Video, LZMA lineage): container + video decoder. Spec: docs/format-ads.md */
#ifndef ADS_DEC_H
#define ADS_DEC_H
#include <stdint.h>
#include <stddef.h>
#include "lzma_dec.h"
#include "../majesco/vq.h"

#define ADS_W 240
#define ADS_H 160
#define ADS_RING 0x20000          /* index-plane LZMA ring, as on the cartridge */
#define ADS_MAX_STREAMS 32

typedef struct {
    uint32_t off;        /* ROM offset of the chunk header */
    uint32_t w0, w1;
    uint32_t nfr, mode, ncb;
    uint32_t a_off, a_len, b_off, b_len;
    uint32_t first_frame;
} ads_chunk;

typedef struct {
    char name[64];       /* SFCD member name without ".mmstr", or "res<N>" */
    uint32_t mm_off, mm_len;     /* .mmstr (or resource table) location */
    uint32_t v_off, v_len;       /* type-1 resource payload */
    uint32_t a_off, a_len;       /* type-2 resource payload */
    int size_in_bytes;           /* resource sizes: 0 = words (ADS), 1 = bytes (Hydrogen packaging) */
    /* parsed video header */
    uint32_t hdr0, hdr1, nchunks, frames, nchap;
    uint32_t width, height;      /* from hdr1 if it is a pixel-size header, else 240x160 */
    char title[128];
    uint32_t chap_chunk[16];
    char chap_title[16][64];
    ads_chunk *chunks;
    /* audio */
    uint32_t a_nblocks, a_payload, a_payload_len, a_hdr_field;
    const uint16_t *a_sizes;     /* little-endian u16 words in ROM (unaligned-safe access via helper) */
} ads_stream;

typedef struct {
    const uint8_t *rom;
    size_t size;
    int packaging;               /* 0 = SFCD archive, 1 = Hydrogen resource tables */
    uint32_t sfcd;
    int nstreams;
    ads_stream s[ADS_MAX_STREAMS];
    uint8_t dither[16];          /* the player's ordered-dither table (read from the ROM) */
    int gain[11];                /* the player's brightness gains, level 0..10 (read from the ROM) */
    int base_brightness;         /* "BaseBrightness" from menuresources' Settings text (0 if absent) */
    int gain_bias;               /* = trunc(BaseBrightness * 2560000 / 100000), added to the gain */
} ads_cart;

int ads_open(ads_cart *c, const uint8_t *rom, size_t size);
void ads_close(ads_cart *c);
int ads_find_stream(ads_cart *c, const char *spec);   /* index or name */

/* video decoder state */
typedef struct {
    const ads_cart *c;   /* cart (dither, gain tables) */
    const ads_stream *s;
    uint32_t chunk, fin;         /* current chunk, frame within chunk */
    uint32_t frame;              /* stream frame number of the next frame */
    lzma_dec lz;
    uint16_t *probs;
    uint8_t *ring;
    uint32_t ring_pos;           /* ring offset of the next index plane */
    uint8_t cb[6144];            /* YCbCr codebook after unpredict */
    uint16_t rgb[256][16];       /* BGR555 codebook (player VRAM layout, 16 slots per entry) */
    mvq_tables vt;
    int err;
} ads_video;

int ads_video_init(ads_video *v, const ads_cart *c, int stream);
/* brightness level 0..10 (gain table {128,153,179,204,230,256,281,307,332,358,384}); default 5.
 * gain_bias is added to the gain (0 everywhere except Jimmy Neutron after its logo: 51) */
void ads_video_set_brightness(ads_video *v, int level, int gain_bias);
void ads_video_free(ads_video *v);
int ads_video_seek_chunk(ads_video *v, uint32_t chunk);
int ads_video_seek_frame(ads_video *v, uint32_t frame);  /* nearest chunk start <= frame, then decode forward */
/* decode the next frame; fb = 240*160 BGR555, idx (optional) receives the index plane */
int ads_video_next(ads_video *v, uint16_t *fb, uint8_t *idx, uint32_t *idx_len);

#endif
