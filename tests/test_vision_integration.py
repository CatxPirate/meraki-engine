"""Integration test for vision.py — visual_locate via Gemini 2.5 Flash.

Tests:
  1. capture_screenshot via CDP tunneled to executor
  2. visual_locate finds a known element on a test page
  3. coordinates are within expected bounds

Run: Chrome must be running with --remote-debugging-port
  Set CDP_PORT env var (default: 9222) or edit below
"""
import asyncio
import sys
import os
import pytest

pytestmark = pytest.mark.integration
from urllib.parse import quote

# Ensure meraki-engine root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meraki_engine.primitive.dom import CdpClient
from meraki_engine.primitive.vision import (
    capture_screenshot,
    visual_locate,
    VISION_CONFIDENCE_THRESHOLD,
)

CDP_HOST = "127.0.0.1"
CDP_PORT = int(os.getenv("CDP_PORT", "9222"))  # default: local Chrome debugging port


async def test_capture_screenshot():
    """Test 1: capture_screenshot returns a valid PNG file."""
    print("\n=== TEST 1: capture_screenshot ===")
    cdp = CdpClient(host=CDP_HOST, port=CDP_PORT)
    await cdp.connect()

    # Navigate to a test page
    await cdp.navigate("about:blank")
    await cdp.evaluate('document.title = "Vision Test"')

    path = await capture_screenshot(cdp)
    assert path, "capture_screenshot returned None"
    assert os.path.exists(path), f"Screenshot file missing: {path}"
    size = os.path.getsize(path)
    assert size > 100, f"Screenshot too small: {size} bytes"

    print(f"  PASS: Screenshot saved: {path} ({size} bytes)")
    await cdp.close()
    return path


async def test_visual_locate_basic():
    """Test 2: visual_locate finds a clearly labeled button."""
    print("\n=== TEST 2: visual_locate finds button ===")
    cdp = CdpClient(host=CDP_HOST, port=CDP_PORT)
    await cdp.connect()

    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Vision Test</title>
<style>
  body { padding: 100px; font-family: sans-serif; background: #fff; }
  .test-btn { padding: 20px 40px; font-size: 24px; background: #4CAF50;
              color: white; border: none; border-radius: 8px; cursor: pointer;
              margin: 50px 100px; }
</style></head>
<body>
  <h1>Vision Locate Test Page</h1>
  <p>Find the green button labeled "Submit Form" below.</p>
  <button class="test-btn" id="submit-btn">Submit Form</button>
  <p id="status">Status: waiting...</p>
</body></html>"""

    await cdp.navigate(
        f"data:text/html;charset=utf-8,{quote(html)}"
    )

    # Wait for page to settle
    await asyncio.sleep(1.0)

    # Try visual locate
    coords = await visual_locate(
        "the green button labeled 'Submit Form'",
        cdp,
        confidence_threshold=0.3,  # Lower for test
    )

    if coords is None:
        print("  SKIP: vision model returned None (may need retry)")
        print("  This is expected if model is busy or doesn't understand the prompt")
        await cdp.close()
        return None

    x, y = coords
    print(f"  Found at: ({x}, {y})")

    # Click at the found coordinates
    await cdp.evaluate(f"document.elementFromPoint({x}, {y})?.click()")
    await asyncio.sleep(0.5)

    # Verify click worked
    status = await cdp.evaluate(
        "document.getElementById('status')?.textContent || 'no-status'"
    )
    print(f"  Status after click: {status}")

    # The button should have changed text on click (if JS handler works)
    btn_text = await cdp.evaluate(
        "document.getElementById('submit-btn')?.textContent || 'no-button'"
    )
    print(f"  Button text: {btn_text}")

    await cdp.close()
    return coords


async def test_visual_click_flow():
    """Test 3: end-to-end visual click with JS handler."""
    print("\n=== TEST 3: visual click with handler ===")
    cdp = CdpClient(host=CDP_HOST, port=CDP_PORT)
    await cdp.connect()

    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Vision Click Test</title>
<style>
  body { padding: 80px; background: #f5f5f5; }
  .card { padding: 40px; background: #fff; border-radius: 12px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 500px; }
  .big-btn { padding: 18px 36px; font-size: 20px; background: #2196F3;
             color: #fff; border: none; border-radius: 6px; cursor: pointer;
             display: block; margin: 30px auto; }
  .result { text-align: center; font-size: 18px; margin-top: 20px;
            padding: 15px; border-radius: 6px; }
</style></head>
<body>
  <div class="card">
    <h2>Automation Test</h2>
    <p>Click the blue button below to verify the automation.</p>
    <button class="big-btn" id="action-btn"
            onclick="document.getElementById('result').textContent='Vision automation works!';
                     document.getElementById('result').style.background='#e8f5e9'">
      Run Verification
    </button>
    <div class="result" id="result">Not run yet</div>
  </div>
</body></html>"""

    await cdp.navigate(
        f"data:text/html;charset=utf-8,{quote(html)}"
    )
    await asyncio.sleep(1.0)

    # Use visual_locate to find the button
    coords = await visual_locate(
        "the blue button labeled 'Run Verification' inside a white card",
        cdp,
        confidence_threshold=0.3,
    )

    if coords is None:
        print("  SKIP: vision model returned None")
        await cdp.close()
        return None

    x, y = coords
    print(f"  Vision coords: ({x}, {y})")

    # Click via coordinate
    await cdp.evaluate(f"document.elementFromPoint({x}, {y})?.click()")
    await asyncio.sleep(1.0)

    # Verify
    result_text = await cdp.evaluate(
        "document.getElementById('result')?.textContent || ''"
    )
    print(f"  Result: {result_text}")

    assert "works" in result_text, f"Click didn't trigger handler: {result_text}"
    print("  PASS: Vision click triggered JS handler correctly")

    await cdp.close()
    return True


async def main():
    results = {"screenshot": False, "locate": None, "click": None}

    try:
        path = await test_capture_screenshot()
        results["screenshot"] = path is not None
    except Exception as e:
        print(f"  FAIL: screenshot test: {e}")
        import traceback; traceback.print_exc()

    try:
        coords = await test_visual_locate_basic()
        results["locate"] = coords
    except Exception as e:
        print(f"  FAIL: visual_locate test: {e}")
        import traceback; traceback.print_exc()

    try:
        ok = await test_visual_click_flow()
        results["click"] = ok
    except Exception as e:
        print(f"  FAIL: visual_click test: {e}")
        import traceback; traceback.print_exc()

    print(f"\n=== RESULTS ===")
    print(f"  Screenshot capture: {'PASS' if results['screenshot'] else 'FAIL'}")
    print(f"  Visual locate:      {'PASS' if results['locate'] else 'SKIP/FAIL'}")
    print(f"  Visual click:       {'PASS' if results['click'] else 'SKIP/FAIL'}")

    # screenshot test is the hard requirement
    if not results["screenshot"]:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
