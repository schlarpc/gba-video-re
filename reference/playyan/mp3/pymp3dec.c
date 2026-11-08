/* pymp3dec: decode an MP3 file as the Play-Yan music firmware does.
 *   pymp3dec FILE.mp3 OUT.pcm [--gba OUT8.raw] [--vol N] [--md5] [--info]
 * OUT.pcm: 16-bit LE stereo at the file's rate, 1152 samples per frame (the headphone PCM);
 * OUT8.raw: per 576-sample block, ring A (right) then ring B (left), signed 8-bit at 16384 Hz
 * (the GBA speaker stream); --md5 prints per-frame / per-block md5 lines instead. */
#include "pymp3.h"
#include "pymp3file.h"
#include "md5.h"
#include "fw_music.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static FILE* gout; static int gmd5; static int gblk;
static void emit(void* u, const int8_t* a, const int8_t* b) {
	(void)u;
	uint8_t blk[1152];
	memcpy(blk, a, 576); memcpy(blk + 576, b, 576);
	if (gmd5) { char h[33]; md5_hex(blk, 1152, h); printf("gba,%d,%s\n", gblk, h); }
	else if (gout) fwrite(blk, 1, 1152, gout);
	gblk++;
}

int main(int argc, char** argv) {
	if (argc < 3) { fprintf(stderr, "usage: %s FILE.mp3 OUT.pcm [--gba OUT8.raw] [--vol N] [--md5] [--info]\n", argv[0]); return 2; }
	const char* gba = NULL; int vol = 28, md5 = 0, info = 0;
	for (int i = 3; i < argc; i++) {
		if (!strcmp(argv[i], "--gba") && i + 1 < argc) gba = argv[++i];
		else if (!strcmp(argv[i], "--vol") && i + 1 < argc) vol = atoi(argv[++i]);
		else if (!strcmp(argv[i], "--md5")) md5 = 1;
		else if (!strcmp(argv[i], "--info")) info = 1;
	}
	FILE* fi = fopen(argv[1], "rb");
	if (!fi) { perror(argv[1]); return 1; }
	fseek(fi, 0, SEEK_END); long n = ftell(fi); fseek(fi, 0, SEEK_SET);
	uint8_t* data = malloc((size_t)n + 1);
	if (fread(data, 1, (size_t)n, fi) != (size_t)n) { perror("read"); return 1; }
	fclose(fi);
	pymp3file f;
	if (pymp3file_open(&f, data, (size_t)n)) { fprintf(stderr, "no MPEG-1 Layer III frame\n"); return 1; }
	if (info) printf("tag %d start %zu end %zu rate %u seconds %u title \"%s\" artist \"%s\"\n", f.tag, f.start, f.end, f.rate, f.seconds, f.title, f.artist);
	pymp3* d = pymp3_new(PYMP3_FW, sizeof PYMP3_FW, PYMP3_FW_KIND);
	if (!d) { fprintf(stderr, "firmware tables rejected\n"); return 1; }
	FILE* po = md5 ? NULL : fopen(argv[2], "wb");
	gout = gba && !md5 ? fopen(gba, "wb") : NULL; gmd5 = md5 && gba;
	pygba g; pygba_init(&g, (int)f.rate, vol);
	uint8_t buf[1441 + 8] = {0};
	int16_t pcm[2304];
	size_t off, len, avail; int k = 0, rc;
	while (pymp3file_next(&f, &off, &len, &avail)) {
		memcpy(buf, data + off, avail);
		int used;
		rc = pymp3_decode_frame(d, buf, sizeof buf, pcm, &used);
		if (rc < 0) { fprintf(stderr, "frame %d at %zu: %s\n", k, off, rc == PYMP3_UNSUPPORTED ? "mixed blocks (unsupported)" : "undefined input"); break; }
		if (rc == PYMP3_BADHDR) { k++; continue; }
		if (md5) { char h[33]; md5_hex((const uint8_t*)pcm, sizeof pcm, h); printf("pcm,%d,%zu,%s\n", k, off, h); }
		else fwrite(pcm, 2, 2304, po);
		if (gba) pygba_push(&g, pcm, 1152, emit, NULL);
		k++;
	}
	if (po) fclose(po);
	if (gout) fclose(gout);
	pymp3_free(d);
	fprintf(stderr, "%d frames\n", k);
	return 0;
}
