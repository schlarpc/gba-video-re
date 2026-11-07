ROM ?= ../../../shrek.gba
# any VXGB (Rev 5) cart; only its player tables are used
VXGB_ROM ?= $(firstword $(wildcard ../../../sharktale.gba ../../../shrek2.gba ../../../shrek-rev5.gba))
CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Wno-unused-function
SRC = vxdec.c residual.c vxgb_residual.c
HDR = bits.h residual.h vxdec.h fill_tables.h iwram_lut.h vxgb_tables.h md5.h

all: vxdec libvxdec.so

vxdec: $(SRC) vxdec_main.c $(HDR)
	$(CC) $(CFLAGS) -o $@ $(SRC) vxdec_main.c

libvxdec.so: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $(SRC)

fill_tables.h: gen_fill.py symfill.py
	python3 gen_fill.py

iwram_lut.h: gen_iwram_lut.py $(ROM)
	python3 gen_iwram_lut.py $(ROM) > $@.tmp && mv $@.tmp $@

# without a VXGB ROM a stub is generated and vxdec decodes VX++ only
vxgb_tables.h: gen_vxgb_tables.py $(VXGB_ROM)
	python3 gen_vxgb_tables.py $(if $(VXGB_ROM),$(VXGB_ROM),--stub) > $@.tmp && mv $@.tmp $@

clean:
	rm -f vxdec libvxdec.so iwram_lut.h vxgb_tables.h
.PHONY: all clean
