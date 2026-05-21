"""Screenshot capture and visual element location via CDP.

Layer: primitive/ — foundation for coordinate fallback in retry.py
and visual_diff verification in verify.py.

Flow:
    1. capture_screenshot(cdp) → full-page PNG to disk
    2. visual_locate(description, cdp) → (x, y) from screenshot
    3. visual_click(description, cdp) → click at visual coordinates

AI Vision: uses Google Gemini 2.5 Flash native API.
Requires GEMINI_API_KEY environment variable.
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

# Gemini Vision API
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
VISION_CONFIDENCE_THRESHOLD = 0.6

# Max image size to send to vision API (bytes)
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB


def _get_api_key() -> str:
    """Get Gemini API key from environment."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        logger.warning("No GEMINI_API_KEY or GOOGLE_API_KEY in environment")
    return key


def _gemini_url() -> str:
    """Build Gemini API URL with API key."""
    api_key = _get_api_key()
    return f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent?key={api_key}"


def _build_vision_prompt(description: str, viewport_w: int, viewport_h: int) -> str:
    """Build the prompt for Gemini to locate an element.

    Designed to produce compact JSON output with precise
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
    """Extract JSON object from Gemini text response.

    Handles various formats: raw JSON, JSON in code blocks, JSON buried in text.
    Returns parsed dict or None if parsing fails.
    """
    text = text.strip()

    # Strip markdown code block wrapping (Gemini sometimes wraps JSON in ```json...```)
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # Try direct JSON parse first
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

    # Fallback: try to close truncated JSON
    # e.g. {"found": true, "x": 100, "y" -> add closing brace
    m = re.search(r'\{[\s\S]*?"found"[\s\S]*', text)
    if m:
        truncated = m.group(0).rstrip()
        # Remove trailing comma if present then close
        if truncated.endswith(","):
            truncated = truncated[:-1]
        if not truncated.endswith("}"):
            truncated += "}"
        try:
            return json.loads(truncated)
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
    """Find element coordinates by visual description using Gemini 2.5 Flash.

    1. Captures screenshot (or uses provided screenshot_path)
    2. Sends to Gemini native API with image + prompt
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

    # Get viewport dimensions for context
    viewport_w = await cdp.evaluate("window.innerWidth") or 1920
    viewport_h = await cdp.evaluate("window.innerHeight") or 1080

    # Build Gemini-native request payload
    prompt = _build_vision_prompt(description, viewport_w, viewport_h)
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": img_b64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 500,
        },
    }

    api_url = _gemini_url()
    logger.debug(
        "Sending Gemini vision request: model=%s, desc=%s, img=%d bytes",
        GEMINI_MODEL, description[:60], len(img_bytes),
    )

    try:
        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_resp_text = resp.read().decode("utf-8")
            logger.debug("Gemini API raw response: %s", raw_resp_text[:500])

            # Guard: empty response
            if not raw_resp_text or not raw_resp_text.strip():
                logger.error("Gemini API returned empty response (HTTP %s)", resp.status)
                return None

            resp_data = json.loads(raw_resp_text)

        # Parse Gemini response: candidates[0].content.parts[0].text
        candidates = resp_data.get("candidates", [])
        if not candidates:
            logger.warning("Gemini returned no candidates: %s", resp_data.get("error", {}))
            return None

        content = (
            candidates[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        if not content:
            logger.warning("Empty text in Gemini response")
            return None

        logger.debug("Gemini vision text: %s", content[:300])

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

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        logger.error("Gemini API HTTP %s: %s", e.code, err_body)
        return None
    except urllib.error.URLError as e:
        logger.error("Gemini API request failed: %s", e)
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
    element via Gemini vision, then clicks at the returned coordinates.

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
