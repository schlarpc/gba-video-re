"""Hash manifest of the DSP oracle's output for test files: one line per file
   name,calls,md5   (md5 over the first `calls` synthesis outputs, int16 LE, DSP call order)
python3 make_manifest.py OUT.csv FILE.mp3 ...   (oracle dumps cached in $TMPDIR/nmpx)"""
import sys, os, subprocess, hashlib, numpy as np
TMP = os.environ.get('TMPDIR', '/tmp')
rows = []
for f in sys.argv[2:]:
    name = os.path.basename(f).rsplit('.', 1)[0]
    p = TMP + '/nmpx/%s.json.npy' % name
    if not os.path.exists(p):
        subprocess.run([sys.executable, 'oracle_md5.py', f, p[:-4]], check=True, capture_output=True)
    ref = np.load(p)
    rows.append('%s,%d,%s' % (os.path.basename(f), len(ref), hashlib.md5(ref.astype('<i2').tobytes()).hexdigest()))
open(sys.argv[1], 'w').write('# file,calls,md5 of the NMP DSP synthesis output (tools/playyan/nmp/oracle_md5.py)\n' + '\n'.join(rows) + '\n')
print('\n'.join(rows))
