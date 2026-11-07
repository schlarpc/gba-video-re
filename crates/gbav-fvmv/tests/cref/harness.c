/* Differential-test harness: drives the reference C decoder (fvmvdec.c, fvmvaudio.c) over a
 * binary pipe protocol for tests/differential.rs. All integers little-endian.
 *   'V' u32 out, ref, vlen, avail; u8 data[avail]; u8 ewram[0x40000]
 *       -> i32 rc; u8 ewram[0x40000]; u64 stats[143]
 *   'A' u8 reset; u8 block[40]
 *       -> i32 synth[256]; i8 pcm[1024]; i32 state[13] (v[9], msr, err, fb, prev)
 *   'O' u8 reset; u32 n; i32 in[n]   (output stage alone, its own state)
 *       -> i8 out[4n]; i32 state[3] (err, fb, prev)
 * Build: cc -O2 -std=c99 -I$CREF harness.c $CREF/fvmvdec.c $CREF/fvmvaudio.c */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fvmvdec.h"
#include "fvmvaudio.h"

static void rd(void *p, size_t n) { if (fread(p, 1, n, stdin) != n) exit(3); }
static void wr(const void *p, size_t n) { fwrite(p, 1, n, stdout); }

int main(void)
{
    fvmv_dec *d = malloc(sizeof *d);
    fvmv_audio a, o;
    fvmv_audio_init(&a);
    fvmv_audio_init(&o);
    int c;
    while ((c = getchar()) != EOF) {
        if (c == 'V') {
            uint32_t h[4];
            rd(h, 16);
            uint8_t *data = malloc(h[3] ? h[3] : 1);
            rd(data, h[3]);
            fvmv_init(d);
            rd(d->ewram, FVMV_EWRAM_SIZE);
            int32_t rc = fvmv_decode_frame_at(d, data, h[2], h[3], h[0], h[1]);
            free(data);
            uint64_t st[143], *p = st;
            memcpy(p, d->mode_count, sizeof d->mode_count); p += 128;
            *p++ = d->oob_reads; *p++ = d->oob_writes; *p++ = d->offpic_reads; *p++ = d->offpic_writes;
            *p++ = d->err; *p++ = d->overrun; *p++ = d->mv_xwrap; *p++ = d->mv_yout;
            for (int i = 0; i < 4; i++) *p++ = d->mv_yout_kind[i];
            *p++ = d->edge_x0; *p++ = d->edge_y0; *p++ = d->inexact;
            wr(&rc, 4);
            wr(d->ewram, FVMV_EWRAM_SIZE);
            wr(st, sizeof st);
        } else if (c == 'A') {
            uint8_t reset, blk[40];
            rd(&reset, 1);
            rd(blk, 40);
            if (reset) fvmv_audio_init(&a);
            int32_t syn[256];
            int8_t pcm[1024];
            fvmv_audio_synth(&a, blk, syn);
            fvmv_audio_output(&a, syn, 256, pcm);
            int32_t st[13];
            memcpy(st, a.v, sizeof a.v);
            st[9] = a.msr; st[10] = a.err; st[11] = a.fb; st[12] = a.prev;
            wr(syn, sizeof syn);
            wr(pcm, sizeof pcm);
            wr(st, sizeof st);
        } else if (c == 'O') {
            uint8_t reset;
            uint32_t n;
            rd(&reset, 1);
            rd(&n, 4);
            int32_t *in = malloc(4 * (size_t)n + 4);
            int8_t *out = malloc(4 * (size_t)n + 4);
            rd(in, 4 * (size_t)n);
            if (reset) fvmv_audio_init(&o);
            fvmv_audio_output(&o, in, (int)n, out);
            int32_t st[3] = { o.err, o.fb, o.prev };
            wr(out, 4 * (size_t)n);
            wr(st, sizeof st);
            free(in);
            free(out);
        } else {
            return 2;
        }
        fflush(stdout);
    }
    return 0;
}
