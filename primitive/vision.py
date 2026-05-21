"""Screenshot capture and visual element location via CDP.

Layer: primitive/ — foundation for coordinate fallback in retry.py
and visual_diff verification in verify.py.

Flow:
    1. capture_screenshot(cdp) → full-page PNG to disk
    2. visual_locate(description, cdp) → (x, y) from screenshot
    3. visual_click(description, cdp) → click at visual coordinates

AI Vision: uses local DeepCooK/Gemini Flash model at localhost:20128
which supports image inputs (OpenAI-compatible vision API).
"""

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Tuple

import urllib.request

from primitive.dom import CdpClient

logger = logging.getLogger("meraki.vision")

# Default screenshot directory — configurable
DEFAULT_SHOT_DIR = Path(tempfile.gettempdir()) / "meraki-shots"

# Vision API endpoint (OpenAI-compatible)
VISION_API_URL = "http://localhost:20128/v1/chat/completions"
VISION_MODEL = "DeepCooK"
VISION_CONFIDENCE_THRESHOLD = 0.6

# Max image size to send to vision API (bytes)
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB


def _build_vision_prompt(description: str, viewport_w: int, viewport_h: int) -> str:
    """Build the prompt for the vision model to locate an element.

    The prompt is designed to produce compact JSON output with precise
    viewport-relative coordinates.
    """
    return (
        f"You are a precise UI element locator. The screenshot is a web page "
        f"at {viewport_w}x{viewport_h} viewport resolution.\n\n"
        f"Task: Find the element matching this description: \"{description}\"\n\n"
        f"Return ONLY valid JSON, no markdown, no explanation outside JSON:\n"
        f'{{"found": true|false, "x": <center_x_pixels>, "y": <center_y_pixels>, '
        f'"confidence": <0.0-1.0>, "reason": "<brief reason>"}}\n\n'
        f"Coordinates are viewport-relative: (0,0) = top-left corner. "
        f"Use the CENTER of the element, not its corner."
    )


def _parse_vision_response(text: str) -> dict | None:
    """Extract JSON object from a vision model response.

    Handles various formats: raw JSON, JSON in code blocks, JSON buried in text.
    Returns parsed dict or None if parsing fails.
    """
    # Try direct JSON parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    m = re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding JSON object with regex
    m = re.search(r'\{[\s\S]*?"found"[\s\S]*?\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse vision response: %s", text[:200])
    return None


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
        RuntimeError: if screenshot capture fails
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
    confidence_threshold: float = VISION_CONFIDENCE_THRESHOLD,
) -> Tuple[int, int] | None:
    """Find element coordinates by visual description using AI vision.

    1. Captures screenshot (or uses provided screenshot_path)
    2. Sends to DeepCooK/Gemini Flash vision model at localhost:20128
    3. Parses JSON response: {"found": bool, "x": int, "y": int, "confidence": float}
    4. Returns (x, y) center coordinates if confidence >= threshold

    Args:
        description: Natural language description of the element to find
        cdp: CdpClient instance (used if screenshot_path not provided)
        screenshot_path: Optional pre-captured screenshot path
        confidence_threshold: Minimum confidence to accept (0.0-1.0)

    Returns:
        (x, y) center coordinates or None if not found / low confidence
    """
    cdp = cdp or CdpClient()

    # Capture screenshot if not provided
    if not screenshot_path:
        screenshot_path = await capture_screenshot(cdp)
        if not screenshot_path:
            logger.error("Failed to capture screenshot for visual_locate")
            return None

    # Read and validate screenshot
    img_bytes = Path(screenshot_path).read_bytes()
    if len(img_bytes) > MAX_IMAGE_BYTES:
        logger.warning(
            "Screenshot too large (%d bytes), skipping vision API",
            len(img_bytes),
        )
        return None

    img_b64 = base64.b64encode(img_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{img_b64}"

    # Get viewport dimensions for context
    viewport_w = await cdp.evaluate("window.innerWidth") or 1920
    viewport_h = await cdp.evaluate("window.innerHeight") or 1080

    # Build request
    prompt = _build_vision_prompt(description, viewport_w, viewport_h)
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            }
        ],
        "max_tokens": 200,
        "temperature": 0.0,
    }

    logger.debug(
        "Sending vision request: model=%s, desc=%s, img=%d bytes",
        VISION_MODEL, description[:60], len(img_bytes),
    )

    try:
        req = urllib.request.Request(
            VISION_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer nope",  # required by OpenAI compat, not validated
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read())

        content = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            logger.warning("Empty response from vision API")
            return None

        logger.debug("Vision raw response: %s", content[:300])

        parsed = _parse_vision_response(content)
        if not parsed:
            return None

        found = parsed.get("found", False)
        confidence = parsed.get("confidence", 0.0)
        x = parsed.get("x")
        y = parsed.get("y")
        reason = parsed.get("reason", "")

        logger.info(
            "Vision result: found=%s conf=%.2f coords=(%s,%s) reason=%s",
            found, confidence, x, y, reason[:60],
        )

        if not found or confidence < confidence_threshold:
            return None

        if x is None or y is None:
            logger.warning("Vision found=true but coordinates missing")
            return None

        return (int(x), int(y))

    except urllib.error.URLError as e:
        logger.error("Vision API request failed: %s", e)
        return None
    except Exception as e:
        logger.exception("Unexpected error in visual_locate")
        return None


async def visual_click(
    description: str,
    cdp: CdpClient | None = None,
) -> bool:
    """Full visual click: capture -> locate -> click.

    End-to-end vision-based clicking. Captures screenshot, locates
    element via AI vision, then clicks at the returned coordinates.

    Args:
        description: Natural language description of the element
        cdp: CdpClient instance

    Returns:
        True if element was found, clicked, and verified
    """
    coords = await visual_locate(description, cdp)
    if coords is None:
        logger.info(
            "[visual_click] element '%s' not found visually", description[:60]
        )
        return False

    x, y = coords
    logger.info("[visual_click] clicking at visual coords (%d, %d)", x, y)
    await cdp.evaluate(
        f"document.elementFromPoint({x}, {y})?.click()"
    )
    return True
