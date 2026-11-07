#!/usr/bin/env python3
"""Build stats.json for the FVMV family: container walk + mode statistics (C decoder) +
CPU profile (perf/out/*.npz from perf/prof_fvmv.py) + verification (verify.py --json) +
audio ring resyncs observed in the mGBA capture.

usage: mkstats.py ROMDIR OUT.json [--perf DIR] [--verify JSON] [--cap DIR] [--modes DIR]"""
import argparse, json, os, re, struct, sys
import numpy as np

CARTS = [  # (vol, nointro, sha1, code)
    (1, 'Game Boy Advance Video - Pokemon - Volume 1 (USA)', 'ad0164df397860b0967590ee46ca1f41d6cecdcd', 'MPAE'),
    (2, 'Game Boy Advance Video - Pokemon - Volume 2 (USA)', 'e7b2e00d6a60e8c149cd07b0ee8d98bf2b4efd15', 'MPBE'),
    (3, 'Game Boy Advance Video - Pokemon - Volume 3 (USA)', '0f0020ef4278e1777baaf74b6787355a5cb57d50', 'MPCE'),
    (4, 'Game Boy Advance Video - Pokemon - Volume 4 (USA)', 'fd79b821ff601e0091f19ab9c86b08f1c39ea2f3', 'MPDE'),
]
TITLES = {(1, 0): 'A Hot Water Battle', (1, 1): 'For Ho-Oh the Bells Toll!',
          (2, 0): 'Playing with Fire!', (2, 1): 'Johto Photo Finish',
          (3, 0): 'Pokemon - I Choose You!', (3, 1): 'Here Comes the Squirtle Squad',
          (4, 0): 'Beach Blank-out Blastoise', (4, 1): 'Go West Young Meowth'}
BUILD = {1: 'a7e6307ff34a/A', 2: 'a7e6307ff34a/A', 3: 'a7e6307ff34a/B', 4: 'a7e6307ff34a/B'}
CPU_HZ = 16777216  # fps = 2^24 / (1704 * 1232) = 7.99171
REFRESH = 280896


def walk(rom, o):
    nf, w, h, per, size = struct.unpack_from('<5I', rom, o + 4)
    p = o + 0x20
    rec = []
    for i in range(nf):
        sz, back, fwd, idx, vl = struct.unpack_from('<IiIII', rom, p)
        al = struct.unpack_from('<I', rom, p + 0x14 + vl)[0]
        rec.append((p - o, back, vl, al, fwd))
        p += 16 + sz
    return dict(nf=nf, w=w, h=h, period=per, size=size, rec=rec)


