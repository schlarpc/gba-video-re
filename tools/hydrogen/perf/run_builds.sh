#!/bin/sh
# whole-stream perf of episode 1 for one cart per player build
cd "$(dirname "$0")"
for c in MGUE MYGE MFOE MSSE MTME MNCE MSBE MKDE MCTE MDRE; do
  [ -f ../../../tmp/hydrogen/perf/${c}_1/totals.json ] && continue
  python3 hyperf.py $c 1 ../../../tmp/hydrogen/perf/${c}_1 > ../../../tmp/hydrogen/perf/${c}_1.log 2>&1
done
