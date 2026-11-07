"""Extract the ADS player's RAM images from a cartridge (main.bin from the SFCD archive)."""
import struct, hashlib
import adsfs, carts

BUILD_OF = {
 "srmt":"bde72f6aba53","proud":"bde72f6aba53","disney2":"17d6ab8a82ba","disney1":"882a917e99c5",
 "cn-limited":"e0080e16dac3","cn-platinum":"e0080e16dac3","cn-premium":"e0080e16dac3","cn-special":"e0080e16dac3","cn-vol2":"e0080e16dac3",
 "cnf-platinum":"bfa01253c3c5","tmnt-fr":"bfa01253c3c5","yugioh-fr":"bfa01253c3c5",
 "cnf-premium":"e3117652326c","cnf-speciale":"e3117652326c","nick2":"69261e8948ae","sponge3":"69261e8948ae",
 "nick3":"f364f46e4bef","dbgt":"e19d3efb314e","tmnt-proto":"73b2d7da03af","jimmy":"22db83f74bdf","noah-proto":"8831b3ba8f06"}

def main_bin(rom):
    s = adsfs.parse_sfcd(rom)
    e = [e for e in s["entries"] if e["name"] == "main.bin"][0]
    return e["rom"], rom[e["rom"]:e["rom"] + e["size"]]

def sfcd_images(rom):
    """-> dict(ewram=bytes at 0x02000000, iwram=bytes at 0x03000000, bss=(addr,len), main_rom=off, desc_off)"""
    mrom, m = main_bin(rom)
    for o in range(0, len(m) - 24, 4):
        a, b, c, a2, z, b2 = struct.unpack_from("<6I", m, o)
        if b == 0x03000000 and 0x02000000 <= a < 0x02040000 and c < 0x8000 and a2 == a and z == 0:
            iw = m[a - 0x02000000:a - 0x02000000 + c]
            bss = struct.unpack_from("<I", m, o + 24)[0]
            return dict(ewram=m, iwram=iw, iwram_src=a, iwram_len=c, bss=(b2, bss), main_rom=mrom, desc_off=o)
    raise ValueError("no IWRAM descriptor")

def hydrogen_images(rom):
    """Hydrogen-engine carts (Jimmy Neutron, Noah proto): the IWRAM image sits right after its copy
    descriptor {src, 0x03000000, len, src, 0, bss, bss_len} near the ROM end; Thumb code runs from ROM."""
    pat = struct.pack('<I', 0x03000000)
    i = rom.rfind(pat)
    while i > 0:
        src, dst, ln, src2, z, bss, bssl = struct.unpack_from('<7I', rom, i - 4)
        if dst == 0x03000000 and src == src2 and z == 0 and src - 0x08000000 == i + 24 and ln < 0x8000:
            o = src - 0x08000000
            return dict(ewram=b'', iwram=rom[o:o + ln], iwram_src=src, iwram_len=ln, bss=(bss, bssl), main_rom=None, desc_off=i - 4)
        i = rom.rfind(pat, 0, i)
    raise ValueError('no IWRAM descriptor')
