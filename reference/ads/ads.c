/* ADS container parsing and video decoding. See docs/format-ads.md. */
#include "ads.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <strings.h>

static uint32_t rd32(const uint8_t *p) { return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint32_t rd16(const uint8_t *p) { return p[0] | (uint32_t)p[1] << 8; }

/* ---------------------------------------------------------------- text */
/* the player's substitution alphabet for titles (see format-ads.md 2.4) */
static char deobf_char(uint8_t c) {
    if (c >= 1 && c <= 10) return (char)('0' + c - 1);
    if (c >= 11 && c <= 36) return (char)('a' + c - 11);
    if (c >= 37 && c <= 62) return (char)('A' + c - 37);
    if (c == 0xff) return ' ';
    if (c == 0xfe) return '\n';
    return '?';
}
static void deobf(char *dst, size_t cap, const uint8_t *src, size_t n) {
    size_t k = 0;
    for (size_t i = 0; i < n && k + 1 < cap; i++) dst[k++] = deobf_char(src[i]);
    dst[k] = 0;
}

/* ---------------------------------------------------------------- geometry */
/* block width, height, grid width, grid height per mode (bits 0-2 of the chunk word) */
static const uint8_t GEOM[8][4] = {{4,4,60,40},{4,4,60,40},{4,3,60,53},{4,3,60,53},
                                   {3,3,80,53},{3,3,80,53},{4,2,60,80},{4,2,60,80}};

static uint32_t grid_bytes(unsigned mode) { return (uint32_t)GEOM[mode][2] * GEOM[mode][3]; }

static int lzma_blob(const uint8_t *blob, uint32_t len, uint8_t *out, uint32_t cap, uint32_t *outlen);
static void read_settings(ads_cart *c, uint32_t off, uint32_t len);

/* ---------------------------------------------------------------- container */
static int parse_video(ads_cart *c, ads_stream *s) {
    const uint8_t *r = c->rom;
    uint32_t p = s->v_off, end = s->v_off + s->v_len;
    const uint8_t *z;
    s->hdr0 = rd32(r + p); s->hdr1 = rd32(r + p + 4);
    s->nchap = s->hdr0 & 0xff; s->frames = (s->hdr0 >> 8) & 0xfffff;
    if ((s->hdr1 & 0xfff) == 0x307) { s->width = (s->hdr1 >> 12) & 0x3ff; s->height = s->hdr1 >> 22; }
    else { s->width = (s->hdr1 >> 10) & 0x3ff; s->height = (s->hdr1 >> 20) & 0x3ff; }
    if (!s->size_in_bytes) {
        /* ADS: u32 chunk count, title, chapter records padded to 4 */
        s->nchunks = rd32(r + p + 8);
        p += 12;
        z = memchr(r + p, 0, end - p);
        if (!z) return -1;
        deobf(s->title, sizeof s->title, r + p, (size_t)(z - (r + p)));
        p += ((uint32_t)(z - (r + p)) + 4) & ~3u;
    } else {
        /* Hydrogen packaging: u16 chunk count, u16 0, no title, chapter records padded to 2 */
        s->nchunks = rd16(r + p + 8);
        p += 12;
    }
    for (uint32_t i = 0; i < s->nchap; i++) {
        uint32_t ci = rd16(r + p);
        z = memchr(r + p + 2, 0, end - p - 2);
        if (!z) return -1;
        if (i < 16) { s->chap_chunk[i] = ci; deobf(s->chap_title[i], 64, r + p + 2, (size_t)(z - (r + p + 2))); }
        if (!s->size_in_bytes) p += 2 + (((uint32_t)(z - (r + p + 2)) + 4) & ~3u);
        else p = ((uint32_t)(z - r) + 2) & ~1u;
    }
    p = (p + 3) & ~3u;
    s->chunks = calloc(s->nchunks ? s->nchunks : 1, sizeof(ads_chunk));
    uint32_t f = 0;
    for (uint32_t i = 0; i < s->nchunks; i++) {
        if (p + 8 > end) return -1;
        ads_chunk *k = &s->chunks[i];
        k->off = p; k->w0 = rd32(r + p); k->w1 = rd32(r + p + 4);
        k->nfr = k->w0 >> 16; k->mode = k->w0 & 7; k->ncb = (k->w0 >> 6) & 0x3ff;
        k->a_len = (k->w1 & 0x1fff) * 4; k->b_len = (k->w1 >> 13) * 4;
        k->a_off = p + 8; k->b_off = k->a_off + k->a_len;
        k->first_frame = f; f += k->nfr;
        p = k->b_off + k->b_len;
        if (p > end) return -1;
    }
    return 0;
}

static void parse_audio(ads_cart *c, ads_stream *s) {
    const uint8_t *r = c->rom + s->a_off;
    s->a_hdr_field = rd16(r + 12);
    s->a_nblocks = rd16(r + 14);
    s->a_sizes = (const uint16_t *)(r + 16);
    uint32_t h = (16 + 2 * s->a_nblocks + 3) & ~3u;
    s->a_payload = s->a_off + h;
    s->a_payload_len = s->a_len - h;
}

static int add_mmstr(ads_cart *c, const char *name, uint32_t off, uint32_t len, int bytes) {
    if (c->nstreams >= ADS_MAX_STREAMS) return -1;
    const uint8_t *r = c->rom;
    uint32_t total = rd32(r + off);
    if (bytes) total = total >> 4;           /* Hydrogen: size<<4 | entries (size includes this word) */
    if (total > len || off + total > c->size) return -1;
    ads_stream *s = &c->s[c->nstreams];
    memset(s, 0, sizeof *s);
    snprintf(s->name, sizeof s->name, "%s", name);
    s->mm_off = off; s->mm_len = total; s->size_in_bytes = bytes;
    uint32_t p = off + (bytes ? 4 : 8);
    while (p + 4 <= off + total) {
        uint32_t w = rd32(r + p), typ = w >> 28, n = w & 0x0fffffff;
        if (!bytes) n *= 4;
        if (typ == 1 && !s->v_off) { s->v_off = p + 4; s->v_len = n; }
        if (typ == 2 && !s->a_off) { s->a_off = p + 4; s->a_len = n; }
        p += 4 + n;
    }
    if (!s->v_off) return 0;           /* not a movie */
    if (parse_video(c, s)) return -1;
    if (s->a_off) parse_audio(c, s);
    c->nstreams++;
    return 0;
}

/* Hydrogen-engine carts (Jimmy Neutron, Noah's Final Threat): movie resource tables are found by
 * scanning: u32 (size<<4 | n), then entry 1 = {u32 1<<28 | len, video header w0 = 2<<28 | ...,
 * w1 with width 240 at bits 10..19}. Only tables whose entries tile `size` exactly are accepted. */
static int open_hydrogen(ads_cart *c) {
    const uint8_t *r = c->rom;
    int k = 0;
    c->packaging = 1;
    for (uint32_t o = 0; o + 16 <= c->size; o += 4) {
        uint32_t w0 = rd32(r + o), w1 = rd32(r + o + 4), w2 = rd32(r + o + 8), w3 = rd32(r + o + 12);
        if ((w1 >> 28) != 1 || (w2 >> 28) != 2 || ((w3 >> 10) & 0x3ff) != 240) continue;
        uint32_t n = w0 & 15, size = w0 >> 4, p = o + 4;
        if (!n || n > 7 || size < 16 || o + size > c->size) continue;
        for (uint32_t i = 0; i < n && p + 4 <= c->size; i++) p += 4 + (rd32(r + p) & 0x0fffffff);
        if (p - o != size) continue;
        char nm[16]; snprintf(nm, sizeof nm, "res%d", k);
        if (!add_mmstr(c, nm, o, size, 1)) k++;
        o = p - 4;
    }
    return c->nstreams ? 0 : -1;
}

/* The player's 16-byte ordered-dither table sits right before its block-geometry table
 * {4,4, 4,4, 4,3, 4,3, 3,3, 3,3, 4,2, 4,2} (main.bin, or the ROM for Hydrogen-engine carts), and
 * its 11 brightness gains are 11 increasing s32 with 256 in the middle. Both are read from the
 * cartridge rather than built into the decoder. */
static int find_tables(ads_cart *c) {
    static const uint8_t dims[16] = {4,4,4,4,4,3,4,3,3,3,3,3,4,2,4,2};
    const uint8_t *r = c->rom;
    int okd = 0, okg = 0;
    for (size_t i = 16; i + 16 <= c->size && !okd; i++)
        if (r[i] == 4 && !memcmp(r + i, dims, 16)) { memcpy(c->dither, r + i - 16, 16); okd = 1; }
    for (size_t i = 0; i + 44 <= c->size && !okg; i += 4) {
        if (rd32(r + i + 20) != 256 || rd32(r + i) == 0 || rd32(r + i) >= 256) continue;
        int inc = 1;
        for (int k = 1; k < 11 && inc; k++) inc = rd32(r + i + 4 * k) > rd32(r + i + 4 * k - 4) && rd32(r + i + 4 * k) < 1024;
        if (inc) { for (int k = 0; k < 11; k++) c->gain[k] = (int)rd32(r + i + 4 * k); okg = 1; }
    }
    return okd && okg ? 0 : -1;
}

int ads_open(ads_cart *c, const uint8_t *rom, size_t size) {
    memset(c, 0, sizeof *c);
    c->rom = rom; c->size = size;
    const uint8_t *f = NULL;
    for (size_t i = 0; i + 4 <= size && i < 0x200000; i += 4)
        if (!memcmp(rom + i, "SFCD", 4)) { f = rom + i; break; }
    if (find_tables(c)) return -1;
    if (!f) return open_hydrogen(c);
    c->sfcd = (uint32_t)(f - rom);
    uint32_t hdr = rd16(f + 4), n = rd32(f + 8);
    uint32_t data = c->sfcd + 8 + hdr, p = c->sfcd + 12;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t sz = rd32(rom + p), off = rd32(rom + p + 4);
        const char *nm = (const char *)rom + p + 8;
        size_t l = strnlen(nm, 64);
        p = (p + 8 + (uint32_t)l + 4) & ~3u;
        if (l > 6 && !strcmp(nm + l - 6, ".mmstr")) {
            char b[64]; snprintf(b, sizeof b, "%.*s", (int)(l - 6), nm);
            if (!strcasecmp(b, "menuresources")) read_settings(c, data + off, sz);
            else add_mmstr(c, b, data + off, sz, 0);
        }
    }
    return 0;
}

