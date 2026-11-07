#!/usr/bin/env python3
"""Verify hydec (C) against the hash manifests of every Hydrogen stream.

  verify.py                      all carts/streams that have a manifest
  verify.py --cart MGUE          one cart
  verify.py --build-manifests    (re)build verify/hydrogen/*.csv.gz from emulator captures in tmp/hydrogen/cap
  verify.py --audio              also check audio (reference/majesco/adpcm.c via libhydec.so)

Manifests (hash only, gzipped CSV), per stream:
  <short>_<stream>.frame_md5.csv.gz   frame,fb_md5,idx_md5,vblank   (fb = 240x160 BGR555 LE after the blit)
  <short>_<stream>.audio_md5.csv.gz   call,vblank,n,md5             (output bytes of each ADPCM call)
A manifest frame row is the capture's k-th blit mapped to its stream frame through the index-bytes
md5 (identical to k unless the player skipped a blit).
"""
import sys, os, gzip, csv, subprocess, argparse, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from carts import CARTS, SHORT, BUILD, rom_path
import hycart

MAN = os.path.join(HERE, '..', '..', 'verify', 'hydrogen')
CAP = os.path.join(HERE, '..', '..', 'tmp', 'hydrogen', 'cap')
REF = os.path.join(HERE, '..', '..', 'reference', 'hydrogen')
HYDEC = os.path.join(REF, 'hydec')


def dec_md5(code, stream):
    p = subprocess.run([HYDEC, rom_path(code), '--stream', str(stream), '--md5'], capture_output=True, text=True)
    rows = [l.split() for l in p.stdout.splitlines()]
    return [(int(r[0]), r[1], r[2], int(r[3]), int(r[4])) for r in rows], p.returncode


def build_manifests(only=None):
    os.makedirs(MAN, exist_ok=True)
    out = {}
    for code in CARTS:
        if only and code not in only: continue
        rom = open(rom_path(code), 'rb').read()
        for i, c in enumerate(hycart.find_containers(rom)):
            pre = f'{CAP}/{code}_{i}'
            if not os.path.exists(pre + '.done'): continue
            dec, rc = dec_md5(code, i)
            byidx = {}
            for f, fb, im, seg, mode in dec: byidx.setdefault(im, []).append(f)
            rows = list(csv.DictReader(open(pre + '.frames.csv')))
            fr = []; nxt = 0; skipped = []
            for r in rows:
                # map to the first decoder frame >= nxt with this index md5
                cands = [f for f in byidx.get(r['idx_md5'], []) if f >= nxt]
                f = cands[0] if cands else -1
                if f > nxt: skipped += list(range(nxt, f))
                if f >= 0: nxt = f + 1
                fr.append((f, r['fb_md5'], r['idx_md5'], r['vblank']))
            name = f'{SHORT[code]}_{i}'
            with gzip.open(f'{MAN}/{name}.frame_md5.csv.gz', 'wt') as g:
                g.write('frame,fb_md5,idx_md5,vblank\n')
                for f, fb, im, vb in fr: g.write(f'{f},{fb},{im},{vb}\n')
            arows = list(csv.DictReader(open(pre + '.audio.csv')))
            with gzip.open(f'{MAN}/{name}.audio_md5.csv.gz', 'wt') as g:
                g.write('call,vblank,n,md5\n')
                for r in arows: g.write(f"{r['call']},{r['vblank']},{r['n']},{r['md5']}\n")
            out[name] = dict(blits=len(rows), unmapped=sum(1 for x in fr if x[0] < 0), skipped=len(skipped),
                             audio_calls=len(arows))
            print(name, out[name], flush=True)
    return out


