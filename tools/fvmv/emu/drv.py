#!/usr/bin/env python3
"""Triage driver: boot a cart headless, run an input script, take screenshots and memory dumps.
script tokens: wN (run N frames), pBTN[+BTN] (hold 6 frames, release, 1 frame), sNAME (screenshot), dNAME (dump mem), xNAME (savestate)
"""
import sys, os, glob, struct, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retro
from util import video_to_img
import numpy as np
ROMS = {os.path.basename(p)[len('Game Boy Advance Video - '):].split(' (')[0].replace(' - ','_').replace(' ','_').replace('!',''): p
        for p in glob.glob(os.path.join(os.environ.get('GBAV_ROMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'roms'), '*.gba'))}
def rom_for(key):
    if os.path.exists(key): return os.path.basename(key), key
    for k, p in ROMS.items():
        if k.lower().startswith(key.lower()): return k, p
    # pokemon volumes
    for k,p in ROMS.items():
        if key.lower() in p.lower(): return k,p
    raise KeyError(key)

PROF_CORE = os.environ.get('GBAV_MGBA_PROF_CORE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'mgba', 'prof', 'mgba_libretro.so')  # tools/mgba/mgba-prof.patch
import ctypes as C
PROF={'iwram':(0x2000,0x03000000,4),'ewram':(0x10000,0x02000000,4),'rom':(0x200000,0x08000000,16),'bios':(0x1000,0,4),'vram':(0x6000,0x06000000,4)}
class D:
    def __init__(self, key, out, prof=True):
        self.name, self.path = rom_for(key)
        self.r = retro.Retro(self.path, core=PROF_CORE if prof else None)
        self.out = out; os.makedirs(out, exist_ok=True)
        self.r.run(1)
    def prof_arr(self, nm):
        n=PROF[nm][0]
        return np.ctypeslib.as_array((C.c_uint64*n).in_dll(self.r.lib,'retro_prof_'+nm))
    def prof_reset(self):
        for nm in PROF: self.prof_arr(nm)[:]=0
        for nm in ('thumb','arm','other'): np.ctypeslib.as_array((C.c_uint64*16).in_dll(self.r.lib,'retro_prof_'+nm))[:]=0
    def prof_get(self):
        res={nm:self.prof_arr(nm).copy() for nm in PROF}
        for nm in ('thumb','arm','other'): res[nm]=np.ctypeslib.as_array((C.c_uint64*16).in_dll(self.r.lib,'retro_prof_'+nm)).copy()
        return res
    def shot(self, name):
        if self.r.last_video:
            video_to_img(self.r.last_video).save(f'{self.out}/{name}.png')
    def io(self):
        try: return self.r.read(0x04000000, 0x400)
        except KeyError: return None
    def dump(self, name):
        d = {}
        for start, ln, ptr in self.r.regions:
            pass
        for nm, a, n in [('iwram',0x03000000,0x8000),('ewram',0x02000000,0x40000),('vram',0x06000000,0x18000),('pal',0x05000000,0x400),('oam',0x07000000,0x400),('io',0x04000000,0x400)]:
            try: open(f'{self.out}/{name}.{nm}','wb').write(self.r.read(a,n))
            except Exception as e: print('dump fail',nm,e)
        open(f'{self.out}/{name}.state','wb').write(self.r.save_state())
        self.shot(name)
    def press(self, btns, hold=6):
        self.r.buttons = set(btns.split('+')); self.r.run(hold); self.r.buttons=set(); self.r.run(2)
    def script(self, s):
        for t in s.split():
            c, a = t[0], t[1:]
            if c=='w': self.r.run(int(a))
            elif c=='p': self.press(a)
            elif c=='s': self.shot(a)
            elif c=='d': self.dump(a)
            elif c=='x': open(f'{self.out}/{a}.state','wb').write(self.r.save_state())
            elif c=='l': self.r.load_state(open(a,'rb').read())
if __name__=='__main__':
    d = D(sys.argv[1], sys.argv[2]); d.script(' '.join(sys.argv[3:]))
    print([ (hex(s),hex(l)) for s,l,p in d.r.regions])

def bp_set(d, addrs):
    L=d.r.lib
    arr=(C.c_uint32*8).in_dll(L,'retro_bp_addr')
    for i,a in enumerate(addrs): arr[i]=a
    C.c_int.in_dll(L,'retro_bp_n').value=len(addrs)
    C.c_uint32.in_dll(L,'retro_bp_logn').value=0
def bp_log(d, clear=True):
    L=d.r.lib
    n=C.c_uint32.in_dll(L,'retro_bp_logn').value
    a=np.ctypeslib.as_array((C.c_uint32*(4096*18)).in_dll(L,'retro_bp_log')).reshape(4096,18)[:n].copy()
    if clear: C.c_uint32.in_dll(L,'retro_bp_logn').value=0
    return [[int(x) for x in r] for r in a]  # cols: id, r0..r15, cpsr
