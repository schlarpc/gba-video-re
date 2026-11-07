/* Hydrogen GBA Video container + video decoder. See hydec.h and docs/format-hydrogen.md. */
#include "hydec.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

int hy_find_containers(const uint8_t *rom, size_t len, hy_container *out, int max)
{
    int n = 0;
    for (size_t o = 0; o + 16 <= len && n < max; o += 4) {
        uint32_t e1 = rd32(rom + o + 4), v0 = rd32(rom + o + 8), v1 = rd32(rom + o + 12);
        if ((e1 >> 28) != 1 || (v0 >> 28) != 2 || ((v1 >> 10) & 0x3ff) != 240) continue;
        uint32_t w0 = rd32(rom + o), cnt = w0 & 0xf, size = w0 >> 4;
        if (cnt == 0 || cnt > 7) continue;
        hy_container c = {(uint32_t)o, 0, 0, 0, 0};
        size_t p = o; int ok = 1;
        for (uint32_t k = 0; k < cnt; k++) {
            if (p + 8 > len) { ok = 0; break; }
            uint32_t w = rd32(rom + p + 4), t = w >> 28, l = w & 0xfffffff;
            if (t == 1 && !c.video) { c.video = (uint32_t)p + 8; c.video_len = l - 4; }
            if (t == 2 && !c.audio) { c.audio = (uint32_t)p + 8; c.audio_len = l - 4; }
            p += 4 + l;
            if (p > len) { ok = 0; break; }
        }
        if (!ok || p - o + 4 != size || !c.video) continue;
        out[n++] = c;
    }
    return n;
}

const char *hy_name(const uint8_t *b, char *out, size_t cap)
{
    /* 1..10 digits, 11..36 a-z, 37..62 A-Z, 0xFF space, 0xFE newline; others as {xx} */
    size_t o = 0;
    for (; *b && o + 5 < cap; b++) {
        unsigned c = *b;
        if (c >= 1 && c <= 10) out[o++] = (char)('0' + c - 1);
        else if (c >= 11 && c <= 36) out[o++] = (char)('a' + c - 11);
        else if (c >= 37 && c <= 62) out[o++] = (char)('A' + c - 37);
        else if (c == 0xff || c == 0x4c) out[o++] = ' ';
        else if (c == 0xfe) out[o++] = '\n';
        else o += (size_t)snprintf(out + o, cap - o, "{%02x}", c);
    }
    out[o] = 0;
    return out;
}

int hy_open(hy_stream *s, const uint8_t *rom, size_t len, const hy_container *c)
{
    memset(s, 0, sizeof *s);
    s->rom = rom; s->rom_len = len; s->c = *c;
    const uint8_t *v = rom + c->video;
    s->w0 = rd32(v); s->w1 = rd32(v + 4);
    if ((s->w0 >> 28) != 2) return -1;
    s->nchap = s->w0 & 0xff;
    s->total_frames = (s->w0 >> 8) & 0xfffff;
    s->codec = s->w1 & 0xf;              /* 5 = Inflate (Hydrogen), 7 = LZMA (Jimmy Neutron, Noah proto) */
    s->fps = (s->w1 >> 4) & 0x3f;
    s->width = (s->w1 >> 10) & 0x3ff;
    s->height = (s->w1 >> 20) & 0x3ff;
    s->flags = s->w1 >> 30;
    s->nseg = rd16(v + 8);
    uint32_t p = 12;
    for (int i = 0; i < s->nchap; i++) {
        int seg = rd16(v + p);
        const uint8_t *q = v + p + 2;
        size_t l = strlen((const char *)q);
        if (i < HY_MAX_CHAPTERS) { s->chap[i].segment = seg; hy_name(q, s->chap[i].name, sizeof s->chap[i].name); }
        p = (uint32_t)(p + 2 + l + 1);
        if (p & 1) p++;
    }
    if (s->nchap > HY_MAX_CHAPTERS) s->nchap = HY_MAX_CHAPTERS;
    p = (p + 3) & ~3u;
    s->seg = calloc((size_t)s->nseg + 1, sizeof *s->seg);
    uint32_t q = c->video + p, frame = 0;
    for (int i = 0; i < s->nseg; i++) {
        if (q + 8 > len) return -1;
        uint32_t a = rd32(rom + q), b = rd32(rom + q + 4);
        hy_segment *g = &s->seg[i];
        g->off = q; g->frame = frame; g->nframes = a >> 16;
        g->mode = a & 7; g->mbits = (a >> 3) & 7; g->ncb = (a >> 6) & 0x3ff;
        g->cb_len = (b & 0x1fff) * 4; g->idx_len = (b >> 13) * 4;
        g->cb_off = q + 8; g->idx_off = q + 8 + g->cb_len;
        frame += g->nframes;
        q += 8 + g->cb_len + g->idx_len;
    }
    s->frames = frame;
    return 0;
}

