/* G.726 (16/24/32/40 kbit/s) as the Play-Yan video firmware decodes ASF audio: Sun's g72x
 * reference structure with its 16-bit `short` intermediates, ITU-T tables, output clamped to
 * 16 bits; codes MSB-first in each payload. See crates/gbav-playyan/src/g726.rs. */
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct {
    int yl, yu, dms, dml, ap, a[2], b[6], pk[2], dq[6], sr[2], td;
    int bits;
} pyg726;

void pyg726_init(pyg726 *s, int bits); /* bits = 2..5 */
int16_t pyg726_decode(pyg726 *s, unsigned code);
/* decode a payload; writes len*8/bits samples to out, returns the count */
size_t pyg726_decode_payload(pyg726 *s, const uint8_t *in, size_t len, int16_t *out);
