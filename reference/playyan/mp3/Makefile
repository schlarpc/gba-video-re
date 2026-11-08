# Play-Yan music firmware MP3 decoder, C reference.
#   make FW=/path/to/Play-Yan.gba      (or the Micro ROM, playan.fup, or an extracted image)
# fw_music.h (the music image as a C array) is generated from FW at build time and never
# committed; libpymp3.so takes the image at run time instead (pymp3_new) and needs no FW.
# FW may contain spaces (No-Intro names); it is only used quoted.
FW ?= $(if $(PY_ROMS),$(PY_ROMS)/Play-Yan (Japan).gba)
CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra
SRC = pymp3.c pymp3file.c
HDR = pymp3.h pymp3file.h kernels.h md5.h

all: libpymp3.so $(if $(FW),pymp3dec)

libpymp3.so: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $(SRC)

pymp3dec: $(SRC) pymp3dec.c $(HDR) fw_music.h
	$(CC) $(CFLAGS) -o $@ $(SRC) pymp3dec.c

fw_music.h: gen_fwimage.py
	python3 gen_fwimage.py "$(FW)" > $@.tmp && mv $@.tmp $@

clean:
	rm -f libpymp3.so pymp3dec fw_music.h
.PHONY: all clean
