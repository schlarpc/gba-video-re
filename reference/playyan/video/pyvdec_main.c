/* pyvdec: decode the video of a Play-Yan ASF file as the cartridge firmware does.
 *   pyvdec FILE.asf [--out DIR] [--brightness N] [--rgb565] [--thumb]
 * Prints one line per frame: "index pts_ms type md5" of the 240x160 frame as the GBA receives it
 * (BGR555, little-endian; --rgb565: the firmware's RGB565 before the cartridge window). --out
 * writes each frame as DIR/fNNNNN.bin. --thumb prints the 60x40 thumbnail's md5 and Garage hash. */
#include "pyvideo.h"
#include "../common/md5.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    const char *file = NULL, *out = NULL;
    uint32_t bright = 256;
    int rgb = 0, thumb = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--out") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "--brightness") && i + 1 < argc) bright = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--rgb565")) rgb = 1;
        else if (!strcmp(argv[i], "--thumb")) thumb = 1;
        else file = argv[i];
    }
    if (!file) {
        fprintf(stderr, "usage: pyvdec FILE.asf [--out DIR] [--brightness N] [--rgb565] [--thumb]\n");
        return 2;
    }
    FILE *f = fopen(file, "rb");
    if (!f) return perror(file), 2;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc((size_t)n);
    if (fread(b, 1, (size_t)n, f) != (size_t)n) return 2;
    fclose(f);
    pyv_asf a;
    if (pyv_asf_parse(b, (size_t)n, &a) < 0 || !a.have_video) {
        fprintf(stderr, "%s: not an ASF file with a video stream\n", file);
        return 1;
    }
    pyv_dec *d = pyv_dec_new();
    if (a.codec_len) pyv_dec_decode(d, a.codec_data, a.codec_len);
    static uint16_t fr[240 * 160];
    int idx = 0;
    for (size_t i = 0; i < a.nvideo; i++) {
        int r = pyv_dec_decode(d, a.video[i].data, a.video[i].len);
        if (r < 0) {
            fprintf(stderr, "object %zu: undefined input, stop\n", i);
            break;
        }
        if (r == PYV_HEADERS) continue;
        if (thumb) {
            static uint16_t t[2400];
            if (pyv_thumb_rgb565(d, t) < 0) return 1;
            uint8_t tb[4800];
            for (int k = 0; k < 2400; k++) t[k] = pyv_rgb565_to_bgr555(t[k]), tb[2 * k] = (uint8_t)t[k], tb[2 * k + 1] = (uint8_t)(t[k] >> 8);
            char hex[33];
            md5_hex(tb, sizeof tb, hex);
            printf("thumb %s garage_hash %08X\n", hex, pyv_garage_hash(t, 2400));
            break;
        }
        if (pyv_frame_rgb565(d, bright, fr) < 0) {
            fprintf(stderr, "picture size not supported by the fit stage\n");
            return 1;
        }
        uint8_t raw[240 * 160 * 2];
        for (int k = 0; k < 240 * 160; k++) {
            uint16_t p = rgb ? fr[k] : pyv_rgb565_to_bgr555(fr[k]);
            raw[2 * k] = (uint8_t)p;
            raw[2 * k + 1] = (uint8_t)(p >> 8);
        }
        char hex[33];
        md5_hex(raw, sizeof raw, hex);
        printf("%d %u %c %s\n", idx, a.video[i].pts_ms, r == PYV_FRAME_I ? 'I' : r == PYV_FRAME_P ? 'P' : 'N', hex);
        if (out) {
            char path[4096];
            snprintf(path, sizeof path, "%s/f%05d.bin", out, idx);
            FILE *o = fopen(path, "wb");
            if (o) fwrite(raw, 1, sizeof raw, o), fclose(o);
        }
        idx++;
    }
    pyv_dec_free(d);
    pyv_asf_free(&a);
    free(b);
    return 0;
}
