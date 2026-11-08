/* ASF container (see crates/gbav-playyan/src/asf.rs for the description). */
#include "pyvideo.h"
#include <stdlib.h>
#include <string.h>

static const uint8_t G_HEADER[16] = {0x30, 0x26, 0xB2, 0x75, 0x8E, 0x66, 0xCF, 0x11, 0xA6, 0xD9, 0x00, 0xAA, 0x00, 0x62, 0xCE, 0x6C};
static const uint8_t G_DATA[16] = {0x36, 0x26, 0xB2, 0x75, 0x8E, 0x66, 0xCF, 0x11, 0xA6, 0xD9, 0x00, 0xAA, 0x00, 0x62, 0xCE, 0x6C};
static const uint8_t G_FILEP[16] = {0xA1, 0xDC, 0xAB, 0x8C, 0x47, 0xA9, 0xCF, 0x11, 0x8E, 0xE4, 0x00, 0xC0, 0x0C, 0x20, 0x53, 0x65};
static const uint8_t G_STREAMP[16] = {0x91, 0x07, 0xDC, 0xB7, 0xB7, 0xA9, 0xCF, 0x11, 0x8E, 0xE6, 0x00, 0xC0, 0x0C, 0x20, 0x53, 0x65};
static const uint8_t G_VIDEO[16] = {0xC0, 0xEF, 0x19, 0xBC, 0x4D, 0x5B, 0xCF, 0x11, 0xA8, 0xFD, 0x00, 0x80, 0x5F, 0x5C, 0x44, 0x2B};
static const uint8_t G_AUDIO[16] = {0x40, 0x9E, 0x69, 0xF8, 0x4D, 0x5B, 0xCF, 0x11, 0xA8, 0xFD, 0x00, 0x80, 0x5F, 0x5C, 0x44, 0x2B};

static uint64_t rd(const uint8_t *b, size_t n, size_t o, int w, int *err) {
    uint64_t v = 0;
    if (o + (size_t)w > n) {
        *err = 1;
        return 0;
    }
    for (int i = w - 1; i >= 0; i--) v = v << 8 | b[o + i];
    return v;
}

typedef struct {
    int active;
    uint8_t stream;
    uint32_t number, pts;
    int key;
    size_t size;
    uint8_t *data;
    size_t len, cap;
    size_t *frags, nfrags;
} partial;

static void push_obj(pyv_object **v, size_t *n, pyv_object o) {
    *v = realloc(*v, (*n + 1) * sizeof **v);
    (*v)[(*n)++] = o;
}

static void deliver(pyv_asf *a, partial *p) {
    pyv_object o = {p->stream, p->number, p->pts, p->key, p->data, p->len, p->frags, p->nfrags};
    if (a->have_video && p->stream == a->vstream)
        push_obj(&a->video, &a->nvideo, o);
    else if (a->have_audio && p->stream == a->astream)
        push_obj(&a->audio, &a->naudio, o);
    else {
        free(p->data);
        free(p->frags);
    }
    p->active = 0;
    p->data = NULL;
    p->frags = NULL;
    p->len = p->cap = p->nfrags = 0;
}

static void append(partial *p, const uint8_t *d, size_t l) {
    if (p->len + l > p->cap) {
        p->cap = (p->len + l) * 2 + 64;
        p->data = realloc(p->data, p->cap);
    }
    memcpy(p->data + p->len, d, l);
    p->len += l;
    p->frags = realloc(p->frags, (p->nfrags + 1) * sizeof *p->frags);
    p->frags[p->nfrags++] = l;
}

static int field(const uint8_t *pk, size_t n, size_t *p, int t, uint64_t *v) {
    static const int W[4] = {0, 1, 2, 4};
    int err = 0;
    int w = W[t & 3];
    *v = w ? rd(pk, n, *p, w, &err) : 0;
    *p += (size_t)w;
    return err;
}

static int packet(pyv_asf *a, const uint8_t *pk, size_t n, partial *parts) {
    size_t p = 0;
    uint64_t plen, seq, pad;
    if (pk[0] & 0x80) p = 1 + (pk[0] & 15);
    if (p + 2 > n) return -1;
    uint8_t lt = pk[p], pf = pk[p + 1];
    p += 2;
    int multiple = lt & 1;
    if (field(pk, n, &p, lt >> 5, &plen) || field(pk, n, &p, lt >> 1, &seq) || field(pk, n, &p, lt >> 3, &pad)) return -1;
    if (plen == 0 || plen > n) plen = n;
    p += 6;
    size_t end = plen > pad ? plen - pad : 0;
    int npay = 1, plt = 0;
    if (multiple) {
        if (p >= n) return -1;
        npay = pk[p] & 0x3f;
        plt = pk[p] >> 6;
        p++;
    }
    for (int k = 0; k < npay; k++) {
        if (p >= n) return -1;
        uint8_t sn = pk[p++];
        uint64_t num, off, rl, l = 0;
        if (field(pk, n, &p, pf >> 4, &num) || field(pk, n, &p, pf >> 2, &off) || field(pk, n, &p, pf, &rl)) return -1;
        uint8_t stream = sn & 0x7f;
        int key = (sn & 0x80) != 0;
        size_t rep = p;
        size_t q = p + rl;
        if (multiple) {
            if (field(pk, n, &q, plt, &l)) return -1;
        } else
            l = end > q ? end - q : 0;
        if (q + l > n) return -1;
        if (rl == 1) { /* compressed payload: sub-payloads with a length byte each */
            size_t stop = q + l < end ? q + l : end;
            uint32_t nn = (uint32_t)num;
            while (q < stop) {
                size_t sl = pk[q++];
                if (q + sl > n) return -1;
                partial t = {1, stream, nn++, (uint32_t)off, key, sl, NULL, 0, 0, NULL, 0};
                append(&t, pk + q, sl);
                deliver(a, &t);
                q += sl;
            }
            p = stop;
            continue;
        }
        int err = 0;
        size_t size = 0;
        uint32_t pts = 0;
        if (rl >= 8) {
            size = (size_t)rd(pk, n, rep, 4, &err);
            pts = (uint32_t)rd(pk, n, rep + 4, 4, &err);
        }
        const uint8_t *data = pk + q;
        p = q + l;
        partial *cur = &parts[stream & 127];
        if (cur->active && (cur->number != num || off != cur->len)) {
            if (off == 0)
                deliver(a, cur);
            else {
                free(cur->data);
                free(cur->frags);
                cur->data = NULL;
                cur->frags = NULL;
                cur->active = 0;
                cur->len = cur->cap = cur->nfrags = 0;
            }
        }
        if (off == 0) {
            cur->active = 1;
            cur->stream = stream;
            cur->number = (uint32_t)num;
            cur->pts = pts;
            cur->key = key;
            cur->size = size > l ? size : l;
            cur->len = 0;
            free(cur->frags);
            cur->frags = NULL;
            cur->nfrags = 0;
        }
        if (cur->active && cur->number == num) {
            append(cur, data, l);
            if (cur->len >= cur->size) deliver(a, cur);
        }
    }
    return 0;
}

