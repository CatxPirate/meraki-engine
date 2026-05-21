"""Retry orchestration with fallback chain — state machine pattern.

Fallback order:
    RETRY 3x → SCROLL → COORDINATE → VISION → HUMAN CONFIRM

Every step logs: what tried, why failed, what next.

HumanConfirmChannel (from engine/human.py) can be injected to
enable Telegram-based confirmations. Without it, HUMAN state
immediately raises HumanConfirmRequired.
"""

import asyncio
import logging
from collections.abc import Callable
from enum import Enum, auto
from typing import TYPE_CHECKING, Tuple

from config.settings import Settings
from config.constants import FallbackOrder

if TYPE_CHECKING:
    from engine.human import HumanConfirmChannel

logger = logging.getLogger("meraki.retry")


class HumanConfirmRequired(Exception):
    """Raised when all automated fallbacks exhausted."""

    def __init__(self, action_name: str, last_error: str):
        self.action_name = action_name
        self.last_error = last_error
        super().__init__(
            f"Human confirm needed for '{action_name}'. "
            f"Last error: {last_error}"
        )


class FallbackState(Enum):
    """State machine states"""

    IDLE = auto()
    RETRYING = auto()
    SCROLLING = auto()
    COORDINATE = auto()
    VISION = auto()
    HUMAN = auto()
    SUCCESS = auto()
    FAILED = auto()