/* menuresources.mmstr carries a type-5 text resource "Settings" (LZMA) with "key value" lines.
 * The bfa01253c3c5 player reads BaseBrightness from it and adds BaseBrightness*25.6 to the
 * brightness gain (Cartoon Network Collection Edition Platinum (France): 2 -> +51). */
static void read_settings(ads_cart *c, uint32_t off, uint32_t len) {
    const uint8_t *r = c->rom;
    uint32_t p = off + 8;
    while (p + 4 <= off + len) {
        uint32_t w = rd32(r + p), typ = w >> 28, n = (w & 0x0fffffff) * 4;
        if (typ == 5 && n > 24) {
            /* resource: u32, obfuscated name, then a standard LZMA blob {usize, params, data} */
            for (uint32_t q = p + 8; q + 8 < p + 4 + n && q < p + 64; q += 4) {
                uint32_t us = rd32(r + q), par = rd32(r + q + 4);
                if ((par & 0xffff00c7u) != 0x00010002u || us == 0 || us > 0x8000) continue;
                uint8_t *txt = malloc(us + 1); uint32_t got;
                if (!lzma_blob(r + q, p + 4 + n - q, txt, us, &got)) {
                    char line[128]; size_t k = 0;
                    for (uint32_t i = 0; i <= got; i++) {
                        uint8_t ch = i < got ? txt[i] : 0xfe;
                        if (ch == 0xfe || ch == 0 || k + 1 >= sizeof line) {
                            line[k] = 0; k = 0;
                            int v;
                            if (sscanf(line, "BaseBrightness %d", &v) == 1) {
                                c->base_brightness = v;
                                c->gain_bias = (int)(((int64_t)v * 10000 * 256) / 100000);
                            }
                        } else line[k++] = deobf_char(ch);
                    }
                }
                free(txt);
                break;
            }
        }
        p += 4 + n;
    }
}

