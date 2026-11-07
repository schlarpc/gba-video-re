import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import retro
from util import video_to_img
ROM = os.path.join(os.environ.get('GBAV_ROMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'roms'), 'Game Boy Advance Video - Sonic X - Volume 2 (USA) (Proto).gba')
PROF_CORE = os.environ.get('GBAV_MGBA_PROF_CORE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'mgba', 'prof', 'mgba_libretro.so')  # tools/mgba/mgba-prof.patch
out=sys.argv[1]; os.makedirs(out,exist_ok=True)
r=retro.Retro(ROM, core=PROF_CORE)
for f in range(1, int(sys.argv[2])+1):
    r.run(1)
    if f%30==0 and r.last_video: video_to_img(r.last_video).save(f'{out}/f{f:05d}.png')
open(f'{out}/end.state','wb').write(r.save_state())
