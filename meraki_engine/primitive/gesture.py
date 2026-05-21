"""Minimal realistic human simulation via CDP input events.

Design principles:
- Smooth bezier mouse paths (not linear teleport)
- Variable timing (not uniform delays)
- Occasional imperfection (slight overshoot + correction)
- NOT chaotic — human randomness has patterns
- Minimal API surface — just enough to be believable

CDP reference:
- Input.dispatchMouseEvent(type, x, y, button, clickCount, modifiers)
- Input.dispatchKeyEvent(type, key, text, code, windowsVirtualKeyCode)
"""

import asyncio
import logging
import random
import math
import time
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers — timing & math
# ---------------------------------------------------------------------------

def _human_delay(base_ms: float, jitter_pct: float = 0.3) -> float:
    """Return delay in seconds with Gaussian jitter around base_ms.

    Human reaction time clusters around a mean with natural variance,
    not uniform random. Truncated at 0 to prevent negative delays.
    """
    ms = random.gauss(base_ms, base_ms * jitter_pct)
    return max(1.0, ms) / 1000.0


def _bezier_curve(
    start: tuple[float, float],
    cp1: tuple[float, float],
    cp2: tuple[float, float],
    end: tuple[float, float],
    steps: int = 30,
) -> list[tuple[int, int]]:
    """Cubic bezier curve — natural mouse path with acceleration/deceleration.

    Returns list of (x, y) integer points from start to end.
    Steps controls smoothness — more steps = smoother but slower.
    """
    points = []
    for i in range(steps + 1):
        t = i / steps
        # Cubic bezier formula
        x = (
            (1 - t) ** 3 * start[0]
            + 3 * (1 - t) ** 2 * t * cp1[0]
            + 3 * (1 - t) * t**2 * cp2[0]
            + t**3 * end[0]
        )
        y = (
            (1 - t) ** 3 * start[1]
            + 3 * (1 - t) ** 2 * t * cp1[1]
            + 3 * (1 - t) * t**2 * cp2[1]
            + t**3 * end[1]
        )
        points.append((int(x), int(y)))
    return points