void hy_close(hy_stream *s) { free(s->seg); s->seg = NULL; }

int hy_rom_tables(const uint8_t *rom, size_t len, mvq_tables *t)
{
    static const uint8_t dims[16] = {4, 4, 4, 4, 4, 3, 4, 3, 3, 3, 3, 3, 4, 2, 4, 2};
    size_t lim = len < 0x80000 ? len : 0x80000;
    for (size_t o = 16; o + 16 <= lim; o += 4)
        if (!memcmp(rom + o, dims, 16)) {
            memcpy(t->dither, rom + o - 16, 16);
            t->brightness = 256; t->gain_bias = 0;
            mvq_init(t);
            return 0;
        }
    return -1;
}

int hy_seg_of_frame(const hy_stream *s, uint32_t f)
{
    int lo = 0, hi = s->nseg - 1;
    if (f >= s->frames) return -1;
    while (lo < hi) {
        int m = (lo + hi + 1) / 2;
        if (s->seg[m].frame <= f) lo = m; else hi = m - 1;
    }
    return lo;
}

int hy_dec_init(hy_decoder *d, hy_stream *s)
{
    memset(d, 0, sizeof *d);
    d->s = s; d->cur_seg = -1;
    if (hy_rom_tables(s->rom, s->rom_len, &d->tab) < 0) return -1;
    return 0;
}

void hy_dec_free(hy_decoder *d) { free(d->idx); free(d->cb); }

static int load_segment(hy_decoder *d, int si)
{
    hy_stream *s = d->s;
    hy_segment *g = &s->seg[si];
    const uint8_t *rom = s->rom;
    uint32_t cbsz = hyinf_size(rom + g->cb_off), ixsz = hyinf_size(rom + g->idx_off);
    if (cbsz > d->cb_cap) { d->cb = realloc(d->cb, cbsz + 64); d->cb_cap = cbsz; }
    if (ixsz > d->idx_cap) { d->idx = realloc(d->idx, ixsz + 64); d->idx_cap = ixsz; }
    memset(&d->st_cb, 0, sizeof d->st_cb); memset(&d->st_idx, 0, sizeof d->st_idx);
    long n = hyinf_decode(rom + g->cb_off, g->cb_len, d->cb, cbsz, &d->st_cb);
    if (n != (long)cbsz) { fprintf(stderr, "segment %d: codebook inflate failed (%ld/%u)\n", si, n, cbsz); return -1; }
    if (mvq_codebook_bytes(g->mode, g->ncb) > cbsz) { fprintf(stderr, "segment %d: codebook too small\n", si); return -1; }
    hyinf_init(&d->ix, rom + g->idx_off, g->idx_len, d->idx, ixsz);
    d->ix_next = 0; d->frame_off = -1; d->idx_len = (long)ixsz;
    mvq_unpredict(d->cb, g->mode, g->ncb);
    mvq_convert(d->cb, g->mode, g->ncb, &d->tab, d->vcb);
    d->cur_seg = si;
    return 0;
}

int hy_dec_frame(hy_decoder *d, uint32_t f, uint16_t fb[MVQ_W * MVQ_H])
{
    int si = hy_seg_of_frame(d->s, f);
    if (si < 0) return -1;
    hy_segment *g = &d->s->seg[si];
    uint32_t k = f - g->frame;
    if ((si != d->cur_seg || k + 1 < d->ix_next) && load_segment(d, si) < 0) return -1;
    long nb = mvq_blocks(g->mode);
    /* StreamBase state 3: Inflate::UncompressBlock(blocks per frame) once per displayed frame */
    while (d->ix_next <= k) {
        d->frame_off = hyinf_request(&d->ix, nb);
        if (d->ix.err || d->frame_off < 0) return -1;
        d->ix_next++;
    }
    if (d->frame_off + nb > (long)d->idx_cap) return -1;
    mvq_blit(d->idx + d->frame_off, g->mode, (const uint16_t (*)[16])d->vcb, d->pal24, fb);
    return 0;
}
