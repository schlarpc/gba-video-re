#!/bin/sh
cd "$(dirname "$0")"
while read cart st; do
  echo "$cart $st"
done < jobs.txt | xargs -P 3 -L 1 sh -c 'd=runs/$0_$(printf %x $1); mkdir -p $d; python3 vx_perf.py --rom "$(python3 ../../common/roms.py $0)" --stream $1 --out $d > $d/run.log 2>&1; echo "$0 $1 rc=$?"'
