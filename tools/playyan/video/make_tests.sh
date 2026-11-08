#!/bin/sh
# Encode MPEG-4 SP test streams in ASF (MP4S + G.726) that exercise the decoder features
# Nintendo's files do not: MPEG quantisation with custom matrices, 4MV, resync markers,
# dquant, large f_code, extreme quantisers. Output: $1 (default tmp/vt).
set -e
OUT=${1:-tmp/vt}; mkdir -p "$OUT"
SRC=${PY_MEDIA:?}/playan_support_sample_movie/sample_movie.asf
A="-c:a g726 -ar 8000 -ac 1 -b:a 32k"
V="-c:v mpeg4 -vtag MP4S"
IM="8,16,19,22,26,27,29,34,16,16,22,24,27,29,34,37,19,22,26,27,29,34,34,38,22,22,26,27,29,34,37,40,22,26,27,29,32,35,40,48,26,27,29,32,35,40,48,58,26,27,29,34,38,46,56,69,27,29,35,38,46,56,69,83"
NM="16,20,22,24,26,28,30,32,20,22,24,26,28,30,32,34,22,24,26,28,30,32,34,36,24,26,28,30,32,34,36,38,26,28,30,32,34,36,38,40,28,30,32,34,36,38,40,42,30,32,34,36,38,40,42,44,32,34,36,38,40,42,44,46"
ffmpeg -v error -y -ss 40 -t 3 -i "$SRC" $V -flags +mv4 -ps 300 -lumi_mask 0.4 -dark_mask 0.4 -g 30 -q:v 6 $A "$OUT/B_resync_dq.asf"
ffmpeg -v error -y -ss 60 -t 3 -i "$SRC" $V -mpeg_quant 1 -intra_matrix $IM -inter_matrix $NM -flags +mv4 -g 25 -q:v 5 $A "$OUT/C_matrices.asf"
ffmpeg -v error -y -ss 10 -t 3 -i "$SRC" -vf "scale=720:528,crop=240:176:x='mod(t*900\,480)':y='mod(t*500\,352)'" $V -flags +mv4 -me_range 128 -g 45 -q:v 8 $A "$OUT/D_fastpan.asf"
ffmpeg -v error -y -ss 30 -t 3 -i "$SRC" $V -mpeg_quant 1 -flags +mv4 -ps 500 -g 15 -qmin 1 -qmax 2 -q:v 1 $A "$OUT/E_lowq_mpeg.asf"
ffmpeg -v error -y -ss 50 -t 3 -i "$SRC" $V -g 20 -qmin 24 -qmax 31 -q:v 31 -flags +mv4 $A "$OUT/F_highq.asf"
ffmpeg -v error -y -ss 70 -t 3 -i "$SRC" $V -g 10 -qmin 1 -qmax 1 -q:v 1 -flags +mv4 $A "$OUT/G_q1_h263.asf"
ls -la "$OUT"/*.asf
