/* fvmvdec -- CLI for the FVMV reference decoder (Pokemon GBA Video Vol 1-4).
 *   fvmvdec ROM_OR_FVMVFILE [--list] [--stream IDX|OFF] [--start-frame N] [--count M]
 *                           [--out DIR] [--rgb] [--md5] [--stats] [--init EWRAM.bin]
 * --list         print the FVMV streams in the file and exit.
 * --stream       pick a stream by index (0, 1) or header offset (default 0).
 * --start-frame  N > 0 starts decoding at the seek point at or before N (as the player's
 *                fast-forward does) and prints/writes frames from N on.
 * --out DIR      DIR/NNNNN.rgb555: the decoder output buffer (240x160 u16 LE, bit 15 kept).
 * --rgb          with --out, also DIR/NNNNN.ppm (bit 15 dropped).
 * --md5          per frame: index, md5(buffer, 16 bit), md5(buffer & 0x7fff).
 * --stats        mode counts per block size and off-picture access counters.
 * --init FILE    256 KiB EWRAM image used as the initial memory (default: zeros).
 * --audio-md5    per audio block: frame, block, md5(256 x int32 synthesis output), md5(1024 x s8 PCM).
 * --wav FILE     8-bit mono 65536 Hz WAV of the decoded audio (the bytes the player plays).
 * --no-video     skip video decoding (audio only).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fvmvdec.h"
#include "fvmvaudio.h"
#include "md5.h"

static void hex(const uint8_t d[16], char *s) { for (int i = 0; i < 16; i++) sprintf(s + 2 * i, "%02x", d[i]); }

static const char *sizename[16] = { "1x1", "1x2", "1x4", "1x8", "2x1", "2x2", "2x4", "2x8",
                                    "4x1", "4x2", "4x4", "4x8", "8x1", "8x2", "8x4", "8x8" };
static const char *modename[8] = { "skip", "mv", "splitH", "splitV", "mv+off", "fill", "pattern", "raw" };

int main(int argc, char **argv)
{
    const char *in = NULL, *outdir = NULL, *init = NULL;
    long want = 0, start = 0, count = -1;
    int list = 0, md5 = 0, stats = 0, rgb = 0, amd5 = 0, novideo = 0;
    const char *wav = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--start-frame") && i + 1 < argc) start = atol(argv[++i]);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = atol(argv[++i]);
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) outdir = argv[++i];
        else if (!strcmp(argv[i], "--stream") && i + 1 < argc) want = strtol(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--init") && i + 1 < argc) init = argv[++i];
        else if (!strcmp(argv[i], "--list")) list = 1;
        else if (!strcmp(argv[i], "--md5")) md5 = 1;
        else if (!strcmp(argv[i], "--stats")) stats = 1;
        else if (!strcmp(argv[i], "--rgb")) rgb = 1;
        else if (!strcmp(argv[i], "--audio-md5")) amd5 = 1;
        else if (!strcmp(argv[i], "--no-video")) novideo = 1;
        else if (!strcmp(argv[i], "--wav") && i + 1 < argc) wav = argv[++i];
        else if (argv[i][0] != '-' && !in) in = argv[i];
        else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 2; }
    }
    if (!in) {
        fprintf(stderr, "usage: fvmvdec ROM_OR_FVMV [--list] [--stream IDX|OFF] [--start-frame N] [--count M] "
                        "[--out DIR] [--rgb] [--md5] [--stats] [--init EWRAM.bin]\n");
        return 2;
    }
    FILE *f = fopen(in, "rb");
    if (!f) { perror(in); return 1; }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *rom = malloc((size_t)n);
    if (!rom || fread(rom, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "read error\n"); return 1; }
    fclose(f);

    /* find streams: "FVMV" magic at a 4-byte aligned offset with a 240x160 header */
    long offs[16]; int ns = 0;
    for (long o = 0; o + 0x20 <= n && ns < 16; o += 4) {
        if (memcmp(rom + o, "FVMV", 4)) continue;
        fvmv_header h;
        if (fvmv_parse_header(rom + o, (size_t)(n - o), &h) || h.width != FVMV_W || h.height != FVMV_H) continue;
        if (o + 0x20 + (long)h.data_size > n) continue;
        offs[ns++] = o;
    }
    if (list) {
        for (int i = 0; i < ns; i++) {
            fvmv_header h;
            fvmv_parse_header(rom + offs[i], (size_t)(n - offs[i]), &h);
            printf("%d 0x%lx frames=%u %ux%u period=%u lines data=%u max_record=%u audio_buf=%u\n", i, offs[i],
                   h.nframes, h.width, h.height, h.period, h.data_size, h.max_record, h.audio_buf);
        }
        return 0;
    }
    long so = -1;
    if (want >= 0 && want < ns) so = offs[want];
    else for (int i = 0; i < ns; i++) if (offs[i] == want) so = want;
    if (so < 0) { fprintf(stderr, "no FVMV stream %ld (see --list)\n", want); return 1; }
    const uint8_t *s = rom + so;
    fvmv_header h;
    fvmv_parse_header(s, (size_t)(n - so), &h);
    fvmv_record *rec = malloc(sizeof *rec * h.nframes);
    long nr = fvmv_walk(s, (size_t)(n - so), &h, rec, h.nframes);
    if (nr != (long)h.nframes) { fprintf(stderr, "record walk failed (%ld of %u)\n", nr, h.nframes); return 1; }

    fvmv_dec *d = calloc(1, sizeof *d);
    fvmv_init(d);
    if (init) {
        FILE *g = fopen(init, "rb");
        if (!g || fread(d->ewram, 1, FVMV_EWRAM_SIZE, g) != FVMV_EWRAM_SIZE) { fprintf(stderr, "bad --init\n"); return 1; }
        fclose(g);
    }
    /* starting point: the seek point at or before start (record.back points to it) */
    long first = 0;
    if (start > 0) {
        if (start >= nr) { fprintf(stderr, "start beyond end\n"); return 1; }
        uint32_t target = rec[start].off + (uint32_t)rec[start].back;
        for (first = start; first > 0 && rec[first].off != target; first--) ;
        d->frame_no = (uint32_t)first;   /* keeps the player's buffer parity (it toggles per decode) */
    }
    long last = count < 0 ? nr : start + count;
    if (last > nr) last = nr;
    int rc = 0;
    fvmv_audio au;
    fvmv_audio_init(&au);
    FILE *wf = NULL;
    uint32_t wav_bytes = 0;
    if (wav) {
        wf = fopen(wav, "wb");
        if (!wf) { perror(wav); return 1; }
        static const uint8_t hdr[44] = { 0 };
        fwrite(hdr, 1, 44, wf);
    }
    for (long i = first; i < last; i++) {
        const uint8_t *v = s + rec[i].off + 0x14;
        if (amd5 || wf) {
            const uint8_t *ab = v + rec[i].vlen + 4;
            for (uint32_t k = 0; k < rec[i].alen / FVMV_ABLOCK_BYTES; k++) {
                int32_t syn[FVMV_ABLOCK_SAMPLES];
                int8_t pcm[FVMV_AOUT_BYTES];
                fvmv_audio_synth(&au, ab + k * FVMV_ABLOCK_BYTES, syn);
                fvmv_audio_output(&au, syn, FVMV_ABLOCK_SAMPLES, pcm);
                if (i < start) continue;
                if (amd5) {
                    uint8_t sb[1024], dg[16], dg2[16];
                    char x1[33], x2[33];
                    for (int j = 0; j < 256; j++) {
                        uint32_t u = (uint32_t)syn[j];
                        sb[4 * j] = (uint8_t)u; sb[4 * j + 1] = (uint8_t)(u >> 8); sb[4 * j + 2] = (uint8_t)(u >> 16); sb[4 * j + 3] = (uint8_t)(u >> 24);
                    }
                    md5_buf(sb, 1024, dg); md5_buf((const uint8_t *)pcm, 1024, dg2);
                    hex(dg, x1); hex(dg2, x2);
                    printf("a,%ld,%u,%s,%s\n", i, k, x1, x2);
                }
                if (wf) {
                    uint8_t u8[FVMV_AOUT_BYTES];
                    for (int j = 0; j < FVMV_AOUT_BYTES; j++) u8[j] = (uint8_t)(pcm[j] + 128);
                    fwrite(u8, 1, sizeof u8, wf);
                    wav_bytes += sizeof u8;
                }
            }
        }
        if (novideo) continue;
        uint64_t e0 = d->err;
        fvmv_decode_frame(d, v, rec[i].vlen);
        if (d->err != e0) { fprintf(stderr, "frame %ld: stream error\n", i); rc = 1; }
        if (i < start) continue;
        const uint8_t *fb = fvmv_last_frame(d);
        if (md5) {
            uint8_t dg[16], dg2[16];
            char a[33], b[33];
            static uint8_t tmp[FVMV_W * FVMV_H * 2];
            md5_buf(fb, sizeof tmp, dg);
            for (size_t k = 0; k < sizeof tmp; k += 2) { tmp[k] = fb[k]; tmp[k + 1] = fb[k + 1] & 0x7f; }
            md5_buf(tmp, sizeof tmp, dg2);
            hex(dg, a); hex(dg2, b);
            printf("%ld,%s,%s\n", i, a, b);
        }
        if (outdir) {
            char p[1024];
            snprintf(p, sizeof p, "%s/%05ld.rgb555", outdir, i);
            FILE *o = fopen(p, "wb");
            if (o) { fwrite(fb, 1, FVMV_W * FVMV_H * 2, o); fclose(o); }
            if (rgb) {
                snprintf(p, sizeof p, "%s/%05ld.ppm", outdir, i);
                o = fopen(p, "wb");
                if (o) {
                    fprintf(o, "P6\n%d %d\n255\n", FVMV_W, FVMV_H);
                    for (int k = 0; k < FVMV_W * FVMV_H; k++) {
                        unsigned px = fb[2 * k] | fb[2 * k + 1] << 8;
                        unsigned char c3[3] = { (unsigned char)((px & 31) << 3 | (px & 31) >> 2),
                                                (unsigned char)(((px >> 5) & 31) << 3 | ((px >> 5) & 31) >> 2),
                                                (unsigned char)(((px >> 10) & 31) << 3 | ((px >> 10) & 31) >> 2) };
                        fwrite(c3, 1, 3, o);
                    }
                    fclose(o);
                }
            }
        }
    }
    if (wf) {
        uint8_t h[44];
        uint32_t rate = 65536;
        memcpy(h, "RIFF", 4); uint32_t x = 36 + wav_bytes;
        h[4] = (uint8_t)x; h[5] = (uint8_t)(x >> 8); h[6] = (uint8_t)(x >> 16); h[7] = (uint8_t)(x >> 24);
        memcpy(h + 8, "WAVEfmt ", 8);
        static const uint8_t fmt[20] = { 16, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 8, 0 };
        memcpy(h + 16, fmt, 20);
        h[24] = (uint8_t)rate; h[25] = (uint8_t)(rate >> 8); h[26] = (uint8_t)(rate >> 16); h[27] = 0;
        h[28] = (uint8_t)rate; h[29] = (uint8_t)(rate >> 8); h[30] = (uint8_t)(rate >> 16); h[31] = 0;
        memcpy(h + 36, "data", 4);
        h[40] = (uint8_t)wav_bytes; h[41] = (uint8_t)(wav_bytes >> 8); h[42] = (uint8_t)(wav_bytes >> 16); h[43] = (uint8_t)(wav_bytes >> 24);
        fseek(wf, 0, SEEK_SET);
        fwrite(h, 1, 44, wf);
        fclose(wf);
    }
    if (stats) {
        fprintf(stderr, "size     ");
        for (int m = 0; m < 8; m++) fprintf(stderr, "%10s", modename[m]);
        fprintf(stderr, "\n");
        for (int z = 0; z < 16; z++) {
            uint64_t t = 0;
            for (int m = 0; m < 8; m++) t += d->mode_count[z][m];
            if (!t) continue;
            fprintf(stderr, "%-8s ", sizename[z]);
            for (int m = 0; m < 8; m++) fprintf(stderr, "%10llu", (unsigned long long)d->mode_count[z][m]);
            fprintf(stderr, "\n");
        }
        fprintf(stderr, "oob_reads %llu oob_writes %llu offpic_reads %llu offpic_writes %llu errors %llu overruns %llu inexact %llu\n",
                (unsigned long long)d->oob_reads, (unsigned long long)d->oob_writes,
                (unsigned long long)d->offpic_reads, (unsigned long long)d->offpic_writes,
                (unsigned long long)d->err, (unsigned long long)d->overrun, (unsigned long long)d->inexact);
        fprintf(stderr, "yout kinds above/ref0 %llu above/ref1 %llu below/ref0 %llu below/ref1 %llu\n", (unsigned long long)d->mv_yout_kind[0], (unsigned long long)d->mv_yout_kind[1], (unsigned long long)d->mv_yout_kind[2], (unsigned long long)d->mv_yout_kind[3]);
        fprintf(stderr, "mv_xwrap %llu mv_yout %llu edge_x0 %llu edge_y0 %llu\n", (unsigned long long)d->mv_xwrap,
                (unsigned long long)d->mv_yout, (unsigned long long)d->edge_x0, (unsigned long long)d->edge_y0);
    }
    return rc;
}
