"""Safe click — reliable element interaction with verification.

Flow:
    1. locate → 2. is_visible → 3. scroll_into_view →
    4. click → 5. waitAndVerify → 6. retryOrFallback on failure

This is the primary user-facing primitive of Meraki Engine.
"""

import asyncio
import logging
from typing import Tuple

from config.settings import Settings
from primitive.dom import (
    CdpClient,
    locate,
    is_visible,
    scroll_into_view,
    click,
    wait_for_element,
)
from engine.verify import (
    dom_changed,
    url_changed,
    loader_gone,
    waitAndVerify,
)
from engine.retry import retryOrFallback, HumanConfirmRequired

logger = logging.getLogger("meraki.safe_click")


async def safe_click(
    selector: str,
    *,
    cdp: CdpClient | None = None,
    settings: Settings | None = None,
    wait_checks: list | None = None,
) -> bool:
    """Click an element and verify the action succeeded.

    Full flow:
        1. Wait for element to exist → 2. Check visible →
        3. Scroll into view → 4. Click → 5. Verify state change

    On failure at any step, delegates to retryOrFallback().

    Args:
        selector: CSS selector of the element to click
        cdp: CdpClient instance (auto-creates if None)
        settings: Settings instance
        wait_checks: extra verification checks (async callables)

    Returns:
        True if click succeeded and verified

    Raises:
        HumanConfirmRequired: if all automated fallbacks exhausted
    """
    cdp = cdp or CdpClient()
    s = settings or Settings()

    # Step 1: Wait for element
    logger.info("[safeClick] locating '%s'...", selector)
    if not await wait_for_element(
        selector, timeout=s.verify_timeout, cdp=cdp
    ):
        logger.warning(
            "[safeClick] element '%s' not found — delegating to retry",
            selector,
        )
        return await _retry_click(selector, cdp, s, wait_checks)

    # Step 2: Check visible
    logger.info("[safeClick] checking visibility of '%s'", selector)
    if not await is_visible(selector, cdp):
        logger.warning(
            "[safeClick] element '%s' not visible — delegating to retry",
            selector,
        )
        return await _retry_click(selector, cdp, s, wait_checks)

    # Step 3: Scroll into view
    logger.info("[safeClick] scrolling to '%s'", selector)
    try:
        await scroll_into_view(selector, cdp)
        await asyncio.sleep(s.scroll_delay)
    except Exception as e:
        logger.warning(
            "[safeClick] scroll failed for '%s': %s", selector, e,
        )

    # Step 4: Capture before-state for verification
    before_state = await locate(selector, cdp)
    before_url = await cdp.evaluate("window.location.href")

    # Step 5: Click
    logger.info("[safeClick] clicking '%s'", selector)
    clicked = await click(selector, cdp)
    if not clicked:
        logger.warning(
            "[safeClick] click on '%s' returned False — retrying",
            selector,
        )
        return await _retry_click(selector, cdp, s, wait_checks)

    # Step 6: Verify state change
    logger.info("[safeClick] verifying state change for '%s'", selector)

    # Build verification checks
    checks = [
        lambda: dom_changed(selector, before_state, cdp),
        lambda: url_changed(before_url, cdp),
        lambda: loader_gone(cdp),
    ]
    if wait_checks:
        checks.extend(wait_checks)

    verified = await waitAndVerify(checks, settings=s)

    if not verified:
        logger.warning(
            "[safeClick] verification failed for '%s' — delegating to retry",
            selector,
        )
        return await _retry_click(selector, cdp, s, wait_checks)

    logger.info("[safeClick] SUCCESS — '%s' clicked and verified", selector)
    return True


async def _retry_click(
    selector: str,
    cdp: CdpClient,
    settings: Settings,
    wait_checks: list | None = None,
) -> bool:
    """Internal: wrap full click flow for retryOrFallback."""

    # Get element bounds for coordinate fallback
    info = await locate(selector, cdp)
    coords: Tuple[int, int] | None = None
    if info and info.get("bounds"):
        bounds = info["bounds"]
        coords = (
            int(bounds["x"] + bounds["width"] / 2),
            int(bounds["y"] + bounds["height"] / 2),
        )

    async def click_action() -> bool:
        before_state = await locate(selector, cdp)
        before_url = await cdp.evaluate("window.location.href")

        # Ensure visibility
        await scroll_into_view(selector, cdp)
        await asyncio.sleep(settings.scroll_delay)
        await is_visible(selector, cdp)

        # Click
        ok = await click(selector, cdp)
        if not ok:
            return False

        # Verify
        checks = [
            lambda: dom_changed(selector, before_state, cdp),
            lambda: url_changed(before_url, cdp),
            lambda: loader_gone(cdp),
        ]
        if wait_checks:
            checks.extend(wait_checks)
        return await waitAndVerify(checks, settings=settings)

    async def scroll_fn(sel: str) -> None:
        await scroll_into_view(sel, cdp)

    async def coord_click_fn(x: int, y: int) -> None:
        await cdp.evaluate(
            f"document.elementFromPoint({x}, {y})?.click()"
        )

    return await retryOrFallback(
        action=click_action,
        action_name=f"click('{selector}')",
        scroll_target=selector,
        scroll_fn=scroll_fn,
        coords=coords,
        coord_click_fn=coord_click_fn,
        settings=settings,
    )
