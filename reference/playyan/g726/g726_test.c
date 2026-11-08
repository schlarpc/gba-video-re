/* g726_test ORACLE: replay tools/playyan/g726/oracle.py output ("bits hexpayload hexpcm" lines, bits 0 =
 * new stream) through the C decoder and report bit-exactness. */
#include "g726.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static size_t unhex(const char *s, uint8_t *o) {
    size_t n = strlen(s) / 2;
    for (size_t i = 0; i < n; i++) sscanf(s + 2 * i, "%2hhx", &o[i]);
    return n;
}
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    FILE *f = fopen(argv[1], "r");
    if (!f) return perror(argv[1]), 2;
    static char line[1 << 20], p[1 << 19], q[1 << 19];
    static uint8_t in[1 << 18], want[1 << 18];
    static int16_t out[1 << 18];
    pyg726 s;
    int have = 0, ok = 0, n = 0;
    while (fgets(line, sizeof line, f)) {
        int bits;
        if (sscanf(line, "%d %s %s", &bits, p, q) != 3) continue;
        if (!bits) {
            have = 0;
            continue;
        }
        if (!have) pyg726_init(&s, bits), have = 1;
        size_t ni = unhex(p, in), nw = unhex(q, want);
        size_t k = pyg726_decode_payload(&s, in, ni, out);
        n++;
        ok += k * 2 == nw && !memcmp(out, want, nw);
    }
    printf("G.726 C reference: %d/%d payloads bit-exact vs the firmware oracle\n", ok, n);
    return ok == n ? 0 : 1;
}