def _control_points(
    start: tuple[float, float],
    end: tuple[float, float],
    overshoot_chance: float = 0.15,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Generate two control points for a natural bezier curve.

    Humans don't move in straight lines — slight arc with occasional
    overshoot (15% chance). Control points create natural curvature.

    Returns:
        (cp1, cp2) — two control points
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.sqrt(dx * dx + dy * dy)

    # Base offset — proportional to distance for natural arc
    base_offset = distance * 0.2

    # CP1: slight arc to the right/left (random direction)
    angle = math.atan2(dy, dx)
    perp_angle = angle + random.choice([-1, 1]) * math.pi / 2

    cp1_x = start[0] + dx * 0.33 + math.cos(perp_angle) * base_offset * random.uniform(0.5, 1.0)
    cp1_y = start[1] + dy * 0.33 + math.sin(perp_angle) * base_offset * random.uniform(0.5, 1.0)

    # CP2: approach target — occasionally overshoot
    overshoot = 0.0
    if random.random() < overshoot_chance:
        # Small overshoot past target, then correct
        overshoot = -distance * 0.08  # negative = past target

    cp2_x = start[0] + dx * 0.66 - math.cos(perp_angle) * base_offset * random.uniform(0.3, 0.7)
    cp2_y = start[1] + dy * 0.66 - math.sin(perp_angle) * base_offset * random.uniform(0.3, 0.7) + overshoot

    return (cp1_x, cp1_y), (cp2_x, cp2_y)


# ---------------------------------------------------------------------------
# GestureSimulator — main API
# ---------------------------------------------------------------------------

class GestureSimulator:
    """Inject realistic human-like input events via CDP.

    Usage:
        gs = GestureSimulator(cdp)
        await gs.move_to(500, 300)       # fluid mouse movement
        await gs.click()                   # click at current position
        await gs.type_text("hello x")     # human-like typing
        await gs.scroll(-400)              # smooth scroll down
        await gs.pause(2.0, 5.0)          # natural pause between actions
    """

    def __init__(self, cdp: Any):
        """Args: cdp — CdpClient instance (from primitive/dom.py)."""
        self._cdp = cdp
        self._cursor_x: int = 0
        self._cursor_y: int = 0

    # -- Mouse movement -----------------------------------------------------

    async def _mouse_event(
        self,
        event_type: str,
        x: int,
        y: int,
        button: str = "left",
        click_count: int = 1,
    ) -> None:
        """Dispatch a single mouse event via CDP."""
        await self._cdp._send_cmd("Input.dispatchMouseEvent", {
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "clickCount": click_count,
            "modifiers": 0,
        })

    async def move_to(
        self,
        target_x: int,
        target_y: int,
        steps: int | None = None,
    ) -> None:
        """Move cursor to target via smooth bezier curve.

        Args:
            target_x, target_y: destination coordinates
            steps: number of interpolation points (auto-calculated if None)

        Behavior:
            - Cubic bezier path with slight arc
            - Variable speed per segment
            - 15% chance of slight overshoot
        """
        start = (float(self._cursor_x), float(self._cursor_y))
        end = (float(target_x), float(target_y))

        # Skip if already at target
        distance = math.hypot(target_x - self._cursor_x, target_y - self._cursor_y)
        if distance < 2:
            return

        # Control points for bezier
        cp1, cp2 = _control_points(start, end)

        # Auto-calculate steps based on distance
        if steps is None:
            steps = max(8, min(40, int(distance / 15)))

        points = _bezier_curve(start, cp1, cp2, end, steps)

        # Dispatch mouseMoved with variable delays per segment
        for i, (px, py) in enumerate(points):
            await self._mouse_event("mouseMoved", px, py)
            # Variable delay between segments — faster in middle, slower at start/end
            segment_delay = _human_delay(8, jitter_pct=0.5)
            await asyncio.sleep(segment_delay)

        self._cursor_x = target_x
        self._cursor_y = target_y

    # -- Click --------------------------------------------------------------

    async def click(
        self,
        x: int | None = None,
        y: int | None = None,
        click_count: int = 1,
    ) -> None:
        """Click at target coordinates with natural press-release timing.

        If x/y omitted, clicks at current cursor position.

        Press duration varies (80-200ms) — real clicks aren't instant.
        """
        if x is not None and y is not None:
            await self.move_to(x, y)

        # Mouse press
        await self._mouse_event("mousePressed", self._cursor_x, self._cursor_y,
                                "left", click_count)

        # Human press duration — not instant release
        press_ms = random.uniform(80, 200)
        await asyncio.sleep(press_ms / 1000.0)

        # Mouse release
        await self._mouse_event("mouseReleased", self._cursor_x, self._cursor_y,
                                "left", click_count)

    # -- Typing -------------------------------------------------------------

    async def type_text(
        self,
        text: str,
        wpm: int = 60,
    ) -> None:
        """Type text character-by-character with variable delays.

        Args:
            text: string to type
            wpm: words-per-minute base speed (60 = ~100ms/char average)

        Behavior:
            - Each character dispatched as keyDown + keyUp pair
            - Delay varies per character (Gaussian around wpm base)
            - Longer text = slightly slower cadence (fatigue simulation)
        """
        # Map characters to CDP key events
        base_delay_ms = 12000.0 / wpm  # ~100ms at 60wpm

        for i, char in enumerate(text):
            # Slight fatigue: later characters take longer
            fatigue = 1.0 + (i / max(1, len(text))) * 0.15
            delay_ms = base_delay_ms * fatigue

            # Special keys
            if char == "\n":
                await self._cdp._send_cmd("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                })
                await asyncio.sleep(0.01)
                await self._cdp._send_cmd("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                })
            elif char == " ":
                await self._cdp._send_cmd("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "key": " ",
                    "code": "Space",
                    "windowsVirtualKeyCode": 32,
                })
                await asyncio.sleep(0.01)
                await self._cdp._send_cmd("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": " ",
                    "code": "Space",
                    "windowsVirtualKeyCode": 32,
                })
            else:
                # Regular character — char event + keyDown/keyUp
                await self._cdp._send_cmd("Input.dispatchKeyEvent", {
                    "type": "char",
                    "text": char,
                })
                await asyncio.sleep(0.01)
                await self._cdp._send_cmd("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": char,
                })

            # Variable delay between keystrokes
            await asyncio.sleep(_human_delay(delay_ms, jitter_pct=0.4))

    # -- Scrolling ----------------------------------------------------------

    async def scroll(
        self,
        delta_y: int,
        delta_x: int = 0,
    ) -> None:
        """Scroll with slight horizontal wobble.

        Args:
            delta_y: vertical scroll amount (negative = down)
            delta_x: horizontal wobble (default: small random)

        Behavior:
            - Uses Input.dispatchMouseEvent(type="mouseWheel")
            - Slight random horizontal movement (human hand isn't perfectly steady)
        """
        if delta_x == 0:
            delta_x = random.randint(-3, 3)

        await self._cdp._send_cmd("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": self._cursor_x,
            "y": self._cursor_y,
            "deltaX": delta_x,
            "deltaY": delta_y,
        })

        # Small delay for scroll to render
        await asyncio.sleep(random.uniform(0.1, 0.3))

    # -- Pausing ------------------------------------------------------------

    async def pause(self, min_seconds: float, max_seconds: float) -> None:
        """Natural pause — simulate reading/scanning between actions.

        Uses Gaussian distribution between min and max for realistic
        clustering (not uniform random).
        """
        mean = (min_seconds + max_seconds) / 2
        std = (max_seconds - min_seconds) / 4
        duration = max(min_seconds, min(max_seconds, random.gauss(mean, std)))
        await asyncio.sleep(duration)


# ---------------------------------------------------------------------------
# Warmup workflow — session restore + natural browsing
# ---------------------------------------------------------------------------

async def warmup_browse(
    cdp: Any,
    *,
    scrolls: int = 3,
    hover_tweets: int = 2,
    total_duration: float = 30.0,
) -> None:
    """Simulate natural browsing after session restore.

    Navigate to X home, scroll slowly, hover over tweets —
    makes the session look human before any automated actions.

    Args:
        cdp: CdpClient instance
        scrolls: number of scroll pauses
        hover_tweets: number of tweets to hover over
        total_duration: total warmup time in seconds (approximate)
    """
    gs = GestureSimulator(cdp)
    start = time.monotonic()

    logger.debug("Warmup: starting browsing simulation (~%0.1fs)", total_duration)

    # 1. Start at top of timeline
    await gs.move_to(600, 400)

    # 2. Scroll down slowly with pauses
    for i in range(scrolls):
        elapsed = time.monotonic() - start
        if elapsed > total_duration:
            break

        # Scroll 300-600px per step (natural reading pace)
        delta = random.randint(-600, -300)
        await gs.scroll(delta, delta_x=random.randint(-2, 2))

        # Pause between scrolls (read content)
        await gs.pause(1.5, 4.0)

    # 3. Hover over tweets (simulate reading)
    for i in range(hover_tweets):
        elapsed = time.monotonic() - start
        if elapsed > total_duration:
            break

        # Move to random position in the timeline
        tx = random.randint(200, 800)
        ty = random.randint(200, 700)
        await gs.move_to(tx, ty)
        await gs.pause(1.0, 3.0)

    logger.debug("Warmup complete: %0.1fs elapsed", time.monotonic() - start)
