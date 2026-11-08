"""Craft test streams by editing header / side-info bits of existing Layer III files:
   python3 craft.py IN.mp3 OUT.mp3 is      -> joint-stereo frames get mode_extension |= 1 (intensity)
   python3 craft.py IN.mp3 OUT.mp3 mixed   -> every block_type 2 granule gets mixed_block_flag = 1
   python3 craft.py IN.mp3 OUT.mp3 is+mixed
   python3 craft.py IN.mp3 OUT.mp3 mixed+sfcN  -> also, in MPEG-2/2.5 mixed granules, channel 1's
                                              scalefac_compress = N (425: table row 1, slen 1,1,1,0;
                                              101: row 0, slen 1,1,1,1)
   python3 craft.py IN.mp3 OUT.mp3 mixed+bvmax -> also channel 1's big_values = 288 in mixed granules
                                              (Huffman pairs then run to line 575, past part2_3_length)
The main data is untouched, so the decoder reinterprets it; the streams stay syntactically valid
(part2_3_length bounds every granule).  No CRC (LAME default)."""
import sys, mp3parse
def setbit(buf, pos, v):
    byte, bit = pos >> 3, 7 - (pos & 7)
    buf[byte] = (buf[byte] & ~(1 << bit)) | (v << bit)
src = bytearray(open(sys.argv[1], 'rb').read()); what = sys.argv[3].split('+')
n_is = n_mx = 0
for fr in mp3parse.frames(bytes(src)):
    p = fr['pos']
    assert not fr['crc']
    if 'is' in what and fr['mode'] == 1:
        setbit(src, p * 8 + 27, 1); n_is += 1        # mode_extension bit 0 (header bit 27)
    if 'mixed' in what:
        lsf = fr['lsf']; nch = 1 if fr['mode'] == 3 else 2
        q = p * 8 + 32 + (8 if lsf else 9) + ((1 if nch == 1 else 2) if lsf else (5 if nch == 1 else 3))
        if not lsf: q += 4 * nch
        for g in range(1 if lsf else 2):
            for c in range(nch):
                q0 = q
                q += 12 + 9 + 8 + (9 if lsf else 4)
                ws = (src[q >> 3] >> (7 - (q & 7))) & 1; q += 1
                if ws:
                    bt = sum(((src[(q + k) >> 3] >> (7 - ((q + k) & 7))) & 1) << (1 - k) for k in range(2))
                    if bt == 2: setbit(src, q + 2, 1); n_mx += 1
                    sfc = [int(w[3:]) for w in what if w.startswith('sfc')]
                    if bt == 2 and lsf and c == 1 and sfc:
                        for k in range(9): setbit(src, q0 + 29 + k, (sfc[0] >> (8 - k)) & 1)
                    if bt == 2 and c == 1 and 'bvmax' in what:
                        for k in range(9): setbit(src, q0 + 12 + k, (288 >> (8 - k)) & 1)
                    q += 2 + 1 + 10 + 9
                else:
                    q += 15 + 4 + 3
                q += (0 if lsf else 1) + 2
open(sys.argv[2], 'wb').write(src)
print('is frames', n_is, 'mixed granules', n_mx)
