"""SSH tunnel lifecycle for CDP connection to executor VPS.

Manages the SSH tunnel: localhost:19222 → executor:9222.
Used by bridge.operator to ensure CDP is accessible before operations.

Also auto-loads GEMINI_API_KEY from ~/meraki-engine/.env if available.
"""
import os
import subprocess
import time
import logging
from pathlib import Path

logger = logging.getLogger("meraki.bridge.tunnel")

# Auto-load API key from .env (needed for vision operations)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key and "GEMINI_API_KEY" not in os.environ:
                os.environ["GEMINI_API_KEY"] = key
                logger.debug("Loaded GEMINI_API_KEY from %s", _ENV_PATH)
            break

EXECUTOR_HOST = "62.146.235.5"
EXECUTOR_USER = "root"
SSH_KEY = "/home/ubuntu/.ssh/executor_key"
LOCAL_PORT = 19222
REMOTE_PORT = 9222

TUNNEL_MARKER = f"{LOCAL_PORT}:.*:{REMOTE_PORT}"


def is_up() -> bool:
    """Check if SSH tunnel process is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", TUNNEL_MARKER],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def start():
    """Start SSH tunnel in background. Idempotent."""
    if is_up():
        logger.info("Tunnel already running")
        return

    logger.info("Starting SSH tunnel %s:%d -> %s:%d",
                "127.0.0.1", LOCAL_PORT, EXECUTOR_HOST, REMOTE_PORT)

    subprocess.Popen(
        [
            "ssh", "-i", SSH_KEY,
            "-N", "-L", f"{LOCAL_PORT}:127.0.0.1:{REMOTE_PORT}",
            "-o", "ServerAliveInterval=60",
            "-o", "ServerAliveCountMax=3",
            "-o", "ExitOnForwardFailure=yes",
            f"{EXECUTOR_USER}@{EXECUTOR_HOST}"
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for tunnel to be ready
    for _ in range(10):
        time.sleep(0.5)
        if is_up():
            logger.info("Tunnel ready")
            return

    raise RuntimeError("Tunnel failed to start after 5s")


def stop():
    """Kill SSH tunnel process."""
    try:
        subprocess.run(
            ["pkill", "-f", TUNNEL_MARKER],
            capture_output=True, timeout=5
        )
        logger.info("Tunnel stopped")
    except Exception as e:
        logger.warning("Failed to stop tunnel: %s", e)


def ensure():
    """Ensure tunnel is running. Start if not."""
    if not is_up():
        start()
