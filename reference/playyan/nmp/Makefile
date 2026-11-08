# Nintendo MP3 Player DSP MP3 decoder, C reference.  Tables are generated from your ROM:
#   make ROM="/path/to/Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba"
NMP_NAME := Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba
ROM ?= $(if $(PY_ROMS),$(PY_ROMS)/$(NMP_NAME))
CC ?= gcc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Wno-unused-function
all: pynmpdec libpynmp.so
nmp_tables.h: gen_tables.py
	@test -n "$(ROM)" || (echo "set ROM=<Nintendo MP3 Player ROM> (or PY_ROMS)"; exit 1)
	python3 gen_tables.py "$(ROM)" > $@.tmp && mv $@.tmp $@
pynmpdec: nmpdec.c nmpdec.h nmp_tables.h dct32.h huff.h main.c
	$(CC) $(CFLAGS) -o $@ nmpdec.c main.c
libpynmp.so: nmpdec.c nmpdec.h nmp_tables.h dct32.h huff.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ nmpdec.c
clean:
	rm -f pynmpdec libpynmp.so nmp_tables.h
.PHONY: all clean