class RetryOrchestrator:
    """State machine for retry+fallback orchestration."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.state = FallbackState.IDLE
        self.attempt = 0

    async def execute(
        self,
        action: Callable,
        *,
        action_name: str = "action",
        scroll_target: str | None = None,
        scroll_fn: Callable | None = None,
        coords: Tuple[int, int] | None = None,
        coord_click_fn: Callable | None = None,
        vision_locate_fn: Callable | None = None,
        human_channel: "HumanConfirmChannel | None" = None,
    ) -> bool:
        """Execute action with full retry + fallback chain.

        Args:
            action: async callable returning True on success
            action_name: label for logging
            scroll_target: CSS selector to scroll to
            scroll_fn: async callable(str) — scrolls to selector
            coords: (x, y) for coordinate fallback
            coord_click_fn: async callable(int, int) — clicks at coords
            vision_locate_fn: async callable() -> Tuple[int,int] | None
                AI vision-based element locator. Called when DOM
                coordinate fallback fails. Returns (x,y) or None.
            human_channel: optional HumanConfirmChannel for
                Telegram-based confirm. Without it, HUMAN state
                raises immediately.

        Returns:
            True if action succeeded.

        Raises:
            HumanConfirmRequired: when all fallbacks + human
                confirm exhausted or human says no.
        """
        last_error = "Action returned False"

        # --- Phase 1: Retry with exponential delay ---
        self.state = FallbackState.RETRYING
        delay = 0

        for attempt in range(1, self.settings.retry_limit + 1):
            self.attempt = attempt
            logger.info(
                "[%s] RETRY %d/%d — attempting...",
                action_name, attempt, self.settings.retry_limit,
            )
            await asyncio.sleep(delay)

            try:
                result = await action()
                if result:
                    self.state = FallbackState.SUCCESS
                    logger.info(
                        "[%s] SUCCESS — attempt %d",
                        action_name, attempt,
                    )
                    return True
                else:
                    logger.warning(
                        "[%s] RETRY %d/%d — action returned False, "
                        "retrying...",
                        action_name, attempt, self.settings.retry_limit,
                    )
                    last_error = "Action returned False"
            except Exception as e:
                logger.warning(
                    "[%s] RETRY %d/%d — error: %s",
                    action_name, attempt, self.settings.retry_limit, e,
                )
                last_error = str(e)

            # Exponential backoff
            delay = self.settings.scroll_delay * (2 ** (attempt - 1))

        logger.warning(
            "[%s] RETRY exhausted — all %d attempts failed, next: SCROLL",
            action_name, self.settings.retry_limit,
        )

        # --- Phase 2: Scroll to element then retry ---
        self.state = FallbackState.SCROLLING
        if scroll_target and scroll_fn:
            logger.info(
                "[%s] SCROLL — scrolling to '%s' then retrying",
                action_name, scroll_target,
            )
            try:
                await scroll_fn(scroll_target)
                await asyncio.sleep(self.settings.scroll_delay)
                result = await action()
                if result:
                    self.state = FallbackState.SUCCESS
                    logger.info(
                        "[%s] SUCCESS — after scroll to '%s'",
                        action_name, scroll_target,
                    )
                    return True
            except Exception as e:
                logger.warning(
                    "[%s] SCROLL failed — %s", action_name, e,
                )
                last_error = f"Scroll failed: {e}"
        else:
            logger.info(
                "[%s] SCROLL skipped — no scroll_target provided, "
                "next: COORDINATE",
                action_name,
            )

        # --- Phase 3: Coordinate click fallback ---
        self.state = FallbackState.COORDINATE
        if coords and coord_click_fn:
            logger.info(
                "[%s] COORDINATE — clicking at (%d, %d) then retrying",
                action_name, *coords,
            )
            try:
                await coord_click_fn(*coords)
                await asyncio.sleep(self.settings.click_delay)
                result = await action()
                if result:
                    self.state = FallbackState.SUCCESS
                    logger.info(
                        "[%s] SUCCESS — after coordinate click",
                        action_name,
                    )
                    return True
            except Exception as e:
                logger.warning(
                    "[%s] COORDINATE failed — %s", action_name, e,
                )
                last_error = f"Coordinate click failed: {e}"
        else:
            logger.info(
                "[%s] COORDINATE skipped — no coords provided, "
                "next: HUMAN",
                action_name,
            )

        # --- Phase 4: AI Vision fallback ---
        self.state = FallbackState.VISION
        if vision_locate_fn and coord_click_fn:
            logger.info(
                "[%s] VISION — using AI to locate element visually then clicking",
                action_name,
            )
            try:
                visual_coords = await vision_locate_fn()
                if visual_coords:
                    vx, vy = visual_coords
                    logger.info(
                        "[%s] VISION — element found at (%d, %d), clicking",
                        action_name, vx, vy,
                    )
                    await coord_click_fn(vx, vy)
                    await asyncio.sleep(self.settings.click_delay)
                    result = await action()
                    if result:
                        self.state = FallbackState.SUCCESS
                        logger.info(
                            "[%s] SUCCESS — after AI vision click",
                            action_name,
                        )
                        return True
                else:
                    logger.warning(
                        "[%s] VISION — element not found visually",
                        action_name,
                    )
            except Exception as e:
                logger.warning(
                    "[%s] VISION failed — %s", action_name, e,
                )
                last_error = f"Vision fallback failed: {e}"
        else:
            logger.info(
                "[%s] VISION skipped — no vision_locate_fn, next: HUMAN",
                action_name,
            )

        # --- Phase 5: Human confirm ---
        self.state = FallbackState.HUMAN
        logger.error(
            "[%s] ALL FALLBACKS EXHAUSTED — human confirm required",
            action_name,
        )

        # Try Telegram confirm if channel available
        if human_channel:
            decision = await human_channel.request_confirm(
                action_name=action_name,
                last_error=last_error,
            )
            if decision is True:
                logger.info(
                    "[%s] HUMAN approved — retrying one final time",
                    action_name,
                )
                try:
                    result = await action()
                    if result:
                        self.state = FallbackState.SUCCESS
                        logger.info(
                            "[%s] SUCCESS — after human-approved retry",
                            action_name,
                        )
                        return True
                except Exception as e:
                    logger.error(
                        "[%s] human-approved retry also failed: %s",
                        action_name, e,
                    )
                    last_error = f"Post-human retry failed: {e}"
            elif decision is False:
                logger.info(
                    "[%s] HUMAN rejected — aborting", action_name,
                )
            else:
                logger.warning(
                    "[%s] HUMAN timeout — auto-aborting", action_name,
                )

        raise HumanConfirmRequired(
            action_name=action_name,
            last_error=last_error,
        )


async def retryOrFallback(
    action: Callable,
    *,
    action_name: str = "action",
    scroll_target: str | None = None,
    scroll_fn: Callable | None = None,
    coords: Tuple[int, int] | None = None,
    coord_click_fn: Callable | None = None,
    vision_locate_fn: Callable | None = None,
    settings: Settings | None = None,
    human_channel: "HumanConfirmChannel | None" = None,
) -> bool:
    """Execute action with retry + fallback chain.

    Convenience wrapper around RetryOrchestrator.

    Raises HumanConfirmRequired if all fails.
    """
    orchestrator = RetryOrchestrator(settings=settings)
    return await orchestrator.execute(
        action=action,
        action_name=action_name,
        scroll_target=scroll_target,
        scroll_fn=scroll_fn,
        coords=coords,
        coord_click_fn=coord_click_fn,
        vision_locate_fn=vision_locate_fn,
        human_channel=human_channel,
    )
