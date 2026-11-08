/* MP3 files as the Play-Yan music firmware reads them (see the Rust mp3file.rs for the rules):
 * ID3v2 / ID3v1 data range, AUDIO_INFO strings and seconds, frame sync and lengths. */
#ifndef PYMP3FILE_H
#define PYMP3FILE_H
#include <stddef.h>
#include <stdint.h>
typedef struct {
	const uint8_t* data; size_t size;
	size_t start, end;          /* data range: first and last byte */
	int tag;                    /* 0 none, 1 ID3v1, 2 ID3v2 */
	uint8_t title[65], artist[65];
	uint32_t seconds;
	unsigned rate;              /* first frame */
	size_t pos, last; int done; /* frame iterator */
} pymp3file;
/* 0 = ok, -1 = no MPEG-1 Layer III frame found where the player looks */
int pymp3file_open(pymp3file* f, const uint8_t* data, size_t size);
/* next frame: 1 with *off, *len (header length), *avail (bytes in the file); 0 at the end */
int pymp3file_next(pymp3file* f, size_t* off, size_t* len, size_t* avail);
#endif
