"""State verification after browser actions.

Provides:
    - visual_diff: perceptual screenshot comparison
"""

import numpy as np
from PIL import Image


def visual_diff(before_path, after_path, threshold=2.0, tolerance=10):
    """
    Perceptual diff between two screenshots.

    Args:
        before_path: path to before screenshot
        after_path: path to after screenshot
        threshold: percent changed pixels to flag significant
        tolerance: per-channel color distance to count as changed

    Returns:
        (changed: bool, diff_percent: float)
    """
    before = Image.open(before_path).convert('RGB')
    after = Image.open(after_path).convert('RGB')

    if before.size != after.size:
        after = after.resize(before.size)

    w, h = before.size
    cx, cy = int(w * 0.10), int(h * 0.10)
    box = (cx, cy, w - cx, h - cy)

    arr_before = np.array(before.crop(box), dtype=np.int16)
    arr_after = np.array(after.crop(box), dtype=np.int16)

    diff = np.abs(arr_before - arr_after)
    max_chan = np.max(diff, axis=2)

    diff_px = np.sum(max_chan > tolerance)
    total_px = max_chan.size

    diff_pct = (diff_px / total_px) * 100.0
    return diff_pct > threshold, round(diff_pct, 4)
