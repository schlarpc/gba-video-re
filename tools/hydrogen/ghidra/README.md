Build-comparison tools (need Ghidra/pyghidra and the dev-shell python):

- `gh_build.py ROM NAME 0x20000` builds a Ghidra project: ROM[0:0x20000] at 0x08000000 (Thumb) and the
  cartridge's IWRAM image at 0x03000000 (ARM); `gh_dump.py NAME` decompiles every function to
  `work/gh/NAME.c` (working files, not distributed). All Grown Up must be built as NAME `agu`.
- `ghdiff.py`: function-by-function comparison of every cart's decompilation against All Grown Up
  (matched by the Class::Method assert strings; addresses, assert line numbers and variable names normalised).
  `ghshow.py CODE 'Class::Method()'` shows one diff.
- `iwcmp.py`: word-by-word comparison of the IWRAM codec routines of every build against All Grown Up,
  modulo relocation (branch offsets, literal pointers).
