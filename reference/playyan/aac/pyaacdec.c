/* pyaacdec: decode the AAC track of an MP4 the way the Play-Yan Micro / playan.fup firmware does.
 *   pyaacdec file.mp4 out.pcm     -> 16-bit little-endian interleaved stereo at the track's rate
 * Writes the PCM of every frame the firmware reports (not the first two), one line of status
 * per non-decoded frame on stderr. Tables come from fw_tables.h (generated at build time). */
#include "pyaac.h"
#include "fw_tables.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t be32(const uint8_t* p) { return (uint32_t) p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }

typedef struct { const uint8_t* d; size_t n; } buf;
/* find child box `t` in [s, e); returns payload start and sets *end */
static size_t child(buf f, size_t s, size_t e, const char* t, size_t* end) {
	while (s + 8 <= e) {
		uint64_t sz = be32(f.d + s); size_t h = 8;
		if (sz == 1) { sz = (uint64_t) be32(f.d + s + 8) << 32 | be32(f.d + s + 12); h = 16; }
		else if (sz == 0) sz = e - s;
		if (sz < h || s + sz > e) return 0;
		if (!memcmp(f.d + s + 4, t, 4)) { *end = s + sz; return s + h; }
		s += sz;
	}
	return 0;
}
static size_t desc(const uint8_t* d, size_t p, int* tag, size_t* len) {
	*tag = d[p++]; *len = 0;
	for (int i = 0; i < 4; i++) { uint8_t b = d[p++]; *len = *len << 7 | (b & 0x7f); if (!(b & 0x80)) break; }
	return p;
}

int main(int argc, char** argv) {
	if (argc != 3) { fprintf(stderr, "usage: pyaacdec file.mp4 out.pcm\n"); return 2; }
	FILE* fp = fopen(argv[1], "rb");
	if (!fp) { perror(argv[1]); return 1; }
	fseek(fp, 0, SEEK_END); long n = ftell(fp); fseek(fp, 0, SEEK_SET);
	uint8_t* d = malloc(n);
	if (fread(d, 1, n, fp) != (size_t) n) return 1;
	fclose(fp);
	buf f = {d, (size_t) n};
	static uint8_t img[FW_IMAGE_LEN];
	for (size_t i = 0; i < sizeof FW_SEGS / sizeof FW_SEGS[0]; i++) memcpy(img + FW_SEGS[i].addr, FW_SEGS[i].d, FW_SEGS[i].len);
	size_t me, te, ae;
	size_t ms = child(f, 0, f.n, "moov", &me);
	if (!ms) { fprintf(stderr, "no moov\n"); return 1; }
	/* first trak with an mp4a sample entry */
	size_t p = ms;
	while (p + 8 <= me) {
		size_t ts = child(f, p, me, "trak", &te);
		if (!ts) break;
		p = te;
		size_t e1, e2, e3, e4;
		size_t md = child(f, ts, te, "mdia", &e1), mi = md ? child(f, md, e1, "minf", &e2) : 0;
		size_t st = mi ? child(f, mi, e2, "stbl", &e3) : 0;
		size_t sd = st ? child(f, st, e3, "stsd", &e4) : 0;
		if (!sd || memcmp(d + sd + 12, "mp4a", 4)) continue;
		size_t es = child(f, sd + 8 + 8 + 28, e4, "esds", &ae);
		if (!es) { fprintf(stderr, "no esds\n"); return 1; }
		int tag; size_t len, q = desc(d, es + 4, &tag, &len);
		uint8_t fl = d[q + 2]; q += 3;
		if (fl & 0x80) q += 2;
		if (fl & 0x40) q += 1 + d[q];
		if (fl & 0x20) q += 2;
		q = desc(d, q, &tag, &len);
		q = desc(d, q + 13, &tag, &len);
		pyaac* dec = pyaac_open(img, sizeof img, FW_WHICH);
		pyaac_init(dec, d + q, len);
		size_t zs, cs, ss, x;
		zs = child(f, st, e3, "stsz", &x); ss = child(f, st, e3, "stsc", &x);
		cs = child(f, st, e3, "stco", &x);
		int co64 = 0;
		if (!cs) { cs = child(f, st, e3, "co64", &x); co64 = 1; }
		uint32_t fixed = be32(d + zs + 4), ns = be32(d + zs + 8), nc = be32(d + cs + 4), nsc = be32(d + ss + 4);
		FILE* out = fopen(argv[2], "wb");
		int16_t pcm[2048];
		uint32_t si = 0;
		for (uint32_t c = 0; c < nc; c++) {
			uint32_t per = 0;
			for (uint32_t k = 0; k < nsc; k++) if (c + 1 >= be32(d + ss + 8 + 12 * k)) per = be32(d + ss + 12 + 12 * k);
			uint64_t off = co64 ? (uint64_t) be32(d + cs + 8 + 8 * c) << 32 | be32(d + cs + 12 + 8 * c) : be32(d + cs + 8 + 4 * c);
			for (uint32_t k = 0; k < per && si < ns; k++, si++) {
				uint32_t sz = fixed ? fixed : be32(d + zs + 12 + 4 * si);
				int r = pyaac_frame(dec, d + off, sz, pcm);
				if (r > 0) fwrite(pcm, 2, 2048, out);
				else if (r < 0) fprintf(stderr, "frame %u: %s %d\n", si, r == PYAAC_ERROR ? "firmware error" : "outside the model", r == PYAAC_ERROR ? pyaac_error(dec) : r);
				off += sz;
			}
		}
		fclose(out);
		pyaac_close(dec);
		return 0;
	}
	fprintf(stderr, "no mp4a track\n");
	return 1;
}
