/* Nintendo MP3 Player DSP MP3 decoder (C reference; see the Rust crate gbav-playyan::nmp). */
#ifndef NMPDEC_H
#define NMPDEC_H
#include <stdint.h>
#include <stddef.h>

typedef struct NmpDec NmpDec;

NmpDec *nmp_new(void);
void nmp_free(NmpDec *d);
/* Length of the MPEG-1/2/2.5 Layer III frame at p (0 if none). */
size_t nmp_frame_size(const uint8_t *p, size_t n);
/* Decode one frame.  Returns the number of synthesis calls (granule x channel) written to calls
 * (576 int16 each, DSP order; up to 4) and the interleaved stereo PCM (1152 pairs, 576 for
 * MPEG-2/2.5 frames) to pcm; 0 if
 * the frame's main data is not yet available; -1 = bad frame; -2 = intensity stereo or mixed
 * blocks (not modelled). */
int nmp_decode_frame(NmpDec *d, const uint8_t *frame, size_t n, int16_t calls[4][576], int16_t pcm[2304]);
/* AUDIO_INFO as the DSP builds it: title / artist as 16-bit units (<= 64), the title's ID3v2
 * text encoding (0xFFFF = ID3v1), length in seconds (see gbav-playyan::nmp::id3). */
typedef struct { uint16_t title[64], artist[64]; int ntitle, nartist; uint16_t encoding; uint32_t seconds; } NmpId3;
void nmp_id3(const uint8_t *data, size_t len, NmpId3 *out);
/* The 8-bit speaker stream (no headphones): feed the synthesis output of each granule (left,
 * right; the same pointer twice for mono, n = 576 or less); returns the number of complete
 * 0x480-byte blocks written to blocks[k] (288 ch1 words then 288 ch2 words), at most 8. */
typedef struct NmpSpeaker NmpSpeaker;
NmpSpeaker *nmp_speaker_new(unsigned rate);   /* NULL for a rate without a converter */
void nmp_speaker_free(NmpSpeaker *s);
int nmp_speaker_push(NmpSpeaker *s, const int16_t *l, const int16_t *r, int n, uint16_t blocks[8][576]);
#endif
