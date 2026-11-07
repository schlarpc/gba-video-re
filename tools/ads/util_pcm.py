"""helpers for aligning decoded PCM against captured PCM"""
def common_prefix(x, xo, y, n):
    """length of the common prefix of x[xo:xo+n] and y[:n]"""
    k = 0; step = 1 << 16
    while k < n:
        m = min(step, n - k)
        if x[xo + k:xo + k + m] == y[k:k + m]: k += m; continue
        if m == 1: break
        step = max(1, m // 16)
    return k

def locate(pcm, a, start=0):
    """best (offset, matched) of decoded track a inside captured pcm, anchored on its first
    non-silent 1 KiB window; the player may start a track, stop and restart it, so every
    occurrence of the anchor is tried and the longest match wins"""
    K = next((i for i in range(0, max(1, len(a) - 1024), 256) if len(set(a[i:i + 1024])) > 8), 0)
    best = (-1, 0)
    f = pcm.find(a[K:K + 1024], start)
    while f >= 0:
        st = f - K
        if st >= 0:
            n = common_prefix(pcm, st, a, min(len(a), len(pcm) - st))
            if n > best[1]: best = (st, n)
        f = pcm.find(a[K:K + 1024], f + 1)
    return best
