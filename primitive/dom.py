"""DOM-level interaction via Chrome DevTools Protocol.

Provides primitives: locate, click, scroll, is_visible.
All operations connect to a running Chrome instance via CDP WebSocket.
"""

import asyncio
import json
import logging
import urllib.request
from typing import Any

import websockets

logger = logging.getLogger("meraki.dom")


class CDPError(Exception):
    """CDP protocol error."""
    pass


class CdpClient:
    """Low-level CDP WebSocket client."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222):
        self.host = host
        self.port = port
        self._ws = None
        self._msg_id = 0

    def _get_page_ws_url(self) -> str:
        """Get WebSocket URL for any open page."""
        url = f"http://{self.host}:{self.port}/json"
        with urllib.request.urlopen(url, timeout=5) as resp:
            targets = json.loads(resp.read())
        for t in targets:
            if t.get("type") == "page":
                return t["webSocketDebuggerUrl"]
        raise CDPError("No open page found in Chrome")

    async def connect(self) -> None:
        """Connect to Chrome CDP."""
        ws_url = self._get_page_ws_url()
        self._ws = await websockets.connect(ws_url)

    async def _send(self, method: str, params: dict | None = None) -> Any:
        """Send CDP command and return result."""
        try:
            if self._ws is None:
                raise ConnectionError
            self._msg_id += 1
            cmd = {"id": self._msg_id, "method": method}
            if params:
                cmd["params"] = params
            await self._ws.send(json.dumps(cmd))
        except (ConnectionError, Exception):
            await self.connect()
            return await self._send(method, params)
        resp = await asyncio.wait_for(self._ws.recv(), timeout=10)
        result = json.loads(resp)
        if "error" in result:
            raise CDPError(result["error"].get("message", str(result["error"])))
        return result.get("result", {})

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript and return value."""
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        return result.get("result", {}).get("value")

    async def close(self) -> None:
        try:
            if self._ws:
                await self._ws.close()
        except Exception:
            pass


# ─── Helpers ─────────────────────────────────────────────────────

def _esc(s: str) -> str:
    """Escape string for safe injection into JS single-quoted string.

    Handles backslash, single quote, and newlines so selectors
    like ``button[data-value="it's"]`` don't break the JS template.
    """
    return (
        s.replace("\\", "\\\\")
         .replace("'", "\\'")
         .replace("\n", "\\n")
    )


# ─── DOM primitives ──────────────────────────────────────────────

async def locate(
    selector: str,
    cdp: CdpClient | None = None,
) -> dict | None:
    """Locate element by CSS selector.

    Returns dict with {exists, visible, bounds, text} or None if not found.
    """
    cdp = cdp or CdpClient()
    sel = _esc(selector)
    result = await cdp.evaluate(f"""
        (() => {{
            const el = document.querySelector('{sel}');
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return {{
                exists: true,
                visible: style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0'
                    && rect.width > 0
                    && rect.height > 0,
                bounds: {{
                    x: rect.x, y: rect.y,
                    width: rect.width, height: rect.height,
                }},
                text: el.textContent?.trim().slice(0, 200),
                tag: el.tagName,
            }};
        }})()
    """)
    return result


async def is_visible(selector: str, cdp: CdpClient | None = None) -> bool:
    """Check if element is visible and interactable."""
    info = await locate(selector, cdp)
    if not info or not info.get("exists"):
        return False
    return info.get("visible", False)


async def scroll_into_view(
    selector: str,
    cdp: CdpClient | None = None,
) -> None:
    """Scroll element into view."""
    cdp = cdp or CdpClient()
    sel = _esc(selector)
    await cdp.evaluate(
        f"document.querySelector('{sel}')"
        f"?.scrollIntoView({{behavior: 'instant', block: 'center'}})"
    )


async def click(selector: str, cdp: CdpClient | None = None) -> bool:
    """Click element by selector. Returns True if element clicked."""
    cdp = cdp or CdpClient()
    sel = _esc(selector)
    result = await cdp.evaluate(f"""
        (() => {{
            const el = document.querySelector('{sel}');
            if (!el) return false;
            el.click();
            return true;
        }})()
    """)
    return bool(result)


async def get_text(selector: str, cdp: CdpClient | None = None) -> str:
    """Get text content of element."""
    cdp = cdp or CdpClient()
    sel = _esc(selector)
    result = await cdp.evaluate(
        f"document.querySelector('{sel}')?.textContent?.trim() || ''"
    )
    return result or ""


async def wait_for_element(
    selector: str,
    timeout: int = 5000,
    cdp: CdpClient | None = None,
) -> bool:
    """Wait for element to appear. Returns True if found within timeout."""
    cdp = cdp or CdpClient()
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        info = await locate(selector, cdp)
        if info and info.get("exists") and info.get("visible"):
            return True
        await asyncio.sleep(0.2)
    return False
