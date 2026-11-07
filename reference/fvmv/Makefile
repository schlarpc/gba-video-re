CC ?= cc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra
all: fvmvdec libfvmvdec.so
fvmvdec: fvmvdec.c fvmvaudio.c fvmvdec_main.c fvmvdec.h fvmvaudio.h md5.h
	$(CC) $(CFLAGS) -o $@ fvmvdec.c fvmvaudio.c fvmvdec_main.c
libfvmvdec.so: fvmvdec.c fvmvaudio.c fvmvdec.h fvmvaudio.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ fvmvdec.c fvmvaudio.c
clean:
	rm -f fvmvdec libfvmvdec.so
