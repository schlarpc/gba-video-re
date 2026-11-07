CFLAGS ?= -O2 -std=c99 -Wall -Wextra
all: sx2adec
sx2adec: sx2adec.c sx2_adpcm.c md5.c sx2_adpcm.h md5.h
	$(CC) $(CFLAGS) -o $@ sx2adec.c sx2_adpcm.c md5.c
clean:
	rm -f sx2adec
