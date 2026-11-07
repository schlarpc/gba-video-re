"""Usage: pcm_ref.py ROM HEADER_OFFSET OUT.pcm.csv.gz

Reference 16 kHz PCM digests from container/vxaudio.py: per AFrame, md5 of the 128 samples
(int32 LE) and md5 of the 512-byte 8-bit output block. Decodes every AFrame of the stream
(until p + 4 > len or 4 zero bytes, like emu_audio.py). tests/streams.rs reads the
output through GBAV_VX_PCM_REF (file name <cart>_<off>.pcm.csv.gz)."""
import sys, struct, hashlib, gzip
sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..', '..', '..', 'container'))
import vxaudio
rom = open(sys.argv[1], 'rb').read()
H = int(sys.argv[2], 0)
hd = struct.unpack_from('<4s13I', rom, H)
aud = rom[H + hd[7]:H + hd[8]]
dec = vxaudio.VXAudioDecoder(vxaudio.Codebook(aud))
p = vxaudio.CODEBOOK_SIZE
out = gzip.open(sys.argv[3], 'wt')
out.write('block,pcm_md5,out_md5,minv,maxv\n')
i = 0
while p + 4 <= len(aud) and any(aud[p:p + 4]):
    n, s, o = dec.decode(aud + b'\0' * 32, p) if p + 20 > len(aud) else dec.decode(aud, p)
    out.write(f"{i},{hashlib.md5(struct.pack('<128i', *s)).hexdigest()},{hashlib.md5(o).hexdigest()},{min(s)},{max(s)}\n")
    p += n
    i += 1
out.close()
print(sys.argv[1], hex(H), 'aframes', i, 'end', p, 'len', len(aud))
