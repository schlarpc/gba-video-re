"""Run the player's own audio decoder (IWRAM 0x03006a14; 0x03007170 in the Rev 5 VXGB
player, same code) under Unicorn on the real stream and compare against vxaudio.py.

  emu_audio.py [--rom ROM] [--stream OFF|IDX] [--start BYTE] [--count N|all]

--start is a byte offset into the audio stream (default 3124 = first AFrame); --count all
runs to the end of the AFrames."""
import argparse, os, struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import *
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vxaudio
import vxcart

SENT = 0x02030000
FRM = 0x08001800
OUT = 0x06015f40
# audio_decode_packet(src, dst) entry by player build (container/vxcart.py KNOWN_PLAYERS)
ENTRY_VXGB = 0x03007170
ENTRY_VXPP = 0x03006a14


class ArmAudio:
    def __init__(self, rom, aud):
        L = vxcart.player_layout(rom)
        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        mu.mem_map(0x02000000, 0x40000)
        mu.mem_map(0x03000000, 0x8000)
        mu.mem_map(0x06000000, 0x20000)
        mu.mem_map(0x08000000, 0x10000)
        (eo, en), (io, isz) = L['ewram'], L['iwram']
        mu.mem_write(0x02000000, rom[eo:eo + en])
        mu.mem_write(0x03000000, rom[io:io + isz])
        mu.mem_write(0x06015300, aud[:vxaudio.CODEBOOK_SIZE])   # what init does via two CpuSets
        self.entry = ENTRY_VXGB if en == 0x2b48 else ENTRY_VXPP     # Rev 5 player: EWRAM image 0x2b48

    def run(self, fr):
        mu = self.mu
        mu.mem_write(FRM, fr + b'\0' * 16)
        mu.reg_write(UC_ARM_REG_R0, FRM)
        mu.reg_write(UC_ARM_REG_R1, OUT)
        mu.reg_write(UC_ARM_REG_SP, 0x03007f00)
        mu.reg_write(UC_ARM_REG_LR, SENT)
        mu.emu_start(self.entry, SENT)
        return mu.reg_read(UC_ARM_REG_R0), bytes(mu.mem_read(OUT, 512))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rom', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shrek.gba'))
    ap.add_argument('--stream', type=lambda x: int(x, 0))
    ap.add_argument('--start', type=int, default=vxaudio.CODEBOOK_SIZE)
    ap.add_argument('--count', default='3000')
    ap.add_argument('--md5-out', help='write md5 of each AFrame\'s 512 output bytes (one per line)')
    a = ap.parse_args()
    rom = open(a.rom, 'rb').read()
    H = vxcart.pick_stream(rom, a.stream)
    hd = struct.unpack_from('<4s13I', rom, H)
    aud = rom[H + hd[7]:H + hd[8]]
    arm = ArmAudio(rom, aud)
    count = 1 << 40 if a.count == 'all' else int(a.count)
    dec = vxaudio.VXAudioDecoder(vxaudio.Codebook(aud))
    md5f = open(a.md5_out, 'w') if a.md5_out else None
    import hashlib
    p = a.start
    bad = i = 0
    while i < count:
        if p + 4 > len(aud) or not any(aud[p:p + 4]):
            print('reached end of AFrames'); break
        n1, o1 = arm.run(aud[p:p + 20])
        n2, s, o2 = dec.decode(aud + b'\0' * 32, p) if p + 20 > len(aud) else dec.decode(aud, p)
        if md5f:
            md5f.write(hashlib.md5(o2).hexdigest() + '\n')
        if n1 != n2 or o1 != o2:
            bad += 1
            if bad < 5:
                diff = [k for k in range(512) if o1[k] != o2[k]]
                print('MISMATCH aframe', i, 'off', p, 'n', n1, n2, 'first diff', diff[:8], 'ndiff', len(diff),
                      'pfo', aud[p + 1] >> 1)
        p += n1
        i += 1
    print('stream 0x%x: compared %d aframes from byte %d, mismatches %d, end off %d' % (H, i, a.start, bad, p))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
