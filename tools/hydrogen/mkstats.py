#!/usr/bin/env python3
"""Build docs/hydrogen/stats.json (one record per cart x stream) and tmp/hydrogen/perf_summary.json from:
verify results (verify.py --json), the manifests (blit timing), the perf runs
(tmp/hydrogen/perf/<CODE>_<i>/per_frame.csv, per_vblank.csv), hystat --frames (compressed bytes per
frame) and the runtime menu titles (tmp/hydrogen/titles.json)."""
import os, sys, json, csv, gzip, subprocess, collections
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import hycart
from carts import CARTS, BUILD, SHORT, rom_path

WORK = os.path.join(HERE, '..', '..', 'tmp', 'hydrogen')
FPS = 2 ** 24 / (5 * 280896)          # 11.9455 frames per second
VBLANK = 280896
SHOW = {'MGUE': 'All Grown Up!', 'MSHE': 'Sonic X', 'MYGE': 'Yu-Gi-Oh!', 'MSSE': 'SpongeBob SquarePants',
        'MS2E': 'SpongeBob SquarePants', 'MFOE': 'The Fairly OddParents!', 'MF2E': 'The Fairly OddParents!',
        'MTME': 'Teenage Mutant Ninja Turtles', 'MNCE': 'Nicktoons Collection', 'MSBE': 'Strawberry Shortcake',
        'MKDE': 'Codename: Kids Next Door', 'MCTE': 'Cartoon Network Collection', 'MDRE': 'Dora the Explorer'}


def perf_summary(code, i):
    d = f'{WORK}/perf/{code}_{i}'
    if not os.path.exists(d + '/per_frame.csv'): return None
    pf = list(csv.DictReader(open(d + '/per_frame.csv')))
    wall = np.array([int(r['wall']) for r in pf], float)
    busy = np.array([int(r['busy']) for r in pf], float)
    tot = lambda k: float(sum(int(r[k]) for r in pf))
    W = wall.sum()
    out = dict(frames=len(pf), cpu_busy_mean=float(busy.sum() / W), cpu_busy_p99=float(np.percentile(busy / wall, 99)),
               cpu_busy_max=float((busy / wall).max()),
               video_decode_share=(tot('inflate') + tot('vq_convert') + tot('vq_unpredict')) / W,
               audio_decode_share=tot('adpcm') / W, blit_share=tot('vq_blit') / W,
               inflate_share=tot('inflate') / W, convert_share=(tot('vq_convert') + tot('vq_unpredict')) / W,
               irq_share=(tot('irq_dispatch') + tot('irq_entry') + tot('bios')) / W, rom_code_share=tot('rom_code') / W,
               osd_share=tot('osd_font') / W, dma_cyc_share=tot('dma_cyc') / W,
               rom_read_Bps=(tot('rd_rom')) / (W / 2 ** 24), memwait_share=tot('memwait') / W,
               blit_cycles=float(np.median([int(r['vq_blit']) for r in pf if int(r['vq_blit'])])),
               inflate_frame_p50=float(np.median([int(r['inflate']) for r in pf])),
               inflate_frame_max=float(max(int(r['inflate']) for r in pf)),
               vram_wr_active_share=tot('vram_wr_active') / max(1, tot('vram_wr_active') + tot('vram_wr_hblank') + tot('vram_wr_vblank')))
    if os.path.exists(d + '/per_vblank.csv'):
        pv = list(csv.DictReader(open(d + '/per_vblank.csv')))
        nb = max(int(r['blits_before']) for r in pv)
        vb = np.array([int(r['busy']) for r in pv if 1 <= int(r['blits_before']) < nb], float)
        out.update(vblank_busy_max=float(vb.max() / VBLANK), vblank_busy_p99=float(np.percentile(vb, 99) / VBLANK),
                   vblanks_over=int((vb > VBLANK).sum()), vblanks=len(vb))
    return out


