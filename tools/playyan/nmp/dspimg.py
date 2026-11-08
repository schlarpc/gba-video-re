"""Build the NMP DSP's data memory as it is after its C runtime init, from the GBA ROM alone:
boot table at ROM 0x0A8F34 (keyword 0x08AA, entry, sections) -> HPI image, then the TI cinit records
at program address 0x48C2 ([len][dest][len words] ..., len 0 ends)."""
import struct
import os
TMP = os.environ.get('TMPDIR', '/tmp')
BOOT_OFF = 0x0A8F34
def hpi_fold(a):
    if 0x8000 <= a <= 0xFFFF: return a | 0x10000
    if (a >> 16) in (1, 2, 3, 4) and (a & 0xFFFF) < 0x8000: return a & 0xFFFF
    return a
def data_image(rom):
    w = struct.unpack_from('<%dH' % 0xF730, rom, BOOT_OFF)
    assert w[0] == 0x08AA
    hpi = [0] * 0x40000
    i = 5
    while w[i]:
        n = w[i]; dest = w[i + 1] << 16 | w[i + 2]
        for k in range(n): hpi[hpi_fold(dest + k)] = w[i + 3 + k]
        i += 3 + n
    p = 0x48C2
    while True:
        n = hpi[p]
        if n == 0: break
        dest = hpi[p + 1]
        for k in range(n):
            a = dest + k
            hpi[a if a < 0x8000 else 0x10000 | a] = hpi[p + 2 + k]
        p += 2 + n
    return hpi[0:0x8000] + hpi[0x18000:0x20000]   # data space with DROM = 1
if __name__ == '__main__':
    import pickle
    rom = open(os.path.join(os.environ.get('PY_ROMS', ''), 'Nintendo MP3 Player (Europe) (En,Fr,De,Es,It).gba'), 'rb').read()
    d = data_image(rom)
    R, mem = pickle.load(open(TMP + '/rq.pkl', 'rb'))
    for name, a, n in (('dct', 0x9400, 0xcc), ('aa', 0x94cc, 32), ('win', 0x94ec, 48), ('rq', 0xb182, 0x33a), ('imdct', 0xb4bc, 720), ('synth', 0xb78c, 1024)):
        print(name, all(d[a + k] == int(mem[a + k]) for k in range(n)))
