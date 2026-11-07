CFLAGS ?= -O2 -std=c99 -Wall -Wextra
all: sx2dec libsx2.so audio
audio:
	$(MAKE) -C audio
.PHONY: audio clean
sx2dec: sx2dec.c sx2codec.c hyinflate.c sx2codec.h hyinflate.h md5.h
	$(CC) $(CFLAGS) -o $@ sx2dec.c sx2codec.c hyinflate.c
libsx2.so: sx2codec.c hyinflate.c sx2codec.h hyinflate.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ sx2codec.c hyinflate.c
clean:
	rm -f sx2dec libsx2.so
	$(MAKE) -C audio clean
