"""Logs in to the locally started stack and saves screenshots of the main pages (used by the Screenshots workflow)."""
import asyncio, os
from playwright.async_api import async_playwright

PAGES = ["/", "/market", "/scalping", "/pillars", "/news", "/settings"]

async def main():
    os.makedirs("shots", exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 1100}, ignore_https_errors=True)
        pg = await ctx.new_page()
        await pg.goto("https://localhost/login")
        await pg.fill("input[placeholder*=example]", os.environ["ADMIN_EMAIL"])
        await pg.fill("input[type=password]", os.environ["ADMIN_PASSWORD"])
        await pg.get_by_role("button", name="Sign in", exact=True).click()
        await pg.wait_for_timeout(8000)
        for path in PAGES:
            await pg.goto("https://localhost" + path)
            await pg.wait_for_timeout(5000)
            await pg.screenshot(path=f"shots/{(path.strip('/') or 'dashboard')}.png", full_page=True)
        m = await b.new_context(viewport={"width": 390, "height": 844}, ignore_https_errors=True, storage_state=await ctx.storage_state())
        mp = await m.new_page()
        await mp.goto("https://localhost/")
        await mp.wait_for_timeout(5000)
        await mp.screenshot(path="shots/mobile.png")
        await b.close()

asyncio.run(main())
