#!/usr/bin/env python3
"""Build ads/stats.json: one record per (cart, stream) from the ROM (sizes, frames, chunks),
verify.py results (verify.json), and the perf runs (work/perf/<cart>/<capture>.csv.gz).
usage: mkstats.py VERIFY_JSON OUT_JSON"""
import os, sys, json, gzip, csv, hashlib, subprocess, struct
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import carts, images
from explain_stale import chunks_of
ADSDEC = os.path.join(HERE, '..', '..', 'reference', 'ads', 'adsdec')
PERF = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tmp', 'ads') + '/perf'
FPS = 59.7275 / 5
REFRESH = 280896
BLOCK = 16458

def listing(cid):
    out = subprocess.run([ADSDEC, carts.rom_path(cid), '--list'], capture_output=True, text=True).stdout
    res = []
    for l in out.splitlines():
        if not (l and l[0].isdigit()): continue
        nm = l.split('mmstr@')[0].split(None, 1)[1].strip()
        title = l.split('"')[1] if '"' in l else ''
        res.append(dict(name=nm, frames=int(l.split(' frames')[0].split()[-1]), blocks=int(l.split(' blocks')[0].split()[-1]),
                        video_off=int(l.split('video@')[1].split()[0], 16), title=title,
                        nchap=sum(1 for x in out.splitlines() if False)))
    return res, out

def resource_sizes(cid, video_off):
    rom = carts.load(cid)
    # the 4 bytes before the resource payload hold its header word (type<<28 | size)
    w = struct.unpack_from('<I', rom, video_off - 4)[0]
    hyd = cid in ('jimmy', 'noah-proto')
    vlen = (w & 0x0fffffff) * (1 if hyd else 4)
    p = video_off + vlen
    w2 = struct.unpack_from('<I', rom, p)[0] if p + 4 <= len(rom) else 0
    return vlen

def main():
    vj = {(r['cart'], r['stream']): r for r in json.load(open(sys.argv[1]))}
    out = []
    for cid in carts.CARTS:
        rom = carts.load(cid)
        sha1 = hashlib.sha1(rom).hexdigest()
        lst, raw = listing(cid)
        capj = json.load(open(os.path.join(HERE, '..', '..', 'verify', 'ads', f'{cid}.capture.json')))
        for s in lst:
            nm = s['name']; sid = nm.replace(' ', '_')
            d, chs = chunks_of(cid, nm)
            # sizes: video resource = header..end; audio = type-2 resource
            line = [l for l in raw.splitlines() if l and l[0].isdigit() and l.split('mmstr@')[0].split(None, 1)[1].strip() == nm][0]
            kv = dict(x.split('=') for x in line.split() if '=' in x and x.split('=')[0] in ('vbytes', 'abytes', 'chapters'))
            vbytes, abytes = int(kv['vbytes']), int(kv['abytes'])
            info = dict(chapters=int(kv['chapters']))
            frames = s['frames']; dur = frames / FPS
            # per-frame bytes: each chunk's bytes spread evenly over its frames
            per = np.concatenate([np.full(c['nfr'], (16 + c['A'][1] + c['B'][1]) / max(1, c['nfr'])) for c in chs])
            win = int(round(FPS))
            kb1s = [per[i:i + win].sum() * 8 / 1000 for i in range(0, max(1, len(per) - win + 1))]
            rec = dict(nointro=carts.CARTS[cid], sha1=sha1, family='ADS', player_build=images.BUILD_OF[cid],
                       stream=nm, title=s['title'] or None, width=240, height=160, fps=round(FPS, 4), frames=frames,
                       duration_s=round(dur, 3), video_bytes=vbytes, audio_bytes=abytes,
                       video_kbps_mean=round(vbytes * 8 / dur / 1000, 2), video_kbps_p99_1s=round(float(np.percentile(kb1s, 99)), 2),
                       audio_kbps=round(abytes * 8 / (s['blocks'] * BLOCK / 16384) / 1000, 2) if s['blocks'] else None,
                       audio_codec='Majesco ADPCM (' + ('flat context table' if cid in ('jimmy', 'noah-proto') else 'VLC context table') + ')',
                       audio_rate_hz=16384, audio_channels=1, audio_out_rate_hz=16384,
                       keyframes=len(chs), seek_points=info['chapters'])
            notes = []
            # perf
            cap = capj.get(nm)
            pf = os.path.join(PERF, cid, f"{cap['capture']}.csv.gz") if cap else None
            if pf and os.path.exists(pf):
                R = np.loadtxt(gzip.open(pf, 'rt'), delimiter=',', skiprows=1, ndmin=2)
                lo = cap['first_blit']; R = R[lo:lo + max(0, cap['frames'] - 1)]
                if len(R):
                    w = R[:, 0] * REFRESH; busy = R[:, 1:17].sum(1) / w
                    tot = w.sum()
                    rec.update(cpu_busy_mean=round(float(busy.mean()), 4), cpu_busy_p99=round(float(np.percentile(busy, 99)), 4),
                               cpu_busy_max=round(float(busy.max()), 4),
                               video_decode_share=round(float((R[:, 2] + R[:, 3]).sum() / tot), 4),
                               audio_decode_share=round(float(R[:, 5].sum() / tot), 4),
                               blit_share=round(float(R[:, 4].sum() / tot), 4),
                               late_or_dropped_frames=int((R[:, 0] != 5).sum()) + max(0, frames - cap['frames']))
            else:
                rec.update(cpu_busy_mean=None, cpu_busy_p99=None, cpu_busy_max=None, video_decode_share=None,
                           audio_decode_share=None, blit_share=None, late_or_dropped_frames=None)
                notes.append('no perf run')
            v = vj.get((cid, sid))
            if v:
                rec['verified_video'] = f"{v.get('video_ok', 0)}/{frames}"
                rec['verified_audio'] = f"{v.get('audio_ok', 0)}/{s['blocks']}"
                if v.get('audio_blocks_captured', 0) < s['blocks']:
                    notes.append(f"cartridge stops audio before the last {s['blocks'] - v['audio_blocks_captured']} block(s) (video ended)")
                if v.get('video_ok') != frames:
                    notes.append(f"{frames - v.get('video_ok', 0)} frames differ: player showed them with a stale codebook (explain_stale.py)")
            else:
                rec['verified_video'] = f'0/{frames}'; rec['verified_audio'] = f"0/{s['blocks']}"
                notes.append('not captured')
            notes.append('audio bytes include the block-size table; last audio block partially played; '
                         'video_kbps_p99_1s spreads each segment\'s bytes evenly over its frames; '
                         'shares are fractions of total CPU time; seek_points = chapters (every segment is a keyframe)')
            if cid == 'jimmy' and nm != 'res0': notes.append('brightness gain bias +51 (set by the boot code after the logo)')
            if cid == 'cnf-platinum': notes.append('Settings BaseBrightness 2: gain bias +51')
            rec['notes'] = '; '.join(notes)
            out.append(rec)
            print(cid, nm, rec['verified_video'], rec['verified_audio'], rec.get('cpu_busy_mean'), flush=True)
    json.dump(out, open(sys.argv[2], 'w'), indent=1)

if __name__ == '__main__':
    main()
