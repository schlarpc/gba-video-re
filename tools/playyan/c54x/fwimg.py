"""Parse the NMP C54x boot table (nmp_boottable, extracted from the GBA ROM's 0x0A8F34 by
tools/playyan/extract_firmware.py).
Returns a list of (dest, words) sections and a 0x40000-word HPI-address image (VC5416 on-chip RAM)."""
import struct, os
HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.append(os.path.join(HERE, '..'))
import workdir  # noqa: E402
OFF, NW = 0x0A8F34, 0xF730

def hpi_fold(a):
    if 0x8000 <= a <= 0xFFFF: return a | 0x10000
    if (a >> 16) in (1, 2, 3, 4) and (a & 0xFFFF) < 0x8000: return a & 0xFFFF
    return a

def parse(data=None):
    if data is None:
        data = workdir.load_firmware('nmp_boottable')
    w = struct.unpack('<%dH' % (len(data) // 2), data)
    assert w[0] == 0x08AA
    hdr = dict(swwsr=w[1], bscr=w[2], entry=(w[3] << 16) | w[4])
    i, secs = 5, []
    while True:
        n = w[i]
        if n == 0: break
        dest = (w[i + 1] << 16) | w[i + 2]
        secs.append((dest, list(w[i + 3:i + 3 + n])))
        i += 3 + n
    return hdr, secs

def image():
    hdr, secs = parse()
    mem = [0] * 0x40000
    loaded = bytearray(0x40000)
    for dest, ws in secs:
        for k, v in enumerate(ws):
            a = hpi_fold(dest + k)
            mem[a] = v; loaded[a] = 1
    return hdr, secs, mem, loaded

if __name__ == '__main__':
    hdr, secs, mem, loaded = image()
    print(hdr, len(secs))
    import array
    array.array('H', mem).tofile(open(os.path.join(workdir.work('runs'), 'nmp_hpi_image.u16le'), 'wb'))
