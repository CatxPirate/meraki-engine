# Meraki Engine v0.2.0

Hybrid browser operator — CDP primitives, AI vision, and human-like automation engine.

**Maturity:** Alpha. Modules verified in controlled stress-test environment (8-suite matrix). Not yet deployed in production multi-session scenarios.

## Architecture

```
meraki_engine/
├── primitive/          # Foundation layer
│   ├── dom.py          # CDP selectors, visible check, click (421 lines)
│   ├── vision.py       # Gemini 2.5 Flash visual locator (553 lines)
│   └── gesture.py      # Realistic mouse movement simulation (395 lines)
├── engine/             # Operator layer
│   ├── retry.py        # DOM → scroll → coordinate → vision fallback
│   ├── safe_click.py   # Locate → verify → click → verify
│   ├── verify.py       # DOM, URL, loader, visual diff verification
│   └── human.py        # Telegram human-in-the-loop confirm
└── config/             # Settings, constants, fallback order
```

## v0.2.0 — Vision Hardening (P3)

Three-guard defense chain against Gemini 2.5 Flash hallucinations:

1. **Bounds check** (P0) — reject OOB coordinates before dispatch
2. **DOM sanity** (P3.1) — `elementFromPoint(x,y)` verify element exists at Gemini coords; reject if null
3. **Overconfidence flag** (P3.3) — detect confidence ≥0.995 on pages with <100 DOM elements; log warning
4. **Semantic mismatch** (P3.2) — detect non-interactive element at click coords (e.g. clicking `<div>` when expecting button); log warning

All guards use CDP `evaluate()` only — zero extra API calls.

### Additional Fixes
- **Tunnel reconnect**: `CdpClient.is_connected` + `Operator._get_cdp()` rebuild with `ensure_tunnel()` auto-recovery
- **Session lifecycle**: CDP-level readiness (`_check_cdp_ready`), port-free verification (`_wait_port_free`)
- **is_alive cross-SSH**: Port file persistence for cross-SSH session detection

## Quick Start

```python
from meraki_engine import CdpClient, visual_click, GestureSimulator

cdp = CdpClient(port=9222)
await cdp.navigate("https://example.com")
await GestureSimulator.warmup_browse(cdp)
result = await visual_click("the login button", cdp)
```

## Version

- Engine: `0.2.0`
- Python: `>=3.10`
- Chrome CDP: v148+

## License

Private — CatxPirate
