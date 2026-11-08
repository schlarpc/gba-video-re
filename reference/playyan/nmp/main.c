/* pynmpdec FILE.mp3 OUT.pcm [CALLS.raw]: interleaved stereo s16le headphone PCM (and the raw
 * synthesis calls) as the Nintendo MP3 Player's DSP produces them. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nmpdec.h"
int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "--id3")) {   /* pynmpdec --id3 FILE.mp3 */
        FILE *f = fopen(argv[2], "rb"); if (!f) { perror(argv[2]); return 1; }
        fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
        uint8_t *b = malloc(n); if (fread(b, 1, n, f) != (size_t)n) return 1; fclose(f);
        NmpId3 r; nmp_id3(b, n, &r);
        printf("%d,%u,", r.encoding, r.seconds);
        for (int i = 0; i < r.ntitle; i++) printf(i ? ".%x" : "%x", r.title[i]);
        printf(",");
        for (int i = 0; i < r.nartist; i++) printf(i ? ".%x" : "%x", r.artist[i]);
        printf("\n"); free(b); return 0;
    }
    if (argc < 3) { fprintf(stderr, "usage: %s FILE.mp3 OUT.pcm [CALLS.raw] [--gba SPEAKER.raw] | %s --id3 FILE.mp3\n", argv[0], argv[0]); return 2; }
    FILE *f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc(n); if (fread(buf, 1, n, f) != (size_t)n) return 1; fclose(f);
    FILE *o = fopen(argv[2], "wb"), *oc = NULL, *og = NULL;
    for (int a = 3; a < argc; a++) {
        if (!strcmp(argv[a], "--gba") && a + 1 < argc) og = fopen(argv[++a], "wb");
        else oc = fopen(argv[a], "wb");
    }
    NmpSpeaker *spk = NULL;
    NmpDec *d = nmp_new();
    long p = 0;
    while (p + 10 <= n && !memcmp(buf + p, "ID3", 3)) {
        long sz = (buf[p+6]&0x7f)<<21 | (buf[p+7]&0x7f)<<14 | (buf[p+8]&0x7f)<<7 | (buf[p+9]&0x7f);
        p += 10 + sz + ((buf[p+5] & 0x10) ? 10 : 0);
    }
    static int16_t calls[4][576], pcm[2304];
    long frames = 0, ncalls = 0;
    while (p + 4 <= n) {
        size_t sz = nmp_frame_size(buf + p, n - p);
        if (!sz || p + (long)sz > n) { p++; continue; }
        int k = nmp_decode_frame(d, buf + p, sz, calls, pcm);
        if (k < 0) { fprintf(stderr, "frame %ld: %s\n", frames, k == -2 ? "intensity stereo / mixed blocks not modelled" : "bad frame"); break; }
        if (k > 0) {
            int nch = (buf[p + 3] >> 6) == 3 ? 1 : 2, ngr = k / nch;
            fwrite(pcm, 2, 1152 * ngr, o);
            if (oc) fwrite(calls, 2, 576 * k, oc);
            if (og) {
                static const unsigned RT[9] = {44100, 48000, 32000, 22050, 24000, 16000, 11025, 12000, 8000};
                int ver = (buf[p + 1] >> 3) & 3, sri = (buf[p + 2] >> 2) & 3;
                if (!spk) spk = nmp_speaker_new(RT[sri + (ver == 3 ? 0 : ver == 2 ? 3 : 6)]);
                static uint16_t blk[8][576];
                for (int g = 0; g < ngr; g++) {
                    int nb = nmp_speaker_push(spk, calls[g * nch], calls[g * nch + nch - 1], 576, blk);
                    fwrite(blk, 2, 576 * nb, og);
                }
            }
            ncalls += k;
        }
        p += sz; frames++;
    }
    fclose(o); if (oc) fclose(oc); if (og) fclose(og); if (spk) nmp_speaker_free(spk);
    fprintf(stderr, "%ld frames, %ld synthesis calls\n", frames, ncalls);
    nmp_free(d); free(buf);
    return 0;
}
