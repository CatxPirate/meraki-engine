"""Screenshot capture and visual element location via CDP.

Layer: primitive/ — foundation for coordinate fallback in retry.py
and visual_diff verification in verify.py.

Flow:
    1. capture_screenshot(cdp) → full-page PNG to disk
    2. visual_locate(description, cdp) → (x, y) from screenshot
    3. visual_click(description, cdp) → click at visual coordinates

Phase 1 (now): screenshot capture + visual locate stub.
Phase 2 (future): AI-powered visual element detection.
"""

import asyncio
import base64
import logging
import os
import tempfile
from pathlib import Path
from typing import Tuple

from primitive.dom import CdpClient

logger = logging.getLogger("meraki.vision")

# Default screenshot directory — configurable
DEFAULT_SHOT_DIR = Path(tempfile.gettempdir()) / "meraki-shots"


async def capture_screenshot(
    cdp: CdpClient | None = None,
    *,
    output_path: str | None = None,
    format: str = "png",
    quality: int = 80,
    clip: dict | None = None,
    from_surface: bool = True,
) -> str:
    """Capture a screenshot of the current page via CDP.

    Uses Page.captureScreenshot — returns base64-encoded image data
    which is decoded and saved to disk.

    Args:
        cdp: CdpClient instance (auto-creates if None)
        output_path: Where to save the PNG. Auto-generated if None.
        format: "png" or "jpeg" (default png)
        quality: JPEG compression 0-100 (ignored for PNG)
        clip: Optional viewport clip {"x","y","width","height","scale"}
        from_surface: Capture composited page (True) or raw viewport

    Returns:
        Absolute path to the saved screenshot file.

    Raises:
        CDPError: if screenshot capture fails
    """
    cdp = cdp or CdpClient()

    params: dict = {
        "format": format,
        "fromSurface": from_surface,
    }
    if format == "jpeg":
        params["quality"] = quality
    if clip:
        params["clip"] = clip

    logger.debug("Capturing screenshot (format=%s, clip=%s)", format, clip is not None)
    result = await cdp._send("Page.captureScreenshot", params)

    data_b64 = result.get("data")
    if not data_b64:
        raise RuntimeError("Page.captureScreenshot returned no data")

    image_bytes = base64.b64decode(data_b64)

    # Generate output path if not provided
    if not output_path:
        os.makedirs(DEFAULT_SHOT_DIR, exist_ok=True)
        timestamp = int(asyncio.get_running_loop().time() * 1000)
        ext = "jpg" if format == "jpeg" else "png"
        output_path = str(DEFAULT_SHOT_DIR / f"meraki-{timestamp}.{ext}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(image_bytes)

    logger.info("Screenshot saved: %s (%d bytes)", output_path, len(image_bytes))
    return output_path


async def capture_viewport(
    cdp: CdpClient | None = None,
    *,
    output_path: str | None = None,
) -> str:
    """Capture only the visible viewport.

    Convenience wrapper — figures out viewport dimensions
    from the page and clips to that rectangle.
    """
    cdp = cdp or CdpClient()

    # Get viewport dimensions
    width = await cdp.evaluate("window.innerWidth")
    height = await cdp.evaluate("window.innerHeight")

    logger.debug("Viewport: %dx%d", width, height)
    return await capture_screenshot(
        cdp,
        output_path=output_path,
        clip={"x": 0, "y": 0, "width": width, "height": height, "scale": 1},
    )


async def visual_locate(
    description: str,
    cdp: CdpClient | None = None,
    *,
    screenshot_path: str | None = None,
) -> Tuple[int, int] | None:
    """Find element coordinates by visual description.

    Phase 2 (future): uses AI vision model to locate the element
    in a screenshot and return (x, y) center coordinates.

    Currently stubbed — always returns None.
    When implemented, this feeds into retry.py's coordinate fallback.

    Args:
        description: Natural language description of the element
        cdp: CdpClient instance
        screenshot_path: Optional pre-captured screenshot

    Returns:
        (x, y) center coordinates or None if not found
    """
    logger.warning(
        "[visual_locate] NOT IMPLEMENTED — returning None for '%s'",
        description[:60],
    )
    return None


async def visual_click(
    description: str,
    cdp: CdpClient | None = None,
) -> bool:
    """Full visual click: capture → locate → click.

    Phase 2 (future): end-to-end vision-based clicking.
    Falls back to visual_locate → coordinate click.

    Args:
        description: Natural language description of the element
        cdp: CdpClient instance

    Returns:
        True if element was found and clicked
    """
    coords = await visual_locate(description, cdp)
    if coords is None:
        logger.info("[visual_click] element '%s' not found visually", description[:60])
        return False

    x, y = coords
    logger.info("[visual_click] clicking at visual coords (%d, %d)", x, y)
    await cdp.evaluate(
        f"document.elementFromPoint({x}, {y})?.click()"
    )
    return True
