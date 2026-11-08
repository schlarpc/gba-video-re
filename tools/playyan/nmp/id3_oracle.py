"""ID3 block the NMP DSP returns for AUDIO_INFO (0x0040 -> record 0x4040): python3 id3_oracle.py FILE.mp3...
prints the record and the 0x88-word block (title +2.., artist +0x44.., u32 seconds at +0x86)"""
import sys, os, shutil, hashlib, json
WORK = os.environ.get('PLAYYAN_WORK', '')  # the local emulator tree (not in this repository)
NMP = WORK + '/emu/nmp_lle'
sys.path.insert(0, NMP)
import nmpo, host

def id3(path, name='a.mp3'):
    data = open(path, 'rb').read()
    key = 'id3_' + hashlib.md5(data).hexdigest()[:12]
    sd = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sd', key)
    if not os.path.isdir(sd):
        os.makedirs(sd); shutil.copy(path, os.path.join(sd, name))
    cwd = os.getcwd(); os.chdir(NMP)
    try:
        h = host.Host(sd=sd)
    finally:
        os.chdir(cwd)
    assert h.wait_record({0x8001}, 3000)
    h.command(h.wstr(0x0040, name))
    r = h.wait_record({0x4040})
    A = r[2] | r[3] << 16
    blk = h.rd(A, 0x88) if A else None
    return r, blk

if __name__ == '__main__':
    for p in sys.argv[1:]:
        r, blk = id3(p)
        print(os.path.basename(p), 'rec', [hex(x) for x in r])
        if blk:
            t = blk[2:0x44]; a = blk[0x44:0x86]
            print('  title words', t[:t.index(0) + 1 if 0 in t else 40])
            print('  artist words', a[:a.index(0) + 1 if 0 in a else 40])
            print('  len', blk[0x86] | blk[0x87] << 16, 'w0,1', blk[0], blk[1])
