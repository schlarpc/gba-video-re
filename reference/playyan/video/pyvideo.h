/* Play-Yan video reference decoder: ASF container, MPEG-4 Part 2 Simple Profile as the VC01
 * video firmware (py_fw0 / mi_fw0 / fup_sec0) decodes it, and the firmware's output stages
 * (240x160 frame with brightness, 60x40 thumbnail, RGB565 -> BGR555 on the cartridge window).
 * C99. Non-standard tables come from fw_tables.h, generated from your ROM by the Makefile. */
#pragma once
#include <stddef.h>
#include <stdint.h>

/* ---- ASF */
typedef struct {
    uint8_t stream;
    uint32_t number, pts_ms;
    int key;
    uint8_t *data;
    size_t len;
    size_t *frags, nfrags; /* lengths of the ASF payloads the object was assembled from */
} pyv_object;

typedef struct {
    int have_video, have_audio;
    uint32_t width, height;
    uint8_t fourcc[4];
    uint8_t *codec_data;
    size_t codec_len;
    uint8_t vstream, astream;
    uint16_t format_tag, channels, block_align, bits_per_sample;
    uint32_t rate, avg_bytes;
    uint32_t packet_size;
    pyv_object *video, *audio;
    size_t nvideo, naudio;
} pyv_asf;

int pyv_asf_parse(const uint8_t *b, size_t n, pyv_asf *out); /* 0 ok, <0 error */
void pyv_asf_free(pyv_asf *a);

/* ---- MPEG-4 */
typedef struct pyv_dec pyv_dec;
enum { PYV_HEADERS = 0, PYV_FRAME_I = 1, PYV_FRAME_P = 2, PYV_NOT_CODED = 3 };
pyv_dec *pyv_dec_new(void);
void pyv_dec_free(pyv_dec *d);
/* decode one media object; returns PYV_* or a negative error */
int pyv_dec_decode(pyv_dec *d, const uint8_t *obj, size_t n);
/* last picture: displayed size and planes (stride = MB-aligned width) */
int pyv_dec_picture(const pyv_dec *d, uint32_t *w, uint32_t *h, size_t *stride, const uint8_t **y, const uint8_t **cb, const uint8_t **cr);

/* ---- output stages; return 0 (or -1 without a decoded picture) */
int pyv_frame_rgb565(const pyv_dec *d, uint32_t brightness, uint16_t out[240 * 160]);
int pyv_thumb_rgb565(const pyv_dec *d, uint16_t out[60 * 40]);
uint16_t pyv_rgb565_to_bgr555(uint16_t p);
/* the fit keeps the firmware's temp image and scratch between calls (they only show in the
 * letterbox bars); reset them before a new file */
void pyv_fit_reset(void);
uint32_t pyv_garage_hash(const uint16_t *px, size_t n);
