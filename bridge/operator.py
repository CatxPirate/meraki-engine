"""High-level Meraki Engine operations for Hermes Agent.

All functions are async. Import from execute_code() or use via CLI wrapper.

Usage from execute_code():
    sys.path.insert(0, "/home/ubuntu/meraki-engine")
    from bridge.operator import Operator

    op = Operator()                     # default :9222
    op = Operator(remote_cdp_port=9223) # session.py port

    await op.navigate("https://example.com")
    coords = await op.locate("the green submit button")
    ok = await op.click("the green submit button")
    path = await op.screenshot()
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Tuple, Optional

# Ensure meraki-engine root is importable
_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from bridge import ensure as ensure_tunnel
from bridge import local_port as tunnel_local_port
from primitive.dom import CdpClient
from primitive.vision import (
    capture_screenshot,
    visual_locate,
    visual_click,
    VISION_CONFIDENCE_THRESHOLD,
)

logger = logging.getLogger("meraki.bridge.operator")

CDP_HOST = "127.0.0.1"
DEFAULT_REMOTE_CDP_PORT = 9222
DEFAULT_LOCAL_PORT = 19222


class Operator:
    """Persistent CDP connection for multi-step operations.

    Args:
        remote_cdp_port: Chrome CDP port on executor (default 9222).
                         Session.py assigns ports like 9223, 9224, etc.
    """

    def __init__(self, remote_cdp_port: int = DEFAULT_REMOTE_CDP_PORT):
        self._remote_port = remote_cdp_port
        self._local_port: int | None = None
        self._cdp: CdpClient | None = None

    @property
    def remote_port(self) -> int:
        return self._remote_port

    @property
    def local_port(self) -> int | None:
        return self._local_port

    async def _get_cdp(self) -> CdpClient:
        """Get or create CDP connection. Ensures tunnel is up for this port."""
        if self._cdp is None:
            self._local_port = tunnel_local_port(self._remote_port)
            ensure_tunnel(self._remote_port)
            self._cdp = CdpClient(host=CDP_HOST, port=self._local_port)
            await self._cdp.connect()
            logger.debug("CDP connected via tunnel :%d -> executor:%d",
                         self._local_port, self._remote_port)
        return self._cdp

    async def close(self):
        """Close CDP connection."""
        if self._cdp:
            await self._cdp.close()
            self._cdp = None

    async def navigate(self, url: str) -> dict:
        """Navigate to URL. Returns {url, title}."""
        cdp = await self._get_cdp()
        await cdp.navigate(url)
        await asyncio.sleep(1.0)
        title = await cdp.evaluate("document.title") or ""
        return {"url": url, "title": title}

    async def locate(
        self,
        description: str,
        confidence_threshold: float = VISION_CONFIDENCE_THRESHOLD,
    ) -> dict:
        """Find element coordinates by visual description.

        Returns:
            {"found": bool, "x": int|None, "y": int|None, "confidence": float|None}
        """
        cdp = await self._get_cdp()
        coords = await visual_locate(
            description, cdp, confidence_threshold=confidence_threshold
        )
        if coords:
            return {"found": True, "x": coords[0], "y": coords[1], "confidence": None}
        return {"found": False, "x": None, "y": None, "confidence": None}

    async def click(self, description: str) -> dict:
        """Visually locate and click an element.

        Returns:
            {"clicked": bool}
        """
        cdp = await self._get_cdp()
        result = await visual_click(description, cdp)
        return {"clicked": result}

    async def screenshot(self, output_path: str | None = None) -> dict:
        """Capture screenshot of current page.

        Returns:
            {"path": str, "size_bytes": int}
        """
        cdp = await self._get_cdp()
        path = await capture_screenshot(cdp, output_path=output_path)
        size = Path(path).stat().st_size
        return {"path": path, "size_bytes": size}

    async def evaluate(self, expression: str) -> dict:
        """Evaluate JavaScript in page context.

        Returns:
            {"result": any}
        """
        cdp = await self._get_cdp()
        result = await cdp.evaluate(expression)
        return {"result": result}


# Module-level convenience (one-shot, auto-close, default port)
_operator: Operator | None = None


async def _get_op() -> Operator:
    global _operator
    if _operator is None:
        _operator = Operator()
    return _operator


async def navigate(url: str) -> dict:
    op = await _get_op()
    return await op.navigate(url)


async def locate(description: str, confidence_threshold: float = VISION_CONFIDENCE_THRESHOLD) -> dict:
    op = await _get_op()
    return await op.locate(description, confidence_threshold)


async def click(description: str) -> dict:
    op = await _get_op()
    return await op.click(description)


async def screenshot(output_path: str | None = None) -> dict:
    op = await _get_op()
    return await op.screenshot(output_path)


async def evaluate(expression: str) -> dict:
    op = await _get_op()
    return await op.evaluate(expression)


async def close():
    global _operator
    if _operator:
        await _operator.close()
        _operator = None
