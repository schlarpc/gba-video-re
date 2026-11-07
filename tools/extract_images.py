#!/usr/bin/env python3
"""Extract the player program's RAM images from the ROM into player/ (needed by
residual/gen_tables.py, residual/codebook.py and the unicorn harnesses).

  EWRAM 0x02000000, 0x32c8 bytes, from ROM 0x8a00
  IWRAM 0x03000000, 0x7cf4 bytes, from ROM 0xbcc8
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
root = os.path.join(HERE, "..")
rom = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "shrek.gba"), "rb").read()
os.makedirs(os.path.join(root, "player"), exist_ok=True)
open(os.path.join(root, "player", "ewram.bin"), "wb").write(rom[0x8A00:0x8A00 + 0x32C8])
open(os.path.join(root, "player", "iwram.bin"), "wb").write(rom[0xBCC8:0xBCC8 + 0x7CF4])
print("wrote player/ewram.bin, player/iwram.bin")
