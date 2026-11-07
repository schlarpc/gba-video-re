"""Per-build player hook addresses, found by code signatures in the player's RAM images.
(see docs/player-ads.md, "Decoder entry points").  `discover(cid)` -> dict."""
import sys, os, struct, functools
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import capstone
import carts, images
from images import BUILD_OF

def build_of(cid): return BUILD_OF[cid]

def _words(b): return struct.unpack('<%dI' % (len(b) // 4), b[:len(b) // 4 * 4])

@functools.lru_cache(None)
def discover(cid):
    rom = carts.load(cid)
    im = images.sfcd_images(rom) if cid not in ('jimmy', 'noah-proto') else images.hydrogen_images(rom)
    iw = im['iwram']; W = _words(iw)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    res = {'build': BUILD_OF[cid]}
    # audio sample loop: references the 8-byte VLC table 01 01 01 01 12 12 23 33
    vlc = iw.find(bytes.fromhex('0101010112122333'))
    lit = W.index(0x03000000 + vlc)
    st = lit
    while (W[st] & 0xffff0000) != 0xe92d0000: st -= 1
    res['audio'] = 0x03000000 + st * 4
    k = st + 1
    while W[k] != 0xe8bd5ff0: k += 1        # pop {r4-r12,lr} = end of the loop
    res['audio_end'] = 0x03000000 + k * 4
    # blit: push; ...; mov r1,#0x06000000 ; ldr r2,[r0,#pos] ; lsl r2,r2,#15 within the first 12 insns
    for i, w in enumerate(W):
        if (w & 0xffff0000) == 0xe92d0000:
            body = W[i + 1:i + 14]
            if 0xe3a01660 in body and 0xe1a02782 in body:
                res['blit'] = 0x03000000 + i * 4
                ins = list(md.disasm(iw[i * 4:i * 4 + 64], 0x03000000 + i * 4))
                res['blit_prologue'] = [(x.mnemonic, x.op_str) for x in ins]
                break
    return res

if __name__ == '__main__':
    for c in sys.argv[1:] or carts.CARTS:
        try: r = discover(c)
        except Exception as ex: print(c, 'ERR', ex); continue
        print(c, r['build'], hex(r['audio']), hex(r['audio_end']), hex(r.get('blit', 0)))
