#!/usr/bin/env python3
"""Model of the video firmware's frame fit (0x2749a mode 2 -> 0x27980 strips -> 0x2788e planes ->
0x276de plane scaler -> 0x29014 vertical / 0x28844 horizontal passes and their kernels), with the
firmware's memory behaviour (in-place vertical upscale, kernels that write one byte past the row,
row interleaving of the horizontal kernels). Compared with the emulator by fit_check.py.

All buffers live in one flat bytearray, as in the chip's memory."""

RECIP = [0, 65536, 32768, 21845, 16384, 13107, 10923, 9362, 8192, 7282, 6554, 5958, 5461, 5041, 4681, 4369, 4096]


def op21(a, b):
    return (a * (b & 0xffff) + 0x8000) >> 16


class Mem:
    def __init__(self, size):
        self.m = bytearray(size)

    def rd(self, a):
        return self.m[a] if 0 <= a < len(self.m) else 0

    def wr(self, a, v):
        if 0 <= a < len(self.m):
            self.m[a] = v & 0xff


# ---------------------------------------------------------------- kernels
def memcpy(M, d, s, n):
    for i in range(n):
        M.wr(d + i, M.rd(s + i))


def lerp_rows(M, d, a, n, st, w):
    """0x28e64: out = a + round((b - a) * w / 65536); w <= 63 -> memcpy; writes n+1 bytes unless
    the destination row ends 16-aligned."""
    w &= 0xffffffff
    if w <= 63:
        return memcpy(M, d, a, n)
    w &= 0xffff
    fast = (d + n) % 16 == 0   # one byte too many unless the row ends 16-aligned (146/146 kernel fuzz)
    vals = []
    for i in range(n + (0 if fast else 1)):
        x = M.rd(a + i); y = M.rd(a + st + i)
        vals.append((x + (((y - x) * w + 0x8000) >> 16)) & 0xff)
    for i, v in enumerate(vals):
        M.wr(d + i, v)


def vbox(M, d, s, n_bytes, st, n):
    """0x28f4e: n rows summed (n > 16: rows subsampled by doubling the stride), op21 by 65536/n."""
    if n - 1 <= 1:
        return lerp_rows(M, d, s, n_bytes, st, (n - 1) << 15)
    while n > 16:
        st <<= 1; n >>= 1
    r = RECIP[n]
    vals = []
    for i in range(n_bytes):
        sm = sum(M.rd(s + k * st + i) for k in range(n))
        vals.append(op21(sm, r) & 0xff)
    for i, v in enumerate(vals):
        M.wr(d + i, v)


def hbox(M, d, s, n_out, step):
    """0x2868e: per output pixel, n = min(ceil(step), 16) lanes from src + (pos >> 16), each
    op21(p, 65536/n), summed, min 255."""
    n = min((step + 0xffff) >> 16, 16)
    r = RECIP[n]
    pos = 0
    for j in range(n_out):
        base = s + (pos >> 16)
        sm = sum(op21(M.rd(base + k), r) for k in range(n))
        M.wr(d + j, min(sm, 255))
        pos += step


def hres_rows(M, d, s, n, x0, step, dp, sp, rows, bilinear):
    """16/8/4-row kernels (0x28afa.. bilinear rounded, 0x28d2c.. nearest): n + (n % 16 != 0)
    bytes per row."""
    for r in range(rows):
        # the partial last 16-byte group writes one byte too many unless the row ends 16-aligned
        nl = n + (1 if (d + r * dp + n) % 16 else 0)
        vals = []
        for j in range(nl):
            xx = x0 + j * step; x = xx >> 16; f = xx & 0xffff
            p0 = M.rd(s + r * sp + x)
            if bilinear:
                p1 = M.rd(s + r * sp + x + 1)
                vals.append((p0 + (((p1 - p0) * f + 0x8000) >> 16)) & 0xff)
            else:
                vals.append(p0)
        for j, v in enumerate(vals):
            M.wr(d + r * dp + j, v)


def hres_row1(M, d, s, n, x0, step, bilinear):
    """single-row kernels 0x28ace (bilinear, FLOOR) / 0x28d10 (nearest): exactly n bytes."""
    xx = x0
    for j in range(n):
        x = xx >> 16; f = xx & 0xffff
        p0 = M.rd(s + x)
        if bilinear:
            p1 = M.rd(s + x + 1)
            v = p0 + (((p1 - p0) * f) >> 16)
        else:
            v = p0
        M.wr(d + j, v)
        xx += step


def edge_fill(M, d, s, n, rows, dp, sp):
    """0x28cde: replicate src[r] into n bytes of each dst row."""
    for r in range(rows):
        v = M.rd(s + r * sp)
        for j in range(n):
            M.wr(d + r * dp + j, v)


