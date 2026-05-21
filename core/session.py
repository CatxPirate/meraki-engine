"""Persistent Chrome session management.

Manages Chrome process lifecycle for named profiles. Each Session
runs its own Chrome instance with isolated user-data-dir and CDP port.

Architecture:
    Session.launch()  → starts Chrome process
    Session.close()   → gracefully terminates Chrome
    Session.port      → CDP port for bridge connection
    Session.is_alive()→ check if Chrome is running

Dependencies: Xvfb must be running on :99 (managed by PM2).
"""
import os
import signal
import subprocess
import time
import json
import logging
import socket
from pathlib import Path
from typing import Optional

logger = logging.getLogger("meraki.session")

# Base directory for all Chrome profiles
PROFILES_ROOT = Path("/root/chrome-profiles")

# Chrome binary
CHROME_BIN = "/opt/google/chrome/chrome"

# Base CDP port — sessions get 9223, 9224, ...
BASE_CDP_PORT = 9222

# Default flags shared across all sessions
SHARED_FLAGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-dbus",
    "--proxy-server=http://127.0.0.1:16666",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--lang=id-ID",
    "--accept-lang=id-ID,id,en-US,en",
    "--start-maximized",
    "--no-first-run",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-default-apps",
]

# Files to clean up from profile dir on stale lock
_LOCK_FILES = ["SingletonLock", "SingletonCookie", "SingletonSocket"]


def _find_free_port(start: int = BASE_CDP_PORT + 1) -> int:
    """Find a free TCP port starting from `start`."""
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free ports found in range")


