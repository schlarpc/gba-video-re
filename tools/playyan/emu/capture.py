#!/usr/bin/env python3
"""Whole-file output capture of the real Play-Yan firmware in the LLE emulator.

  capture.py BUILD KIND FILE [--secs S] [--name NAME] [--tag TAG]

BUILD  orig  = Play-Yan ROM, py_fw0 (video) / py_fw1 (music) uploaded by the GBA
       v111  = Play-Yan ROM + playan.fup on the SD card: the loader downloads fup_sec0/fup_sec1 (v1.11)
       micro = Play-Yan Micro ROM, mi_fw0 / mi_fw1
KIND   music | video
FILE   the media file; it is the only file on the SD card (FAT16 image) under NAME (default: its
       own base name; Garage ASFs must be renamed or the GBA starts the Garage Game).

Plays the file from the browser without touching the keys until the cartridge reports the end
(music 0x40000801, video 0x40000701) or S seconds of emulated time pass, and writes to
$PY_CAPTURE_DIR/cap/<tag>/:
  hp.s16          headphone PCM: every word the AC'97 DMA (channel 5) moved to ACFIFO, s16 L/R
  hp_blocks.csv   one row per firmware audio buffer the DMA started (data control blocks):
                  index, pcm offset (frames), src, bytes, VC cycles, fnv1a64 of the buffer at start,
                  fnv1a64 of what was played (differs = overwritten while playing)
  a8.bin          GBA 8-bit blocks, concatenated (record 0x80001000 with rec[3] != 0; rec[4] bytes)
  a8.csv          index, offset in a8.bin, bytes, GBA cycle, rec[2..7]
  frames.bin      240x160 frames as the GBA reads them through WIN_R1 (BGR555, after the bridge's
                  RGB565->BGR555 conversion), concatenated, 0x12C00 bytes each
  frames.csv      index, GBA cycle, VC addr, rec[2..7]
  pos.csv         music position records (0x80001000 with rec[3] == 0): GBA cycle, rec[5], rec[6]
  thumbs.bin/csv  60x40 thumbnails (0x40000500), BGR555 as the GBA reads them
  records.csv     every record: GBA cycle, frame, rec[0..7]
  info.json       build, file, images run, counters, emulated seconds
"""
import argparse, ctypes as C, hashlib, json, os, shutil, struct, sys, time
import numpy as np

# The emulator (VC01 core, peripheral model, patched mGBA bridge) is local and not in this repository:
# PLAYYAN_WORK names the tree that holds it (emu/, roms/, media/); captures go to $PY_CAPTURE_DIR.
WORK = os.environ.get('PLAYYAN_WORK', '')
EMU = os.path.join(WORK, 'emu')
HERE = os.environ.get('PY_CAPTURE_DIR', os.path.join(EMU, 'final'))
sys.path.insert(0, EMU); sys.path.insert(0, os.path.join(EMU, 'lle'))
import run as RUN  # noqa: E402
import lle_model, vcm, scenarios  # noqa: E402

GBA_HZ = 16777216
FUP = os.path.join(EMU, '..', 'media', 'nintendo', 'playan_firmware_playan', 'playan.fup')
ROMS = dict(orig=os.path.join(EMU, '..', 'roms', 'Play-Yan (Japan).gba'), v111=os.path.join(EMU, '..', 'roms', 'Play-Yan (Japan).gba'),
            micro=os.path.join(EMU, '..', 'roms', 'Play-Yan Micro (Japan).gba'))


def bgr555(raw):
    p = np.frombuffer(raw, '<u2')
    return ((p >> 11) | (((p >> 6) & 31) << 5) | ((p & 31) << 10)).astype('<u2').tobytes()


