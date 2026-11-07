#!/usr/bin/env python3
"""Make a playback savestate for the unicorn oracle: inject PlayMovie (as capture.py), run until
blit K, finish that VBlank and save the state. Writes OUT.state and OUT.json {ctx, addresses}."""
import sys, os, json, struct, ctypes as C
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import retro, hycart, capture
rom_path, stream, k, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
rom = open(rom_path, 'rb').read()
cont = hycart.find_containers(rom)[stream]
ad = capture.addresses(rom)
r = retro.Retro(rom_path, core=capture.CORE); L = r.lib
arr = (C.c_uint32 * 8).in_dll(L, 'retro_bp_addr')
st = dict(inj=0, n=0, ctx=None)
def cb(i, R):
    if i == 0 and not st['inj']:
        r.write(ad['state'], bytes([ad['play']])); r.write(ad['state'] + 4, struct.pack('<i', -1))
        r.write(ad['movie'], struct.pack('<I', 0x08000000 + cont['container'])); st['inj'] = 1
    elif i == 1:
        st['n'] += 1; st['ctx'] = R[0]
cbf = C.CFUNCTYPE(None, C.c_int, C.POINTER(C.c_uint32))(cb)
C.c_void_p.in_dll(L, 'retro_bp_cb').value = C.cast(cbf, C.c_void_p).value
arr[0] = ad['dispatch']; arr[1] = ad['blit']; C.c_int.in_dll(L, 'retro_bp_n').value = 2
C.c_int.in_dll(L, 'retro_prof_on').value = 0
while not st['inj'] or st['n'] < k: r.run(1)
r.run(1)
C.c_int.in_dll(L, 'retro_bp_n').value = 0
open(out + '.state', 'wb').write(r.save_state())
json.dump(dict(ctx=st['ctx'], blits=st['n'], **ad), open(out + '.json', 'w'))
print('ctx', st['ctx'] and hex(st['ctx']), 'blits', st['n'])
