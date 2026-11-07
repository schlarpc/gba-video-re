/* Test glue: run the C decoder on a context laid out exactly like the player's (128 bytes). */
#include "sx2_adpcm.h"
#include <string.h>
static uint32_t g32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static void p32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
void glue_run(uint8_t *a, const uint8_t *buf, uint32_t buf_addr, uint32_t buf_len, int8_t *out, uint32_t n,
              int clip, int driver)
{
    sx2a_ctx c; memset(&c, 0, sizeof c);
    c.x = g32(a); c.dprev = g32(a + 4); c.p1 = g32(a + 8); c.p2 = g32(a + 12); c.a1 = g32(a + 16);
    c.a2 = g32(a + 20); c.b = g32(a + 24); c.word = g32(a + 28); c.bits = g32(a + 32); c.next = g32(a + 36);
    c.nbits = g32(a + 40); c.ptr = buf + (g32(a + 44) - buf_addr); c.end = buf + buf_len;
    c.rem = g32(a + 52); c.st = g32(a + 56); memcpy(c.perm, a + 60, 64); c.s1 = a[124]; c.s2 = a[125];
    c.clip = clip;
    if (driver) sx2a_decode(&c, out, n); else sx2a_samples(&c, out, n);
    p32(a, c.x); p32(a + 4, c.dprev); p32(a + 8, c.p1); p32(a + 12, c.p2); p32(a + 16, c.a1); p32(a + 20, c.a2);
    p32(a + 24, c.b); p32(a + 28, c.word); p32(a + 32, c.bits); p32(a + 36, c.next); p32(a + 40, c.nbits);
    p32(a + 44, (uint32_t)(c.ptr - buf) + buf_addr); p32(a + 52, c.rem); p32(a + 56, c.st);
    memcpy(a + 60, c.perm, 64); a[124] = c.s1; a[125] = c.s2;
}
