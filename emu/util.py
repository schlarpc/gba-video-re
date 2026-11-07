import numpy as np
from PIL import Image
def bgr555_to_img(raw, w=240, h=160):
    a = np.frombuffer(raw, '<u2')[:w*h].reshape(h, w)
    r = (a & 31); g = (a >> 5) & 31; b = (a >> 10) & 31
    rgb = np.stack([r, g, b], -1).astype(np.uint16)
    rgb = (rgb << 3) | (rgb >> 2)
    return Image.fromarray(rgb.astype(np.uint8), 'RGB')
def video_to_img(v):
    data, w, h, pitch = v
    a = np.frombuffer(data, '<u2').reshape(h, pitch // 2)[:, :w]  # RGB565
    r = (a >> 11) & 31; g = (a >> 5) & 63; b = a & 31
    rgb = np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.uint8)
    return Image.fromarray(rgb, 'RGB')
