#!/usr/bin/env python3
"""Ground-truth capture for ADS carts.

Runs the cart in mGBA (libretro core patched with synchronous PC hooks, see ademu.py) and records
what the cartridge's own code produced:
  * every displayed video frame: md5 of the 240x160 BGR555 framebuffer right after the player's
    VQ blit returns (hook on the blit entry, then on its caller's return address);
  * every audio sample the player's ADPCM routine writes (hook on its entry and on its final pop),
    concatenated in decode order.

Modes:
  capture.py CART boot   OUT     power-on until the title screen (logo + jingle streams)
  capture.py CART movie K OUT    from the saved menu state, select episode K (1-based) and play it
Writes OUT/frames.csv (blit#, emu frame, fb md5), OUT/pcm.s8, OUT/acalls.csv, OUT/meta.json.
--dump A:B also writes raw framebuffers for blits A..B-1.
"""
import os, sys, json, hashlib, argparse, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import ademu, carts
from builds import discover

MENU_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'ads') + '/menus'

def md5(b): return hashlib.md5(b).hexdigest()

class Capture:
    def __init__(self, cid, out, dump=(0, 0)):
        self.cid = cid
        self.B = discover(cid)
        self.e = ademu.Emu(carts.emu_rom_path(cid))
        self.out = out; os.makedirs(out, exist_ok=True)
        self.dump = dump
        self.frames = []          # (emu_frame, fb_md5)
        self.pcm = bytearray()
        self.acalls = []          # (pcm_off, n, emu_frame)
        self.blit_pending = False
        self.a_start = None
        self.ret_addr = None
        self.last_blit_emu = 0

    def on_blit(self, g):
        self.blit_pending = True
        if self.ret_addr is None:
            self.ret_addr = (g[14] & 0xffffffff) & ~1
            self.install(extra=True)

    def on_blit_ret(self, g):
        if not self.blit_pending: return
        self.blit_pending = False
        fb = self.e.vram_fb()
        k = len(self.frames)
        if self.dump[0] <= k < self.dump[1]:
            d = os.path.join(self.out, 'raw'); os.makedirs(d, exist_ok=True)
            open(f'{d}/{k:05d}.fb', 'wb').write(fb)
        self.frames.append((self.e.r.frame_no, md5(fb)))
        self.last_blit_emu = self.e.r.frame_no

    def on_audio(self, g):
        self.a_start = g[1] & 0xffffffff

    def on_audio_end(self, g):
        if self.a_start is None: return
        st, end = self.a_start, g[1] & 0xffffffff
        self.acalls.append((len(self.pcm), end - st, self.e.r.frame_no))
        self.pcm += self.e.read(st, end - st)
        self.a_start = None

    def install(self, extra=False):
        h = [(self.B['blit'], self.on_blit), (self.B['audio'], self.on_audio), (self.B['audio_end'], self.on_audio_end)]
        if self.ret_addr is not None:
            h.append((self.ret_addr, self.on_blit_ret))
        self.e.set_hooks(h)

    def save(self, meta):
        with open(os.path.join(self.out, 'frames.csv'), 'w') as f:
            f.write('blit,emu,fb_md5\n')
            for i, (a, b) in enumerate(self.frames): f.write(f'{i},{a},{b}\n')
        open(os.path.join(self.out, 'pcm.s8'), 'wb').write(self.pcm)
        with open(os.path.join(self.out, 'acalls.csv'), 'w') as f:
            f.write('pcm_off,n,emu\n')
            for a, b, c in self.acalls: f.write(f'{a},{b},{c}\n')
        meta.update(cart=self.cid, build=self.B['build'], blit=hex(self.B['blit']), blit_ret=hex(self.ret_addr or 0),
                    audio=hex(self.B['audio']), audio_end=hex(self.B['audio_end']),
                    nframes=len(self.frames), npcm=len(self.pcm), emu_end=self.e.r.frame_no)
        json.dump(meta, open(os.path.join(self.out, 'meta.json'), 'w'), indent=1)

def run_until_idle(c, max_frames, idle=900, want=None, step=60):
    """run until no blit for `idle` frames after at least one blit, or `want` blits, or max_frames"""
    e = c.e; start = e.r.frame_no
    while e.r.frame_no - start < max_frames:
        e.run(step)
        if want and len(c.frames) >= want: break
        if c.frames and e.r.frame_no - c.last_blit_emu > idle: break

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cart'); ap.add_argument('mode'); ap.add_argument('args', nargs='*')
    ap.add_argument('--dump', default='0:0'); ap.add_argument('--want', type=int, default=0)
    ap.add_argument('--nav', default='', help='override navigation, tokens: D U L R A B S(tart) wN')
    a = ap.parse_args()
    d0, d1 = map(int, a.dump.split(':'))
    t0 = time.time()
    if a.mode == 'boot':
        out = a.args[0]
        c = Capture(a.cart, out, (d0, d1)); c.install()
        # logo + jingle play by themselves; stop when the blits stop (title screen)
        run_until_idle(c, 12000, idle=600)
        c.save(dict(mode='boot'))
    elif a.mode == 'movie':
        k, out = int(a.args[0]), a.args[1]
        c = Capture(a.cart, out, (d0, d1))
        c.e.load(open(f'{MENU_DIR}/{a.cart}_menu.state', 'rb').read())
        e = c.e
        nav = a.nav or ' '.join(['D w90'] * (k - 1) + ['A'])
        for t in nav.split():
            if t[0] == 'w': e.run(int(t[1:]))
            else:
                b = dict(D='DOWN', U='UP', L='LEFT', R='RIGHT', A='A', B='B', S='START')[t]
                e.press(b, 3, 20)
        c.install()
        # wait for playback; the instructions screen waits for a key press
        waited = 0
        while not c.frames and waited < 3000:
            e.run(60); waited += 60
            if not c.frames and waited % 600 == 0:
                e.press('A', 3, 1)
        run_until_idle(c, 400000, idle=900, want=a.want or None)
        c.save(dict(mode='movie', episode=k, nav=nav))
    print(a.cart, a.mode, a.args, 'blits', len(c.frames), 'pcm', len(c.pcm), f'{time.time() - t0:.0f}s')

if __name__ == '__main__':
    main()
