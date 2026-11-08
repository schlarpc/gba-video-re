"""ISO/IEC 11172-3 polyphase synthesis in floating point, for comparison with the firmware's fixed-point
synthesis (mp3_test.py synth_window_316b4).  The window D is the standard's table; it is read at run
time from an FFmpeg source tree you provide (FFMPEG_SRC = its libavcodec directory, file
mpegaudiodsp_data.c, table ff_mpa_enwindow: D[i] = enwindow[i] / 65536, D[512-i] = -D[i] unless
i % 64 == 0).  Without FFMPEG_SRC, available() is False and the comparison is skipped."""
import math
import os
import re

SRC = os.environ.get('FFMPEG_SRC', '')
D = None
N = [[math.cos((16 + i) * (2 * k + 1) * math.pi / 64) for k in range(32)] for i in range(64)]


def available():
    global D
    if D is not None:
        return True
    p = os.path.join(SRC, 'mpegaudiodsp_data.c')
    if not SRC or not os.path.exists(p):
        return False
    src = open(p).read()
    body = src[src.index('ff_mpa_enwindow'):]
    body = body[body.index('{') + 1: body.index('}')]
    ew = [int(x) for x in re.findall(r'-?\d+', body)]
    assert len(ew) == 257, len(ew)
    D = [0.0] * 512
    for i in range(257):
        v = ew[i] / 65536.0
        D[i] = v
        if i:
            D[512 - i] = v if i % 64 == 0 else -v
    return True


class Synth:
    def __init__(self):
        assert available(), 'set FFMPEG_SRC'
        self.V = [0.0] * 1024

    def slot(self, S):
        V = self.V
        V[64:] = V[:-64]
        for i in range(64):
            V[i] = sum(N[i][k] * S[k] for k in range(32))
        U = [0.0] * 512
        for i in range(8):
            for j in range(32):
                U[64 * i + j] = V[128 * i + j]
                U[64 * i + 32 + j] = V[128 * i + 96 + j]
        W = [U[i] * D[i] for i in range(512)]
        return [sum(W[j + 32 * i] for i in range(16)) for j in range(32)]
