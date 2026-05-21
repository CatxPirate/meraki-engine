"""Session client — manages Chrome sessions on executor via SSH.

Provides Python API matching core.session.Session but running
commands on the executor VPS via SSH.

Usage:
    from bridge.session_client import SessionClient

    client = SessionClient()
    port = client.launch("my_user")
    print(f"Session on port {port}")

    # Use with Operator:
    from bridge.operator import Operator
    op = Operator(remote_cdp_port=port)
    await op.navigate("https://example.com")

    client.close("my_user")
"""
import json
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("meraki.bridge.session_client")

EXECUTOR_HOST = "62.146.235.5"
EXECUTOR_USER = "root"
SSH_KEY = "/home/ubuntu/.ssh/executor_key"
MERAKI_ROOT = "/root/meraki-engine"

_SSH_BASE = [
    "ssh", "-i", SSH_KEY,
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    f"{EXECUTOR_USER}@{EXECUTOR_HOST}",
]


def _ssh_exec(python_code: str, timeout: int = 30) -> str:
    """Execute Python code on executor via SSH. Returns stdout."""
    cmd = _SSH_BASE + [
        "python3", "-c",
        f"import sys; sys.path.insert(0, '{MERAKI_ROOT}'); {python_code}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"SSH command failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


class SessionClient:
    """Manage Chrome sessions on the executor VPS."""

    @staticmethod
    def launch(profile_name: str) -> int:
        """Launch a Chrome session for the given profile.

        Returns:
            CDP port number (int).
        """
        code = (
            f"from core.session import Session; "
            f"s = Session({repr(profile_name)}); "
            f"port = s.launch(); "
            f"print(port)"
        )
        output = _ssh_exec(code)
        port = int(output.strip())
        logger.info("Session '%s' launched on port %d", profile_name, port)
        return port

    @staticmethod
    def close(profile_name: str, timeout: float = 10.0) -> bool:
        """Close a Chrome session for the given profile.

        Returns:
            True if closed cleanly.
        """
        code = (
            f"from core.session import Session; "
            f"s = Session({repr(profile_name)}); "
            f"ok = s.close(timeout={timeout}); "
            f"print(str(ok).lower())"
        )
        output = _ssh_exec(code)
        ok = output.strip() == "true"
        logger.info("Session '%s' closed (clean=%s)", profile_name, ok)
        return ok

    @staticmethod
    def is_alive(profile_name: str) -> bool:
        """Check if a Chrome session is running."""
        code = (
            f"from core.session import Session; "
            f"s = Session({repr(profile_name)}); "
            f"print(str(s.is_alive()).lower())"
        )
        output = _ssh_exec(code)
        return output.strip() == "true"

    @staticmethod
    def list_sessions() -> list[dict]:
        """List all existing sessions on disk."""
        code = (
            "from core.session import list_sessions; "
            "import json; "
            "print(json.dumps(list_sessions()))"
        )
        output = _ssh_exec(code)
        return json.loads(output)

    @staticmethod
    def get_port(profile_name: str) -> Optional[int]:
        """Get the CDP port for a session. Returns None if not running."""
        code = (
            f"from core.session import Session; "
            f"s = Session({repr(profile_name)}); "
            f"if s.is_alive(): print(s.port); "
            f"else: print('NONE')"
        )
        output = _ssh_exec(code)
        if output.strip() == "NONE":
            return None
        return int(output.strip())
