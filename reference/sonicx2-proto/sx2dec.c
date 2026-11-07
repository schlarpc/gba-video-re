/* sx2dec: decoder for the Majesco "Hydrogen" video streams of the Sonic X Vol 2 prototype
 * (Test6_Sonic_2_17March2004.gba). Reimplements the cart's StreamBase/MoviePlayer path:
 *
 *   container -> type-1 video resource -> scenes -> {codebook blob, frame-index blob}
 *   codebook: inflate -> UnPredictLuminance -> UnPredictChrominance -> YUV->RGB555 (dithered)
 *   frame:    inflate N index bytes (window persists across the scene) -> VQ blit
 *
 * Output frames are the GBA mode-3 bitmap (240 x 160 RGB555 little endian) as the cart leaves it
 * in VRAM after the blit; --md5 hashes the rows the blit writes (rows*block_h lines).
 *
 * usage: sx2dec ROM [--list] [--stream N] [--start-frame F] [--count C] [--out FILE] [--md5]
 *                   [--brightness K]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "hyinflate.h"
#include "md5.h"
#include "sx2codec.h"
#define BW SX2_BW
#define BH SX2_BH

static uint8_t *rom; static size_t romlen;
static uint32_t rd32(size_t o) { return o + 4 <= romlen ? rom[o] | rom[o+1] << 8 | rom[o+2] << 16 | (uint32_t)rom[o+3] << 24 : 0; }
static uint16_t rd16(size_t o) { return o + 2 <= romlen ? (uint16_t)(rom[o] | rom[o+1] << 8) : 0; }

/* ---- container / stream discovery ------------------------------------------------------- */
/* A "file" is u32 (size<<4 | nres) followed by nres x { u32 (type<<28 | bytes), data }.
 * Files are reached through {u32 ptr, u32 size} slots that the build tool patched into the ROM;
 * we find them by scanning for such slots (ptr in ROM, size == file header size). */
typedef struct { size_t file, video, audio; uint32_t vlen, alen; } stream_t;
static stream_t streams[16]; static int nstreams;

static void find_streams(void)
{
    for (size_t i = 0; i + 8 <= romlen; i += 4) {
        uint32_t p = rd32(i), sz = rd32(i + 4);
        if (p < 0x08000000 || p >= 0x08000000 + romlen || !sz) continue;
        size_t f = p - 0x08000000; uint32_t h = rd32(f);
        if ((h >> 4) != sz || (h & 15) == 0 || (h & 15) > 8) continue;
        stream_t s = { f, 0, 0, 0, 0 };
        size_t q = f + 4;
        for (unsigned k = 0; k < (h & 15); k++) {
            uint32_t e = rd32(q); uint32_t ty = e >> 28, n = e & 0x0fffffff;
            if (ty == 1) { s.video = q + 4; s.vlen = n; }
            if (ty == 2) { s.audio = q + 4; s.alen = n; }
            q += 4 + n;
        }
        if (s.video && nstreams < 16) {
            int dup = 0; for (int j = 0; j < nstreams; j++) dup |= streams[j].file == f;
            if (!dup) streams[nstreams++] = s;
        }
    }
    /* order by ROM position */
    for (int a = 0; a < nstreams; a++) for (int b = a + 1; b < nstreams; b++)
        if (streams[b].file < streams[a].file) { stream_t t = streams[a]; streams[a] = streams[b]; streams[b] = t; }
}

typedef struct {
    size_t hdr; uint32_t w0, w1; unsigned nscenes, nchap, width, height, fps, vbl;
    size_t scene0;
} vstream_t;

static void parse_vstream(size_t v, vstream_t *vs)
{
    vs->hdr = v; vs->w0 = rd32(v); vs->w1 = rd32(v + 4); vs->nscenes = rd16(v + 8);
    vs->nchap = vs->w0 & 0xff;
    vs->height = (vs->w1 >> 20) & 0x3ff; vs->width = (vs->w1 >> 10) & 0x3ff;
    vs->fps = (vs->w1 >> 4) & 0x3f; vs->vbl = vs->w1 & 15;
    size_t q = v + 12;                    /* chapter table: u16 scene, NUL name, pad to even */
    for (unsigned c = 0; c < vs->nchap; c++) {
        size_t k = q + 2; while (k < romlen && rom[k]) k++;
        k++; if (k & 1) k++;
        q = k;
    }
    q = (q + 3) & ~(size_t)3;
    vs->scene0 = q;
}

/* ---- main --------------------------------------------------------------------------------- */
static void usage(void)
{
    fprintf(stderr, "usage: sx2dec ROM [--list] [--stream N] [--start-frame F] [--count C] [--out FILE] [--md5] [--brightness K]\n");
    exit(2);
}