void ads_close(ads_cart *c) { for (int i = 0; i < c->nstreams; i++) free(c->s[i].chunks); }

int ads_find_stream(ads_cart *c, const char *spec) {
    char *e; long v = strtol(spec, &e, 0);
    if (*spec && !*e) return (v >= 0 && v < c->nstreams) ? (int)v : -1;
    for (int i = 0; i < c->nstreams; i++) if (!strcmp(c->s[i].name, spec)) return i;
    return -1;
}

/* ---------------------------------------------------------------- codebook */
static int lzma_blob(const uint8_t *blob, uint32_t len, uint8_t *out, uint32_t cap, uint32_t *outlen) {
    if (len < 8) return -1;
    uint32_t us = rd32(blob), par = rd32(blob + 4);
    unsigned lc = (par >> 6) & 15, lp = (par >> 3) & 7, pb = par & 7;
    if (us > cap || lc > 8 || lp > 4 || pb > 4) return -1;
    size_t np = lzma_probs_size(lc, lp);
    uint16_t *pr = malloc(np * 2);
    uint8_t ring[8192];
    lzma_dec d;
    lzma_init(&d, lc, lp, pb, pr, ring, sizeof ring - 1, blob + 8, blob + len);
    int rc = lzma_decode(&d, us);
    for (uint32_t i = 0; i < us; i++) out[i] = ring[i & (sizeof ring - 1)];
    free(pr);
    *outlen = us;
    return rc;
}

