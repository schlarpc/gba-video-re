#!/usr/bin/env python3
"""Build unicorn-oracle seed states perf/states/wait_N.state (main loop idle right after VX frame N)
using emu/mkstate.make (unmodified), for the cross-validation sample.  mkseeds.py N [N ...]"""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "emu"))
import mkstate  # noqa: E402

for n in map(int, sys.argv[1:]):
    out = os.path.join(HERE, "states", f"wait_{n:05d}.state")
    if not os.path.exists(out):
        mkstate.make(n, out)
