"""Capture the cart's own decoded video: md5 of the VRAM bitmap right after every VQ blit.
usage: capvid.py OUT.csv (--state S | --boot) [--script "pA ..."] [--max-vbl N] [--idle N]
CSV: seq, vblank, ctx, frame_counter(ctx+0x1c after blit), mode, lines, md5(lines*240*2 bytes)
"""
import sys, os, argparse, hashlib, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nav import N
from bp import bp_set, bp_log
ENTRY = 0x0300202c
EXITS = {0x03002148: 4, 0x030021e4: 2, 0x030022a8: 3, 0x0300240c: 3}   # exit pc -> block height
ap = argparse.ArgumentParser()
ap.add_argument('out'); ap.add_argument('--state'); ap.add_argument('--boot', action='store_true')
ap.add_argument('--script', default=''); ap.add_argument('--max-vbl', type=int, default=200000)
ap.add_argument('--idle', type=int, default=600); ap.add_argument('--ctx', type=lambda x:int(x,0), default=None); ap.add_argument('--dump', default=None)
a = ap.parse_args()
n = N(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'sonicx2-proto', 'cap'), None if a.boot else a.state)
r = n.r
bp_set(r, [ENTRY] + list(EXITS))
if a.script: n.script(a.script); bp_log(r)
out = open(a.out, 'w'); out.write('seq,vblank,ctx,fc,mode,lines,md5\n')
dump = open(a.dump, 'wb') if a.dump else None
seq = 0; idle = 0; started = False; ctx = a.ctx; mode = None
for v in range(a.max_vbl):
    if a.ctx is not None and ctx is not None and r.u32(ctx + 0x1614):
        r.write(ctx + 0x1614, b'\0\0\0\0'); osd_kills = globals().get('osd_kills', 0) + 1; globals()['osd_kills'] = osd_kills
    r.run(1)
    hit = None
    for row in bp_log(r):
        pc = [ENTRY] + list(EXITS)
        addr = pc[row[0]]
        if addr == ENTRY:
            ctx = row[1]; mode = r.u32(r.u32(ctx + 0xc)) & 7
        else:
            hit = addr
    if hit:
        bh = EXITS[hit]; lines = (160 // bh) * bh
        fb = r.read(0x06000000, 240 * 2 * lines)
        fc = r.u32(ctx + 0x1c)
        out.write(f'{seq},{v},{ctx:#x},{fc},{mode},{lines},{hashlib.md5(fb).hexdigest()}\n')
        if dump: dump.write(r.read(0x06000000, 240*160*2))
        seq += 1; idle = 0; started = True
    elif started:
        idle += 1
        if idle > a.idle: break
out.close()
print('frames', seq, 'vblanks', v + 1, 'osd_suppressed', globals().get('osd_kills', 0))
