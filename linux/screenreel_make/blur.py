"""Privacy blurs in source pixels, top-left origin, y down.

Blurs are painted into the source image before the camera transform, so a
zoom cannot reveal the masked pixels.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def apply_blurs(image: np.ndarray, rects: list[dict]) -> np.ndarray:
    if image.size == 0 or not rects:
        return image
    out = image.copy()
    height, width = out.shape[:2]
    for rect in rects:
        x = float(rect["x"])
        y = float(rect["y"])
        w = float(rect["width"])
        h = float(rect["height"])
        radius = float(rect.get("radius") or 18)
        x0 = max(0, min(width, int(math.floor(x))))
        y0 = max(0, min(height, int(math.floor(y))))
        x1 = max(0, min(width, int(math.ceil(x + w))))
        y1 = max(0, min(height, int(math.ceil(y + h))))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        kernel = max(3, int(radius) * 2 + 1)
        if kernel % 2 == 0:
            kernel += 1
        patch = out[y0:y1, x0:x1]
        out[y0:y1, x0:x1] = cv2.GaussianBlur(patch, (kernel, kernel), sigmaX=max(0.1, radius))
    return out
