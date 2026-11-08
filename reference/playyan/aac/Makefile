# AAC reference decoder (Play-Yan Micro / playan.fup video firmware).
#   make FW_MICRO="/path/Play-Yan Micro (Japan).gba"     or     make FUP=/path/playan.fup
# fw_tables.h is generated from your file at build time and must not be committed.
FW_MICRO ?=
FUP ?=
CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Wno-unused-parameter
# only for `make hcb`: any aactab.c that lists the ISO codebooks (FFmpeg libavcodec/aactab.c)
AACTAB ?=

# without a firmware source only the table-free shared library (differential tests) is built
all: libpyaac.so $(if $(FW_MICRO)$(FUP),pyaacdec)

pyaacdec: pyaacdec.c pyaac.c pyaac.h hcb.h fw_tables.h
	$(CC) $(CFLAGS) -o $@ pyaacdec.c pyaac.c

libpyaac.so: pyaac.c pyaac.h hcb.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ pyaac.c

fw_tables.h: gen_fw_tables.py
	@if [ -n "$(FW_MICRO)" ]; then python3 gen_fw_tables.py --micro "$(FW_MICRO)" > $@.tmp; \
	elif [ -n "$(FUP)" ]; then python3 gen_fw_tables.py --fup "$(FUP)" > $@.tmp; \
	else echo "set FW_MICRO=<Play-Yan Micro ROM> or FUP=<playan.fup>"; exit 1; fi; mv $@.tmp $@

# hcb.h and the crate's hcb.rs are the ISO codebooks (committed); regenerate with
#   make hcb AACTAB=/path/to/ffmpeg/libavcodec/aactab.c
hcb: gen_hcb.py
	@test -n "$(AACTAB)" || (echo "set AACTAB=<path to an aactab.c>"; exit 1)
	python3 gen_hcb.py "$(AACTAB)"

clean:
	rm -f pyaacdec libpyaac.so fw_tables.h
.PHONY: all clean hcb
