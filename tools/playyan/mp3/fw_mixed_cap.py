"""Full-system (real GBA ROM + music firmware in the LLE) capture of what the mixed-block reorder
0x2c52a reads beyond its copy: wraps tools/playyan/emu/capture.py (unchanged) with LLE_CAP at the reorder's
load 0x2c63a (r11 = sp + 4 * source index) and writes the raw capture records to
$MP3WORK/mixcap_<tag>.bin (records: VRF 4096 B, r0..r31, 1 KiB at r11).
usage: python3 fw_mixed_cap.py BUILD music FILE --secs S --name NAME --tag TAG"""
import os, sys, ctypes as C
os.environ.setdefault('LLE_CAP', 'py_fw1:0x2c63a:2200')
os.environ.setdefault('LLE_CAP_REG', '11')
FINAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'emu')  # capture.py
WORK = os.environ.get('MP3WORK', (os.environ.get('PLAYYAN_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'playyan')) + '/mp3work')
sys.path.insert(0, FINAL)
import capture  # noqa: E402
inst = []
_init = capture.RUN.Runner.__init__
def init(self, *a, **k):
    _init(self, *a, **k); inst.append(self)
capture.RUN.Runner.__init__ = init
tag = sys.argv[sys.argv.index('--tag') + 1]
sys.argv = ['capture.py'] + sys.argv[1:]
capture.main()
m = inst[0].model
n = m.L.vc_cap_n(m.m.c) if getattr(m, '_cap', None) else 0
open(os.path.join(WORK, 'mixcap_%s.bin' % tag), 'wb').write(bytes(m._capbuf)[:n * (4096 + 128 + 1024)] if n else b'')
print('mixed-reorder captures:', n)
