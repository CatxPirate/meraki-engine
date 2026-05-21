"""
Human confirm channel via Telegram Bot API.

When automated fallbacks are exhausted, this sends a confirmation
request to the human operator via Telegram and waits for a reply.

Architecture:
    Injected into retry.py by core/session layer.
    Engine does NOT hard-depend on core.

Environment:
    HUMAN_CONFIRM_BOT_TOKEN — Telegram bot token (required)
    HUMAN_CONFIRM_CHAT_ID   — Telegram chat ID to message (required)

Usage:
    channel = HumanConfirmChannel()
    result = await channel.request_confirm(
        action_name="click('#submit')",
        last_error="Element not found after 3 retries",
    )
    # True  → user replied "yes" → retry the action
    # False → user replied "no"  → abort
    # None  → timeout           → auto-abort
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

from meraki_engine.config.settings import Settings

logger = logging.getLogger("meraki.human")

# ── Telegram API helpers ─────────────────────────────────────────

async def _telegram_api(
    session,
    method: str,
    payload: dict,
    bot_token: str,
) -> dict:
    """Call Telegram Bot API method. Returns JSON response."""
    try:
        import aiohttp
    except ImportError:
        logger.error("aiohttp not installed — cannot call Telegram API")
        return {"ok": False, "description": "aiohttp not installed"}

    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            return await resp.json()
    except Exception as e:
        logger.error("Telegram API call failed: %s", e)
        return {"ok": False, "description": str(e)}


async def _poll_reply(
    session,
    bot_token: str,
    chat_id: int,
    sent_message_id: int,
    timeout: int,
) -> "bool | None":
    """Poll getUpdates for a reply to sent_message_id.

    Returns:
        True if user replied "yes"
        False if user replied "no" or anything else
        None on timeout
    """
    try:
        import aiohttp
    except ImportError:
        return None

    deadline = asyncio.get_running_loop().time() + timeout
    offset = 0

    logger.info(
        "[human] polling for reply (timeout=%ds, chat=%s, msg=%s)",
        timeout, chat_id, sent_message_id,
    )

    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break

        poll_timeout = min(remaining, 30)

        url = (
            f"https://api.telegram.org/bot{bot_token}/getUpdates"
            f"?offset={offset}&timeout={int(poll_timeout)}"
        )
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=poll_timeout + 5)
            ) as resp:
                data = await resp.json()
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.warning("[human] getUpdates error: %s", e)
            await asyncio.sleep(1)
            continue

        if not data.get("ok"):
            continue

        for update in data.get("result", []):
            update_id = update.get("update_id", 0)
            offset = max(offset, update_id + 1)

            msg = update.get("message") or update.get("channel_post")
            if not msg:
                continue

            # Check if this is a reply to our sent message
            reply_to = msg.get("reply_to_message")
            if not reply_to or reply_to.get("message_id") != sent_message_id:
                continue

            # Check it is from the right chat
            msg_chat = msg.get("chat", {})
            if str(msg_chat.get("id")) != str(chat_id):
                continue

            text = (msg.get("text") or "").strip().lower()
            logger.info("[human] got reply: '%s' from chat %s", text, chat_id)

            if text in ("yes", "y", "ya", "ok", "lanjut", "gas", "go"):
                return True
            elif text in ("no", "n", "tidak", "stop", "abort", "batal"):
                return False
            else:
                logger.warning(
                    "[human] unrecognized reply '%s' — treating as abort",
                    text,
                )
                return False

    logger.warning("[human] poll timeout — no reply received")
    return None


# ── Channel class ────────────────────────────────────────────────

class HumanConfirmChannel:
    """Telegram-based human confirm channel.

    Injected into RetryOrchestrator by core/session layer.
    Falls back to auto-abort if Telegram is not configured.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | int | None = None,
        settings: Settings | None = None,
    ):
        self._bot_token = bot_token or os.getenv("HUMAN_CONFIRM_BOT_TOKEN", "")
        self._chat_id = str(chat_id or os.getenv("HUMAN_CONFIRM_CHAT_ID", ""))
        self.settings = settings or Settings()

    @property
    def configured(self) -> bool:
        """Check if Telegram is properly configured."""
        return bool(self._bot_token and self._chat_id)

    async def request_confirm(
        self,
        action_name: str,
        last_error: str,
    ) -> "bool | None":
        """Request human confirmation via Telegram.

        Sends a message describing what failed, then waits for
        a reply. The reply is parsed as yes/no.

        Args:
            action_name: label of the failed action
            last_error: description of what went wrong

        Returns:
            True  → human says retry
            False → human says abort
            None  → timeout / no Telegram configured → auto-abort
        """
        if not self.configured:
            logger.warning(
                "[human] Telegram not configured — auto-aborting '%s'",
                action_name,
            )
            return None

        import aiohttp

        timeout = self.settings.human_confirm_timeout
        now = datetime.now().strftime("%H:%M:%S")

        text = (
            "🤖 *Meraki Engine — Human Confirm Required*\n\n"
            f"*Action:* `{action_name}`\n"
            f"*Error:* {last_error}\n"
            f"*Time:* {now}\n\n"
            "Reply *yes* to retry, *no* to abort.\n"
            f"⏳ Auto-abort in {timeout}s if no reply."
        )

        async with aiohttp.ClientSession() as session:
            # Step 1: Send confirmation message
            send_resp = await _telegram_api(
                session,
                "sendMessage",
                {
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                self._bot_token,
            )

            if not send_resp.get("ok"):
                logger.error(
                    "[human] failed to send message: %s",
                    send_resp.get("description", "unknown"),
                )
                return None

            sent_msg_id = send_resp.get("result", {}).get("message_id")
            logger.info(
                "[human] sent confirm request msg_id=%s for '%s'",
                sent_msg_id, action_name,
            )

            # Step 2: Poll for reply
            result = await _poll_reply(
                session,
                self._bot_token,
                int(self._chat_id),
                sent_msg_id,
                timeout,
            )

        if result is True:
            logger.info(
                "[human] CONFIRMED — user approved retry for '%s'",
                action_name,
            )
        elif result is False:
            logger.info(
                "[human] ABORTED — user rejected '%s'", action_name,
            )
        else:
            logger.warning(
                "[human] TIMEOUT — auto-aborting '%s'", action_name,
            )

        return result
