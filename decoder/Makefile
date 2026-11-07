ROM ?= ../shrek.gba
CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Wno-unused-function
SRC = vxdec.c residual.c
HDR = bits.h residual.h vxdec.h fill_tables.h iwram_lut.h md5.h

all: vxdec libvxdec.so

vxdec: $(SRC) vxdec_main.c $(HDR)
	$(CC) $(CFLAGS) -o $@ $(SRC) vxdec_main.c

libvxdec.so: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $(SRC)

fill_tables.h: gen_fill.py symfill.py
	python3 gen_fill.py

iwram_lut.h: gen_iwram_lut.py $(ROM)
	python3 gen_iwram_lut.py $(ROM) > $@.tmp && mv $@.tmp $@

clean:
	rm -f vxdec libvxdec.so iwram_lut.h
.PHONY: all clean
