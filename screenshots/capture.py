"""Capture README screenshots with Playwright. Runs against a server already
listening on http://127.0.0.1:8765."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"
OUT = Path(__file__).parent

# (filename, viewport, url, optional pre-screenshot JS)
SHOTS = [
    ("01-add.png",       {"width": 420, "height": 820}, "/#capture",  None),
    ("02-items.png",     {"width": 420, "height": 1100}, "/#items",    None),
    ("03-activity.png",  {"width": 420, "height": 1100}, "/#events",   None),
    ("04-event-modal.png", {"width": 1100, "height": 900}, "/#events",
        "(async () => { const r = document.querySelector('.event-row'); if (r) r.click(); await new Promise(r=>setTimeout(r,800)); })()"),
    ("05-tax-year.png",  {"width": 1100, "height": 1100}, "/summary",  None),
    ("06-settings-fmv.png", {"width": 1100, "height": 1100}, "/sources#donation", None),
    ("07-settings-tech.png", {"width": 1100, "height": 1300}, "/sources#technical", None),
]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for fname, viewport, path, pre_js in SHOTS:
            ctx = await browser.new_context(viewport=viewport, device_scale_factor=2,
                                            color_scheme="dark")
            page = await ctx.new_page()
            await page.goto(BASE + path, wait_until="networkidle")
            # Wait for app JS to populate dynamic content
            await page.wait_for_timeout(700)
            if pre_js:
                await page.evaluate(pre_js)
                await page.wait_for_timeout(800)
            out = OUT / fname
            await page.screenshot(path=out, full_page=False)
            print(f"  OK {fname} ({viewport['width']}x{viewport['height']})")
            await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
