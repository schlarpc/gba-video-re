"""ID3 oracle manifest: python3 make_id3_manifest.py OUT.csv FILE.mp3 ...   (runs the DSP per file)
line: file,encoding,seconds,title units (hex, '.'-separated),artist units"""
import sys, os, id3_oracle
rows = []
for p in sys.argv[2:]:
    r, blk = id3_oracle.id3(p)
    t = blk[2:0x44]; a = blk[0x44:0x86]
    t = t[:t.index(0)]; a = a[:a.index(0)]
    rows.append('%s,%d,%d,%s,%s' % (os.path.basename(p), blk[1], blk[0x86] | blk[0x87] << 16, '.'.join('%x' % v for v in t), '.'.join('%x' % v for v in a)))
open(sys.argv[1], 'w').write('# file,encoding,seconds,title,artist: the NMP DSP AUDIO_INFO block (tools/playyan/nmp/id3_oracle.py)\n' + '\n'.join(rows) + '\n')
print(len(rows))
