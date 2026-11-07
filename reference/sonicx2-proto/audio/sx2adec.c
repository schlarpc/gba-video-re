/* sx2adec: Sonic X Vol 2 (proto) audio extractor.
   usage: sx2adec ROM [--list] [--stream intro|ep103|ep104|N|0xOFF] [--start-block K] [--count N]
                      [--out file.s8|file.wav] [--md5] [--clip] */
#include "sx2_adpcm.h"
#include "md5.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

typedef struct { size_t slot, cont, res, len; unsigned frames; } stream_t;

/* Containers are reached through build-time "file slots" {u32 ptr, u32 size} in ROM; a container
   starts with u32 (size<<4 | count). Find every slot whose target matches that shape, then take
   the container's type-2 entry. Order = ascending container offset. */
static int find_streams(const uint8_t *d, size_t n, stream_t *s, int max)
{
    int k = 0;
    for (size_t i = 0; i + 8 <= n && k < max; i += 4) {
        uint32_t p = rd32(d + i), sz = rd32(d + i + 4);
        if (p < 0x08000000 || p - 0x08000000 + 8 > n || sz == 0 || sz >= 0x2000000) continue;
        size_t c = p - 0x08000000;
        uint32_t w = rd32(d + c);
        if ((w >> 4) != sz || (w & 15) == 0 || (w & 15) > 8 || c + sz > n) continue;
        size_t e = c + 4;
        for (unsigned j = 0; j < (w & 15) && e + 4 <= n; j++) {
            uint32_t h = rd32(d + e);
            if (h >> 28 == 2) {
                s[k].slot = i; s[k].cont = c; s[k].res = e + 4; s[k].len = h & 0xfffffff;
                sx2a_info in;
                if (sx2a_parse(d + e + 4, h & 0xfffffff, &in) == 0) { s[k].frames = in.frames; k++; }
                break;
            }
            e += 4 + (h & 0xfffffff);
        }
    }
    for (int a = 0; a < k; a++) for (int b = a + 1; b < k; b++)
        if (s[b].cont < s[a].cont) { stream_t t = s[a]; s[a] = s[b]; s[b] = t; }
    return k;
}

static void wav_hdr(FILE *f, uint32_t n)
{
    uint8_t h[44]; memcpy(h, "RIFF", 4); uint32_t v = 36 + n; memcpy(h + 4, &v, 4);
    memcpy(h + 8, "WAVEfmt ", 8); v = 16; memcpy(h + 16, &v, 4);
    uint16_t s = 1; memcpy(h + 20, &s, 2); memcpy(h + 22, &s, 2);
    v = 16384; memcpy(h + 24, &v, 4); memcpy(h + 28, &v, 4); s = 1; memcpy(h + 32, &s, 2);
    s = 8; memcpy(h + 34, &s, 2); memcpy(h + 36, "data", 4); memcpy(h + 40, &n, 4);
    fwrite(h, 1, 44, f);
}

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: %s ROM [--list] [--stream S] [--start-block K] [--count N] [--out F] [--md5] [--clip]\n", argv[0]); return 2; }
    const char *stream = "ep103", *outp = NULL; int list = 0, md5 = 0, clip = 0; long start = 0, count = -1;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--list")) list = 1;
        else if (!strcmp(argv[i], "--md5")) md5 = 1;
        else if (!strcmp(argv[i], "--clip")) clip = 1;
        else if (!strcmp(argv[i], "--stream") && i + 1 < argc) stream = argv[++i];
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) outp = argv[++i];
        else if (!strcmp(argv[i], "--start-block") && i + 1 < argc) start = strtol(argv[++i], 0, 0);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = strtol(argv[++i], 0, 0);
        else { fprintf(stderr, "bad arg %s\n", argv[i]); return 2; }
    }
    FILE *f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); size_t n = ftell(f); rewind(f);
    uint8_t *d = malloc(n); if (fread(d, 1, n, f) != n) return 1; fclose(f);
    stream_t s[16]; int ns = find_streams(d, n, s, 16);
    /* names for this cart: largest two are the episodes in ROM order, the small one the intro */
    const char *names[16]; int ep = 103;
    for (int i = 0; i < ns; i++) names[i] = NULL;
    for (int i = 0; i < ns; i++) {
        static char buf[16][16];
        if (s[i].frames < 1000) snprintf(buf[i], 16, "intro"); else snprintf(buf[i], 16, "ep%d", ep++);
        names[i] = buf[i];
    }
    if (list) {
        for (int i = 0; i < ns; i++) {
            sx2a_info in; sx2a_parse(d + s[i].res, s[i].len, &in);
            printf("%d %-6s slot=0x%zx container=0x%zx audio=0x%zx len=%zu blocks=%u frames=%zu samples=%zu (%.2f s @16384 Hz)\n",
                   i, names[i], s[i].slot, s[i].cont, s[i].res, s[i].len, in.nblocks, in.frames, in.samples, in.samples / 16384.0);
        }
        return 0;
    }
    int si = -1;
    for (int i = 0; i < ns; i++) if (!strcmp(stream, names[i])) si = i;
    if (si < 0) { char *e; long v = strtol(stream, &e, 0);
        if (!*e) for (int i = 0; i < ns; i++) if ((long)i == v || (long)s[i].res == v) si = i; }
    if (si < 0) { fprintf(stderr, "no stream %s\n", stream); return 1; }
    sx2a_info in; sx2a_parse(d + s[si].res, s[si].len, &in);
    sx2a_ctx c; sx2a_init(&c, &in, clip);
    if (start) sx2a_seek_block(&c, &in, (unsigned)start);
    size_t first = (size_t)start * SX2A_BLOCK_SAMPLES;
    size_t total = in.samples > first ? in.samples - first : 0;
    size_t nb = (total + SX2A_BLOCK_SAMPLES - 1) / SX2A_BLOCK_SAMPLES;
    if (count >= 0 && (size_t)count < nb) { nb = count; if (nb * SX2A_BLOCK_SAMPLES < total) total = nb * SX2A_BLOCK_SAMPLES; }
    int8_t *pcm = malloc(total + 1);
    sx2a_decode(&c, pcm, (uint32_t)total);
    if (getenv("SX2A_STATS")) fprintf(stderr, "headers: resync %lu continue %lu\n", sx2a_hdr_resync, sx2a_hdr_cont);
    if (md5) {
        for (size_t b = 0; b < nb; b++) {
            size_t o = b * SX2A_BLOCK_SAMPLES, m = total - o < SX2A_BLOCK_SAMPLES ? total - o : SX2A_BLOCK_SAMPLES;
            char h[33]; md5_hex(pcm + o, m, h);
            printf("%zu,%zu,%s\n", b + start, m, h);
        }
    }
    if (outp) {
        FILE *o = fopen(outp, "wb");
        size_t L = strlen(outp);
        if (L > 4 && !strcmp(outp + L - 4, ".wav")) {
            wav_hdr(o, (uint32_t)total);
            for (size_t i = 0; i < total; i++) fputc((uint8_t)(pcm[i] + 128), o);
        } else fwrite(pcm, 1, total, o);
        fclose(o);
    }
    return 0;
}
