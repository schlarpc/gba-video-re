#!/usr/bin/env python3
"""Run emu/run.py (real firmware, --lle) with an extra record hook that dumps every
DECODE_DATA_INDICATION: frames (240x160 raw from rec[1]) and audio blocks (rec[4] bytes from rec[3])
plus a records.jsonl with the record words.  Does not modify the shared emulator files.
usage: LLE_AV=OUTDIR [VIDEO_SECS=..] [LLE_MEMDUMP=n,m: whole VC memory at those frames] fw_av.py ROM SCENARIO --sd DIR --tag TAG [run.py options]"""
import json, os, struct, sys
WORK = os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')  # tools/playyan/workdir.py
try:
    import numpy  # noqa: F401
except ImportError:
    pass
EMU = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'emu')  # the mGBA harness (tools/playyan/emu)
sys.path.insert(0, EMU)
os.chdir(EMU)
import lle_model, run

OUT = os.environ['LLE_AV']
os.makedirs(OUT, exist_ok=True)
_orig = lle_model.LLEModel.on_record
_n = {'f': 0, 'a': 0}
_log = open(os.path.join(OUT, 'records.jsonl'), 'w')


def on_record(self):
    rec = struct.unpack_from('<8I', self.m.mv, 0xFF000)
    e = {'gba_frame': self.c.frame(), 'rec': ['%08X' % x for x in rec]}
    if rec[0] == 0x80001000:
        if rec[1] and rec[1] + 0x12C00 <= len(self.m.mv):
            open(os.path.join(OUT, 'f%05d.bin' % _n['f']), 'wb').write(bytes(self.m.mv[rec[1]:rec[1] + 0x12C00]))
            e['frame'] = _n['f']; _n['f'] += 1
            if str(e['frame']) in os.environ.get('LLE_MEMDUMP', '').split(','):
                open(os.path.join(OUT, 'mem_%05d.bin' % e['frame']), 'wb').write(bytes(self.m.mv[:0x100000]))
        if rec[3] and 0 < rec[4] <= 0x480:
            n = ((rec[4] >> 3) + 3 & ~3) * 4        # per ring, as the GBA copies it
            open(os.path.join(OUT, 'a%06d.bin' % _n['a']), 'wb').write(bytes(self.m.mv[rec[3]:rec[3] + 2 * n]))
            e['audio'] = _n['a']; _n['a'] += 1
    elif rec[0] in (0x40000700, 0x40000701, 0x40000800, 0x40000801, 0x40000500):
        pass
    else:
        e = None
    if e:
        _log.write(json.dumps(e) + '\n'); _log.flush()
    return _orig(self)


lle_model.LLEModel.on_record = on_record
_oc = lle_model.LLEModel.on_command


def on_command(self, nbytes, nwrites):
    raw = bytes(self.m.mv[0xFF020:0xFF020 + 0x40])
    cid = struct.unpack_from('<I', raw, 0)[0]
    _log.write(json.dumps({'gba_frame': self.c.frame(), 'cmd': '%X' % cid, 'words': ['%X' % w for w in struct.unpack_from('<8I', raw, 4)]}) + '\n')
    _log.flush()
    return _oc(self, nbytes, nwrites)


lle_model.LLEModel.on_command = on_command
sys.argv = ['run.py'] + sys.argv[1:] + ['--lle']
run.main()