# ---------------------------------------------------------------- passes
def hpass(M, dst, dw, rows, dp, src, sw, sp, mode):
    """0x28844(dst, dw, rows, dpitch, src, sw; [sp] spitch, [sp+4] mode)"""
    step = (sw << 16) // dw if dw else 0
    x0 = (step - 1) >> 1
    bil = mode != 0
    if bil:
        if step > (2 << 16):
            for r in range(rows):
                hbox(M, dst + r * dp, src + r * sp, dw, step)
            return
        x0 = ((sw - dw) << 15) // dw if dw else 0
        # Python // floors; the firmware's divs truncates toward zero
        num = (sw - dw) << 15
        x0 = -((-num) // dw) if num < 0 else num // dw
        if dw > sw:
            e = (sw + dw - 1) // (2 * sw)
            edge_fill(M, dst, src, e, rows, dp, sp)
            edge_fill(M, dst + dw - e, src + sw - 1, e, rows, dp, sp)
            dst += e; x0 += e * step; dw -= 2 * e
    r = rows
    if not ((dp | sp) & 8):
        # both pitches multiples of 16: 16-row kernels, then 8..15 rows as two overlapping 8-row
        # calls, then single rows
        while r >= 16:
            hres_rows(M, dst, src, dw, x0, step, dp, sp, 16, bil)
            dst += 16 * dp; src += 16 * sp; r -= 16
        if r >= 8:
            r15 = r & 7
            if r15 > 1:
                hres_rows(M, dst, src, dw, x0, step, dp, sp, 8, bil)
                dst += r15 * dp; src += r15 * sp; r -= r15
            hres_rows(M, dst, src, dw, x0, step, dp, sp, 8, bil)
            dst += 8 * dp; src += 8 * sp; r -= 8
    else:
        # a pitch of 8 mod 16: row pairs with doubled pitches (even rows, then odd rows)
        while r >= 32:
            for par in (0, 1):
                hres_rows(M, dst + par * dp, src + par * sp, dw, x0, step, 2 * dp, 2 * sp, 16, bil)
            dst += 32 * dp; src += 32 * sp; r -= 32
        if r >= 16:
            for par in (0, 1):
                hres_rows(M, dst + par * dp, src + par * sp, dw, x0, step, 2 * dp, 2 * sp, 8, bil)
            dst += 16 * dp; src += 16 * sp; r -= 16
        if r >= 8:
            for par in (0, 1):
                hres_rows(M, dst + par * dp, src + par * sp, dw, x0, step, 2 * dp, 2 * sp, 4, bil)
            dst += 8 * dp; src += 8 * sp; r -= 8
    while r > 0:
        hres_row1(M, dst, src, dw, x0, step, bil)
        dst += dp; src += sp; r -= 1


def tdiv(a, b):
    return -((-a) // b) if (a < 0) != (b < 0) and a % b else a // b


def vpass(M, dst, n, rows, dp, src, sp, y0, step, last, mode):
    """0x29014(dst, len, rows, dpitch, src, spitch; [sp] y0, [sp+4] step, [sp+8] last row, mode)"""
    if mode == 0:
        y = y0 + ((step - 1) >> 1)
        for _ in range(rows):
            memcpy(M, dst, src + (y >> 16) * sp, n)
            dst += dp; y += step
        return
    if step >= (2 << 16):
        y = y0
        for _ in range(rows):
            a = y >> 16; y += step; b = y >> 16
            vbox(M, dst, src + a * sp, n, sp, b - a)
            dst += dp
        return
    y = y0 + (step >> 1) - 0x8000
    for _ in range(rows):
        r3 = y >> 16
        if y <= 0:
            memcpy(M, dst, src, n)
        elif r3 >= last:
            memcpy(M, dst, src + r3 * sp, n)
        else:
            lerp_rows(M, dst, src + r3 * sp, n, sp, y)
        dst += dp; y += step


def plane(M, dst, dw, dh, dp, src, sw, sh, sp, mode, scratch, scratch_size):
    """0x276de"""
    if dh <= 0 or sh <= 0 or sw <= 0 or dw <= 0:
        return
    if dw == sw and dh == sh:
        for r in range(dh):
            memcpy(M, dst + r * dp, src + r * sp, sw)
        return
    if dh >= sh:
        off = (dh - sh) * dp
        hpass(M, dst + off, dw, sh, dp, src, sw, sp, mode)
        vpass(M, dst, dw, dh, dp, dst + off, dp, 0, tdiv(sh << 16, dh), sh - 1, mode)
        return
    if sh == 2 * dh and sw == 2 * dw and ((dh | sw | dp | sp | dst | src) & 15) == 0:
        # 0x286e4 mode 1 (the only mode 0x2749a reaches): 2x2 box, (sum + 2) >> 2
        for r in range(dh):
            for x in range(dw):
                a = src + 2 * r * sp + 2 * x
                M.wr(dst + r * dp + x, (M.rd(a) + M.rd(a + 1) + M.rd(a + sp) + M.rd(a + sp + 1) + 2) >> 2)
        return
    pitch = (sw + 15) & ~15
    if scratch_size >= pitch * 64:
        chunk = 64
    elif scratch_size >= pitch * 32:
        chunk = 32
    else:
        chunk = min(scratch_size // pitch, 16)
    step = tdiv(sh << 16, dh)
    y0 = 0; done = 0
    while done < dh:
        c = min(chunk, dh - done)
        vpass(M, scratch, sw, c, pitch, src, sp, y0, step, sh - 1, mode)
        hpass(M, dst, dw, c, dp, scratch, sw, pitch, mode)
        y0 += step * c; dst += c * dp; done += c


def planes(M, temp, tpitch, th, dx, dy, dw, dh, src, spitch, sheight, sx, sy, sw, sh, mode, scratch, ssz):
    """0x2788e for a type-3 temp image (pitch tpitch, height th) from a type-3 source."""
    ty = ((th + 15) >> 4) * tpitch * 16
    sy_ = ((sheight + 15) >> 4) * spitch * 16
    plane(M, temp + dy * tpitch + dx, dw, dh, tpitch, src + sy * spitch + sx, sw, sh, spitch, mode, scratch, ssz)
    tu = temp + ty + (dy >> 1) * (tpitch >> 1) + (dx >> 1)
    su = src + sy_ + (sy >> 1) * (spitch >> 1) + (sx >> 1)
    plane(M, tu, dw >> 1, dh >> 1, tpitch >> 1, su, sw >> 1, sh >> 1, spitch >> 1, mode, scratch, ssz)
    tv = temp + ty + ((th + 15) >> 4) * tpitch * 4 + (dy >> 1) * (tpitch >> 1) + (dx >> 1)
    sv = src + sy_ + ((sheight + 15) >> 4) * spitch * 4 + (sy >> 1) * (spitch >> 1) + (sx >> 1)
    plane(M, tv, dw >> 1, dh >> 1, tpitch >> 1, sv, sw >> 1, sh >> 1, spitch >> 1, mode, scratch, ssz)


def fl8(a, b):
    return (a * b) >> 8


def conv565(y, cb, cr):
    yy = fl8(y - 16, 298); u = cb - 128; v = cr - 128
    g = max(-32768, min(32767, fl8(u, -100) + fl8(v, -200)))
    lo = max(0, min(255, yy + fl8(u, 408))); hi = max(0, min(255, yy + fl8(v, 516))); gg = max(0, min(255, yy + g))
    return (lo >> 3) | ((gg >> 2) << 5) | ((hi & 0xF8) << 8)


def fit_frame(yuv, W, H, DW=240, DH=160, state=None):
    """Return the DW x DH RGB565 frame. `state` keeps the temp image and scratch across calls."""
    sp = (W + 15) & ~15
    hp = (H + 15) & ~15
    # flat memory: [src Y|U|V][temp][scratch]
    srcsz = sp * hp * 3 // 2
    tp = (DW + 15) & ~15
    big = DW * DH * 2 >= 28800
    th = DH // 2 if big else DH
    tsz = tp * ((th + 15) & ~15) * 3 // 2
    ssz = 24576
    if state is None:
        state = {}
    M = Mem(srcsz + tsz + 16 + ssz + 64)
    src, temp, scratch = 0, srcsz, srcsz + tsz + 16   # 16 zero bytes after the temp image (gp+0x1dbb4)
    if 'temp' in state:
        M.m[temp:temp + tsz] = state['temp']; M.m[scratch:scratch + ssz] = state['scratch']
    # source planes from I420 (W x H, chroma W/2 x H/2) into the padded layout
    Y = yuv[:W * H]; U = yuv[W * H:W * H * 5 // 4]; V = yuv[W * H * 5 // 4:]
    for r in range(H):
        M.m[src + r * sp:src + r * sp + W] = Y[r * W:(r + 1) * W]
    ub = src + sp * hp; vb = ub + sp * hp // 4
    for r in range(H // 2):
        M.m[ub + r * (sp // 2):ub + r * (sp // 2) + W // 2] = U[r * (W // 2):(r + 1) * (W // 2)]
        M.m[vb + r * (sp // 2):vb + r * (sp // 2) + W // 2] = V[r * (W // 2):(r + 1) * (W // 2)]
    # 0x2749a mode 2
    dx, dy, dw, dh = 0, 0, DW, DH
    sx, sy, sw, sh = 0, 0, W, H
    h2 = tdiv(DW * H, W)
    if h2 < DH:
        dy = (DH - h2) // 2; dh = h2
    elif h2 > DH:
        used = tdiv(DH * H, h2); sy = (H - used) // 2; sh = used
    out = [0] * (DW * DH)
    strips = 2 if big else 1
    for k in range(strips):
        planes(M, temp, tp, th, dx, dy, dw, dh // strips, src, sp, H, sx, sy + k * (sh // strips), sw, sh // strips, 1, scratch, ssz)
        # 0x27130 type1 <- type3: whole temp image, nearest chroma
        tyb = ((th + 15) >> 4) * tp * 16
        for r in range(th):
            for x in range(DW):
                c = (r >> 1) * (tp >> 1) + (x >> 1)
                out[(k * th + r) * DW + x] = conv565(M.rd(temp + r * tp + x), M.rd(temp + tyb + c), M.rd(temp + tyb + ((th + 15) >> 4) * tp * 4 + c))
    state['temp'] = bytes(M.m[temp:temp + tsz]); state['scratch'] = bytes(M.m[scratch:scratch + ssz])
    return out
