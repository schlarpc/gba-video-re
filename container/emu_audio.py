"""Run the player's own audio decoder (IWRAM 0x03006a14) under Unicorn on the real
stream and compare against vxaudio.py. Usage: emu_audio.py [start_aframe_byte] [count]"""
import os, struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM
from unicorn.arm_const import *
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vxaudio

ROM = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shrek.gba'), 'rb').read()
H = 0x20200
hd = struct.unpack_from('<4s13I', ROM, H)
A = H + hd[7]
AUD = ROM[A:H + hd[8]]

mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
mu.mem_map(0x02000000, 0x40000)
mu.mem_map(0x03000000, 0x8000)
mu.mem_map(0x06000000, 0x20000)
mu.mem_map(0x08000000, 0x10000)
mu.mem_write(0x02000000, ROM[0x8a00:0xbcc8])
mu.mem_write(0x03000000, ROM[0xbcc8:0x13cc8])
mu.mem_write(0x06015300, AUD[:3124])          # what init does via two CpuSets
SENT = 0x02030000
FRM = 0x08001800
OUT = 0x06015f40


def run(fr):
    mu.mem_write(FRM, fr + b'\0' * 16)
    mu.reg_write(UC_ARM_REG_R0, FRM)
    mu.reg_write(UC_ARM_REG_R1, OUT)
    mu.reg_write(UC_ARM_REG_SP, 0x03007f00)
    mu.reg_write(UC_ARM_REG_LR, SENT)
    mu.emu_start(0x03006a14, SENT)
    return mu.reg_read(UC_ARM_REG_R0), bytes(mu.mem_read(OUT, 512))


def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 3124
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    dec = vxaudio.VXAudioDecoder(vxaudio.Codebook(AUD))
    p = start
    bad = 0
    for i in range(count):
        if p + 4 > len(AUD) or not any(AUD[p:p + 4]):
            print('reached end of AFrames'); break
        n1, o1 = run(AUD[p:p + 20])
        n2, s, o2 = dec.decode(AUD + b'\0' * 32, p) if p + 20 > len(AUD) else dec.decode(AUD, p)
        if n1 != n2 or o1 != o2:
            bad += 1
            if bad < 5:
                diff = [k for k in range(512) if o1[k] != o2[k]]
                print('MISMATCH aframe', i, 'off', p, 'n', n1, n2, 'first diff', diff[:8], 'ndiff', len(diff),
                      'pfo', AUD[p + 1] >> 1)
        p += n1
    print('compared', i, 'aframes from', start, 'mismatches', bad, 'end off', p)


if __name__ == '__main__':
    main()
