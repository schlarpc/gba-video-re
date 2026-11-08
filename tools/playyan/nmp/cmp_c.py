"""compare the C reference (reference/playyan/nmp/pynmpdec) with the DSP oracle on files:
python3 cmp_c.py FILE.mp3 ...   (oracle dumps cached as $TMPDIR/nmpx/<name>.json.npy)"""
import sys, os, subprocess, numpy as np, json
TMP = os.environ.get('TMPDIR', '/tmp')
C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../reference/playyan/nmp/pynmpdec')
for f in sys.argv[1:]:
    name = os.path.basename(f).rsplit('.', 1)[0]
    ref_p = TMP + '/nmpx/%s.json.npy' % name
    if not os.path.exists(ref_p):
        subprocess.run([sys.executable, 'oracle_md5.py', f, ref_p[:-4]], check=True, capture_output=True)
    ref = np.load(ref_p)
    subprocess.run([C, f, TMP + '/c.pcm', TMP + '/c.calls'], check=True, capture_output=True)
    c = np.fromfile(TMP + '/c.calls', np.int16).reshape(-1, 576)
    n = min(len(ref), len(c))
    bad = [i for i in range(n) if not np.array_equal(ref[i], c[i])]
    print('%-16s oracle calls %5d  C calls %5d  compared %5d  mismatching %d %s' % (name, len(ref), len(c), n, len(bad), bad[:5]), flush=True)
