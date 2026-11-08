#!/usr/bin/env bash
# Build the patched mGBA libretro core with the Play-Yan family cartridge model (mgba-pyan.patch,
# MPL-2.0 like mGBA itself) from the nixpkgs mGBA source (nix build nixpkgs#libretro.mgba.src).
#   ./build.sh            fresh tree in $PLAYYAN_WORK/build/mgba-pyan, patched, then make
#   ./build.sh --inplace  rebuild the existing tree (after editing src/gba/cart/pyan.c there)
#   ./build.sh --diff     regenerate mgba-pyan.patch from the edited tree (keeps the SPDX header)
# Output: $PLAYYAN_WORK/build/mgba-pyan/mgba_libretro.so (what pyan_core.py loads; MGBA_PYAN overrides).
# $PLAYYAN_WORK defaults to tmp/playyan in the repository.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${PLAYYAN_WORK:-$HERE/../../../tmp/playyan}"
mkdir -p "$WORK/build"
cd "$WORK/build"
SRC=$(nix build --no-link --print-out-paths nixpkgs#libretro.mgba.src | tail -1)
case "${1:-}" in
--diff)
  { sed -n '1,/^$/p' "$HERE/mgba-pyan.patch"
    (diff -ruN -x '*.o' -x '*.d' -x '*.so' -x build.log "$SRC" mgba-pyan || true) \
      | sed -e "s#^--- $SRC/#--- a/#" -e "s#^+++ mgba-pyan/#+++ b/#" -e "s#^diff -ruN .*##" | sed '/^$/d'
  } > "$HERE/mgba-pyan.patch.new"
  mv "$HERE/mgba-pyan.patch.new" "$HERE/mgba-pyan.patch"
  echo "wrote $HERE/mgba-pyan.patch ($(grep -c '^+++' "$HERE/mgba-pyan.patch") files)"
  exit 0 ;;
--inplace) ;;
*)
  rm -rf mgba-pyan
  cp -r "$SRC" mgba-pyan
  chmod -R u+w mgba-pyan
  (cd mgba-pyan && patch -p1 -s < "$HERE/mgba-pyan.patch") ;;
esac
# headers are not tracked by Makefile.libretro: touch the users of pyan.h
touch mgba-pyan/src/gba/memory.c mgba-pyan/src/gba/cart/unlicensed.c
make -C mgba-pyan -f Makefile.libretro -j"$(nproc)" > mgba-pyan/build.log 2>&1 || { tail -30 mgba-pyan/build.log; exit 1; }
ls -la "$WORK/build/mgba-pyan/mgba_libretro.so"