def main():
    import csv
    with open(os.path.join(HERE, '..', '..', 'verify', 'carts.csv'), newline='') as f:
        idx = {r['nointro']: r for r in csv.DictReader(f)}
    ver = json.load(open(f'{WORK}/verify_all.json'))
    titles = json.load(open(f'{WORK}/titles.json'))
    recs = []; psum = {}
    for code in CARTS:
        rp = rom_path(code); rom = open(rp, 'rb').read()
        nointro = 'Game Boy Advance Video - ' + CARTS[code]
        sha1 = idx[nointro]['sha1']
        fb = {}
        p = subprocess.run([os.path.join(HERE, '..', '..', 'reference', 'hydrogen', 'hystat'), rp, '--frames'], capture_output=True, text=True)
        for l in p.stdout.splitlines():
            r = json.loads(l); fb[r['stream']] = r['frame_bytes']
        conts = hycart.find_containers(rom)
        tmap = {t[1]: t[2] for t in titles[code] if t[1] is not None}
        for i, c in enumerate(conts):
            s = hycart.Stream(rom, c['video'][0], c['video'][1])
            name = f'{SHORT[code]}_{i}'
            v = ver.get(name, {})
            if i == 0: title = 'Majesco Digital Video logo'
            elif i == len(conts) - 1 and s.nchap == 0: title = f'{SHOW[code]} intro'
            else: title = tmap.get(i)
            dur = s.frames / FPS
            fbytes = np.array(fb[i], float)
            win = 12
            kb1 = [fbytes[k:k + win].sum() * 8 / (win / FPS) / 1000 for k in range(0, max(1, len(fbytes) - win + 1))]
            # blit timing from the manifest
            rows = list(csv.DictReader(gzip.open(f'{HERE}/manifests/{name}.frame_md5.csv.gz', 'rt')))
            vbs = [int(r['vblank']) for r in rows]
            late = sum(1 for a, b in zip(vbs, vbs[1:]) if b - a != 5)
            ps = perf_summary(code, i)
            psum[name] = ps
            modes = collections.Counter(g['mode'] for g in s.segments)
            rec = dict(nointro=nointro, sha1=sha1, family='Hydrogen', player_build=BUILD[code], stream=i, title=title,
                       width=240, height=160, fps=round(FPS, 4), frames=s.frames, duration_s=round(dur, 2),
                       video_bytes=c['video'][1], audio_bytes=c['audio'][1],
                       video_kbps_mean=round(c['video'][1] * 8 / dur / 1000, 2),
                       video_kbps_p99_1s=round(float(np.percentile(kb1, 99)), 2),
                       audio_kbps=round(c['audio'][1] * 8 / dur / 1000, 2),
                       audio_codec='Majesco ADPCM (2-bit codes, 1/2/3-bit VLC, flat 5-bit context table)',
                       audio_rate_hz=16384, audio_channels=1, audio_out_rate_hz=16384,
                       keyframes=s.nseg, seek_points=s.nseg,
                       cpu_busy_mean=None, cpu_busy_p99=None, cpu_busy_max=None,
                       video_decode_share=None, audio_decode_share=None, blit_share=None,
                       late_or_dropped_frames=late,
                       verified_video=f"{v.get('frames_ok', 0)}/{s.frames}",
                       verified_audio=f"{v.get('audio_ok', 0)}/{v.get('audio_calls', 0)}")
            notes = [f"segments per mode {dict(sorted(modes.items()))} (1=4x4, 3=4x3, 5=3x3, 7=4x2)",
                     f"{s.nseg} segments (each one a keyframe and a seek point: own codebook and index stream), "
                     f"{s.nchap} chapters",
                     "picture 240x159 + black line 159 in 4x3/3x3 segments",
                     "fps = 2^24/(5*280896) exactly (header says 12)",
                     "verified_audio counts ADPCM calls (1371/1372 samples each, 4096-sample prefill first)"]
            if ps:
                for k in ('cpu_busy_mean', 'cpu_busy_p99', 'cpu_busy_max', 'video_decode_share', 'audio_decode_share', 'blit_share'):
                    rec[k] = round(ps[k], 4)
                notes.append('CPU figures per 5-VBlank frame window, instrumented mGBA, whole stream; '
                             f"heaviest VBlank {ps.get('vblank_busy_max', 0):.3f} of its period")
            else:
                notes.append('perf: not measured')
            rec['notes'] = '; '.join(notes)
            recs.append(rec)
    json.dump(recs, open(os.path.join(HERE, '..', '..', 'docs', 'hydrogen', 'stats.json'), 'w'), indent=1)
    json.dump(psum, open(f'{WORK}/perf_summary.json', 'w'), indent=1)
    print(len(recs), 'records')


if __name__ == '__main__':
    main()
