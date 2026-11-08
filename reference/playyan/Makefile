# C reference decoders for the Play-Yan family. Each codec lives in its own directory with its
# own Makefile; tables that are not standards are generated from your firmware source at build
# time:  make FW=/path/to/Play-Yan.gba [FW_MICRO=/path/to/Play-Yan-Micro.gba] [FUP=playan.fup]
#         [ROM=/path/to/Nintendo-MP3-Player.gba]   (or PY_ROMS=<directory with the No-Intro names>)
SUBDIRS = video g726 mp3 aac nmp
all clean:
	@for d in $(SUBDIRS); do if [ -f $$d/Makefile ]; then $(MAKE) -C $$d $@ || exit 1; fi; done
.PHONY: all clean
