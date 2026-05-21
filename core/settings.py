"""Meraki Engine — core settings.

Overridable via environment variables.
"""

import os

# Proxy port for Chrome --proxy-server flag
PROXY_PORT = int(os.getenv("PROXY_PORT", "16666"))
