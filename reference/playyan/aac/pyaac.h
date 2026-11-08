/* Play-Yan Micro / playan.fup AAC-LC decoder, bit-exact against the cartridge firmware
 * (Alphamosaic VC01, mi_fw0 / fup_sec0 video image).  See pyaac.c and docs/playyan/decoders.md. */
#ifndef PYAAC_H
#define PYAAC_H
#include <stdint.h>
#include <stddef.h>

typedef struct pyaac pyaac;

/* Firmware image as loaded at VC address 0 (only the table regions listed in pyaac.c are read).
 * which: 0 = mi_fw0 (Play-Yan Micro video image A), 1 = fup_sec0 (playan.fup video section). */
pyaac* pyaac_open(const uint8_t* image, size_t len, int which);
void pyaac_close(pyaac* d);
/* AudioSpecificConfig (from the MP4 esds).  Returns 0 or a negative firmware error. */
int pyaac_init(pyaac* d, const uint8_t* asc, size_t len);
/* Decode one raw_data_block.  pcm receives 1024 interleaved stereo frames (2048 int16) whenever the
 * frame decodes (mono is duplicated to both channels, as the firmware does).  Returns the sample
 * count the firmware reports (channels * 1024; 0 for the first two decoded frames and for frames
 * without a channel element), or one of: */
#define PYAAC_ERROR (-1)        /* the firmware rejects the frame; pyaac_error() = its error code */
#define PYAAC_UNDEFINED (-2)    /* the firmware's result depends on memory this model does not cover */
#define PYAAC_HANG (-3)         /* the firmware never returns (endless zero-length sections) */
#define PYAAC_UNSUPPORTED (-4)  /* program_config_element: not modelled */
int pyaac_frame(pyaac* d, const uint8_t* raw, size_t len, int16_t* pcm);
/* LCG state used by PNS (a firmware global at gp+756). */
uint32_t pyaac_rng(pyaac* d);
int pyaac_error(pyaac* d);
void pyaac_set_rng(pyaac* d, uint32_t v);
#endif
