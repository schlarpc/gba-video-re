# Play-Yan video reference decoder (ASF + MPEG-4 SP + output stages).
# The firmware tables are generated from your own dump at build time:
#   make FW=/path/to/Play-Yan.gba        (or the Micro ROM, or playan.fup)
FW ?=
CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra
SRC = asf.c m4v.c pixel.c
HDR = pyvideo.h fw_tables.h m4v_tables.h ../common/md5.h

all: pyvdec libpyvideo.so

pyvdec: $(SRC) pyvdec_main.c $(HDR)
	$(CC) $(CFLAGS) -o $@ $(SRC) pyvdec_main.c

libpyvideo.so: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $(SRC)

fw_tables.h: gen_fw_tables.py ../common/fwimage.py
	@test -n "$(FW)" || (echo "set FW=<your Play-Yan ROM, Micro ROM or playan.fup>"; exit 1)
	python3 gen_fw_tables.py "$(FW)" > $@.tmp && mv $@.tmp $@

clean:
	rm -f pyvdec libpyvideo.so fw_tables.h
.PHONY: all clean
