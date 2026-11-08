#!/usr/bin/env bash
# Builds the LAME test streams used by nmp_manifest.csv into $1 (default ${TMPDIR:-/tmp}).
# Needs lame (nix build nixpkgs#lame; LAME 4.0 made the manifest) and the python env.
set -euo pipefail
D=${1:-${TMPDIR:-/tmp}}; L=${LAME:-$(nix build --no-link --print-out-paths nixpkgs#lame)/bin/lame}
H0=$(cd "$(dirname "$0")" && pwd)
cd "$D"
python3 - <<'PY'
import numpy as np, wave
sr=44100; rng=np.random.default_rng(1)
def save(name, st, k=32767):
    w=wave.open(name,'wb'); w.setnchannels(st.shape[1]); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((np.clip(st,-1,1)*k).astype('<i2').tobytes()); w.close()
n=sr*6; t=np.arange(n)/sr
x=0.2*np.sin(2*np.pi*440*t)
for k in range(0,n,sr//4):
    L=min(400,n-k); x[k:k+L]+=rng.normal(0,0.6,L)*np.exp(-np.arange(L)/60)
y=0.15*np.sin(2*np.pi*1000*t+1)+0.03*rng.normal(0,1,n)
save('trans.wav', np.stack([x,y+x*0.3],1), 32000)
rng=np.random.default_rng(7); n=sr*4; t=np.arange(n)/sr
x=np.sign(np.sin(2*np.pi*220*t))*0.98 + rng.normal(0,0.3,n)*(np.sin(2*np.pi*1.5*t)>0.7)
y=np.sin(2*np.pi*3000*t*(1+t))*0.95
save('loud.wav', np.stack([x,y],1))
save('noise.wav', rng.normal(0,0.35,(n,2)))
c=np.sin(2*np.pi*(50+4000*t)*t)*0.6*(t%1<0.6)
save('chirp.wav', np.stack([c, c*0.97+0.01*rng.normal(0,1,n)],1))
PY
q() { "$L" "$@" >/dev/null 2>&1; }
q -b 128 -m j trans.wav trans_j.mp3; q -b 128 -m s trans.wav trans_s.mp3; q -b 64 -m m trans.wav trans_m.mp3
q -V2 -m j --resample 32 trans.wav trans_v32.mp3; q -b 192 -m j --resample 48 trans.wav trans_48.mp3
q -b 320 -m s loud.wav f_loud320.mp3; q -b 96 -m j noise.wav f_noise96.mp3; q -b 32 -m m --resample 32 chirp.wav f_chirp32m.mp3
q -V0 -m j chirp.wav f_chirpv0.mp3; q -b 160 -m f --resample 48 noise.wav f_noise48f.mp3; q -b 64 -m j -q 9 loud.wav f_loud64q9.mp3
q -b 128 -m j --nores loud.wav f_loudnores.mp3
q -b 32 -m j --resample 22.05 trans.wav g_lsf22.mp3; q -b 24 -m m --resample 16 chirp.wav g_lsf16m.mp3
q -b 16 -m m --resample 8 chirp.wav g_m25_8.mp3; q -b 48 -m s --resample 11.025 trans.wav g_m25_11.mp3
# crafted intensity-stereo and mixed-block streams (header / side-info bit edits, tools/playyan/nmp/craft.py)
H=$H0
q -b 32 -m j --resample 24 trans.wav y_24.mp3; q -b 24 -m j --resample 16 trans.wav y_16.mp3
q -b 24 -m j --resample 12 trans.wav y_12.mp3; q -b 24 -m j --resample 11.025 trans.wav y_11.mp3
q -b 16 -m j --resample 8 trans.wav y_8.mp3
for spec in "trans_j is" "trans_j mixed" "trans_j is+mixed" "f_loud64q9 is" "g_lsf22 is" "f_noise96 is" "trans_48 mixed" \
            "g_lsf22 mixed" "y_24 mixed" "y_16 mixed" "y_12 mixed" "y_11 mixed" "y_8 mixed" "y_24 mixed+sfc425" "y_8 mixed+sfc425"; do
  set -- $spec; python3 "$H/craft.py" $1.mp3 x_$1_${2//+/_}.mp3 $2 >/dev/null
done
