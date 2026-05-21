# Meraki Engine — Status Matrix

Last updated: 2026-05-21 08:30 WIB

## Layer Architecture

```
┌─────────────────────────────────────────────────┐
│                  core/                           │
│  session.py  [ ]    profile.py  [ ]             │
├─────────────────────────────────────────────────┤
│                  engine/                         │
│  verify.py   [✓]    safe_click.py  [✓]          │
│  retry.py    [✓]    human.py       [✓]          │
├─────────────────────────────────────────────────┤
│                primitive/                        │
│  dom.py      [✓]    vision.py      [✓]          │
│  gesture.py  [ ]                                 │
├─────────────────────────────────────────────────┤
│                 bridge/         NEW              │
│  __init__.py [✓]    operator.py  [✓]            │
└─────────────────────────────────────────────────┘
```

## Primitive Layer

| Module | Status | Lines | Description |
|--------|--------|-------|-------------|
| `dom.py` | **✓ DONE** | 377 | CDP WebSocket client, selectors, scroll, visible check, viewport lock |
| `vision.py` | **✓ DONE** | 392 | AI visual locator — screenshot capture + Gemini 2.5 Flash native API, visual click |
| `gesture.py` | **○ STUB** | 1 | Realistic mouse movement (future) |

## Engine Layer

| Module | Status | Lines | Description |
|--------|--------|-------|-------------|
| `verify.py` | **✓ DONE** | 177 | DOM change, URL transition, loader detection, visual diff |
| `safe_click.py` | **✓ DONE** | 197 | Click safety: locate → verify pre-state → click → verify post-state |
| `retry.py` | **✓ DONE** | 318 | Multi-strategy fallback: DOM → scroll → coordinate → human confirm |
| `human.py` | **✓ DONE** | 264 | Telegram human confirm interface (dependency injection) |

## Bridge Layer (Hermes Integration)

| Module | Status | Lines | Description |
|--------|--------|-------|-------------|
| `bridge/__init__.py` | **✓ DONE** | 80 | SSH tunnel lifecycle + GEMINI_API_KEY auto-load |
| `bridge/operator.py` | **✓ DONE** | 160 | High-level operations: navigate, locate, click, screenshot, evaluate |

**Integration verified:**
- SSH tunnel: auto-start on first operation ✓
- CDP connection: stable via tunnel :19222 → executor :9222 ✓
- Visual locate: Gemini 2.5 Flash native API, found at (180, 178) ✓
- Screenshot: capture + save to /tmp/meraki-shots/ ✓
- JavaScript evaluate: DOM access, click verification ✓
- Usage: `execute_code()` with `from bridge.operator import Operator`

## Core Layer

| Module | Status | Lines | Description |
|--------|--------|-------|-------------|
| `session.py` | **○ STUB** | 1 | Persistent Chrome session management — **NEXT** |
| `profile.py` | **○ STUB** | 1 | Profile aging, cookies, cache management |

## Stealth Roadmap

| Phase | Status | Items |
|-------|--------|-------|
| **FASE 1** | **✓ DONE** | ID timezone/locale/fonts, stealth JS, WebRTC leak prevention, `lang=id-ID` |
| **FASE 2** | **✓ DONE** | `--no-sandbox` banner suppressed, uBlock Origin, 1920×1080 resolution |
| **FASE 3** | **○ PENDING** | Non-root Chrome + sandbox, behavioral warming |

## Infrastructure (Executor VPS)

| Component | Status |
|-----------|--------|
| Xvfb :99 (1920×1080) | ✓ |
| Openbox WM | ✓ |
| Chrome CDP :9222 | ✓ |
| x11vnc :5900 | ✓ |
| PM2 lifecycle (xvfb → openbox → x11vnc + chrome) | ✓ |
| D-Bus (dbus-x11) | ✓ |
| Gemini API key (.env) | ✓ |
| uBlock Origin extension | ✓ |
| Stealth JS injection | ✓ |

## Commit History (Today)

| Commit | Description |
|--------|-------------|
| `9fc255d` | fix(vision): strip markdown code block wrapping |
| `d3b93a5` | fix(vision): maxOutputTokens 500→2048 |
| `eb79518` | docs: update status matrix |
| `f69f09b` | feat(bridge): Hermes integration — operator + tunnel manager |

## Next Steps

### HIGH — `core/session.py`
Persistent Chrome session lifecycle:
- Launch Chrome with meraki profile directory
- Session persistence across restarts
- Cookie/localStorage retention
- Profile isolation per task

### MEDIUM — `core/profile.py`, `primitive/gesture.py`
- Profile aging with realistic history
- Human-like mouse movement curves

### LOW — FASE 3 Stealth
- Non-root Chrome + sandbox
- Behavioral warming cron job
