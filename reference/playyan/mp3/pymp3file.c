#include "pymp3file.h"
#include <string.h>

static const unsigned KBPS[16] = {0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0};
static const unsigned RATE[4] = {44100, 48000, 32000, 0};

static uint32_t be(const uint8_t* b, int n) { uint32_t v = 0; while (n--) v = v << 8 | *b++; return v; }

static int accept(uint32_t w, size_t* len, unsigned* rate, unsigned* kbps) {
	if ((w & 0xfff00000u) != 0xfff00000u) return 0;
	unsigned layer = w >> 17 & 3, bri = w >> 12 & 15, sri = w >> 10 & 3;
	if (layer > 2 || bri == 0 || bri > 14 || sri > 2 || (w & 3) == 2 || !(w & 0x80000) || layer != 1) return 0;
	*kbps = KBPS[bri]; *rate = RATE[sri];
	*len = 144000u * *kbps / *rate + (w >> 9 & 1);
	return 1;
}

static long sync(const pymp3file* f, size_t pos, size_t limit) {
	if (pos + 4 > f->size) return -1;
	uint32_t w = be(f->data + pos, 4);
	size_t p = pos, n = 0, l; unsigned r, k;
	for (;;) {
		if (accept(w, &l, &r, &k)) return (long)p;
		if (p + 4 >= f->size) return -1;
		w = w << 8 | f->data[p + 4]; p++;
		if (++n > limit) return -1;
	}
}

static void text(uint8_t* field, const uint8_t* d, long n) {
	if (n <= 1) return;
	int enc = d[0], o = 0;
	for (long i = 1; i < n; i++) {
		if (!d[i]) break;
		if (!enc && o < 64) field[o++] = d[i];
	}
	field[o] = 0;
}

/* 0x21140: returns the data start, or -1 when the walk fails */
static long id3v2(pymp3file* f) {
	const uint8_t* d = f->data;
	if (f->size < 10 || memcmp(d, "ID3", 3)) return -1;
	long r7 = (d[6] & 127) << 21 | (d[7] & 127) << 14 | (d[8] & 127) << 7 | (d[9] & 127);
	if (r7 >= (long)f->size) return -1;
	int ver = d[3], hl = ver == 2 ? 6 : 10, idl = ver == 2 ? 3 : 4;
	long pos = 10;
	if (ver >= 2)
		while (r7 > 0) {
			if (pos + hl > (long)f->size) return -1;
			const uint8_t* h = d + pos; pos += hl;
			int zero = 1;
			for (int i = 0; i < idl; i++) if (h[i]) zero = 0;
			if (zero) break;
			long size = (long)(ver == 2 ? be(h + 3, 3) : be(h + 4, 4));
			if (size > r7) return -1;
			if (pos + size > (long)f->size) return -1;
			const uint8_t* body = d + pos; pos += size;
			if (ver == 2 ? !memcmp(h, "TT2", 3) : !memcmp(h, "TIT2", 4)) text(f->title, body, size);
			else if (ver == 2 ? !memcmp(h, "TP1", 3) : !memcmp(h, "TPE1", 4)) text(f->artist, body, size);
			r7 -= size + hl;
		}
	long s = pos + r7 - 10;
	return s < 0 ? 0 : s;
}

int pymp3file_open(pymp3file* f, const uint8_t* data, size_t size) {
	memset(f, 0, sizeof *f);
	f->data = data; f->size = size;
	if (!size) return -1;
	int v1 = size >= 128 && !memcmp(data + size - 128, "TAG", 3);
	long s = id3v2(f);
	if (s >= 0) { f->tag = 2; f->start = (size_t)s; }
	else if (v1) {
		f->tag = 1;
		const uint8_t* t = data + size - 128;
		for (int fld = 0; fld < 2; fld++) {
			uint8_t* dst = fld ? f->artist : f->title; int off = fld ? 33 : 3; uint8_t fill = 0;
			for (int i = 29; i >= 0; i--) {
				uint8_t c = t[off + i];
				if (c == 32) dst[i] = fill; else { dst[i] = c; fill = 32; }
			}
		}
	}
	f->end = v1 ? size - 129 : size - 1;
	long p = sync(f, f->start, 1441);
	if (p < 0) return -1;
	size_t len; unsigned rate, kbps;
	accept(be(data + p, 4), &len, &rate, &kbps);
	f->rate = rate;
	int32_t bits = (int32_t)(((long long)f->end - (long long)f->start) * 8 + 8);
	f->seconds = (uint32_t)((bits / (int32_t)kbps) / 1000);
	size_t b0 = (size_t)p + 4, b1 = (size_t)p + len < size ? (size_t)p + len : size;
	const uint8_t* body = data + b0;
	size_t bl = b1 > b0 ? b1 - b0 : 0;
	if (bl >= 120)
		for (size_t o = 0; o + 120 <= bl; o++)
			if (!memcmp(body + o, "Xing", 4)) {
				if (be(body + o + 4, 4) & 1) f->seconds = be(body + o + 8, 4) * 1152u / rate;
				break;
			}
	if (bl >= 50 && !memcmp(body + 32, "VBRI", 4)) {
		uint32_t fr = be(body + 46, 4);
		if (fr) f->seconds = fr * 1152u / rate;
	}
	f->pos = f->last = f->start;
	return 0;
}

int pymp3file_next(pymp3file* f, size_t* off, size_t* len, size_t* avail) {
	if (f->done || f->last > f->end) return 0;
	long p = sync(f, f->pos, 144000);
	if (p < 0) { f->done = 1; return 0; }
	unsigned r, k;
	accept(be(f->data + p, 4), len, &r, &k);
	*off = (size_t)p;
	*avail = *len < f->size - (size_t)p ? *len : f->size - (size_t)p;
	f->last = (size_t)p; f->pos = (size_t)p + *len;
	return 1;
}
