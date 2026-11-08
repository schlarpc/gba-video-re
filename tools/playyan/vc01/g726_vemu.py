#!/usr/bin/env python3
"""Compatibility shim: the G.726 vector-unit model now lives in vc01v.py (imported by vc01.py).
machine(img) returns an emu.Machine whose CPU executes vector instructions natively."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vc01v
from vc01v import VOPS, CONDF as COND, VPU  # noqa: F401  (ablation hooks)
import emu


def machine(img='py_fw0'):
    m = emu.Machine(img)
    m.cpu.vpu = VPU(m.cpu)
    return m
