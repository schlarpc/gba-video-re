# G.726 reference decoder (all tables are ITU-T G.726 values; no firmware data needed).
CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra
all: pyg726dec g726_test libpyg726.so
pyg726dec: g726.c pyg726dec_main.c ../video/asf.c g726.h
	$(CC) $(CFLAGS) -o $@ g726.c pyg726dec_main.c ../video/asf.c
g726_test: g726.c g726_test.c g726.h
	$(CC) $(CFLAGS) -o $@ g726.c g726_test.c
libpyg726.so: g726.c g726.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ g726.c
clean:
	rm -f pyg726dec g726_test libpyg726.so
.PHONY: all clean
