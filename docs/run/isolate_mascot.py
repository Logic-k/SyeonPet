#!/usr/bin/env python3
"""Build clean mascot base: silhouette-fill so white body stays opaque, decorations dropped."""
import numpy as np
from PIL import Image
from scipy import ndimage

orig = np.array(Image.open('/home/ubuntu/syeon-src.jpg').convert('RGB'))
cut = np.array(Image.open('/home/ubuntu/syeon-cutout.png').convert('RGBA'))
alpha = cut[:, :, 3] > 20
lbl, n = ndimage.label(alpha)

keep_labels = set()
main = int(np.argmax(np.array(ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))))) + 1
keep_labels.add(main)

# proximity pass: merge components close to the kept set, but only ones whose
# center x is inside the main body horizontal span (drops side decorations)
ys, xs = np.where(lbl == main)
mx0, mx1 = xs.min(), xs.max()
kept = lbl == main
for _ in range(6):
    grown = ndimage.binary_dilation(kept, iterations=20)
    added = False
    for i in range(1, n + 1):
        if i in keep_labels:
            continue
        ys_i, xs_i = np.where(lbl == i)
        cx = xs_i.mean()
        touches = np.any(grown[ys_i, xs_i])
        inside_x = mx0 - 30 <= cx <= mx1 + 30
        if touches and inside_x:
            keep_labels.add(i)
            kept |= lbl == i
            added = True
    if not added:
        break
print('kept labels:', sorted(keep_labels))

# close small outline gaps, fill interior so white body is opaque
sil = ndimage.binary_dilation(kept, iterations=8)
sil = ndimage.binary_fill_holes(sil)
sil = ndimage.binary_erosion(sil, iterations=8)
sil = ndimage.binary_fill_holes(sil)

out = np.dstack([orig, (sil * 255).astype(np.uint8)])
ys, xs = np.where(sil)
x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
pad = 6
crop = out[max(0,y0-pad):min(out.shape[0],y1+pad+1), max(0,x0-pad):min(out.shape[1],x1+pad+1)]
Image.fromarray(crop).save('/home/ubuntu/syeon-base.png')
print('saved', crop.shape)
