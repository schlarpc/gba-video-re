#!/usr/bin/env python3
"""Run a kernel test script with vector-model switches changed (ablation).
Usage: vrun.py [KEY=VAL ...] script.py [args]   (KEY=VAL set vc01v.CFG entries, e.g. memrep=0)"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'vec'))
import vc01v  # noqa: E402

args = sys.argv[1:]
while args and '=' in args[0] and not args[0].endswith('.py'):
    k, v = args.pop(0).split('=', 1)
    vc01v.CFG[k] = int(v) if v.isdigit() else v
sys.argv = args
runpy.run_path(args[0], run_name='__main__')
