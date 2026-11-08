"""Rust decoder (tests/nmp.rs nmp_dump) vs the cached DSP oracle dumps: python3 cmp_rust.py FILE.mp3 ..."""
import sys, os, subprocess, numpy as np
TMP = os.environ.get('TMPDIR', '/tmp')
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../..')
for f in sys.argv[1:]:
    name = os.path.basename(f).rsplit('.', 1)[0]
    p = TMP + '/nmpx/%s.json.npy' % name
    if not os.path.exists(p):
        subprocess.run([sys.executable, 'oracle_md5.py', f, p[:-4]], check=True, capture_output=True, cwd=os.path.dirname(os.path.abspath(__file__)))
    ref = np.load(p)
    env = dict(os.environ, NMP_FILE=f, NMP_OUT=TMP + '/rs.raw')
    r = subprocess.run(['cargo', 'test', '--release', '-q', '-p', 'gbav-playyan', '--test', 'nmp', '--', '--ignored', 'nmp_dump', '--nocapture'], cwd=D, env=env, capture_output=True, text=True)
    stop = [l for l in r.stderr.splitlines() + r.stdout.splitlines() if 'stopped' in l]
    c = np.fromfile(TMP + '/rs.raw', np.int16).reshape(-1, 576)
    n = min(len(ref), len(c))
    bad = [i for i in range(n) if not np.array_equal(ref[i], c[i])]
    print('%-22s oracle %5d rust %5d compared %5d mismatching %d %s %s' % (name, len(ref), len(c), n, len(bad), bad[:4], stop[:1]), flush=True)
