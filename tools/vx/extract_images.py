#!/usr/bin/env python3
"""Extract the player program's RAM images from the ROM into player/ (needed by
residual/gen_tables.py, residual/codebook.py and the unicorn harnesses).

  EWRAM 0x02000000, 0x32c8 bytes, from ROM 0x8a00
  IWRAM 0x03000000, 0x7cf4 bytes, from ROM 0xbcc8
(offsets as in every VX++ cartridge seen so far; they are read from the player's load
header, see tools/vx/container/vxcart.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
root = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(root, "tools", "vx", "container"))
import vxcart  # noqa: E402

rom = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "shrek.gba"), "rb").read()
L = vxcart.player_layout(rom)
os.makedirs(os.path.join(root, "reference", "vx", "player"), exist_ok=True)
for name in ("ewram", "iwram"):
    off, size = L[name]
    open(os.path.join(root, "reference", "vx", "player", name + ".bin"), "wb").write(rom[off:off + size])
    print(f"wrote player/{name}.bin ({size:#x} bytes from ROM {off:#x})")
