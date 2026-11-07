/* hydec -- reference decoder for Majesco "Hydrogen" GBA Video cartridges.
 *
 *   hydec ROM [--list] [--stream IDX|0xOFF] [--start-frame N] [--count M]
 *             [--out FILE|DIR/] [--md5] [--stats] [--png-every K]
 *
 * --list         list the movie containers (streams) and exit
 * --stream       index from --list (default: the first stream with chapters, i.e. episode 1)
 *                or the ROM offset of a container or of its video entry
 * --start-frame  first frame (decoding starts at the segment holding it: every segment is a
 *                random-access point with its own codebook and its own compressed index stream)
 * --count        frames to decode (default: to the end of the stream)
 * --out          FILE: raw 240x160 BGR555 little-endian frames appended; DIR/: DIR/NNNNN.bgr555
 * --md5          print "frame md5(240x160 BGR555 LE) md5(index bytes) segment mode" per frame
 * --stats        print per-segment statistics (blocks, sizes)
 */
#include "hydec.h"
#include "md5.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t *readfile(const char *p, size_t *len)
{
    FILE *f = fopen(p, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); *len = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(*len + 16);
    if (fread(b, 1, *len, f) != *len) { fclose(f); free(b); return NULL; }
    fclose(f);
    return b;
}

int main(int argc, char **argv)
{
    const char *rom_path = NULL, *out = NULL;
    long want = -1, start = 0, count = -1;
    int list = 0, md5 = 0, stats = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--list")) list = 1;
        else if (!strcmp(argv[i], "--md5")) md5 = 1;
        else if (!strcmp(argv[i], "--stats")) stats = 1;
        else if (!strcmp(argv[i], "--stream") && i + 1 < argc) want = strtol(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--start-frame") && i + 1 < argc) start = atol(argv[++i]);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = atol(argv[++i]);
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) out = argv[++i];
        else if (argv[i][0] != '-' && !rom_path) rom_path = argv[i];
        else { fprintf(stderr, "bad arg %s\n", argv[i]); return 2; }
    }
    if (!rom_path) {
        fprintf(stderr, "usage: hydec ROM [--list] [--stream IDX|0xOFF] [--start-frame N] [--count M] [--out FILE|DIR/] [--md5] [--stats]\n");
        return 2;
    }
    size_t len;
    uint8_t *rom = readfile(rom_path, &len);
    if (!rom) { perror(rom_path); return 1; }
    hy_container cs[HY_MAX_STREAMS];
    int nc = hy_find_containers(rom, len, cs, HY_MAX_STREAMS);
    if (list) {
        for (int i = 0; i < nc; i++) {
            hy_stream s;
            if (hy_open(&s, rom, len, &cs[i]) < 0) continue;
            printf("%d container 0x%x video 0x%x+0x%x audio 0x%x+0x%x frames %u segments %d chapters %d codec %d fps %d %dx%d\n",
                   i, cs[i].container, cs[i].video, cs[i].video_len, cs[i].audio, cs[i].audio_len, s.frames, s.nseg,
                   s.nchap, s.codec, s.fps, s.width, s.height);
            for (int k = 0; k < s.nchap; k++)
                printf("    chapter %d: segment %d frame %u \"%s\"\n", k, s.chap[k].segment,
                       s.seg[s.chap[k].segment].frame, s.chap[k].name);
            hy_close(&s);
        }
        return 0;
    }
    int si = -1;
    if (want < 0) {
        for (int i = 0; i < nc && si < 0; i++) {
            hy_stream s; hy_open(&s, rom, len, &cs[i]);
            if (s.nchap) si = i;
            hy_close(&s);
        }
    } else if (want < nc) si = (int)want;
    else for (int i = 0; i < nc; i++) if (cs[i].container == (uint32_t)want || cs[i].video == (uint32_t)want) si = i;
    if (si < 0) { fprintf(stderr, "no such stream (see --list)\n"); return 1; }
    hy_stream s;
    if (hy_open(&s, rom, len, &cs[si]) < 0) { fprintf(stderr, "bad stream header\n"); return 1; }
    if (s.codec != 5) { fprintf(stderr, "stream codec %d is not Inflate (5); LZMA streams are decoded by the ADS tools\n", s.codec); return 1; }
    hy_decoder d;
    if (hy_dec_init(&d, &s) < 0) { fprintf(stderr, "dither table not found in ROM\n"); return 1; }
    if (count < 0 || start + count > (long)s.frames) count = (long)s.frames - start;
    FILE *fo = NULL; int dir = 0;
    if (out) {
        size_t l = strlen(out);
        if (l && out[l - 1] == '/') dir = 1;
        else if (!(fo = fopen(out, "wb"))) { perror(out); return 1; }
    }
    static uint16_t fb[MVQ_W * MVQ_H];
    int last_seg = -1, rc = 0;
    for (long f = start; f < start + count; f++) {
        if (hy_dec_frame(&d, (uint32_t)f, fb) < 0) { fprintf(stderr, "decode failed at frame %ld\n", f); rc = 1; break; }
        if (stats && d.cur_seg != last_seg) {
            hy_segment *g = &s.seg[d.cur_seg];
            printf("seg %d frame %u n %u mode %d ncb %d cb %u idx %u blocks cb[%u %u %u] idx[%u %u %u]\n", d.cur_seg, g->frame,
                   g->nframes, g->mode, g->ncb, g->cb_len, g->idx_len, d.st_cb.blocks[0], d.st_cb.blocks[1], d.st_cb.blocks[2],
                   d.ix.blocks[0], d.ix.blocks[1], d.ix.blocks[2]);
            last_seg = d.cur_seg;
        }
        if (md5) {
            uint8_t h[16];
            md5_buf((const uint8_t *)fb, sizeof fb, h);
            printf("%ld ", f);
            for (int i = 0; i < 16; i++) printf("%02x", h[i]);
            hy_segment *g = &s.seg[d.cur_seg];
            size_t nb = (size_t)mvq_blocks(g->mode);
            md5_buf(d.idx + d.frame_off, nb, h);
            printf(" ");
            for (int i = 0; i < 16; i++) printf("%02x", h[i]);
            printf(" %d %d\n", d.cur_seg, g->mode);
        }
        if (fo) fwrite(fb, 1, sizeof fb, fo);
        if (dir) {
            char p[1024];
            snprintf(p, sizeof p, "%s%05ld.bgr555", out, f);
            FILE *g = fopen(p, "wb");
            if (g) { fwrite(fb, 1, sizeof fb, g); fclose(g); }
        }
    }
    if (fo) fclose(fo);
    hy_dec_free(&d); hy_close(&s); free(rom);
    return rc;
}
