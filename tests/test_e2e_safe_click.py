"""
End-to-end integration test for safe_click().

Runs against live Chrome CDP on the executor.
Tests the full flow: locate -> visible -> scroll -> click -> verify.
"""

import asyncio
import logging
import sys
import urllib.parse

sys.path.insert(0, "/root/meraki-engine")

from primitive.dom import CdpClient
from engine.safe_click import safe_click
from engine.retry import HumanConfirmRequired

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("meraki.e2e")

TEST_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Meraki E2E Test</title></head>
<body style="padding:50px;font-family:sans-serif;">
<h1>Safe Click Integration Test</h1>
<div style="margin-top:100vh;padding:20px;border:2px solid #ccc;border-radius:8px;">
<p id="status">Not clicked yet</p>
<button id="target-btn" style="padding:12px 24px;font-size:16px;cursor:pointer;">Click Me</button>
</div>
<script>
document.getElementById("target-btn").onclick = function(){
 document.getElementById("status").textContent="Clicked!"
}
</script>
</body>
</html>"""


async def setup_test_page(cdp):
    """Navigate to a data: URL containing the test page."""
    # Use data: URL via Page.navigate
    encoded = urllib.parse.quote(TEST_HTML, safe="")
    data_url = f"data:text/html;charset=utf-8,{encoded}"
    await cdp._send("Page.enable")
    await cdp._send("Page.navigate", {"url": data_url})
    await asyncio.sleep(1.0)
    title = await cdp.evaluate("document.title")
    logger.info("Title: %s", title)


async def test_safe_click_success():
    logger.info("=== TEST 1: safe_click success ===")
    cdp = CdpClient()
    await cdp.connect()
    await setup_test_page(cdp)

    status = await cdp.evaluate("document.getElementById('status')?.textContent")
    logger.info("Initial: %s", status)
    assert status == "Not clicked yet", f"Unexpected: {status}"

    result = await safe_click("#target-btn", cdp=cdp)
    assert result is True, f"safe_click returned {result}"
    logger.info("safe_click: %s", result)

    status = await cdp.evaluate("document.getElementById('status')?.textContent")
    assert status == "Clicked!", f"Not clicked: {status}"
    logger.info("Final: %s", status)
    await cdp.close()
    logger.info("TEST 1 PASSED")


async def test_safe_click_missing():
    logger.info("=== TEST 2: safe_click missing element ===")
    cdp = CdpClient()
    await cdp.connect()
    await setup_test_page(cdp)

    try:
        await safe_click("#nonexistent", cdp=cdp)
        assert False, "Should have raised HumanConfirmRequired"
    except HumanConfirmRequired as e:
        logger.info("Got: %s", e)

    await cdp.close()
    logger.info("TEST 2 PASSED")


async def test_safe_click_escaped_selector():
    logger.info("=== TEST 3: selector with special chars ===")
    cdp = CdpClient()
    await cdp.connect()
    await setup_test_page(cdp)

    await cdp.evaluate("""
        var b = document.createElement("button");
        b.id = "data-btn";
        b.setAttribute("data-label", "it's working");
        b.textContent = "Data Button";
        b.style.padding = "12px";
        document.body.appendChild(b);
        var s = document.createElement("p");
        s.id = "data-status";
        s.textContent = "Not clicked";
        document.body.appendChild(s);
        b.onclick = function(){
            document.getElementById("data-status").textContent = "Data clicked!";
        };
    """)
    await asyncio.sleep(0.3)

    result = await safe_click('''button[data-label="it's working"]''', cdp=cdp)
    assert result is True, f"Got {result}"

    status = await cdp.evaluate("document.getElementById('data-status')?.textContent")
    assert status == "Data clicked!", f"Got: {status}"
    logger.info("Status: %s", status)

    await cdp.close()
    logger.info("TEST 3 PASSED")


async def main():
    logger.info("=" * 50)
    logger.info("MERAKI ENGINE — E2E Safe Click Integration Test")
    logger.info("=" * 50)
    await test_safe_click_success()
    print()
    await test_safe_click_missing()
    print()
    await test_safe_click_escaped_selector()
    print()
    logger.info("=" * 50)
    logger.info("ALL TESTS PASSED")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