/* VQ layer: shared with the Hydrogen family (reference/majesco/vq.c) */
static void build_codebook(ads_video *v, unsigned mode, unsigned count) {
    mvq_unpredict(v->cb, (int)mode, (int)count);
    mvq_convert(v->cb, (int)mode, (int)count, &v->vt, v->rgb);
}

int ads_video_init(ads_video *v, const ads_cart *c, int stream) {
    memset(v, 0, sizeof *v);
    v->c = c; v->s = &c->s[stream];
    v->ring = calloc(ADS_RING, 1);
    v->probs = malloc(lzma_probs_size(8, 4) * 2);
    /* ordered-dither table and brightness gains come from the cartridge (ads_open) */
    memcpy(v->vt.dither, c->dither, 16);
    v->vt.brightness = c->gain[5]; v->vt.gain_bias = c->gain_bias;
    mvq_init(&v->vt);
    return ads_video_seek_chunk(v, 0);
}

void ads_video_set_brightness(ads_video *v, int level, int gain_bias) {
    if (level < 0) level = 0;
    if (level > 10) level = 10;
    v->vt.brightness = v->c->gain[level];
    v->vt.gain_bias = gain_bias;
    mvq_init(&v->vt);
    if (v->s->nchunks) mvq_convert(v->cb, (int)v->s->chunks[v->chunk].mode, (int)v->s->chunks[v->chunk].ncb, &v->vt, v->rgb);
}

void ads_video_free(ads_video *v) { free(v->ring); free(v->probs); }

static int start_chunk(ads_video *v) {
    const ads_chunk *k = &v->s->chunks[v->chunk];
    const uint8_t *r = v->c->rom;
    uint32_t n;
    if (lzma_blob(r + k->a_off, k->a_len, v->cb, sizeof v->cb, &n)) return -1;
    build_codebook(v, k->mode, k->ncb);
    uint32_t par = rd32(r + k->b_off + 4);
    lzma_init(&v->lz, (par >> 6) & 15, (par >> 3) & 7, par & 7, v->probs, v->ring, ADS_RING - 1,
              r + k->b_off + 8, r + k->b_off + k->b_len);
    v->fin = 0;
    return 0;
}

int ads_video_seek_chunk(ads_video *v, uint32_t chunk) {
    if (chunk >= v->s->nchunks) return -1;
    v->chunk = chunk; v->frame = v->s->chunks[chunk].first_frame;
    return start_chunk(v);
}

int ads_video_seek_frame(ads_video *v, uint32_t frame) {
    uint32_t i = 0;
    while (i + 1 < v->s->nchunks && v->s->chunks[i + 1].first_frame <= frame) i++;
    if (ads_video_seek_chunk(v, i)) return -1;
    while (v->frame < frame) if (ads_video_next(v, NULL, NULL, NULL)) return -1;
    return 0;
}

int ads_video_next(ads_video *v, uint16_t *fb, uint8_t *idx, uint32_t *idx_len) {
    while (v->fin >= v->s->chunks[v->chunk].nfr) {
        if (v->chunk + 1 >= v->s->nchunks) return 1;
        v->chunk++;
        if (start_chunk(v)) return -1;
    }
    const ads_chunk *k = &v->s->chunks[v->chunk];
    uint32_t g = grid_bytes(k->mode);
    uint32_t start = v->lz.pos;
    if (lzma_decode(&v->lz, g)) { v->err = v->lz.error; return -1; }
    uint8_t plane[80 * 80];
    for (uint32_t i = 0; i < g; i++) plane[i] = v->ring[(start + i) & (ADS_RING - 1)];
    if (fb) { static const uint16_t pal0[24]; mvq_blit(plane, (int)k->mode, (const uint16_t (*)[16])v->rgb, pal0, fb); }
    if (idx) { memcpy(idx, plane, g); if (idx_len) *idx_len = g; }
    v->fin++; v->frame++;
    return 0;
}