int main(int argc, char **argv)
{
    if (argc < 2) usage();
    const char *rompath = argv[1], *outpath = NULL;
    int list = 0, md5 = 0, fstats = 0, si = 0; long start = 0, count = -1; unsigned coef = 256;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--list")) list = 1;
        else if (!strcmp(argv[i], "--md5")) md5 = 1;
        else if (!strcmp(argv[i], "--frame-stats")) fstats = 1;
        else if (!strcmp(argv[i], "--stream") && i + 1 < argc) si = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--start-frame") && i + 1 < argc) start = atol(argv[++i]);
        else if (!strcmp(argv[i], "--count") && i + 1 < argc) count = atol(argv[++i]);
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) outpath = argv[++i];
        else if (!strcmp(argv[i], "--brightness") && i + 1 < argc) coef = (unsigned)atoi(argv[++i]);
        else usage();
    }
    FILE *f = fopen(rompath, "rb"); if (!f) { perror(rompath); return 1; }
    fseek(f, 0, SEEK_END); romlen = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    rom = malloc(romlen); if (fread(rom, 1, romlen, f) != romlen) return 1; fclose(f);
    find_streams();
    if (list) {
        for (int s = 0; s < nstreams; s++) {
            vstream_t vs; parse_vstream(streams[s].video, &vs);
            size_t q = vs.scene0; unsigned long fr = 0; unsigned modes = 0;
            for (unsigned k = 0; k < vs.nscenes; k++) { uint32_t s0 = rd32(q), s1 = rd32(q + 4); fr += s0 >> 16; modes |= 1u << (s0 & 7); q += 8 + (s1 & 0x1fff) * 4 + (s1 >> 13) * 4; }
            printf("stream %d file 0x%zx video 0x%zx (%u B) audio 0x%zx (%u B) %ux%u fps %u vbl %u chapters %u scenes %u frames %lu modes 0x%02x end 0x%zx\n",
                   s, streams[s].file, streams[s].video, streams[s].vlen, streams[s].audio, streams[s].alen,
                   vs.width, vs.height, vs.fps, vs.vbl, vs.nchap, vs.nscenes, fr, modes, q);
        }
        return 0;
    }
    if (si < 0 || si >= nstreams) { fprintf(stderr, "no stream %d\n", si); return 1; }
    sx2_build_tables(coef);
    vstream_t vs; parse_vstream(streams[si].video, &vs);
    FILE *out = outpath ? fopen(outpath, "wb") : NULL;
    static uint16_t fb[240 * 160], cb[256 * 16];
    static uint8_t cbyuv[0x1800 + 64];
    uint8_t *ibuf = NULL; size_t ibcap = 0;
    size_t q = vs.scene0; long fno = 0; long done = 0;
    memset(fb, 0, sizeof fb);
    for (unsigned sc = 0; sc < vs.nscenes && (count < 0 || done < count); sc++) {
        uint32_t s0 = rd32(q), s1 = rd32(q + 4);
        unsigned nf = s0 >> 16, ncb = (s0 >> 6) & 0x3ff, mode = s0 & 7;
        size_t cbo = q + 8, cbl = (s1 & 0x1fff) * 4, fro = cbo + cbl, frl = (s1 >> 13) * 4;
        size_t next = fro + frl;
        if (fno + (long)nf <= start) { fno += nf; q = next; continue; }
        long r = hy_inflate(rom + cbo, cbl, cbyuv, 0x1800, NULL);
        if (r < 0) { fprintf(stderr, "scene %u: codebook inflate error %ld\n", sc, r); return 1; }
        sx2_build_codebook(cbyuv, ncb > 256 ? 256 : ncb, mode, cb);
        unsigned blocks = (240 / BW[mode]) * (160 / BH[mode]);
        size_t need = rd32(fro);
        if (need > ibcap) { ibcap = need; ibuf = realloc(ibuf, ibcap); }
        unsigned bpf0 = blocks * (ncb > 256 ? 2 : 1);
        static size_t marks[4096]; hy_inflate_stats fst; memset(&fst, 0, sizeof fst);
        fst.mark_step = bpf0; fst.nmarks = nf < 4096 ? nf : 4096; fst.marks = marks;
        r = hy_inflate(rom + fro, frl, ibuf, ibcap, &fst);
        if (fstats) {   /* frame, scene, mode, bytes attributed (codebook+scene header on first frame) */
            for (unsigned k = 0; k < nf && k < 4096; k++) {
                size_t a = k ? marks[k - 1] : 0, z = (k + 1 == nf) ? frl : marks[k];
                printf("%ld,%u,%u,%zu\n", fno + k, sc, mode, (z - a) + (k ? 0 : 8 + cbl));
            }
        }
        if (r < 0) { fprintf(stderr, "scene %u: frame inflate error %ld\n", sc, r); return 1; }
        unsigned bpf = blocks * (ncb > 256 ? 2 : 1);
        if ((size_t)nf * bpf != (size_t)r) fprintf(stderr, "scene %u: index bytes %ld != %u frames x %u\n", sc, r, nf, bpf);
        for (unsigned k = 0; k < nf && (count < 0 || done < count); k++, fno++) {
            if (fno < start) continue;
            sx2_blit(fb, cb, ibuf + (size_t)k * bpf, mode);
            unsigned lines = (160 / BH[mode]) * BH[mode];
            if (md5) {
                uint8_t dg[16]; md5_buf((const uint8_t *)fb, lines * 240 * 2, dg);
                printf("%ld,%u,%u,", fno, sc, lines);
                for (int j = 0; j < 16; j++) printf("%02x", dg[j]);
                printf("\n");
            }
            if (out) fwrite(fb, 2, 240 * 160, out);
            done++;
        }
        q = next;
    }
    if (out) fclose(out);
    return 0;
}
