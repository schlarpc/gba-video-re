"""Build the ID3 / length probe files into DIR (needs the LAME streams of make_test_mp3.sh in SRC):
python3 make_id3_tests.py SRC DIR"""
import sys, os, struct, subprocess
src, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)
lame = os.environ.get('LAME') or subprocess.check_output(['nix', 'build', '--no-link', '--print-out-paths', 'nixpkgs#lame'], text=True).strip() + '/bin/lame'
q = lambda *a: subprocess.run([lame, *a], capture_output=True, check=True)
q('-V2', '-t', os.path.join(src, 'trans.wav'), os.path.join(out, 'vbr_noxing.mp3'))
q('-V2', os.path.join(src, 'trans.wav'), os.path.join(out, 'vbr_xing.mp3'))
q('-b', '128', '-t', os.path.join(src, 'trans.wav'), os.path.join(out, 'cbr_noxing.mp3'))
rd = lambda n: open(os.path.join(src, n), 'rb').read()
audio = rd('trans_j.mp3')
def syncsafe(n): return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])
def f23(fid, p, ver=3): return fid.encode() + (struct.pack('>I', len(p)) if ver == 3 else syncsafe(len(p))) + b'\0\0' + p
def tag2(frames, ver=3, pad=0, flags=0):
    body = b''.join(frames) + b'\0' * pad
    return b'ID3' + bytes([ver, 0, flags]) + syncsafe(len(body)) + body
def f22(fid, p): return fid.encode() + len(p).to_bytes(3, 'big') + p
def v1(t, a):
    f = lambda b, n: b[:n].ljust(n, b'\0')
    return b'TAG' + f(t, 30) + f(a, 30) + b'\0' * 64 + b'\x0c'
L = b'\0'
cbr = open(os.path.join(out, 'cbr_noxing.mp3'), 'rb').read()
files = {
 'v23.mp3': tag2([f23('TIT2', L + b'Title Two'), f23('TPE1', L + b'Artist Two')]) + audio,
 'v1.mp3': audio + v1(b'Title One', b'Artist One'),
 'both.mp3': tag2([f23('TIT2', L + b'V2 Title'), f23('TPE1', L + b'V2 Artist')]) + audio + v1(b'V1 Title', b'V1 Artist'),
 'v23utf16.mp3': tag2([f23('TIT2', b'\x01\xff\xfe' + 'Uni'.encode('utf-16-le') + b'\0\0'), f23('TPE1', L + b'Plain')]) + audio,
 'v24.mp3': tag2([f23('TIT2', b'\x03Four', 4), f23('TPE1', b'\x03Art4', 4)], ver=4) + audio,
 'v22.mp3': b'ID3\x02\x00\x00' + syncsafe(len(f22('TT2', L + b'Title22') + f22('TP1', L + b'Artist22'))) + f22('TT2', L + b'Title22') + f22('TP1', L + b'Artist22') + audio,
 'long.mp3': tag2([f23('TIT2', L + bytes(range(65, 91)) * 4), f23('TPE1', L + b'X' * 100)]) + audio,
 'longv1.mp3': audio + v1(b'A' * 30, b'B' * 30),
 'pad.mp3': tag2([f23('TALB', L + b'Album'), f23('TIT2', L + b'Padded')], pad=4000) + audio,
 'notag_v32.mp3': rd('trans_v32.mp3'), 'notag_48.mp3': rd('trans_48.mp3'), 'notag_lsf.mp3': rd('g_lsf22.mp3'),
 'notag_320.mp3': rd('f_loud320.mp3'),
 'bigtag.mp3': tag2([f23('TIT2', L + b'Big')], pad=30000) + audio,
 'bigv1.mp3': audio + v1(b'T', b'A'),
 'utf16be.mp3': tag2([f23('TIT2', b'\x01\xfe\xff' + 'Be'.encode('utf-16-be') + b'\0\0'), f23('TPE1', b'\x02' + 'Xy'.encode('utf-16-be'))], pad=64 - 2 * 10 - 9 - 5) + audio,
 'order.mp3': tag2([f23('TPE1', L + b'First'), f23('TIT2', L + b'Second')], pad=60 - 2 * 10 - 6 - 7) + audio,
 'unsync.mp3': tag2([f23('TIT2', L + b'Uns\xff\x00x')], pad=40 - 10 - 7, flags=0x80) + audio,
 'd_a.mp3': cbr[:63950] + v1(b'T', b'A'), 'd_b.mp3': cbr[:63950],
 'd_c.mp3': b'ID3\x03\x00\x00' + syncsafe(10) + b'\0' * 10 + cbr[:63995],
 'd_d.mp3': cbr[:64000], 'd_e.mp3': cbr[:63999],
}
x = bytearray(open(os.path.join(out, 'vbr_xing.mp3'), 'rb').read()); i = x.find(b'Xing')
for n in (267, 268, 38):
    y = bytearray(x); y[i + 8:i + 12] = n.to_bytes(4, 'big'); files['x_%d.mp3' % n] = bytes(y)
y = bytearray(x); y[i + 4:i + 8] = (0xe).to_bytes(4, 'big'); files['x_noframes.mp3'] = bytes(y)
y = bytearray(audio); j = audio.find(b'Info'); y[j + 8:j + 12] = (500).to_bytes(4, 'big'); files['info500.mp3'] = bytes(y)
for k, v in files.items(): open(os.path.join(out, k), 'wb').write(v)
print(len(files) + 3, 'files')