def fnv(b):
    f = 1469598103934665603
    for x in b:
        f = ((f ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f


class Cap:
    def __init__(self, out):
        self.out = out
        self.a8 = open(os.path.join(out, 'a8.bin'), 'wb'); self.a8n = 0; self.a8rows = []
        self.fr = open(os.path.join(out, 'frames.bin'), 'wb'); self.frrows = []
        self.th = open(os.path.join(out, 'thumbs.bin'), 'wb'); self.throws = []
        self.pos = []; self.recs = []
        self.on = False
        self.end_id = None; self.end = None   # (gba cycle, headphone marks count) at the end record

    def record(self, model):
        mv = model.m.mv
        rec = struct.unpack_from('<8I', mv, 0xFF000)
        cyc = model.c.cycles()
        self.recs.append((cyc, model.c.frame()) + rec)
        if rec[0] == 0x40000500 and rec[2]:       # browser thumbnails come before playback starts
            self.th.write(bgr555(bytes(mv[rec[2]:rec[2] + 0x12C0])))
            self.throws.append((len(self.throws), cyc) + rec[1:])
            return
        if not self.on:
            return
        if rec[0] == self.end_id:
            self.on = False
            self.end = (cyc, model.L.hw_pcm_marks_n(model.m.h))
            return
        if rec[0] == 0x80001000:
            if rec[1]:
                if rec[1] + 0x12C00 <= len(mv):
                    self.fr.write(bgr555(bytes(mv[rec[1]:rec[1] + 0x12C00])))
                    self.frrows.append((len(self.frrows), cyc, rec[1]) + rec[2:])
            elif rec[3]:
                n = rec[4]
                b = bytes(mv[rec[3]:rec[3] + n])
                self.a8.write(b)
                self.a8rows.append((len(self.a8rows), self.a8n, n, cyc) + rec[2:])
                self.a8n += n
            else:
                self.pos.append((cyc, rec[5], rec[6]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('build', choices=list(ROMS))
    ap.add_argument('kind', choices=['music', 'video'])
    ap.add_argument('file')
    ap.add_argument('--secs', type=float, default=None)
    ap.add_argument('--name')
    ap.add_argument('--tag')
    ap.add_argument('--ips', type=float, default=50e6)
    ap.add_argument('--period', type=int, default=1024)
    ap.add_argument('--ac-scale', type=float, default=1.0, help='experiment: AC\'97 DAC consumption speed factor')
    ap.add_argument('--memrep7', type=int, default=0, help='vector vld/vst REP code 7: 0 = 64 (default), 1 = 128 wrap (old), 2 = 128 zero rows')
    ap.add_argument('--hp', action='store_true', help='headphones plugged (WM9712 reg 0x54 bit 1)')
    a = ap.parse_args()
    src = os.path.abspath(a.file)
    name = a.name or os.path.basename(src)
    tag = a.tag or '%s_%s' % (a.build, os.path.splitext(name)[0])
    out = os.path.join(HERE, 'cap', tag); os.makedirs(out, exist_ok=True)
    sd = os.path.join(HERE, 'sd', tag)
    shutil.rmtree(sd, ignore_errors=True); os.makedirs(sd)
    os.symlink(src, os.path.join(sd, name))
    if a.build == 'v111':
        os.symlink(os.path.abspath(FUP), os.path.join(sd, 'playan.fup'))
    img = vcm.make_sd(sd, os.path.join(HERE, 'sd', tag + '.img'))
    dur = a.secs
    if dur is None:
        import subprocess
        d = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', src],
                           capture_output=True, text=True).stdout.strip()
        dur = float(d) + 8
    cap = Cap(out)
    orig = lle_model.LLEModel.on_record

    def on_record(self):
        cap.record(self)
        return orig(self)
    lle_model.LLEModel.on_record = on_record
    RUN.HERE = HERE
    for d in ('traces', 'out', 'shots'):
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
    r = RUN.Runner(ROMS[a.build], tag, None, dict(sd_image=img, ips=a.ips, period=a.period, ac_scale=a.ac_scale, hp_jack=int(a.hp), memrep7=a.memrep7), sd, lle=True)
    m = r.model; L = m.L; h = m.m.h
    PCMCAP = int(48000 * 2 * (dur + 30))
    pcm = (C.c_int16 * PCMCAP)()
    MKCAP = 5 * 200000
    mk = (C.c_uint64 * MKCAP)()
    t0 = time.time()
    if a.kind == 'music':
        if r.core.family == 2:
            scenarios.to_menu(r)
            n = r.ncmd(); r.press('A', after=10); r.until_cmd(0x200, 600, after=n); r.step(120)
        else:
            scenarios.to_music_browser(r)
        r.shot('browser')
        L.hw_pcm_buffer(h, pcm, PCMCAP); L.hw_pcm_marks(h, mk, MKCAP)
        cap.on = True
        n = r.ncmd(); nr = r.nrec()
        r.press('A', after=30)
        if r.until_cmd(0x800, 180, after=n) is None:
            r.press('A', after=10); r.until_cmd(0x800, 300, after=n)
        end_id = 0x40000801
        cap.end_id = end_id
    else:
        if r.core.family == 2:
            scenarios.to_menu(r)
            r.press('RIGHT', after=10)
            n = r.ncmd(); r.press('A', after=10); r.until_cmd(0x500, 900, after=n); r.step(120)
        else:
            scenarios.to_video_browser(r)
        r.shot('browser')
        L.hw_pcm_buffer(h, pcm, PCMCAP); L.hw_pcm_marks(h, mk, MKCAP)
        cap.on = True
        n = r.ncmd(); nr = r.nrec()
        r.press('A', after=5)
        r.until_cmd(0x700, 300, after=n)
        end_id = 0x40000701
        cap.end_id = end_id
    c_start = r.core.cycles(); f_start = r.core.frame()
    r.mark('play')
    ended = None
    lim = int(dur * 60)
    k = 0
    while k < lim:
        r.step(30); k += 30
        if k % 1800 == 0:
            r.shot('t%04d' % (k // 60))
        if any(x[1] == end_id for x in m.rec_log[nr:]):
            ended = r.core.frame()
            r.step(60)
            break
    r.shot('end')
    c_end = r.core.cycles()
    cap.on = False
    r.core.flush_trace()
    # headphone PCM + blocks
    pn = L.hw_pcm_count(h); mn = L.hw_pcm_marks_n(h)
    if cap.end:                       # only the blocks started before the end record
        mn = cap.end[1]
    marks = np.frombuffer(bytes(mk), np.uint64, count=mn).reshape(-1, 5)
    if cap.end and len(marks):
        pn = min(pn, int(marks[-1, 0] + marks[-1, 2] // 2))
    hp = np.frombuffer(bytes(pcm), np.int16, count=pn)
    hp.tofile(os.path.join(out, 'hp.s16'))
    rows = []
    over = 0
    for i, (p, s, nb, cy, f) in enumerate(marks.tolist()):
        seg = hp[p:p + nb // 2]
        played = fnv(seg.tobytes()) if len(seg) == nb // 2 else None
        if played is not None and played != f:
            over += 1
        rows.append((i, p // 2, s, nb, cy, '%016x' % f, '%016x' % played if played is not None else ''))
    with open(os.path.join(out, 'hp_blocks.csv'), 'w') as fo:
        fo.write('block,pcm_frame,src,bytes,vc_cycle,fnv_start,fnv_played\n')
        for row in rows:
            fo.write('%d,%d,%#x,%d,%d,%s,%s\n' % row)
    for nm, rws, hdr in (('a8.csv', cap.a8rows, 'block,offset,bytes,gba_cycle,r2,r3,r4,r5,r6,r7'),
                         ('frames.csv', cap.frrows, 'frame,gba_cycle,addr,r2,r3,r4,r5,r6,r7'),
                         ('thumbs.csv', cap.throws, 'thumb,gba_cycle,r1,r2,r3,r4,r5,r6,r7'),
                         ('pos.csv', cap.pos, 'gba_cycle,r5,r6'),
                         ('records.csv', [(c0, f0) + tuple('%08X' % x for x in rr) for c0, f0, *rr in cap.recs],
                          'gba_cycle,frame,r0,r1,r2,r3,r4,r5,r6,r7')):
        with open(os.path.join(out, nm), 'w') as fo:
            fo.write(hdr + '\n')
            for row in rws:
                fo.write(','.join(str(x) for x in row) + '\n')
    for f in (cap.a8, cap.fr, cap.th):
        f.close()
    st = m.m.stats() if hasattr(m.m, 'stats') else None
    info = dict(build=a.build, kind=a.kind, file=src, name=name, md5=hashlib.md5(open(src, 'rb').read()).hexdigest(),
                images=[k.get('image') for k in m.kicks] + [v.get('image') for v in m.verify if v.get('image')][-1:],
                ips=a.ips, start_cycle=c_start, end_cycle=c_end, emu_secs=(c_end - c_start) / GBA_HZ,
                start_frame=f_start, end_frame=r.core.frame(), ended_frame=ended, end_record_cycle=cap.end[0] if cap.end else None,
                hp_frames=int(pn // 2), hp_blocks=len(rows), hp_overwritten=over,
                ac_silence_words=int(L.hw_ac_silence(h)), ac_rate=int(st['ac_rate']) if isinstance(st, dict) and 'ac_rate' in st else None,
                a8_blocks=len(cap.a8rows), frames=len(cap.frrows), thumbs=len(cap.throws), pos=len(cap.pos),
                gba_errors=m.errors[:50], wall_s=round(time.time() - t0, 1),
                uart=''.join(s for _, s in m.uart_log)[-2000:])
    json.dump(info, open(os.path.join(out, 'info.json'), 'w'), indent=1, default=str)
    print('CAPTURE', tag, json.dumps({k: info[k] for k in ('images', 'emu_secs', 'ended_frame', 'hp_frames', 'hp_blocks',
                                                          'hp_overwritten', 'ac_silence_words', 'a8_blocks', 'frames', 'thumbs', 'wall_s')}, default=str))


if __name__ == '__main__':
    main()
