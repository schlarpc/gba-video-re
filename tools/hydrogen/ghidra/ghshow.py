import sys, difflib, re
sys.argv += []
exec(open('ghdiff.py').read().split("ref = load('agu')")[0])
a = load('agu'); b = load(sys.argv[1]); name = sys.argv[2]
fa = [v for v in a.values() if v[0] == name][0][1]; fb = [v for v in b.values() if v[0] == name][0][1]
for l in difflib.unified_diff(fa.splitlines(), fb.splitlines(), lineterm='', n=1): print(l)
