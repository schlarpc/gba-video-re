/* The C reference's MP3 kernels (reference/playyan/mp3: pymp3.c, kernels.h) exported one by one, so
 * that mp3_test.py can check the firmware's vector routines, run in the VC01 model, against them.
 * Every constant comes from the image passed to mp3k_new (py_fw1, mi_fw1 or fup_sec1). Built by
 * mp3ref.py into $PLAYYAN_WORK/build. */
#include "pymp3.c"

void* mp3k_new(const uint8_t* img, size_t len) {
	return pymp3_new(img, len, pymp3_image_kind(img, len));
}
void mp3k_free(void* d) { pymp3_free((pymp3*)d); }

/* bt: block type 0, 1 or 3 (long) or 2 (short); X, ov: 18 values; out, nov: 18 values */
void mp3k_imdct(void* d, int bt, const int32_t* X, const int32_t* ov, int32_t* out, int32_t* nov) {
	const mp3consts* k = &((pymp3*)d)->kc;
	if (bt == 2) mp3_imdct_short(X, ov, out, nov, k);
	else mp3_imdct_long(X, ov, out, nov, bt == 1 ? 1 : bt == 3 ? 2 : 0, k);
}

/* 32 subband samples -> the 34 rows 0x49c stores */
void mp3k_dct32(void* d, const int32_t* x, int32_t* row) { mp3_dct32(x, row, &((pymp3*)d)->kc); }

/* one synthesis slot of channel ch (0x316b4 with the window): row = this slot's 34 dct32 rows;
 * out[2k] = sample k (32 samples, stride 2 as the firmware writes them) */
void mp3k_synth_slot(void* d, int ch, const int32_t* row, int16_t* out) { synth_slot((pymp3*)d, ch, row, out); }
