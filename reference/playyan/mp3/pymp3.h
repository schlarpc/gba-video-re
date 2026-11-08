/* Play-Yan music firmware MP3 decoder (VC01 py_fw1 0x222f2 and below), bit-exact C reference.
 * Tables that are not ISO standards are read from the firmware image at init (pymp3_init). */
#ifndef PYMP3_H
#define PYMP3_H
#include <stdint.h>
#include <stddef.h>

enum { PYMP3_IMG_PY = 0, PYMP3_IMG_MI = 1, PYMP3_IMG_FUP = 2 };

/* results of pymp3_decode_frame */
enum {
	PYMP3_OK = 1,          /* frame decoded, pcm holds 1152 stereo samples */
	PYMP3_SILENCE = 2,     /* main_data_begin beyond the reservoir: firmware outputs 1152 x 0 */
	PYMP3_BADHDR = 0,      /* header rejected (firmware returns 0, pcm untouched) */
	PYMP3_UNDEFINED = -1,  /* input whose firmware result depends on memory we do not model */
	PYMP3_UNSUPPORTED = -2 /* mixed blocks: the firmware reorders them from stack garbage */
};

typedef struct pymp3 pymp3;

/* img: the music image (py_fw1, mi_fw1 or fup_sec1, as loaded at VC 0); kind: PYMP3_IMG_*.
 * Returns NULL if a table does not match its recorded md5. */
pymp3* pymp3_new(const uint8_t* img, size_t len, int kind);
void pymp3_free(pymp3* d);
/* Identify the image by md5: returns PYMP3_IMG_* or -1. */
int pymp3_image_kind(const uint8_t* img, size_t len);
/* Decode one frame starting at its sync word (avail bytes readable). pcm: 2304 int16, L/R
 * interleaved. *consumed = frame length in bytes (firmware's own computation). */
int pymp3_decode_frame(pymp3* d, const uint8_t* frame, size_t avail, int16_t* pcm, int* consumed);
void pymp3_set_strict(pymp3* d, int strict);
/* md5 of the constants read from the image (checked by pymp3_new) */
void pymp3_tables_md5(const pymp3* d, char out[33]);

/* The 8-bit GBA speaker stream (py_fw1 0x3185e): volume, high byte, rate conversion to
 * 16384 Hz, 576-sample blocks. Ring A = right channel, ring B = left. */
typedef struct {
	int rate, vol, c, last;
	int n;                 /* samples in the current block */
	int8_t a[576], b[576];
} pygba;
void pygba_init(pygba* g, int rate, int vol);
/* Push n stereo sample pairs; calls emit(user, a, b) for every complete 576-sample block. */
void pygba_push(pygba* g, const int16_t* lr, int n, void (*emit)(void*, const int8_t*, const int8_t*), void* user);
#endif
