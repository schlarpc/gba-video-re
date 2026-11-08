"""Where the Play-Yan emulator tools find what is not in this repository, and where they write.

Environment (the same names as the rest of tools/playyan and the Rust tests):
  PLAYYAN_WORK  work directory for everything generated: the extracted firmware images
                (firmware/extract/), the built C cores (build/), SD card images (sd/), emulator runs
                (runs/), captures (cap/).  Default: tmp/playyan in the repository (gitignored).
  PY_ROMS       your ROM dumps and playan.fup (default $GBAV_ROMS, then roms/ in the repository)
  PY_MEDIA      Nintendo's sample files, under the names of their downloads
                (playan_support_sample_movie/sample_movie.asf, playan_garage_avoid/AVOID.ASF, ...)
  MGBA_PYAN     the patched mGBA libretro core (default: what emu/build.sh built)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', '..'))
WORK = os.path.abspath(os.environ.get('PLAYYAN_WORK') or os.path.join(REPO, 'tmp', 'playyan'))
FIRMWARE = os.path.join(WORK, 'firmware', 'extract')
BUILD = os.path.join(WORK, 'build')

# directories of the code (for sys.path)
VC01 = os.path.join(HERE, 'vc01')
VEC = os.path.join(VC01, 'vec')
C54X = os.path.join(HERE, 'c54x')
EMU = os.path.join(HERE, 'emu')

ROM_NAMES = {
    'orig': 'Play-Yan (Japan).gba',
    'micro': 'Play-Yan Micro (Japan).gba',
    'nmp': 'Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba',
    'fup': 'playan.fup',
}


def work(*parts, mkdir=True):
    """A directory under $PLAYYAN_WORK (created)."""
    p = os.path.join(WORK, *parts)
    if mkdir:
        os.makedirs(p, exist_ok=True)
    return p


def firmware(name):
    """Path of an extracted image (py_fw0, mi_fwldr, fup_sec1, nmp_boottable, ...)."""
    p = os.path.join(FIRMWARE, name + '.bin')
    if not os.path.exists(p):
        sys.exit('%s not found: run tools/playyan/extract_firmware.py first (PY_ROMS, PLAYYAN_WORK)' % p)
    return p


def load_firmware(name):
    with open(firmware(name), 'rb') as f:
        return f.read()


def roms_dir():
    return os.environ.get('PY_ROMS') or os.environ.get('GBAV_ROMS') or os.path.join(REPO, 'roms')


def rom(key):
    """Path of a ROM by key (orig, micro, nmp, fup) or a path given as is."""
    if key not in ROM_NAMES:
        return key
    p = os.path.join(roms_dir(), ROM_NAMES[key])
    if not os.path.exists(p):
        sys.exit('%s not found (set PY_ROMS to the directory of your dumps)' % p)
    return p


def media_dir():
    d = os.environ.get('PY_MEDIA')
    if not d:
        sys.exit('PY_MEDIA is not set (the directory of Nintendo\'s sample downloads)')
    return d


def media(name):
    """A media file: NAME as a path relative to $PY_MEDIA (playan_garage_avoid/AVOID.ASF), or else the
    first file with exactly that base name anywhere under it."""
    d = media_dir()
    p = os.path.join(d, name)
    if os.path.isfile(p):
        return p
    base = os.path.basename(name)
    for root, _, files in sorted(os.walk(d, followlinks=True)):
        if base in files:
            return os.path.join(root, base)
    sys.exit('%s not found under PY_MEDIA=%s' % (name, d))


def mgba_core():
    p = os.environ.get('MGBA_PYAN') or os.path.join(BUILD, 'mgba-pyan', 'mgba_libretro.so')
    if not os.path.exists(p):
        sys.exit('%s not found: build it with tools/playyan/emu/build.sh (or set MGBA_PYAN)' % p)
    return p
