/* md5.h -- minimal MD5 (RFC 1321) for frame checksums (copied from the gba-video-shrek decoder). */
#ifndef VX_MD5_H
#define VX_MD5_H
#include <stdint.h>
#include <string.h>
static void md5_buf(const uint8_t *msg, size_t len, uint8_t out[16])
{
    static const uint32_t K[64] = {
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
        0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
        0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
        0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
        0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
        0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
        0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
        0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391};
    static const uint8_t R[64] = {7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
                                  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
                                  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
                                  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21};
    uint32_t h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476;
    size_t nblk = (len + 8) / 64 + 1;
    for (size_t b = 0; b < nblk; b++) {
        uint8_t blk[64];
        uint32_t M[16];
        for (int i = 0; i < 64; i++) {
            size_t p = b * 64 + i;
            blk[i] = p < len ? msg[p] : p == len ? 0x80 : 0;
        }
        if (b == nblk - 1) {
            uint64_t bits = (uint64_t)len * 8;
            for (int i = 0; i < 8; i++) blk[56 + i] = (uint8_t)(bits >> (8 * i));
        }
        for (int i = 0; i < 16; i++) M[i] = blk[4 * i] | blk[4 * i + 1] << 8 | blk[4 * i + 2] << 16 | (uint32_t)blk[4 * i + 3] << 24;
        uint32_t a = h0, bb = h1, c = h2, d = h3;
        for (int i = 0; i < 64; i++) {
            uint32_t f, g;
            if (i < 16) { f = (bb & c) | (~bb & d); g = i; }
            else if (i < 32) { f = (d & bb) | (~d & c); g = (5 * i + 1) & 15; }
            else if (i < 48) { f = bb ^ c ^ d; g = (3 * i + 5) & 15; }
            else { f = c ^ (bb | ~d); g = (7 * i) & 15; }
            uint32_t t = d;
            d = c; c = bb;
            uint32_t x = a + f + K[i] + M[g];
            bb = bb + ((x << R[i]) | (x >> (32 - R[i])));
            a = t;
        }
        h0 += a; h1 += bb; h2 += c; h3 += d;
    }
    uint32_t hs[4] = {h0, h1, h2, h3};
    for (int i = 0; i < 16; i++) out[i] = (uint8_t)(hs[i / 4] >> (8 * (i % 4)));
}
#endif
