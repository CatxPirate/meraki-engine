"""SSH tunnel lifecycle for CDP connection to executor VPS.

Manages SSH tunnels: localhost:<local> → executor:<remote>.
Used by bridge.operator to ensure CDP is accessible before operations.

Also auto-loads GEMINI_API_KEY from ~/meraki-engine/.env if available.

Port mapping:   local_port = 17000 + remote_port
                e.g., :9222→:19222, :9223→:19223
"""
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

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
REMOTE_PORT = 9222
LOCAL_PORT = 19222

TUNNEL_OFFSET = 17000  # local = offset + remote


def _local_for(remote_port: int) -> int:
    """Map remote CDP port to local tunnel port."""
    return TUNNEL_OFFSET + remote_port


def _marker_for(remote_port: int) -> str:
    """Pgrep marker for a specific tunnel."""
    local = _local_for(remote_port)
    return f"{local}:.*:{remote_port}"


def is_up(remote_port: int = REMOTE_PORT) -> bool:
    """Check if SSH tunnel for a specific port is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", _marker_for(remote_port)],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def start(remote_port: int = REMOTE_PORT):
    """Start SSH tunnel for a specific remote port. Idempotent."""
    if is_up(remote_port):
        logger.debug("Tunnel for port %d already running", remote_port)
        return

    local = _local_for(remote_port)
    logger.info("Starting SSH tunnel :%d -> %s:%d",
                local, EXECUTOR_HOST, remote_port)

    subprocess.Popen(
        [
            "ssh", "-i", SSH_KEY,
            "-N", "-L", f"{local}:127.0.0.1:{remote_port}",
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
        if is_up(remote_port):
            logger.info("Tunnel :%d ready", local)
            return

    raise RuntimeError(f"Tunnel for port {remote_port} failed to start after 5s")


def stop(remote_port: Optional[int] = None):
    """Kill SSH tunnel(s). If port is None, kill all CDP tunnels."""
    if remote_port is not None:
        marker = _marker_for(remote_port)
    else:
        marker = f"{TUNNEL_OFFSET}:.*:"
    try:
        subprocess.run(
            ["pkill", "-f", marker],
            capture_output=True, timeout=5
        )
        logger.info("Tunnel stopped (port=%s)", remote_port or "all")
    except Exception as e:
        logger.warning("Failed to stop tunnel: %s", e)


def ensure(remote_port: int = REMOTE_PORT):
    """Ensure tunnel for a specific port is running. Start if not."""
    if not is_up(remote_port):
        start(remote_port)


def local_port(remote_port: int = REMOTE_PORT) -> int:
    """Get the local tunnel port for a given remote CDP port."""
    return _local_for(remote_port)


def all_tunnels() -> list[int]:
    """List remote ports that have active tunnels."""
    try:
        result = subprocess.run(
            ["pgrep", "-a", "-f", f"{TUNNEL_OFFSET}:.*:"],
            capture_output=True, text=True, timeout=5
        )
        ports = set()
        for line in result.stdout.strip().split("\n"):
            import re
            m = re.search(rf"{TUNNEL_OFFSET}\+(\d+)", line)
            if m:
                continue
            # Parse -L local:host:remote from command line
            m2 = re.search(r"-L\s+(\d+):[^:]+:(\d+)", line)
            if m2:
                ports.add(int(m2.group(2)))
        return sorted(ports)
    except Exception:
        return []
