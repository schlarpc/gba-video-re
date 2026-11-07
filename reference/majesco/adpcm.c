/* Majesco GBA Video ADPCM decoder. See adpcm.md for the bitstream and the derivation. */
#include "adpcm.h"
#include <string.h>

/* sample VLC, indexed by the next 3 bits: low nibble = code length, value>>3 = shift into the
 * context-table byte (0, 2, 4, 6). Same bytes as the cartridge table. */
static const uint8_t VLC[8] = {0x01, 0x01, 0x01, 0x01, 0x12, 0x12, 0x23, 0x33};
/* context-table palette: 7 permutations of {0,1,2,3}, 2 bits each */
static const uint8_t PAL[7] = {0xd8, 0x72, 0xe4, 0x4e, 0x78, 0xd2, 0xe1};
/* prefix decode by the next 4 bits (values 0..11): palette index, code length */
static const uint8_t PFX[12][2] = {{0,2},{0,2},{0,2},{0,2},{1,3},{1,3},{2,3},{2,3},{3,4},{4,4},{5,4},{6,4}};
/* escape completion table, indexed by one-hot pairs */
static const uint8_t ESC[4] = {0x0c, 0x0d, 0x08, 0x06};

static uint32_t peek(mj_adpcm *a, unsigned n) {
    uint32_t v = 0;
    for (unsigned i = 0; i < n; i++) {
        size_t p = a->pos + i;
        uint32_t bit = 0;
        if (p < a->nbits_total) {
            const uint8_t *w = a->buf + (p >> 5) * 4;
            uint32_t word = w[0] | (uint32_t)w[1] << 8 | (uint32_t)w[2] << 16 | (uint32_t)w[3] << 24;
            bit = (word >> (31 - (p & 31))) & 1;
        } else a->overrun = 1;
        v = (v << 1) | bit;
    }
    return v;
}
static uint32_t get(mj_adpcm *a, unsigned n) { uint32_t v = peek(a, n); a->pos += n; return v; }

void mj_adpcm_init(mj_adpcm *a, const uint8_t *payload, size_t len, int coding) {
    memset(a, 0, sizeof *a);
    a->buf = payload; a->nbits_total = (len & ~(size_t)3) * 8; a->coding = coding;
}

void mj_adpcm_seek(mj_adpcm *a, size_t off, uint32_t block_index) {
    a->pos = off * 8; a->left = 0; a->block = block_index;
}

static void block_header(mj_adpcm *a) {
    if (peek(a, 1)) get(a, 1);                       /* continuation: keep predictor state */
    else {
        get(a, (32 - (a->pos & 31)) & 31);           /* align to the next 32-bit word */
        get(a, 1);
        uint32_t v = get(a, 24);
        a->step = (int32_t)(v << 8) >> 8;            /* signed 24-bit initial step */
        a->f2 = 0; a->prev = 1; a->f0 = a->f1 = 0; a->h0 = a->h1 = 1; a->s1 = a->s2 = 0;
    }
    for (int i = 0; i < 64; i++) {
        uint32_t t = a->coding == MJ_CTAB_VLC ? peek(a, 4) : 12;
        if (t < 12) { get(a, PFX[t][1]); a->tab[i] = PAL[PFX[t][0]]; continue; }
        if (a->coding == MJ_CTAB_VLC) get(a, 2);     /* the "11" escape prefix */
        uint32_t x = get(a, 2), y = get(a, 2), sel = get(a, 1);
        uint32_t m = (1u << x) | (1u << y);
        uint8_t lo = ESC[m & 3], hi = ESC[m >> 2], p, q;
        if (sel) { p = hi >> 2; q = lo & 3; } else { p = lo & 3; q = hi >> 2; }
        a->tab[i] = (uint8_t)(x | y << 2 | p << 4 | q << 6);
    }
    a->left = MJ_ADPCM_BLOCK; a->ctx = 0; a->block++;
}

/* 32-bit wrapping multiply, as the ARM MUL does */
static inline int32_t mul32(int32_t x, int32_t y) { return (int32_t)((uint32_t)x * (uint32_t)y); }

static int8_t sample(mj_adpcm *a) {
    uint8_t e = VLC[peek(a, 3)];
    get(a, e & 15);
    unsigned sym = (a->tab[a->ctx] >> (e >> 3)) & 3;
    a->ctx = (uint8_t)(((a->ctx << 2) & 0x3f) | sym);
    int32_t st = a->step;
    int32_t r = (st >> 8) + 0x74 + ((sym & 1) ? 0xf9 : 0);
    int32_t mag = ((((r & 0x7f) | 0x80) << 7) >> (14 - ((r >> 7) & 15)));
    if (sym & 2) mag = -mag;
    int32_t l = st >> 6, fp;
    if (sym & 1) fp = l > 0x12e0 ? 0x1400 : l + ((0x36e0 - l) >> 5);
    else fp = l < 0x248 ? 0x220 : l + ((-(l + 0x2c0)) >> 5);
    a->step = st + fp + ((-st) >> 6);
    int32_t pf = mul32(a->prev, a->f2);
    int32_t acc = (mul32(a->h1, a->f1 >> 2) >> 11) + (mul32(a->h0, a->f0 >> 2) >> 11) + (pf >> 11);
    int32_t h = mag + (acc >> 1);
    a->h1 = a->h0;
    a->h0 = h ? h : 1;
    int32_t sb = mag + (pf >> 12);
    int32_t o = (h + (int32_t)((uint32_t)(h >> 31) >> 26)) >> 6;   /* truncate toward zero */
    if (o > 127) o = 127;
    if (o < -128) o = -128;
    int32_t f0 = a->f0 - (a->f0 >> 8), f1 = a->f1 - (a->f1 >> 7), r5 = f0, r4 = f0;
    uint8_t sg = sb < 0;
    if (sb != 0) {
        if (sg == a->s1) { r5 = r4 + 0xc0; r4 = -r4; } else r5 = r4 - 0xc0;
        if ((uint32_t)(r4 + 0x1fff) <= 0x3fff) f1 += r4 >> 5;
        else if (r4 < 0) f1 -= 0x100;
        else f1 += 0xff;
        f1 += (sg != a->s2) ? -0x80 : 0x80;
    }
    if (r5 > 0x3c00 - f1) r5 = 0x3c00 - f1;
    a->f0 = r5; a->f1 = f1;
    a->f2 = a->f2 - (a->f2 >> 8) + (((mag ^ a->prev) >= 0) ? 0x20 : -0x20);
    a->s2 = a->s1; a->s1 = sg; a->prev = mag;
    a->left--;
    return (int8_t)o;
}

size_t mj_adpcm_decode(mj_adpcm *a, int8_t *out, size_t n) {
    size_t k = 0;
    while (k < n) {
        if (!a->left) block_header(a);
        if (a->overrun) break;
        out[k++] = sample(a);
    }
    return k;
}
