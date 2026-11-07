"""Find the user's ROM dumps for the tools.

ROMs are never part of this repository. Put your dumps in one directory, named as No-Intro
names them ("Game Boy Advance Video - Shrek (USA) (Rev 6).gba") or by the short ids of
verify/carts.csv ("shrek-rev6.gba"), and point GBAV_ROMS at it. Without GBAV_ROMS the tools
look in roms/ at the repository root.

    from roms import rom_path, carts
    rom_path("shrek-rev6")            # short id
    rom_path("Game Boy Advance Video - Shrek (USA) (Rev 6)")
"""
import csv
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CARTS_CSV = os.path.join(ROOT, "verify", "carts.csv")


def rom_dir():
    return os.environ.get("GBAV_ROMS") or os.path.join(ROOT, "roms")


def carts(family=None):
    """Rows of verify/carts.csv: dicts with family, short_id, sha1, nointro."""
    with open(CARTS_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if family is None or r["family"] == family]


def rom_path(key, romdir=None):
    """Path of a cart's ROM, by short id or No-Intro name. Raises FileNotFoundError."""
    d = romdir or rom_dir()
    row = next((r for r in carts() if key in (r["short_id"], r["nointro"])), None)
    names = [key]
    if row:
        names = [row["short_id"], row["nointro"]]
    for n in names:
        p = os.path.join(d, n if n.endswith(".gba") else n + ".gba")
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no ROM for {key!r} in {d} (set GBAV_ROMS; tried {names})")


def short_id(sha1):
    """Short id for a ROM sha1, or None."""
    return next((r["short_id"] for r in carts() if r["sha1"] == sha1), None)


if __name__ == "__main__":
    import sys
    for k in sys.argv[1:]:
        print(rom_path(k))
