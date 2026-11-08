/* pyg726dec FILE.asf OUT.raw [--gba OUT8.raw [--volume N]]: the ASF's G.726 audio as the video
 * firmware plays it. OUT.raw: headphone PCM (16-bit mono LE); OUT8.raw: the 8-bit GBA speaker
 * stream (576-byte ring A then 576-byte ring B per block). Both are made from the block the
 * output stage plays.
 *
 * Feed (py_fw0 set-param 0x1271a, payload loop 0x1234e): each ASF payload's even part is
 * decoded, n = (len & ~1) * 8 / bits codes, into the next of three 1152-word output buffers,
 * and the block passed on is 2 * ceil(n * bits / 8) stereo words long: at 40 kbit/s its tail is
 * what the buffer held before, at 16/24 kbit/s it is cut short. The ring is followed by 748
 * words the firmware no longer uses, then live data. A payload of more than 1152 samples (none
 * of Nintendo's 400-byte ones below 16 kbit/s) has undefined headphone output (DMA timing); a
 * write past the spare words, or a block reading a spare word nothing wrote, makes both streams
 * undefined from there on. Exit status 3 reports either. */
#include "g726.h"
#include "../video/pyvideo.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RING_WORDS 1152
#define RING_BUFS 3
#define SPILL_WORDS 748
#define RING_ALL (RING_BUFS * RING_WORDS + SPILL_WORDS)

typedef struct {
    int vol, c, last;
    int8_t a[576], b[576];
    int n;
    FILE *o;
} gbapcm;

static void gba_emit(gbapcm *g, int l, int r) {
    g->a[g->n] = (int8_t)(r >> 8);
    g->b[g->n] = (int8_t)(l >> 8);
    if (++g->n == 576) {
        fwrite(g->a, 1, 576, g->o);
        fwrite(g->b, 1, 576, g->o);
        g->n = 0;
    }
}

static int gba_vol(const gbapcm *g, int s) {
    int p = s * g->vol;
    p = (p + (p < 0 ? 31 : 0)) >> 5;
    return p < -32768 ? -32768 : p > 32767 ? 32767 : p;
}

/* 8000 Hz takes the duplicating branch of the resampler (0x21bf6, shift 13) */
static void gba_push(gbapcm *g, int s) {
    int v = gba_vol(g, s);
    int pos = abs((g->c << 13) / 8000);
    if (++g->c > 8000) g->c = 0;
    gba_emit(g, v, v);
    int dup = g->last + 1 != pos;
    g->last = pos;
    if (dup) gba_emit(g, v, v);
}

int main(int argc, char **argv) {
    if (argc < 3) return fprintf(stderr, "usage: pyg726dec FILE.asf OUT.raw [--gba OUT8.raw [--volume N]]\n"), 2;
    const char *gpath = NULL;
    int vol = 28;
    for (int i = 3; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--gba")) gpath = argv[i + 1];
        else if (!strcmp(argv[i], "--volume")) vol = atoi(argv[i + 1]);
        else return fprintf(stderr, "unknown option %s\n", argv[i]), 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) return perror(argv[1]), 2;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc((size_t)n);
    if (fread(b, 1, (size_t)n, f) != (size_t)n) return 2;
    fclose(f);
    pyv_asf a;
    if (pyv_asf_parse(b, (size_t)n, &a) < 0 || !a.have_audio || a.format_tag != 0x45 || !a.rate) return fprintf(stderr, "no G.726 audio\n"), 1;
    int bits = (int)((uint64_t)a.avg_bytes * 8 / a.rate);
    if (bits < 2 || bits > 5) return 1;
    pyg726 s;
    pyg726_init(&s, bits);
    FILE *o = fopen(argv[2], "wb");
    gbapcm g = {vol, 0, 0, {0}, {0}, 0, gpath ? fopen(gpath, "wb") : NULL};
    static int16_t ring[RING_ALL];
    static uint8_t have[RING_ALL];
    memset(have, 1, RING_BUFS * RING_WORDS);
    int16_t pcm[65536];
    size_t total = 0, k = 0;
    int undefined = 0, hp_ok = 1, gba_ok = 1;
    for (size_t i = 0; i < a.naudio && !undefined; i++) {
        const uint8_t *p = a.audio[i].data;
        for (size_t j = 0; j < a.audio[i].nfrags && !undefined; p += a.audio[i].frags[j++], k++) {
            size_t len = a.audio[i].frags[j] & ~(size_t)1;
            if (len * 8 / (size_t)bits > sizeof pcm / sizeof *pcm) return 1;
            size_t m = pyg726_decode_payload(&s, p, len, pcm);
            size_t words = 2 * ((m * (size_t)bits + 7) / 8), base = (k % RING_BUFS) * RING_WORDS;
            if (base + (m > words ? m : words) > RING_ALL) hp_ok = gba_ok = 0; /* live data */
            if (m > RING_WORDS) hp_ok = 0;
            if (gba_ok) {
                memcpy(ring + base, pcm, m * 2);
                memset(have + base, 1, m);
                for (size_t x = 0; x < words; x++)
                    if (!have[base + x]) gba_ok = 0;
            }
            if (!gba_ok) hp_ok = 0;
            if (hp_ok) { /* the headphones play the block, as the speaker stage reads it */
                fwrite(ring + base, 2, words, o);
                total += words;
            }
            if (g.o && gba_ok)
                for (size_t x = 0; x < words; x++) gba_push(&g, ring[base + x]);
            undefined = !gba_ok || (!hp_ok && !g.o);
        }
    }
    fclose(o);
    if (g.o) fclose(g.o);
    printf("%zu samples, %d bits/sample, %u Hz\n", total, bits, a.rate);
    if (!hp_ok || (g.o && !gba_ok)) {
        fprintf(stderr, "undefined from here on: %s\n", !gba_ok ? "the output ring overruns into live data" : "a payload of more than 1152 samples");
        return 3;
    }
    return 0;
}
