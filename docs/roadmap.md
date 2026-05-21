# Meraki Engine — Status Matrix

Last updated: 2026-05-21 08:00 WIB

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
└─────────────────────────────────────────────────┘
```

## Primitive Layer

| Module | Status | Lines | Description |
|--------|--------|-------|-------------|
| `dom.py` | **✓ DONE** | 377 | CDP WebSocket client, selectors, scroll, visible check, viewport lock |
| `vision.py` | **✓ DONE** | 392 | AI visual locator — screenshot capture + Gemini 2.5 Flash native API, visual click |
| `gesture.py` | **○ STUB** | 1 | Realistic mouse movement (future) |

### vision.py details
- **API**: Gemini 2.5 Flash native (NOT DeepCooK proxy)
- **Endpoint**: `generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`
- **Features**: `capture_screenshot()`, `visual_locate()`, `visual_click()`, `capture_viewport()`
- **Confidence threshold**: 0.6 (configurable)
- **Max output tokens**: 2048
- **Fixes applied** (2026-05-21):
  - Strip markdown code block wrapping (` ```json ``` `) from Gemini response
  - `maxOutputTokens` 500 → 2048 (prevent MAX_TOKENS truncation)
- **Tests**: 3/3 PASS (screenshot capture, green button locate, blue button locate)

## Engine Layer

| Module | Status | Lines | Description |
|--------|--------|-------|-------------|
| `verify.py` | **✓ DONE** | 177 | DOM change, URL transition, loader detection, visual diff |
| `safe_click.py` | **✓ DONE** | 197 | Click safety: locate → verify pre-state → click → verify post-state |
| `retry.py` | **✓ DONE** | 318 | Multi-strategy fallback: DOM → scroll → coordinate → human confirm |
| `human.py` | **✓ DONE** | 264 | Telegram human confirm interface (dependency injection) |

## Core Layer

| Module | Status | Lines | Description |
|--------|--------|-------|-------------|
| `session.py` | **○ STUB** | 1 | Persistent Chrome session management — NEXT |
| `profile.py` | **○ STUB** | 1 | Profile aging, cookies, cache management |

## Stealth Roadmap

| Phase | Status | Items |
|-------|--------|-------|
| **FASE 1** | **✓ DONE** | `env -i`, ID timezone/locale/fonts, stealth JS, WebRTC leak prevention, `lang=id-ID` |
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

## Next Steps

### HIGH — `core/session.py`
Persistent Chrome session lifecycle:
- Launch Chrome with meraki profile directory
- Session persistence across restarts
- Cookie/localStorage retention
- Profile isolation per task

### MEDIUM — Hermes Integration
Wire Meraki Engine into Hermes Agent as a skill/tool:
- CDP tunnel auto-management
- `meraki_locate()` → Hermes tool
- `meraki_click()` → Hermes tool
- Vision screenshot → Hermes media delivery

### LOW — `core/profile.py`, `primitive/gesture.py`
- Profile aging with realistic history
- Human-like mouse movement curves
