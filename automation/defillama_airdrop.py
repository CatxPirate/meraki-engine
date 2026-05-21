"""DeFiLlama airdrop eligibility checker — HTTP API.

Endpoint: https://airdrops.llama.fi/check/{address}
No browser automation required.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger("meraki.automation.defillama")

AIRDROP_API = "https://airdrops.llama.fi/check"
CONFIG_API = "https://airdrops.llama.fi/config"

# Module-level config cache
_config_cache = None


async def _get_config(session) -> dict:
    """Fetch protocol config (id→name mapping). Cached in memory."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    import aiohttp
    url = CONFIG_API
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                _config_cache = await resp.json()
                return _config_cache
    except Exception as e:
        logger.warning("Failed to fetch airdrop config: %s", e)
    return {}


def _resolve_names(protocols: dict, config: dict) -> list[dict]:
    """Map protocol IDs to human-readable names using config."""
    result = []
    for proto_id, amount in protocols.items():
        info = config.get(proto_id, {})
        result.append({
            "id": proto_id,
            "name": info.get("name", proto_id),
            "symbol": info.get("tokenSymbol", ""),
            "amount": amount,
        })
    return result


async def check_airdrop(address: str, session=None) -> dict:
    """Check wallet address for unclaimed airdrops via DeFiLlama API.

    Args:
        address: EVM or Solana address
        session: Optional aiohttp.ClientSession (reuse for batch)

    Returns:
        {
            "address": str,
            "found": bool,
            "protocols": [{"id", "name", "symbol", "amount"}, ...],
            "raw": dict | None,
            "timestamp": str,
            "error": str | None,
        }
    """
    result = {
        "address": address,
        "found": False,
        "protocols": [],
        "raw": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    import aiohttp
    close_session = session is None

    try:
        if session is None:
            session = aiohttp.ClientSession()

        # Fetch data + config in parallel
        async with session.get(
            f"{AIRDROP_API}/{address}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                result["error"] = f"HTTP {resp.status}"
                return result
            data = await resp.json()

        result["raw"] = data
        protocols = data.get(address.lower()) or data.get(address)
        result["raw"] = data

        if protocols and isinstance(protocols, dict):
            config = await _get_config(session)
            result["found"] = True
            result["protocols"] = _resolve_names(protocols, config)

        return result

    except Exception as e:
        logger.error("check_airdrop(%s) failed: %s", address[:10], e)
        result["error"] = str(e)
        return result

    finally:
        if close_session and session:
            await session.close()


async def batch_check(addresses: list[str]) -> list[dict]:
    """Check multiple addresses in one API call (comma-separated)."""
    import aiohttp

    results = []
    comma_addr = ",".join(addresses)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{AIRDROP_API}/{comma_addr}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return [
                        {
                            "address": a,
                            "found": False,
                            "protocols": [],
                            "error": f"HTTP {resp.status}",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        for a in addresses
                    ]
                data = await resp.json()

            config = await _get_config(session)

        for addr in addresses:
            entry = data.get(addr.lower(), data.get(addr))
            r = {
                "address": addr,
                "found": False,
                "protocols": [],
                "raw": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": None,
            }
            if entry and isinstance(entry, dict):
                r["found"] = True
                r["protocols"] = _resolve_names(entry, config)
            results.append(r)

        return results

    except Exception as e:
        logger.error("batch_check failed: %s", e)
        return [
            {
                "address": a,
                "found": False,
                "protocols": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            for a in addresses
        ]


def check_airdrop_sync(address: str) -> dict:
    """Synchronous wrapper using requests (no aiohttp dependency)."""
    import json
    try:
        import requests
    except ImportError:
        import urllib.request
        import urllib.error

    result = {
        "address": address,
        "found": False,
        "protocols": [],
        "raw": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    try:
        try:
            import requests
            resp = requests.get(f"{AIRDROP_API}/{address}", timeout=10)
            data = resp.json()
        except ImportError:
            req = urllib.request.Request(f"{AIRDROP_API}/{address}")
            with urllib.request.urlopen(req, timeout=10) as f:
                data = json.loads(f.read().decode())

        result["raw"] = data
        protocols = data.get(address.lower()) or data.get(address)

        if protocols and isinstance(protocols, dict):
            result["found"] = True
            # Config fetch (sync)
            try:
                try:
                    c_resp = requests.get(CONFIG_API, timeout=10)
                    config = c_resp.json()
                except ImportError:
                    c_req = urllib.request.Request(CONFIG_API)
                    with urllib.request.urlopen(c_req, timeout=10) as f:
                        config = json.loads(f.read().decode())
                result["protocols"] = _resolve_names(protocols, config)
            except Exception:
                result["protocols"] = [
                    {"id": k, "name": k, "symbol": "", "amount": v}
                    for k, v in protocols.items()
                ]

    except Exception as e:
        result["error"] = str(e)

    return result
