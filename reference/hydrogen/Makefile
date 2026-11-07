CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Wno-unused-function
SHARED = ../majesco
all: hydec hystat libhydec.so
hydec: hydec_main.c hydec.c hyinflate.c $(SHARED)/vq.c hydec.h hyinflate.h $(SHARED)/vq.h md5.h
	$(CC) $(CFLAGS) -o $@ hydec_main.c hydec.c hyinflate.c $(SHARED)/vq.c
libhydec.so: hydec.c hyinflate.c $(SHARED)/vq.c $(SHARED)/adpcm.c hydec.h hyinflate.h $(SHARED)/vq.h $(SHARED)/adpcm.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ hydec.c hyinflate.c $(SHARED)/vq.c $(SHARED)/adpcm.c
clean:
	rm -f hydec libhydec.so
hystat: hystat.c hydec.c hyinflate.c $(SHARED)/vq.c hydec.h hyinflate.h $(SHARED)/vq.h
	$(CC) $(CFLAGS) -o $@ hystat.c hydec.c hyinflate.c $(SHARED)/vq.c
