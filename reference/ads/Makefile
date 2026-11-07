CC ?= gcc
CFLAGS ?= -O2 -std=c99 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Wno-unused-parameter
SHARED = ../majesco
all: adsdec libads.so
adsdec: adsdec_main.c ads.c ads.h lzma_dec.c lzma_dec.h md5.h $(SHARED)/adpcm.c $(SHARED)/adpcm.h $(SHARED)/vq.c $(SHARED)/vq.h
	$(CC) $(CFLAGS) -o $@ adsdec_main.c ads.c lzma_dec.c $(SHARED)/adpcm.c $(SHARED)/vq.c
libads.so: ads.c lzma_dec.c $(SHARED)/adpcm.c $(SHARED)/vq.c
	$(CC) $(CFLAGS) -fPIC -shared -o $@ ads.c lzma_dec.c $(SHARED)/adpcm.c $(SHARED)/vq.c
clean:
	rm -f adsdec libads.so
