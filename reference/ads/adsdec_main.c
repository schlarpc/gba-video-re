/* adsdec -- CLI for the ADS (Majesco GBA Video, LZMA) reference decoder.
 *   adsdec ROM --list
 *   adsdec ROM [--stream NAME|IDX] [--start-frame N] [--count M] [--out DIR] [--md5] [--idx-md5]
 *              [--wav FILE] [--audio-md5] [--ppm]
 * --out DIR   writes DIR/NNNNN.bgr555 (240x160 little-endian BGR555, = the GBA framebuffer), or
 *             DIR/NNNNN.ppm with --ppm.
 * --md5       prints "frame md5(framebuffer)" per frame (compare with the emulator manifests).
 * --idx-md5   also prints md5 of the frame's index plane.
 * --wav FILE  decodes the whole audio track to a 16384 Hz 8-bit mono WAV.
 * --brightness L  player brightness level 0..10 (default 5).
 * --gain-bias N   added to the brightness gain. Default: the cart's BaseBrightness setting (only the
 *                  French Cartoon Network Edition Platinum sets it: +51). Jimmy Neutron's boot code
 *                  adds 51 after its logo: pass --gain-bias 51 for its other streams.
 * --audio-md5 prints "block md5" for every 16458-sample audio block.
 */
#include "ads.h"
#include "md5.h"
#include "../majesco/adpcm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t *load(const char *p, size_t *n) {
    FILE *f = fopen(p, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); *n = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(*n);
    if (fread(b, 1, *n, f) != *n) { fclose(f); free(b); return NULL; }
    fclose(f);
    return b;
}
static void hex(const uint8_t d[16], char *o) { for (int i = 0; i < 16; i++) sprintf(o + 2 * i, "%02x", d[i]); }

static size_t audio_samples(const ads_stream *s) { return (size_t)s->a_nblocks * MJ_ADPCM_BLOCK; }

