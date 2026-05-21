"""Meraki Engine — Hybrid Browser Operator.

CDP primitives, vision-based element location, and human-like
automation engine for browser orchestration.

Quick start:
    from meraki_engine import CdpClient

    cdp = CdpClient(host="127.0.0.1", port=9222)
    await cdp.connect()
    await cdp.navigate("https://example.com")
"""

# ── Primitive layer ──────────────────────────────────────────
from meraki_engine.primitive.dom import CdpClient
from meraki_engine.primitive.vision import visual_locate, capture_screenshot, visual_click, VISION_CONFIDENCE_THRESHOLD
from meraki_engine.primitive.gesture import GestureSimulator, warmup_browse

# ── Engine layer ─────────────────────────────────────────────
from meraki_engine.engine.retry import RetryOrchestrator, FallbackState, HumanConfirmRequired
from meraki_engine.engine.safe_click import safe_click
from meraki_engine.engine.verify import (
    waitAndVerify, dom_changed, url_changed, loader_gone, visual_diff,
)
from meraki_engine.engine.human import HumanConfirmChannel

# ── Config ───────────────────────────────────────────────────
from meraki_engine.config.settings import Settings
from meraki_engine.config.constants import FallbackOrder, VerifyStrategy

__all__ = [
    "CdpClient",
    "visual_locate", "capture_screenshot", "visual_click", "VISION_CONFIDENCE_THRESHOLD",
    "GestureSimulator", "warmup_browse",
    "RetryOrchestrator", "FallbackState", "HumanConfirmRequired",
    "safe_click",
    "waitAndVerify", "dom_changed", "url_changed", "loader_gone", "visual_diff",
    "HumanConfirmChannel",
    "Settings",
    "FallbackOrder", "VerifyStrategy",
]
