"""State verification after browser actions.

Provides:
    - visual_diff: perceptual screenshot comparison
    - waitAndVerify: multi-strategy DOM state verification
"""

import asyncio
import logging

import numpy as np
from PIL import Image

from config.settings import Settings

logger = logging.getLogger("meraki.verify")


def visual_diff(
    before_path: str,
    after_path: str,
    threshold: float = 2.0,
    tolerance: int = 10,
) -> tuple[bool, float]:
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
    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")

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


# ─── DOM state verification ─────────────────────────────────────

async def dom_changed(
    selector: str,
    before_state: dict,
    cdp=None,
) -> bool:
    """Check if DOM element state changed since before snapshot.

    Args:
        selector: CSS selector to check
        before_state: dict from primitive/dom.locate()
        cdp: CdpClient instance

    Returns True if the element's visible state or text changed.
    """
    from primitive.dom import locate
    after_state = await locate(selector, cdp)

    if not after_state:
        # Element disappeared — that's a change
        return before_state is not None

    if not before_state:
        # Element appeared
        return True

    # Check visible state change
    if before_state.get("visible") != after_state.get("visible"):
        return True

    # Check text content change
    if before_state.get("text") != after_state.get("text"):
        return True

    return False


async def url_changed(
    before_url: str,
    cdp=None,
) -> bool:
    """Check if page URL changed."""
    from primitive.dom import CdpClient
    cdp = cdp or CdpClient()
    current = await cdp.evaluate("window.location.href")
    return current != before_url


async def loader_gone(cdp=None) -> bool:
    """Check if page loader has disappeared."""
    from primitive.dom import CdpClient
    cdp = cdp or CdpClient()
    ready = await cdp.evaluate(
        "document.readyState === 'complete'"
    )
    return bool(ready)


# ─── Combined verification ──────────────────────────────────────

async def waitAndVerify(
    checks: list,
    timeout: int | None = None,
    interval: float = 0.3,
    settings: Settings | None = None,
) -> bool:
    """Multi-strategy state verification — waits until all checks pass.

    Args:
        checks: list of async callables returning True on success
        timeout: max wait time in ms (default from Settings)
        interval: polling interval in seconds
        settings: Settings instance

    Returns True if all checks pass within timeout.
    """
    s = settings or Settings()
    timeout = timeout or s.verify_timeout
    deadline = asyncio.get_event_loop().time() + timeout / 1000

    logger.info(
        "[verify] waiting up to %dms for %d checks",
        timeout, len(checks),
    )

    while asyncio.get_event_loop().time() < deadline:
        results = []
        for i, check in enumerate(checks):
            try:
                ok = await check()
                results.append(ok)
                if not ok:
                    logger.debug("[verify] check %d not yet ready", i + 1)
            except Exception as e:
                logger.debug("[verify] check %d error: %s", i + 1, e)
                results.append(False)

        if all(results):
            logger.info("[verify] all %d checks passed", len(checks))
            return True

        await asyncio.sleep(interval)

    logger.warning("[verify] timeout — %d checks not satisfied", len(checks))
    return False
