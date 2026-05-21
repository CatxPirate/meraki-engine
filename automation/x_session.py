"""X.com session lifecycle — restore, verify, warmup, save.

Pure session management. No business actions (follow/like/RT) —
those go in x_actions.py.

Architecture:
    restore → verify → warmup → ready
    ensure() handles the full flow + login recovery trigger

Session files live on the executor (where Chrome and cookies reside).
File I/O uses _ssh_exec callable to run Python code remotely.
"""

import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from primitive.dom import CdpClient
from primitive.gesture import GestureSimulator, warmup_browse

logger = logging.getLogger(__name__)

# Default session directory on executor
DEFAULT_SESSION_DIR = "/root/chrome-profiles/test/sessions"


class XSessionManager:
    """Manage X.com session lifecycle.

    Usage:
        from bridge.session_client import _ssh_exec
        mgr = XSessionManager(cdp, ssh_exec=_ssh_exec)
        ok = await mgr.ensure("myhandle")
        if ok:
            await mgr.save("myhandle")
    """

    def __init__(
        self,
        cdp: CdpClient,
        ssh_exec: Callable | None = None,
        session_dir: str = DEFAULT_SESSION_DIR,
    ):
        self._cdp = cdp
        self._ssh = ssh_exec
        self.session_dir = session_dir
        self._verified = False
        self._handle: str | None = None

        # Ensure session dir exists on executor
        if self._ssh:
            self._ssh(
                f"import os; os.makedirs({self.session_dir!r}, exist_ok=True)"
            )

    # ------------------------------------------------------------------
    # Session file I/O
    # ------------------------------------------------------------------

    def _session_path(self, handle: str) -> str:
        return f"{self.session_dir}/x_{handle}.json"

    def _load_session(self, handle: str) -> dict | None:
        """Load session from executor disk via _ssh_exec."""
        if not self._ssh:
            logger.error("Cannot load session — no ssh_exec configured")
            return None
        path = self._session_path(handle)
        result = self._ssh(f"""
import json, base64
try:
    with open({path!r}) as f:
        data = json.load(f)
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    print("SESSION_OK:" + encoded)
except FileNotFoundError:
    print("SESSION_MISSING")
except Exception as e:
    print("SESSION_ERR:" + str(e))
""")
        if "SESSION_MISSING" in result:
            return None
        if "SESSION_ERR" in result:
            logger.warning("Session load error: %s", result)
            return None
        # Extract base64-encoded JSON
        marker = "SESSION_OK:"
        idx = result.index(marker)
        b64_str = result[idx + len(marker):].strip().split()[0]
        try:
            json_bytes = base64.b64decode(b64_str)
            return json.loads(json_bytes)
        except Exception:
            logger.warning("Failed to decode session JSON")
            return None

    # ------------------------------------------------------------------
    # Cookie injection
    # ------------------------------------------------------------------

    async def _inject_cookies(self, cookies: list[dict]) -> int:
        """Inject cookies via CDP Network.setCookie. Returns count."""
        count = 0
        for c in cookies:
            try:
                await self._cdp._send_cmd("Network.setCookie", {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                    "sameSite": c.get("sameSite", "Lax"),
                })
                count += 1
            except Exception as e:
                logger.debug("Cookie %s failed: %s", c.get("name"), e)
        return count

    # ------------------------------------------------------------------
    # Session restore
    # ------------------------------------------------------------------

    async def restore(self, handle: str) -> bool:
        """Restore session from saved file.

        Flow:
            1. Load session JSON
            2. Navigate to about:blank
            3. Inject all cookies via CDP
            4. Navigate to x.com/home
            5. Verify auth state
            6. If verified → warmup → return True

        Returns True if session restored and verified.
        """
        self._handle = handle
        self._verified = False

        data = self._load_session(handle)
        if not data:
            logger.info("No saved session for %s", handle)
            return False

        cookies = data.get("cookies", [])
        if not cookies:
            logger.warning("Session file has no cookies: %s", handle)
            return False

        # Check age — older than 7 days is suspicious
        extracted_at = data.get("extracted_at", "")
        if extracted_at:
            try:
                ts = datetime.fromisoformat(extracted_at)
                age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                if age_hours > 168:  # 7 days
                    logger.warning("Session %s is %0.0fh old — may be stale", handle, age_hours)
            except ValueError:
                pass

        # Inject
        await self._cdp._send_cmd("Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(0.5)

        injected = await self._inject_cookies(cookies)
        logger.debug("Injected %d/%d cookies for %s", injected, len(cookies), handle)

        # Navigate to X
        await self._cdp._send_cmd("Page.navigate", {"url": "https://x.com/home"})
        await asyncio.sleep(4)

        # Verify
        if await self.verify():
            await self.warmup(duration=30)
            return True

        logger.warning("Session restore failed verification for %s", handle)
        return False

    # ------------------------------------------------------------------
    # Multi-signal verification
    # ------------------------------------------------------------------

    async def verify(self) -> bool:
        """Verify X authentication via multi-signal check.

        Signals (need >= 2 of 3 to pass):
            1. auth_token cookie exists
            2. Timeline/home element found in DOM
            3. URL is x.com/home (not /login or /i/flow)

        Also checks:
            - "Post" button presence (bonus signal)
            - Account menu / avatar (bonus signal)
        """
        # Signal 1: auth_token cookie
        cookies_resp = await self._cdp._send_cmd("Network.getCookies", {
            "urls": ["https://x.com"]
        })
        cookie_names = {c["name"] for c in cookies_resp.get("cookies", [])}
        has_auth = "auth_token" in cookie_names

        # Signals 2-3 + bonus: DOM state
        dom_state = await self._cdp.evaluate("""
            (() => {
                const signals = {};
                signals.title = document.title || "";
                signals.url = location.href;
                signals.timeline = !!(
                    document.querySelector('[data-testid="primaryColumn"]') ||
                    document.querySelector('[aria-label="Home timeline"]') ||
                    document.querySelector('div[aria-label*="Timeline"]')
                );
                signals.postButton = !!(
                    document.querySelector('[data-testid="tweetButtonInline"]') ||
                    document.querySelector('[aria-label="Post"]')
                );
                signals.accountMenu = !!(
                    document.querySelector('[data-testid="AccountSwitcher"]') ||
                    document.querySelector('[aria-label="Account menu"]')
                );
                signals.loginForm = !!(
                    document.querySelector('input[name="text"][autocomplete="username"]') ||
                    location.href.includes('/login') ||
                    location.href.includes('/i/flow')
                );
                return signals;
            })()
        """)

        title = dom_state.get("title", "")
        url = dom_state.get("url", "")
        has_timeline = dom_state.get("timeline", False)
        has_post = dom_state.get("postButton", False)
        has_account = dom_state.get("accountMenu", False)
        is_login = dom_state.get("loginForm", False)

        # Score: 3 primary signals + 2 bonus
        score = 0

        if has_auth:
            score += 1
        if has_timeline:
            score += 1
        if url and "/home" in url and not is_login:
            score += 1

        # Bonus (push over threshold, not replace)
        if has_post:
            score += 0.5
        if has_account:
            score += 0.5

        passed = score >= 2.0 and not is_login

        logger.info(
            "X verify: auth=%s timeline=%s url_ok=%s post=%s acct=%s "
            "login=%s → score=%0.1f → %s",
            has_auth, has_timeline, url and "/home" in url,
            has_post, has_account, is_login,
            score, "PASS" if passed else "FAIL",
        )

        self._verified = passed
        return passed

    # ------------------------------------------------------------------
    # Warmup — natural browsing
    # ------------------------------------------------------------------

    async def warmup(self, duration: float = 30.0) -> None:
        """Warm up session with natural browsing after restore.

        Scrolls timeline, hovers tweets — makes session look human
        before any automated actions begin.
        """
        if not self._verified:
            logger.warning("Warmup skipped — session not verified")
            return
        await warmup_browse(self._cdp, total_duration=duration)

    # ------------------------------------------------------------------
    # Session save
    # ------------------------------------------------------------------

    async def save(self, handle: str | None = None) -> str:
        """Extract cookies from live CDP, save to executor via _ssh_exec.

        Returns path to saved session file (on executor).
        """
        handle = handle or self._handle
        if not handle:
            raise ValueError("No handle provided for session save")
        if not self._ssh:
            raise RuntimeError("Cannot save session — no ssh_exec configured")

        # Extract cookies for X domains
        cookies_resp = await self._cdp._send_cmd("Network.getCookies", {
            "urls": ["https://x.com", "https://twitter.com"]
        })

        cookies = cookies_resp.get("cookies", [])

        session = {
            "version": 2,
            "handle": handle,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "verified": self._verified,
            "cookies": [
                {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                    "sameSite": c.get("sameSite", "Lax"),
                    "expires": c.get("expires"),
                }
                for c in cookies
            ],
        }

        path = self._session_path(handle)

        # Write via scp (avoids shell injection / Python-syntax-in-shell hell)
        session_json = json.dumps(session)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(session_json)
            tmp = f.name

        subprocess.run([
            "scp", "-i", "/home/ubuntu/.ssh/executor_key",
            "-o", "ConnectTimeout=10",
            tmp, f"root@62.146.235.5:{path}"
        ], capture_output=True, timeout=15, check=True)
        os.unlink(tmp)

        self._ssh(f"import os, stat; os.chmod({path!r}, stat.S_IRUSR | stat.S_IWUSR)")

        logger.info("Session saved: %s (%d cookies)", path, len(cookies))
        return path

    # ------------------------------------------------------------------
    # Ensure — unified entry point
    # ------------------------------------------------------------------

    async def ensure(self, handle: str, *, warmup_duration: float = 30.0) -> bool:
        """Ensure authenticated X session — restore or trigger login.

        Flow:
            1. Try restore from saved session
            2. If verify fails → mark stale, trigger login recovery
            3. If verify passes → warmup → ready

        Returns True if authenticated session is ready for actions.
        Login recovery is signalled via return value + logging —
        caller is responsible for triggering x_login flow.
        """
        logger.info("Ensuring session for %s...", handle)

        # Try restore
        restored = await self.restore(handle)

        if restored and self._verified:
            logger.info("Session ready: %s ✅", handle)
            return True

        # Restore failed — need login
        logger.warning("Session %s needs login — restore failed", handle)
        return False

    @property
    def is_verified(self) -> bool:
        return self._verified

    @property
    def handle(self) -> str | None:
        return self._handle
