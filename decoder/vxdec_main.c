/* vxdec_main.c -- CLI for the VX++ reference decoder.
 *   vxdec ROM_OR_VXFILE [--start-frame N] [--count M] [--out DIR] [--y4m FILE] [--rgb]
 *                       [--md5] [--trace FILE] [--stats]
 * ROM: the VX++ file is located by its 'VX++' magic on a 0x200 boundary (0x20200 in Shrek).
 * --start-frame N: N = 0 starts like the player's power-on (first frame); otherwise the
 *   decoder seeks to the last seek-table entry <= N (exactly like the player's seek,
 *   0x02000584) and decodes forward, emitting frames N .. N+M-1.
 * --out DIR: DIR/NNNNN.yuv (0xF000-byte decoder slot), and with --rgb DIR/NNNNN.bgr555
 *   (the player's mode-3 framebuffer image, 240x160 u16 LE BGR555).
 * --y4m FILE: 4:2:0 y4m of the visible WxH picture.
 * --md5: print per frame: index, md5(slot 0xF000 bytes), md5(BGR555 240x160 LE), start bit.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "vxdec.h"
#include "md5.h"

static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

int main(int argc, char **argv)
{
    const char *in = NULL, *outdir = NULL, *y4m = NULL, *tracef = NULL;
    long start = 0, count = 1;
    int rgb = 0, md5 = 0, stats = 0;
    const char *initfill = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--start-frame") && i + 1 < argc) start = atol(argv[++i]);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = atol(argv[++i]);
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) outdir = argv[++i];
        else if (!strcmp(argv[i], "--y4m") && i + 1 < argc) y4m = argv[++i];
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc) tracef = argv[++i];
        else if (!strcmp(argv[i], "--rgb")) rgb = 1;
        else if (!strcmp(argv[i], "--md5")) md5 = 1;
        else if (!strcmp(argv[i], "--stats")) stats = 1;
        else if (!strcmp(argv[i], "--init-fill") && i + 1 < argc) initfill = argv[++i];  /* experiment: "Y,UV" */
        else if (argv[i][0] != '-' && !in) in = argv[i];
        else { fprintf(stderr, "bad arg %s\n", argv[i]); return 2; }
    }
    if (!in) { fprintf(stderr, "usage: vxdec ROM_OR_VXFILE [--start-frame N] [--count M] [--out DIR] [--y4m F] [--rgb] [--md5]\n"); return 2; }
    FILE *f = fopen(in, "rb");
    if (!f) { perror(in); return 1; }
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *file = malloc(fsz);
    if (fread(file, 1, fsz, f) != (size_t)fsz) { perror("read"); return 1; }
    fclose(f);

    long vx = -1;
    for (long o = 0; o + 0x38 <= fsz; o += 0x200)
        if (!memcmp(file + o, "VX++", 4)) { vx = o; break; }
    if (vx < 0) { fprintf(stderr, "no VX++ header\n"); return 1; }
    const uint8_t *h = file + vx;
    uint32_t nframes = rd32(h + 4), w = rd32(h + 8), hh = rd32(h + 12);
    uint32_t seekoff = rd32(h + 0x28), audio = rd32(h + 0x1c);
    const uint8_t *ewimg = NULL;
    if (vx == 0x20200 && fsz > 0x8a00 + 0x32c8) ewimg = file + 0x8a00;   /* the cart's player EWRAM image */
    if (w > 240 || hh > 160 || (w & 15) || (hh & 15)) { fprintf(stderr, "bad size %ux%u\n", w, hh); return 1; }

    static vx_dec d;
    size_t vlen = audio ? audio - 0x38 : (size_t)(fsz - vx - 0x38);
    vx_dec_init(&d, h + 0x38, vlen, w, hh, ewimg, 0x32c8);
    if (tracef) d.trace = fopen(tracef, "w");
    if (initfill) {   /* non-player experiment: other initial slot contents */
        int fy = 0, fc = 0x80;
        sscanf(initfill, "%i,%i", &fy, &fc);
        for (int i = 0; i < 4; i++) {
            memset(&d.ew[0x32c8 + i * 0xF000], fy, 0xA000);
            memset(&d.ew[0x32c8 + i * 0xF000 + 0xA000], fc, 0x5000);
        }
    }

    long cur = 0;
    if (start > 0) {
        const uint8_t *st = h + seekoff;
        uint32_t ebit = 0, efr = 0;
        for (int i = 0; vx + seekoff + 16 * (i + 1) <= fsz; i++) {
            uint32_t fr = rd32(st + 16 * i);
            if (fr > (uint32_t)start || fr >= nframes) break;
            efr = fr; ebit = rd32(st + 16 * i + 4);
        }
        if (efr) vx_dec_seek(&d, ebit);
        cur = efr;
    }
    FILE *yf = NULL;
    if (y4m) {
        yf = fopen(y4m, "wb");
        fprintf(yf, "YUV4MPEG2 W%u H%u F7:1 Ip A1:1 C420jpeg\n", w, hh);
    }
    uint16_t *rgbbuf = malloc(w * hh * 2);
    uint8_t *plane = malloc(w * hh);
    for (; cur < start + count && cur < (long)nframes; cur++) {
        uint32_t idx = d.dec_index;
        if (d.trace) fprintf(d.trace, "FRAME %ld bit %u\n", cur, vx_bits_pos(&d.bs));
        vx_decode_frame(&d);
        if (cur < start) continue;
        const uint8_t *slot = vx_frame_slot(&d, idx);
        char path[4096];
        if (outdir) {
            snprintf(path, sizeof path, "%s/%05ld.yuv", outdir, cur);
            FILE *o = fopen(path, "wb");
            if (!o) { perror(path); return 1; }
            fwrite(slot, 1, 0xF000, o);
            fclose(o);
        }
        if (rgb || yf || md5) vx_yuv_to_bgr555(slot, rgbbuf, w, hh);
        if (rgb && outdir) {
            snprintf(path, sizeof path, "%s/%05ld.bgr555", outdir, cur);
            FILE *o = fopen(path, "wb");
            fwrite(rgbbuf, 2, w * hh, o);
            fclose(o);
        }
        if (yf) {
            fputs("FRAME\n", yf);
            for (uint32_t y = 0; y < hh; y++) fwrite(slot + y * 256, 1, w, yf);
            for (int c = 0; c < 2; c++) {
                for (uint32_t y = 0; y < hh / 2; y++)
                    for (uint32_t x = 0; x < w / 2; x++) plane[y * (w / 2) + x] = slot[0xA000 + y * 256 + 2 * x + c];
                fwrite(plane, 1, (w / 2) * (hh / 2), yf);
            }
        }
        if (md5) {
            uint8_t dg[16];
            md5_buf(slot, 0xF000, dg);
            printf("%ld ", cur);
            for (int i = 0; i < 16; i++) printf("%02x", dg[i]);
            md5_buf((const uint8_t *)rgbbuf, w * hh * 2, dg);
            printf(" ");
            for (int i = 0; i < 16; i++) printf("%02x", dg[i]);
            printf(" bit=%u err=%lu oob=%lu q=%u\n", d.frame_bit, d.err, d.oob, d.hdr_q);
        }
    }
    if (stats) {
        static const char *nm[4] = {"w16", "w8", "w4", "w2"};
        fprintf(stderr, "errors %lu, MVs leaving the picture %lu, bilinear corner overflows %lu, intra blocks at the top/left picture edge %lu\n", d.err, d.oob, d.fill_ovf, d.edge_intra);
        for (int i = 0; i < 4; i++) {
            fprintf(stderr, "%s:", nm[i]);
            for (int m = 0; m < 12; m++) fprintf(stderr, " %lu", d.stat_modes[i][m]);
            fprintf(stderr, "\n");
        }
    }
    if (yf) fclose(yf);
    return d.err ? 3 : 0;
}
