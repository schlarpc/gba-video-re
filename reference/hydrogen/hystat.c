/* hystat -- per-segment statistics of every Hydrogen stream in a ROM, as JSON lines:
 * {"stream":i,"seg":k,"frame":f,"nframes":n,"mode":m,"ncb":c,"cb_bytes":..,"idx_bytes":..,
 *  "cb_blocks":[s,f,d],"idx_blocks":[s,f,d],"end_ok":0/1,"cb_end_ok":0/1,"usize_idx":..} */
#include "hydec.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: hystat ROM [--frames]\n"); return 2; }
    int frames_mode = argc > 2 && !strcmp(argv[2], "--frames");
    FILE *f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); size_t len = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *rom = malloc(len); if (fread(rom, 1, len, f) != len) return 1; fclose(f);
    hy_container cs[HY_MAX_STREAMS]; int nc = hy_find_containers(rom, len, cs, HY_MAX_STREAMS);
    uint8_t *buf = malloc(1 << 24);
    for (int i = 0; i < nc; i++) {
        hy_stream s; if (hy_open(&s, rom, len, &cs[i]) < 0) continue;
        if (s.codec != 5) { hy_close(&s); continue; }
        if (frames_mode) {
            /* compressed bytes attributable to each frame: segment header + codebook blob on the
             * segment's first frame, plus the index-stream input consumed by that frame's request */
            printf("{\"stream\":%d,\"frame_bytes\":[", i);
            for (int k = 0; k < s.nseg; k++) {
                hy_segment *g = &s.seg[k];
                hyinf_stream b;
                hyinf_init(&b, rom + g->idx_off, g->idx_len, buf, 1 << 24);
                const uint8_t *p0 = b.p;
                for (uint32_t fr = 0; fr < g->nframes; fr++) {
                    hyinf_request(&b, mvq_blocks(g->mode));
                    long used = (long)(b.p - p0) - (long)(b.n / 16) * 2;   /* halfwords still buffered are not used yet */
                    if (fr + 1 == g->nframes) used = (long)(rom + g->idx_off + g->idx_len - p0);
                    long fb = used + (fr == 0 ? 8 + (long)g->cb_len + 4 : 0);
                    printf("%s%ld", (k || fr) ? "," : "", fb);
                    p0 += used;
                }
            }
            printf("]}\n");
            hy_close(&s);
            continue;
        }
        for (int k = 0; k < s.nseg; k++) {
            hy_segment *g = &s.seg[k];
            hyinf_stream a, b;
            hyinf_init(&a, rom + g->cb_off, g->cb_len, buf, 1 << 24);
            hyinf_request(&a, (long)a.size);
            int cbe = hyinf_check_end(&a);
            hyinf_init(&b, rom + g->idx_off, g->idx_len, buf, 1 << 24);
            long nb = mvq_blocks(g->mode);
            for (uint32_t fr = 0; fr < g->nframes; fr++) hyinf_request(&b, nb);
            int ide = hyinf_check_end(&b);
            printf("{\"stream\":%d,\"seg\":%d,\"frame\":%u,\"nframes\":%u,\"mode\":%d,\"mbits\":%d,\"ncb\":%d,\"cb_bytes\":%u,\"idx_bytes\":%u,"
                   "\"cb_usize\":%u,\"idx_usize\":%u,\"cb_blocks\":[%u,%u,%u],\"idx_blocks\":[%u,%u,%u],\"cb_end_ok\":%d,\"idx_end_ok\":%d,"
                   "\"idx_consumed\":%ld,\"err\":%d}\n",
                   i, k, g->frame, g->nframes, g->mode, g->mbits, g->ncb, g->cb_len, g->idx_len, a.size, b.size,
                   a.blocks[0], a.blocks[1], a.blocks[2], b.blocks[0], b.blocks[1], b.blocks[2], cbe, ide,
                   (long)(b.p - b.start), a.err | b.err);
        }
        hy_close(&s);
    }
    return 0;
}