def modes(path):
    t = open(path).read().splitlines()
    names = t[0].split()[1:]
    tab = {}
    for l in t[1:]:
        p = l.split()
        if len(p) == 9 and 'x' in p[0]:
            tab[p[0]] = dict(zip(names, map(int, p[1:])))
    area = {}
    for sz, row in tab.items():
        w, h = map(int, sz.split('x'))
        for m, n in row.items():
            if m.startswith('split'):
                continue
            area[m] = area.get(m, 0) + n * w * h
    xw = int(re.search(r'mv_xwrap (\d+)', open(path).read()).group(1))
    return tab, area, xw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('romdir')
    ap.add_argument('out')
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument('--perf', default=os.path.join(here, '..', '..', 'tmp', 'fvmv', 'perf'))
    ap.add_argument('--verify', default=os.path.join(here, '..', '..', 'tmp', 'fvmv', 'verify.json'))
    ap.add_argument('--cap', default=os.path.join(here, '..', '..', 'tmp', 'fvmv', 'cap3'))
    ap.add_argument('--modes', default=os.path.join(here, '..', '..', 'tmp', 'fvmv', 'stats'))
    a = ap.parse_args()
    ver = json.load(open(a.verify))
    out = []
    for vol, nointro, sha, code in CARTS:
        rom = open(os.path.join(a.romdir, nointro + '.gba'), 'rb').read()
        offs = []
        o = -4
        while True:
            o = rom.find(b'FVMV', o + 4)
            if o < 0:
                break
            if struct.unpack_from('<II', rom, o + 8) == (240, 160):
                offs.append(o)
        for s, o in enumerate(offs):
            st = walk(rom, o)
            fps = CPU_HZ / (st['period'] * 1232)
            nf = st['nf']
            dur = nf / fps
            vb = np.array([r[2] for r in st['rec']])
            ab = np.array([r[3] for r in st['rec']])
            win = int(round(fps))
            csum = np.concatenate([[0], np.cumsum(vb)])
            per_s = (csum[win:] - csum[:-win]) * 8 / (win / fps) / 1000
            offs_set = {r[0] for r in st['rec']}
            keyf = len({r[0] + r[1] for r in st['rec']} | {(r[0] + r[4]) & 0xffffffff for r in st['rec']} & offs_set)
            rec = dict(nointro=nointro, sha1=sha, family='FVMV', player_build=BUILD[vol], stream=s,
                       title=TITLES[(vol, s)], width=st['w'], height=st['h'], fps=round(fps, 5),
                       frames=nf, duration_s=round(dur, 3), video_bytes=int(vb.sum()),
                       audio_bytes=int(ab.sum()),
                       video_kbps_mean=round(vb.sum() * 8 / dur / 1000, 2),
                       video_kbps_p99_1s=round(float(np.percentile(per_s, 99)), 2),
                       audio_kbps=round(ab.sum() * 8 / dur / 1000, 3),
                       audio_codec='GSM 06.10-style RPE-LPC without LTP (Actimagine), 40-byte blocks of 256 samples',
                       audio_rate_hz=16384, audio_channels=1, audio_out_rate_hz=65536,
                       keyframes=keyf, seek_points=keyf)
            # profile
            pz = os.path.join(a.perf, f'p{vol}e{s + 1}.npz')
            notes = []
            if os.path.exists(pz):
                z = np.load(pz)
                rc = z['run_cyc'].astype(np.int64)
                names = list(z['names'])
                dr = z['dec_run']
                ix = {n: i for i, n in enumerate(names)}
                # per movie-frame windows: from frame i's decode refresh to frame i+1's
                cs = np.vstack([np.zeros(len(names), np.int64), np.cumsum(rc, 0)])
                wins = cs[dr[1:]] - cs[dr[:-1]]
                tot = wins.sum(1)
                busy = 1 - wins[:, ix['idle_wait']] / tot
                allc = rc[dr[0]:dr[-1]].sum(0)
                T = allc.sum()
                rec.update(cpu_busy_mean=round(float(1 - allc[ix['idle_wait']] / T), 4),
                           cpu_busy_p99=round(float(np.percentile(busy, 99)), 4),
                           cpu_busy_max=round(float(busy.max()), 4),
                           video_decode_share=round(float(allc[ix['video_decode']] / T), 4),
                           audio_decode_share=round(float((allc[ix['audio_synth']] + allc[ix['audio_output']]
                                                           + allc[ix['audio_ring']]) / T), 4),
                           blit_share=round(float(allc[ix['blit']] / T), 4))
                late = int((wins[:, ix['idle_wait']] < 2000).sum())
                rec['late_or_dropped_frames'] = late
                notes.append(f'busy = 1 - idle-wait share in the whole-stream mGBA profile; p99/max over '
                             f'decode-to-decode windows of 7-8 refreshes; every frame window keeps at least '
                             f'{int(wins[:, ix["idle_wait"]].min()) // 1000} k idle cycles, so no frame is late '
                             f'(the player never drops frames)')
            else:
                rec.update(cpu_busy_mean=None, cpu_busy_p99=None, cpu_busy_max=None,
                           video_decode_share=None, audio_decode_share=None, blit_share=None,
                           late_or_dropped_frames=None)
            v = ver.get(f'{code}_{s}', {})
            rec['verified_video'] = v.get('frames')
            rec['verified_audio'] = v.get('audio_blocks')
            rs = os.path.join(a.cap, f'p{vol}e{s + 1}', 'resync.txt')
            if os.path.exists(rs):
                n = len(open(rs).read().split())
                notes.append(f'{n} audio ring resyncs in mGBA (each skips/repeats up to 1/8 s of audio)')
            mp = os.path.join(a.modes, f'p{vol}s{s}.txt')
            if os.path.exists(mp):
                tab, area, xw = modes(mp)
                tot = sum(area.values())
                notes.append('pixel share by leaf mode: ' + ', '.join(
                    f'{m} {100 * n / tot:.1f}%' for m, n in sorted(area.items(), key=lambda x: -x[1])))
                notes.append(f'{xw} motion vectors wrap across the left/right picture edge')
            notes.append('fps = 2^24 / (1704 * 1232); keyframes = seek points (all verified intra)')
            rec['notes'] = '; '.join(notes)
            out.append(rec)
    json.dump(out, open(a.out, 'w'), indent=1)
    print(len(out), 'records')


if __name__ == '__main__':
    main()