int pyv_asf_parse(const uint8_t *b, size_t n, pyv_asf *a) {
    int err = 0;
    memset(a, 0, sizeof *a);
    if (n < 30 || memcmp(b, G_HEADER, 16)) return -1;
    size_t hsize = (size_t)rd(b, n, 16, 8, &err);
    uint32_t nobj = (uint32_t)rd(b, n, 24, 4, &err);
    size_t p = 30;
    for (uint32_t i = 0; i < nobj && !err; i++) {
        size_t sz = (size_t)rd(b, n, p + 16, 8, &err);
        if (err || sz < 24 || p + sz > n) return -1;
        const uint8_t *o = b + p;
        if (!memcmp(o, G_FILEP, 16)) {
            uint32_t minp = (uint32_t)rd(o, sz, 92, 4, &err), maxp = (uint32_t)rd(o, sz, 96, 4, &err);
            if (minp != maxp) return -1;
            a->packet_size = minp;
        } else if (!memcmp(o, G_STREAMP, 16)) {
            size_t tsl = (size_t)rd(o, sz, 64, 4, &err);
            uint16_t flags = (uint16_t)rd(o, sz, 72, 2, &err);
            if (78 + tsl > sz) return -1;
            const uint8_t *ts = o + 78;
            if (!memcmp(o + 24, G_VIDEO, 16) && !a->have_video) {
                a->have_video = 1;
                a->vstream = flags & 0x7f;
                a->width = (uint32_t)rd(ts, tsl, 0, 4, &err);
                a->height = (uint32_t)rd(ts, tsl, 4, 4, &err);
                size_t fsize = (size_t)rd(ts, tsl, 9, 2, &err);
                if (11 + fsize > tsl || fsize < 40) return -1;
                memcpy(a->fourcc, ts + 11 + 16, 4);
                a->codec_len = fsize - 40;
                a->codec_data = malloc(a->codec_len + 1);
                memcpy(a->codec_data, ts + 11 + 40, a->codec_len);
            } else if (!memcmp(o + 24, G_AUDIO, 16) && !a->have_audio) {
                a->have_audio = 1;
                a->astream = flags & 0x7f;
                a->format_tag = (uint16_t)rd(ts, tsl, 0, 2, &err);
                a->channels = (uint16_t)rd(ts, tsl, 2, 2, &err);
                a->rate = (uint32_t)rd(ts, tsl, 4, 4, &err);
                a->avg_bytes = (uint32_t)rd(ts, tsl, 8, 4, &err);
                a->block_align = (uint16_t)rd(ts, tsl, 12, 2, &err);
                a->bits_per_sample = (uint16_t)rd(ts, tsl, 14, 2, &err);
            }
        }
        p += sz;
    }
    if (err) return -1;
    p = hsize;
    if (p + 50 > n || memcmp(b + p, G_DATA, 16)) return -1;
    size_t dsize = (size_t)rd(b, n, p + 16, 8, &err);
    uint64_t npk = rd(b, n, p + 40, 8, &err);
    size_t end = dsize >= 50 && p + dsize <= n ? p + dsize : n;
    p += 50;
    if (!a->packet_size) return -1;
    partial *parts = calloc(128, sizeof *parts);
    for (uint64_t k = 0; p + a->packet_size <= end && (npk == 0 || k < npk); k++) {
        if (packet(a, b + p, a->packet_size, parts) < 0) {
            err = 1;
            break;
        }
        p += a->packet_size;
    }
    for (int i = 0; i < 128; i++) free(parts[i].data), free(parts[i].frags);
    free(parts);
    return err ? -1 : 0;
}

void pyv_asf_free(pyv_asf *a) {
    for (size_t i = 0; i < a->nvideo; i++) free(a->video[i].data), free(a->video[i].frags);
    for (size_t i = 0; i < a->naudio; i++) free(a->audio[i].data), free(a->audio[i].frags);
    free(a->video);
    free(a->audio);
    free(a->codec_data);
    memset(a, 0, sizeof *a);
}
