import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import retro
from util import video_to_img
ROM = os.path.join(os.environ.get('GBAV_ROMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'roms'), 'Game Boy Advance Video - Sonic X - Volume 2 (USA) (Proto).gba')
PROF_CORE = os.environ.get('GBAV_MGBA_PROF_CORE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'mgba', 'prof', 'mgba_libretro.so')  # tools/mgba/mgba-prof.patch
class N:
    def __init__(s,out,state=None):
        s.out=out; os.makedirs(out,exist_ok=True)
        s.r=retro.Retro(ROM, core=PROF_CORE); s.r.run(1)
        if state: s.r.load_state(open(state,'rb').read())
    def shot(s,n):
        video_to_img(s.r.last_video).save(f'{s.out}/{n}.png')
    def press(s,b,hold=6):
        s.r.buttons=set(b.split('+')); s.r.run(hold); s.r.buttons=set(); s.r.run(2)
    def script(s,txt):
        for t in txt.split():
            c,a=t[0],t[1:]
            if c=='w': s.r.run(int(a))
            elif c=='p': s.press(a)
            elif c=='s': s.shot(a)
            elif c=='x': open(f'{s.out}/{a}.state','wb').write(s.r.save_state())
if __name__=='__main__':
    st=None; args=sys.argv[2:]
    if args and args[0].startswith('@'): st=args[0][1:]; args=args[1:]
    N(sys.argv[1],st).script(' '.join(args))
