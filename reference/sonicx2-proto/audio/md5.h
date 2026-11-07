#ifndef SX2_MD5_H
#define SX2_MD5_H
#include <stdint.h>
#include <stddef.h>
typedef struct { uint32_t h[4]; uint64_t len; uint8_t buf[64]; } md5_ctx;
void md5_init(md5_ctx *c);
void md5_update(md5_ctx *c, const void *p, size_t n);
void md5_final(md5_ctx *c, uint8_t out[16]);
void md5_hex(const void *p, size_t n, char out[33]);
#endif
