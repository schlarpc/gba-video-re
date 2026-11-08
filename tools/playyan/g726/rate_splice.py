#!/usr/bin/env python3
"""Make a G.726 test file with Nintendo-sized audio payloads at another bit rate: copy a
32 kbit/s ASF (e.g. the support sample movie, 400-byte payloads) and overwrite each audio
payload, in place, with the next bytes of a code stream at the new rate taken from another
ASF's audio payloads; the stream header's average byte rate and bits per sample are patched to
match. Payload sizes and timestamps stay as they were, so the firmware feeds every payload
with the same lengths as for Nintendo's files (the audio plays at the wrong speed; only the
decoding matters).
usage: rate_splice.py BASE.asf CODES.asf BITS OUT.asf"""
import struct, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'video'))
from asf_py import audio_payloads, objects

base, codes, bits, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
raw = bytearray(open(base, 'rb').read())
src = b''.join(audio_payloads(codes))
frags = []
streams, _ = objects(base, frags)
aud = [k for k, t in streams.items() if t == 'audio'][0]
pos = 0
for sn, num, off, l in frags:
    if sn != aud:
        continue
    if pos + l > len(src):
        break
    raw[off:off + l] = src[pos:pos + l]
    pos += l
w = raw.find(struct.pack('<HHI', 0x45, 1, 8000))
assert w >= 0
struct.pack_into('<I', raw, w + 8, 1000 * bits)
struct.pack_into('<H', raw, w + 14, bits)
open(out, 'wb').write(raw)
print('wrote', out, 'spliced', pos, 'bytes')