int main(int argc, char **argv) {
    const char *in = NULL, *want = NULL, *outdir = NULL, *wav = NULL;
    long start = 0, count = -1, bright = 5, bias = -100000;
    int list = 0, md5 = 0, imd5 = 0, amd5 = 0, ppm = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--start-frame") && i + 1 < argc) start = atol(argv[++i]);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = atol(argv[++i]);
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) outdir = argv[++i];
        else if (!strcmp(argv[i], "--stream") && i + 1 < argc) want = argv[++i];
        else if (!strcmp(argv[i], "--wav") && i + 1 < argc) wav = argv[++i];
        else if (!strcmp(argv[i], "--brightness") && i + 1 < argc) bright = atol(argv[++i]);
        else if (!strcmp(argv[i], "--gain-bias") && i + 1 < argc) bias = atol(argv[++i]);
        else if (!strcmp(argv[i], "--list")) list = 1;
        else if (!strcmp(argv[i], "--md5")) md5 = 1;
        else if (!strcmp(argv[i], "--idx-md5")) imd5 = md5 = 1;
        else if (!strcmp(argv[i], "--audio-md5")) amd5 = 1;
        else if (!strcmp(argv[i], "--ppm")) ppm = 1;
        else if (argv[i][0] != '-' && !in) in = argv[i];
        else { fprintf(stderr, "bad arg %s\n", argv[i]); return 2; }
    }
    if (!in) { fprintf(stderr, "usage: adsdec ROM [--list] [--stream NAME|IDX] [--start-frame N] [--count M] [--out DIR] [--md5] [--idx-md5] [--wav F] [--audio-md5] [--ppm]\n"); return 2; }
    size_t n; uint8_t *rom = load(in, &n);
    if (!rom) { perror(in); return 1; }
    ads_cart c;
    if (ads_open(&c, rom, n)) { fprintf(stderr, "no ADS archive found\n"); return 1; }
    if (list) {
        for (int i = 0; i < c.nstreams; i++) {
            ads_stream *s = &c.s[i];
            if (i == 0 && c.gain_bias) printf("# BaseBrightness %d: gain bias %+d\n", c.base_brightness, c.gain_bias);
            printf("%d %-14s mmstr@0x%07x video@0x%07x %6u frames %4u chunks audio %4u blocks vbytes=%u abytes=%u chapters=%u  \"%s\"\n", i, s->name,
                   s->mm_off, s->v_off, s->frames, s->nchunks, s->a_nblocks, s->v_len, s->a_len, s->nchap, s->title);
            for (uint32_t k = 0; k < s->nchap && k < 16; k++)
                printf("    chapter %u: chunk %u frame %u \"%s\"\n", k + 1, s->chap_chunk[k],
                       s->chap_chunk[k] < s->nchunks ? s->chunks[s->chap_chunk[k]].first_frame : 0, s->chap_title[k]);
        }
        return 0;
    }
    int si = 0;
    if (want) si = ads_find_stream(&c, want);
    else for (int i = 0; i < c.nstreams; i++) if (!strncmp(c.s[i].name, "movie", 5)) { si = i; break; }
    if (si < 0) { fprintf(stderr, "no stream %s (see --list)\n", want); return 1; }
    ads_stream *s = &c.s[si];
    int rc = 0;
    if (wav || amd5) {
        mj_adpcm a;
        /* the cartridge's word reader may run a word past the payload into the following ROM bytes */
        size_t alen = s->a_payload_len + 64;
        if (s->a_payload + alen > n) alen = n - s->a_payload;
        mj_adpcm_init(&a, rom + s->a_payload, alen, c.packaging ? MJ_CTAB_FLAT : MJ_CTAB_VLC);
        size_t tot = audio_samples(s);   /* nblocks * 16458; the last block usually ends inside the padding */
        int8_t *pcm = malloc(tot);
        size_t got = mj_adpcm_decode(&a, pcm, tot);
        if (amd5)
            for (size_t b = 0; b * MJ_ADPCM_BLOCK < got; b++) {
                uint8_t d[16]; char h[33];
                size_t l = got - b * MJ_ADPCM_BLOCK; if (l > MJ_ADPCM_BLOCK) l = MJ_ADPCM_BLOCK;
                md5_buf((uint8_t *)pcm + b * MJ_ADPCM_BLOCK, l, d); hex(d, h);
                printf("a %zu %s\n", b, h);
            }
        if (wav) {
            FILE *f = fopen(wav, "wb");
            uint32_t hdr[11] = {0x46464952, 36 + (uint32_t)got, 0x45564157, 0x20746d66, 16, 0x00010001,
                                MJ_ADPCM_RATE, MJ_ADPCM_RATE, 0x00080001, 0x61746164, (uint32_t)got};
            fwrite(hdr, 4, 11, f);
            for (size_t i = 0; i < got; i++) fputc((uint8_t)(pcm[i] + 128), f);
            fclose(f);
        }
        free(pcm);
        if (got < tot) fprintf(stderr, "audio: decoded %zu of %zu samples\n", got, tot);
    }
    if (count == 0 || (!md5 && !outdir && (wav || amd5))) return rc;
    ads_video v;
    if (ads_video_init(&v, &c, si)) { fprintf(stderr, "video init failed\n"); return 1; }
    if (bright != 5 || bias != -100000) ads_video_set_brightness(&v, (int)bright, bias != -100000 ? (int)bias : c.gain_bias);
    if (start && ads_video_seek_frame(&v, (uint32_t)start)) { fprintf(stderr, "video seek failed\n"); return 1; }
    static uint16_t fb[ADS_W * ADS_H];
    uint8_t idx[80 * 80]; uint32_t il;
    for (long k = 0; count < 0 || k < count; k++) {
        uint32_t f = v.frame;
        int r = ads_video_next(&v, fb, idx, &il);
        if (r > 0) break;
        if (r < 0) { fprintf(stderr, "frame %u: decode error %d\n", f, v.err); rc = 1; break; }
        if (md5) {
            uint8_t d[16]; char h[33], h2[33];
            md5_buf((uint8_t *)fb, sizeof fb, d); hex(d, h);
            if (imd5) { md5_buf(idx, il, d); hex(d, h2); printf("%u %s %s\n", f, h, h2); }
            else printf("%u %s\n", f, h);
        }
        if (outdir) {
            char p[1024];
            snprintf(p, sizeof p, "%s/%05u.%s", outdir, f, ppm ? "ppm" : "bgr555");
            FILE *o = fopen(p, "wb");
            if (ppm) {
                fprintf(o, "P6 240 160 255\n");
                for (int i = 0; i < ADS_W * ADS_H; i++) {
                    uint16_t x = fb[i];
                    uint8_t rgb[3] = {(uint8_t)((x & 31) << 3 | (x & 31) >> 2), (uint8_t)((x >> 5 & 31) << 3 | (x >> 5 & 31) >> 2),
                                      (uint8_t)((x >> 10 & 31) << 3 | (x >> 10 & 31) >> 2)};
                    fwrite(rgb, 1, 3, o);
                }
            } else fwrite(fb, 2, ADS_W * ADS_H, o);
            fclose(o);
        }
    }
    ads_video_free(&v);
    ads_close(&c);
    free(rom);
    return rc;
}