def _check_port(port: int) -> bool:
    """Check if a port is listening (Chrome CDP ready)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _kill_processes_using_dir(profile_dir: Path) -> int:
    """Kill all processes using a profile directory. Returns count killed."""
    count = 0
    try:
        result = subprocess.run(
            ["fuser", str(profile_dir)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split()
            for pid_str in pids:
                try:
                    pid = int(pid_str)
                    os.kill(pid, signal.SIGKILL)
                    count += 1
                except (ValueError, ProcessLookupError):
                    pass
            if count:
                time.sleep(1)  # Let OS clean up
    except Exception as e:
        logger.debug("fuser failed: %s", e)
    return count


def _clean_lock_files(profile_dir: Path):
    """Remove Chrome singleton lock files from profile directory."""
    for name in _LOCK_FILES:
        lock_path = profile_dir / name
        if lock_path.exists():
            try:
                lock_path.unlink()
                logger.debug("Removed %s", lock_path)
            except OSError as e:
                logger.warning("Failed to remove %s: %s", lock_path, e)


class Session:
    """Manage a named Chrome profile session.

    Usage:
        session = Session("my_user")
        port = session.launch()
        # ... use CDP on port ...
        session.close()
    """

    def __init__(self, profile_name: str, port: Optional[int] = None):
        self.profile_name = profile_name
        self.profile_dir = PROFILES_ROOT / profile_name
        self.port = port or _find_free_port()
        self._process: Optional[subprocess.Popen] = None

    def _cleanup_if_stale(self):
        """If profile has lock files but no running Chrome, clean up."""
        singleton = self.profile_dir / "SingletonLock"
        if not singleton.exists():
            return

        # Check if any Chrome is actually using this profile
        if _check_port(self.port):
            logger.debug("Session '%s' appears alive on port %d",
                         self.profile_name, self.port)
            return

        # Stale — kill any lingering processes and clean locks
        logger.warning("Stale lock detected for session '%s', cleaning up",
                       self.profile_name)
        killed = _kill_processes_using_dir(self.profile_dir)
        _clean_lock_files(self.profile_dir)
        if killed:
            logger.info("Killed %d stale processes for session '%s'",
                        killed, self.profile_name)
        # Wait for port to free
        time.sleep(1)

    def launch(self) -> int:
        """Start Chrome with this session's profile.

        Creates profile directory if needed. Starts Chrome
        in background. Waits for CDP port to become ready.

        Returns:
            CDP port number (int).

        Raises:
            RuntimeError: if Chrome fails to start or CDP not ready.
        """
        if self.is_alive():
            logger.warning("Session '%s' already running on port %d",
                           self.profile_name, self.port)
            return self.port

        # Clean up stale locks/processes from previous crash
        self._cleanup_if_stale()

        # Ensure profile directory exists
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # Build Chrome command
        cmd = [
            CHROME_BIN,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--window-size=1920,1080",
            *SHARED_FLAGS,
            "about:blank",  # Start with blank page
        ]

        env = os.environ.copy()
        env["DISPLAY"] = ":99"
        env["LANG"] = "id_ID.UTF-8"
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)  # Avoid D-Bus issues

        logger.info("Launching Chrome for session '%s' on port %d",
                    self.profile_name, self.port)
        logger.debug("Profile dir: %s", self.profile_dir)

        try:
            self._process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to start Chrome for session '{self.profile_name}': {e}"
            )

        # Wait for CDP port to become available
        deadline = time.time() + 30  # fresh profile + proxy may be slow
        while time.time() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"Chrome exited prematurely (code {self._process.returncode}) "
                    f"for session '{self.profile_name}'"
                )
            if _check_port(self.port):
                logger.info("Session '%s' ready on port %d (pid %d)",
                            self.profile_name, self.port, self._process.pid)
                return self.port
            time.sleep(0.5)

        # Timeout — kill the Chrome process to avoid orphan
        logger.error("Chrome CDP port %d not ready after 30s for session '%s', killing",
                     self.port, self.profile_name)
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
            self._process.wait(timeout=3)
        except Exception:
            pass
        self._process = None
        raise RuntimeError(
            f"Chrome CDP port {self.port} not ready after 30s "
            f"for session '{self.profile_name}'"
        )

    def close(self, timeout: float = 10.0) -> bool:
        """Gracefully close Chrome session.

        Sends SIGTERM, waits for graceful shutdown, then
        force-kills if still running after timeout.
        Cleans up lock files on successful exit.

        Returns:
            True if Chrome exited cleanly, False if force-killed.
        """
        if self._process is None:
            logger.debug("Session '%s' not running, nothing to close",
                         self.profile_name)
            _clean_lock_files(self.profile_dir)
            return True

        pid = self._process.pid
        logger.info("Closing session '%s' (pid %d, port %d)",
                    self.profile_name, pid, self.port)

        # Send SIGTERM to the process group
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            logger.debug("Process %d already gone", pid)
            self._process = None
            _clean_lock_files(self.profile_dir)
            return True

        # Wait for graceful exit
        clean = True
        try:
            self._process.wait(timeout=timeout)
            logger.info("Session '%s' exited cleanly", self.profile_name)
        except subprocess.TimeoutExpired:
            logger.warning("Session '%s' did not exit, force-killing",
                           self.profile_name)
            clean = False
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                self._process.wait(timeout=2)
            except Exception:
                pass

        self._process = None

        # Clean up lock files
        _clean_lock_files(self.profile_dir)
        # Also ensure no lingering processes
        time.sleep(0.5)
        _kill_processes_using_dir(self.profile_dir)

        return clean

    def is_alive(self) -> bool:
        """Check if Chrome process is running and CDP port is listening."""
        if self._process is None:
            return False
        if self._process.poll() is not None:
            self._process = None
            return False
        return _check_port(self.port)

    def pid(self) -> Optional[int]:
        """Get Chrome process PID, or None if not running."""
        if self._process and self._process.poll() is None:
            return self._process.pid
        return None

    def cdp_url(self) -> str:
        """Get CDP WebSocket debugger URL."""
        return f"ws://127.0.0.1:{self.port}"


def list_sessions() -> list[dict]:
    """List all existing profiles on disk.

    Returns list of {profile_name, profile_dir, has_chrome_running}.
    """
    if not PROFILES_ROOT.exists():
        return []

    sessions = []
    for d in PROFILES_ROOT.iterdir():
        if d.is_dir():
            singleton = d / "SingletonLock"
            has_lock = singleton.exists()
            sessions.append({
                "profile_name": d.name,
                "profile_dir": str(d),
                "has_chrome_running": has_lock,
                "size_mb": _dir_size_mb(d),
            })
    return sessions


def _dir_size_mb(path: Path) -> float:
    """Calculate directory size in MB."""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except PermissionError:
        pass
    return round(total / (1024 * 1024), 1)
