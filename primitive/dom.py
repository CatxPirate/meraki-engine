"""DOM-level interaction via Chrome DevTools Protocol.

Provides primitives: locate, click, scroll, is_visible.
All operations connect to a running Chrome instance via CDP WebSocket.

CdpClient now handles CDP events properly via a background listener task.
After Page.navigate, Runtime.evaluate won't return None because
execution context lifecycle is tracked and contextId is passed.
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
    """Low-level CDP WebSocket client with event handling.

    Architecture:
        - Background listener task reads all WS messages
        - Command responses matched via msg id → pending Future
        - CDP events dispatched to _handle_event()
        - Runtime.executionContextCreated/Cleared tracked for
          automatic contextId injection in evaluate()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9222):
        self.host = host
        self.port = port
        self._ws: Any = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listener_task: asyncio.Task | None = None
        self._execution_context_id: int | None = None
        self._context_ready = asyncio.Event()

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
        """Connect to Chrome CDP and start event listener."""
        ws_url = self._get_page_ws_url()
        self._ws = await websockets.connect(ws_url)
        self._listener_task = asyncio.create_task(self._listen())

        # Enable Runtime domain to receive execution context events
        await self._send_cmd("Runtime.enable")

        # Wait for initial execution context
        try:
            await asyncio.wait_for(self._context_ready.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("No initial execution context — evaluate() "
                           "will run without contextId until one arrives")

    async def _listen(self) -> None:
        """Background task: read all WS messages, dispatch responses + events."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_id = msg.get("id")

                if msg_id is not None:
                    # --- Command response ---
                    future = self._pending.pop(msg_id, None)
                    if future and not future.done():
                        if "error" in msg:
                            err = msg["error"]
                            future.set_exception(
                                CDPError(err.get("message", str(err)))
                            )
                        else:
                            future.set_result(msg.get("result", {}))
                elif "method" in msg:
                    # --- CDP event ---
                    self._handle_event(msg["method"], msg.get("params", {}))
        except websockets.ConnectionClosed:
            logger.warning("CDP WebSocket closed")
        except Exception:
            logger.exception("CDP listener crashed")

    def _handle_event(self, method: str, params: dict) -> None:
        """Dispatch CDP events to state trackers."""
        if method == "Runtime.executionContextCreated":
            ctx = params.get("context", {})
            self._execution_context_id = ctx.get("id")
            logger.debug("Execution context created: %s", self._execution_context_id)
            self._context_ready.set()

        elif method == "Runtime.executionContextsCleared":
            logger.debug("Execution contexts cleared — resetting contextId")
            self._execution_context_id = None
            self._context_ready.clear()

    async def _send_cmd(self, method: str, params: dict | None = None) -> Any:
        """Send CDP command, return result. Internal — no auto-reconnect.

        Public callers should use _send() which has reconnect logic.
        """
        if self._ws is None:
            raise CDPError("Not connected — call connect() first")

        self._msg_id += 1
        msg_id = self._msg_id
        cmd: dict = {"id": msg_id, "method": method}
        if params:
            cmd["params"] = params

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[msg_id] = future

        await self._ws.send(json.dumps(cmd))

        try:
            result = await asyncio.wait_for(future, timeout=10)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise CDPError(f"Timeout waiting for response to {method}")

    async def _send(self, method: str, params: dict | None = None) -> Any:
        """Send CDP command with auto-reconnect on connection loss.

        Maintains backward compatibility with existing callers.
        """
        try:
            return await self._send_cmd(method, params)
        except (CDPError, ConnectionError, websockets.ConnectionClosed):
            # Reconnect and retry once
            logger.debug("CDP connection lost, reconnecting...")
            await self._reconnect()
            return await self._send_cmd(method, params)

    async def _reconnect(self) -> None:
        """Close old connection and establish new one."""
        await self.close()
        await self.connect()

    async def navigate(
        self,
        url: str,
        wait_context: bool = True,
        timeout: float = 10.0,
    ) -> dict:
        """Navigate to URL and optionally wait for execution context.

        Args:
            url: Target URL (supports data:, http:, https:)
            wait_context: If True, wait for Runtime.executionContextCreated
            timeout: Max wait time for execution context (seconds)

        Returns:
            CDP Page.navigate result dict with frameId, loaderId
        """
        await self._send_cmd("Page.enable")
        result = await self._send_cmd("Page.navigate", {"url": url})

        if wait_context:
            ctx_id = await self.wait_for_execution_context(timeout=timeout)
            logger.debug("Navigated to %s, context: %s", url[:80], ctx_id)

        return result

    async def wait_for_execution_context(
        self,
        timeout: float = 5.0,
    ) -> int | None:
        """Wait for Runtime.executionContextCreated event.

        Useful after Page.navigate to ensure evaluate() works.
        Clears the context_ready event first so it waits for a
        fresh event, not a stale one before the navigate.
        """
        # If we already have a context and event is set, clear it
        # so we wait for a new one post-navigation.
        if self._context_ready.is_set():
            self._context_ready.clear()

        try:
            await asyncio.wait_for(
                self._context_ready.wait(),
                timeout=timeout,
            )
            return self._execution_context_id
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout waiting for execution context after navigation"
            )
            return None

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript and return value.

        Passes executionContextId if we have one — prevents
        Runtime.evaluate returning None after navigation.
        """
        params: dict = {
            "expression": expression,
            "returnByValue": True,
        }
        if self._execution_context_id is not None:
            params["contextId"] = self._execution_context_id

        result = await self._send("Runtime.evaluate", params)
        return result.get("result", {}).get("value")

    async def close(self) -> None:
        """Close WebSocket and cancel listener task."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        try:
            if self._ws:
                await self._ws.close()
        except Exception:
            pass
        self._ws = None
        self._pending.clear()
        self._execution_context_id = None
        self._context_ready.clear()


# ─── Helpers ─────────────────────────────────────────────────────

def _esc(s: str) -> str:
    """Escape string for safe injection into JS single-quoted string.

    Handles backslash, single quote, and newlines so selectors
    like ``button[data-value="it's"]`` don't break the JS template.
    """
    return (
        s.replace("\\", "\\\\\\\\")
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
