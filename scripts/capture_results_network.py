"""Capture network calls from TM2 results pages for player/roster APIs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path("data/results_network_capture.json")


async def main() -> None:
    urls = [
        "https://tm2sign.com/app/results/2136",
        "https://tm2sign.com/app/events/2136",
    ]
    captured = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        async def on_response(resp):
            url = resp.url
            if "api" not in url and "player" not in url.lower() and "roster" not in url.lower():
                if "/api/" not in url:
                    return
            try:
                ct = resp.headers.get("content-type", "")
                body = None
                if "json" in ct:
                    body = await resp.json()
                captured.append(
                    {
                        "url": url,
                        "status": resp.status,
                        "content_type": ct,
                        "keys": list(body.keys())[:30] if isinstance(body, dict) else None,
                        "n": len(body) if isinstance(body, list) else (len(body.get("data") or []) if isinstance(body, dict) else None),
                    }
                )
                print(resp.status, url)
            except Exception as exc:  # noqa: BLE001
                captured.append({"url": url, "error": str(exc)})

        page.on("response", on_response)
        for url in urls:
            print("nav", url)
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(3000)
                # click anything that looks like team/results
                for sel in ["text=Results", "text=Teams", "text=Roster", "text=Players", "a[href*='result']"]:
                    loc = page.locator(sel)
                    if await loc.count():
                        try:
                            await loc.first.click(timeout=2000)
                            await page.wait_for_timeout(2000)
                        except Exception:
                            pass
            except Exception as exc:  # noqa: BLE001
                print("nav fail", exc)
        await browser.close()

    OUT.write_text(json.dumps(captured, indent=2), encoding="utf-8")
    print("captured", len(captured), "->", OUT)


if __name__ == "__main__":
    asyncio.run(main())
