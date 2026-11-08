#!/usr/bin/env python3
"""Discriminating test for the WIN_R1 pixel conversion of the cartridge bridge (pyan.c).

Runs `run.py orig garage --lle` (SD card $PLAYYAN_WORK/sd/orig from make_sd.py; cursor on TRIPLE.ASF, then A) and checks what the *GBA ROM itself*
decided: with a thumbnail whose hash matches the table at 0x0C0F1EA8 the browser starts the Garage
Game and never sends VIDEO_DECODE_START (0x700); otherwise it plays the ASF as a video.  The GBA's
hash (0x0C019410) sums the raw halfwords it read through WIN_R1, so this passes only when the bridge
delivers BGR555.  Also checks lle_model's own hash check (garage match=True) is unchanged.
  python3 win1_test.py            # expects PASS (conversion on)
  python3 win1_test.py --ablate   # conversion off: expects the GBA to play the video -> FAIL
"""
import json, os, subprocess, sys
EMU = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(EMU, '..'))
import workdir  # noqa: E402
OUT = os.path.join(workdir.WORK, 'runs', 'out')
abl = '--ablate' in sys.argv
tag = 'win1_test' + ('_ablate' if abl else '')
cmd = [sys.executable, 'run.py', 'orig', 'garage', '--lle', '--tag', tag]
if abl:
    cmd += ['--opt', 'win1_bgr555=0']
subprocess.run(cmd, cwd=EMU, check=True, stdout=subprocess.DEVNULL)
s = json.load(open(os.path.join(OUT, tag + '.summary.json')))
ev = [json.loads(l) for l in open(os.path.join(OUT, tag + '.events.jsonl'))]
vds = [e for e in ev if e.get('kind') == 'cmd' and e.get('id') == '700']
ok_hash = bool(s['garage']) and all(g['match'] for g in s['garage'])
ok_gba = not vds
print('lle_model hashes (%d thumbnails) match: %s' % (len(s['garage']), ok_hash))
print('GBA started the Garage Game (no 0x700 sent): %s%s' % (ok_gba, '' if ok_gba else '  -> played %s as video' % vds[0].get('name')))
ok = ok_hash and ok_gba
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