import ctypes as C, hashlib, struct
_lib = None
class _A(C.Structure):
    _fields_ = [('buf', C.c_void_p), ('nbits_total', C.c_size_t), ('pos', C.c_size_t), ('overrun', C.c_int), ('coding', C.c_int),
                ('step', C.c_int32), ('prev', C.c_int32), ('h0', C.c_int32), ('h1', C.c_int32), ('f0', C.c_int32), ('f1', C.c_int32),
                ('f2', C.c_int32), ('s1', C.c_uint8), ('s2', C.c_uint8), ('ctx', C.c_uint8), ('tab', C.c_uint8 * 64),
                ('left', C.c_uint32), ('block', C.c_uint32)]


def audio_check(rom, c, rows):
    """decode the type-2 entry with the shared ADPCM (Hydrogen = MJ_CTAB_FLAT) in the player's call sizes"""
    global _lib
    if _lib is None: _lib = C.CDLL(os.path.join(REF, 'libhydec.so'))
    ao, al = c['audio']
    n = struct.unpack_from('<H', rom, ao + 14)[0]; po = ao + ((16 + 2 * n + 3) & ~3)
    # the cartridge's bit reader runs a few bits past the end of the entry on the last block of
    # some streams; give it the ROM bytes that follow, as on the cartridge
    tail = 64
    a = _A(); buf = C.create_string_buffer(rom[po:ao + al + tail], al + tail)
    _lib.mj_adpcm_init(C.byref(a), buf, al - (po - ao) + tail, 1)
    ok = 0; first = None; total = 0
    for i, r in enumerate(rows):
        k = int(r['n']); out = (C.c_int8 * k)()
        _lib.mj_adpcm_decode(C.byref(a), out, k); total += k
        if hashlib.md5(bytes(out)).hexdigest() == r['md5']: ok += 1
        elif first is None: first = i
    return ok, first, total


def verify(only=None, audio=False):
    res = {}
    for code in CARTS:
        if only and code not in only: continue
        rom = open(rom_path(code), 'rb').read()
        for i, c in enumerate(hycart.find_containers(rom)):
            name = f'{SHORT[code]}_{i}'
            mf = f'{MAN}/{name}.frame_md5.csv.gz'
            if not os.path.exists(mf):
                res[name] = dict(status='no manifest'); continue
            s = hycart.Stream(rom, c['video'][0], c['video'][1])
            dec, rc = dec_md5(code, i)
            d = {f: fb for f, fb, im, seg, mode in dec}
            rows = list(csv.DictReader(gzip.open(mf, 'rt')))
            ok = sum(1 for r in rows if int(r['frame']) >= 0 and d.get(int(r['frame'])) == r['fb_md5'])
            covered = len(set(int(r['frame']) for r in rows if int(r['frame']) >= 0))
            bad = [int(r['frame']) for r in rows if not (int(r['frame']) >= 0 and d.get(int(r['frame'])) == r['fb_md5'])]
            res[name] = dict(frames=s.frames, manifest_rows=len(rows), frames_ok=ok, covered=covered,
                             first_bad=bad[:5], decoder_rc=rc)
            if audio and os.path.exists(f'{MAN}/{name}.audio_md5.csv.gz'):  # per-block audio manifest not in verify/ (optional; gbav verify checks the audio segment manifests)
                am = f'{MAN}/{name}.audio_md5.csv.gz'
                arows = list(csv.DictReader(gzip.open(am, 'rt')))
                aok, afirst, total = audio_check(rom, c, arows)
                res[name].update(audio_calls=len(arows), audio_ok=aok, audio_first_bad=afirst, audio_samples=total)
            print(name, res[name], flush=True)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cart', action='append')
    ap.add_argument('--build-manifests', action='store_true')
    ap.add_argument('--audio', action='store_true')
    ap.add_argument('--json')
    a = ap.parse_args()
    if a.build_manifests:
        build_manifests(a.cart)
    r = verify(a.cart, a.audio)
    if a.json: json.dump(r, open(a.json, 'w'), indent=1)
    tot = sum(v.get('frames_ok', 0) for v in r.values()); n = sum(v.get('manifest_rows', 0) for v in r.values())
    print(f'TOTAL frames ok {tot}/{n} rows; streams with manifest {sum(1 for v in r.values() if "frames" in v)}/{len(r)}')
