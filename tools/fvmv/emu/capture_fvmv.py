#!/usr/bin/env python3
"""Whole-stream ground-truth capture for the FVMV player (Pokemon GBA Video Vol 1-4).

Runs the cart headless in the instrumented mGBA libretro core (PC breakpoints that
log registers, no stop) and, for every frame the player decodes, records:
  * md5 of the decoder's output buffer (240x160 u16, all 16 bits, as stored in EWRAM)
  * md5 of every 1024-byte decoded audio block (8-bit PCM, 65536 Hz) for that frame
  * the stream offset of the frame record (-> frame index)
Optionally dumps the raw frames/audio and a savestate taken at the VBlank before
the first decode (seed for the unicorn oracle).

Usage: capture_fvmv.py VOL EP OUTDIR [--frames N] [--dump-frames K] [--state]
"""
import argparse, csv, gzip, hashlib, os, struct, sys, time
import ctypes as C
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retro

PROF_CORE = os.environ.get('GBAV_MGBA_PROF_CORE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tmp', 'mgba', 'prof', 'mgba_libretro.so')  # tools/mgba/mgba-prof.patch
ROMS = os.path.join(os.environ.get('GBAV_ROMS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'roms'), 'Game Boy Advance Video - Pokemon - Volume %d (USA).gba')

BP_VDEC = 0x03005fe8      # video decode entry, r0 -> {ref, out, frame, w, h}
BP_VEND = 0x080034d4      # return from video decode (r7 = player A/V ctx)
BP_ACOPY = 0x08003534     # bl ring-copy: r0 = decoded PCM buffer, r1 = bytes
BP_RING = 0x03001d18      # 4x interpolation + 8-bit quantiser: r0 = src int32, r1 = dst s8 ring, r2 = bytes
BP_RING_END = 0x03001e40  # its return (pop): the ring bytes are complete
BP_RESYNC = 0x08003420    # ring write position reset (audio under/overrun)
FRAME_BYTES = 240 * 160 * 2


def bp_set(r, addrs):
    L = r.lib
    arr = (C.c_uint32 * 8).in_dll(L, 'retro_bp_addr')
    for i, a in enumerate(addrs):
        arr[i] = a
    C.c_int.in_dll(L, 'retro_bp_n').value = len(addrs)
    C.c_uint32.in_dll(L, 'retro_bp_logn').value = 0


def bp_log(r):
    L = r.lib
    n = C.c_uint32.in_dll(L, 'retro_bp_logn').value
    a = np.ctypeslib.as_array((C.c_uint32 * (4096 * 18)).in_dll(L, 'retro_bp_log')).reshape(4096, 18)[:n].copy()
    C.c_uint32.in_dll(L, 'retro_bp_logn').value = 0
    return a


def stream_offsets(rom):
    """{record_video_ptr: frame_index} for every FVMV stream in the ROM."""
    out = []
    o = 0
    while True:
        o = rom.find(b'FVMV', o)
        if o < 0:
            break
        nf, w, h, per, size = struct.unpack_from('<5I', rom, o + 4)
        if w == 240 and h == 160:
            p = o + 0x20
            m = {}
            for i in range(nf):
                sz = struct.unpack_from('<I', rom, p)[0]
                m[0x08000000 + p + 0x14] = i
                p += 16 + sz
            out.append((o, nf, m))
        o += 4
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('vol', type=int)
    ap.add_argument('ep', type=int)
    ap.add_argument('out')
    ap.add_argument('--frames', type=int, default=0)
    ap.add_argument('--dump-frames', type=int, default=0, help='dump raw output buffers of the first K frames')
    ap.add_argument('--state', action='store_true', help='save savestate from the VBlank before decode 0')
    ap.add_argument('--checkpoints', type=int, default=0, help='also save a savestate every N frames')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rom = open(ROMS % a.vol, 'rb').read()
    streams = stream_offsets(rom)
    hdr, nframes, fmap = streams[a.ep - 1]
    if a.frames:
        nframes = min(nframes, a.frames)
    r = retro.Retro(ROMS % a.vol, core=PROF_CORE)
    C.c_int.in_dll(r.lib, 'retro_prof_on').value = 0
    r.run(1000)
    if a.ep == 2:
        r.buttons = {'DOWN'}; r.run(6); r.buttons = set(); r.run(10)
    sh = 0x20 if a.vol >= 3 else 0     # Vol 3/4 ROM code is 0x20 bytes longer before these
    bp_set(r, [BP_VDEC, BP_VEND + sh, BP_ACOPY + sh, BP_RING, BP_RESYNC + sh, BP_RING_END])
    r.buttons = {'A'}
    fcsv = gzip.open(os.path.join(a.out, 'frames.csv.gz'), 'wt')
    acsv = gzip.open(os.path.join(a.out, 'audio.csv.gz'), 'wt')
    fcsv.write('frame,emu_frame,ref,out,md5,md5_15\n')
    acsv.write('frame,block,md5_synth,md5_pcm\n')
    ring_parts = []
    resyncs = []
    pending_synth = []
    pending_done = False
    ring_done = 0
    pcm = open(os.path.join(a.out, 'pcm.s8'), 'wb')
    prev_state = r.save_state() if a.state else None
    pending = None
    got = 0
    t0 = time.time()
    last_frame = -1
    timeout = 0
    tail = 0
    while got < nframes or (pending_synth or tail < 60) and tail < 600:
        if got >= nframes:
            tail += 1
        r.run(1)
        if r.buttons and r.frame_no >= 1006 + (16 if a.ep == 2 else 0):
            r.buttons = set()
        log = bp_log(r)
        if len(log) == 0:
            timeout += 1
            if timeout > 600:
                print('timeout waiting for decode at frame', got)
                break
            if a.state and got == 0:
                prev_state = r.save_state()
            continue
        timeout = 0
        for row in log:
            bid = int(row[0])
            regs = [int(x) for x in row[1:17]]
            if bid == 0:
                if got >= nframes:
                    continue
                ref, out, fptr, w, h = struct.unpack('<5I', r.read(regs[0], 20))
                fi = fmap.get(fptr)
                if fi is None:
                    raise SystemExit('unknown frame ptr %08x' % fptr)
                if fi == 0 and got == 0 and a.state:
                    open(os.path.join(a.out, 'pre_decode0.state'), 'wb').write(prev_state)
                    open(os.path.join(a.out, 'pre_decode0.ewram'), 'wb').write(r.read(0x02000000, 0x40000))
                pending = (fi, ref, out)
            elif bid == 1:
                if got >= nframes:
                    continue
                fi, ref, out = pending
                buf = r.read(out, FRAME_BYTES)
                m = hashlib.md5(buf).hexdigest()
                m15 = hashlib.md5((np.frombuffer(buf, '<u2') & 0x7fff).tobytes()).hexdigest()
                fcsv.write(f'{fi},{r.frame_no},{ref:08x},{out:08x},{m},{m15}\n')
                if fi < a.dump_frames:
                    os.makedirs(os.path.join(a.out, 'raw'), exist_ok=True)
                    open(os.path.join(a.out, 'raw', f'{fi:05d}.rgb555'), 'wb').write(buf)
                if fi != last_frame + 1:
                    print('frame order jump', last_frame, '->', fi)
                last_frame = fi
                got += 1
                if a.checkpoints and fi % a.checkpoints == 0:
                    os.makedirs(os.path.join(a.out, 'states'), exist_ok=True)
                    open(os.path.join(a.out, 'states', f'f{fi:05d}.state'), 'wb').write(r.save_state())
            elif bid == 2:
                if got >= nframes and pending_done:
                    continue
                pending_done = got >= nframes
                fi = pending[0]
                src, n = regs[0], regs[1]
                buf = r.read(src, n)
                pending_synth = [hashlib.md5(buf[k*1024:(k+1)*1024]).hexdigest() for k in range(n // 1024)]
                ring_parts = []
                ring_done = 0
            elif bid == 3:
                if not pending_synth:
                    continue
                ring_parts.append((regs[1], regs[2]))
            elif bid == 5:
                if ring_parts and ring_done < len(ring_parts):
                    ring_done += 1
            elif bid == 4:
                resyncs.append((pending[0], r.frame_no))
        if pending_synth and sum(n for _, n in ring_parts[:ring_done]) >= 1024 * len(pending_synth):
            fi = pending[0]
            pb = b''.join(r.read(d, n) for d, n in ring_parts)
            pcm.write(pb)
            for k, ms in enumerate(pending_synth):
                acsv.write(f'{fi},{k},{ms},{hashlib.md5(pb[k*1024:(k+1)*1024]).hexdigest()}\n')
            pending_synth = []
            ring_parts = []
        if got and got % 1000 == 0 and log is not None and any(x[0] == 1 for x in log):
            print(f'{got} frames {time.time()-t0:.0f}s', flush=True)
    fcsv.close(); acsv.close(); pcm.close()
    open(os.path.join(a.out, 'resync.txt'), 'w').write(''.join(f'{f},{e}\n' for f, e in resyncs))
    print('done', got, 'frames in', round(time.time() - t0), 's; emu frames', r.frame_no)


if __name__ == '__main__':
    main()
