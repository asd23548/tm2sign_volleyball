"""Discover player/coach APIs from a TM2 team results page via Playwright."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

TARGET = "https://tm2sign.com/app/event/2136/division/10257/team/249672"
OUT = Path("data/team_page_api_capture.json")
RAW = Path("data/team_page_api_raw.jsonl")


async def main() -> None:
    captures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        )

        async def on_response(resp):
            req = resp.request
            if req.resource_type not in {"xhr", "fetch", "document"}:
                return
            url = resp.url
            try:
                ct = (await resp.all_headers()).get("content-type", "")
                body = None
                text = None
                if "json" in ct or "/api/" in url:
                    try:
                        text = await resp.text()
                        body = json.loads(text)
                    except Exception:
                        body = None
                if body is None and "/api/" not in url:
                    return
                entry = {
                    "url": url,
                    "method": req.method,
                    "status": resp.status,
                    "content_type": ct,
                    "resource_type": req.resource_type,
                    "sample_keys": (
                        list(body.keys())[:40]
                        if isinstance(body, dict)
                        else (["[]", f"n={len(body)}"] if isinstance(body, list) else None)
                    ),
                    "sample": _truncate(body),
                }
                captures.append(entry)
                print(resp.status, req.method, url[:160])
            except Exception as exc:  # noqa: BLE001
                captures.append({"url": url, "error": str(exc)})

        page.on("response", on_response)
        print("nav", TARGET)
        await page.goto(TARGET, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(4000)
        # Scroll to bottom where roster lives
        for _ in range(8):
            await page.mouse.wheel(0, 1600)
            await page.wait_for_timeout(800)
        # Click roster-ish tabs if present
        for sel in [
            "text=Roster",
            "text=Players",
            "text=Athletes",
            "text=Coaches",
            "text=Staff",
            "text=Results",
            "text=Team",
        ]:
            loc = page.locator(sel)
            if await loc.count():
                try:
                    await loc.first.click(timeout=2000)
                    await page.wait_for_timeout(2000)
                    print("clicked", sel)
                except Exception:
                    pass
        await page.wait_for_timeout(3000)
        html = await page.content()
        Path("data/team_page.html").write_text(html, encoding="utf-8")
        # Extract visible text around players
        text = await page.inner_text("body")
        Path("data/team_page_text.txt").write_text(text, encoding="utf-8")
        await browser.close()

    OUT.write_text(json.dumps(captures, indent=2), encoding="utf-8")
    with RAW.open("w", encoding="utf-8") as fh:
        for c in captures:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"captured {len(captures)} -> {OUT}")


def _truncate(obj, max_list=5, max_str=400):
    if obj is None:
        return None
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= 50:
                out["__truncated__"] = True
                break
            out[k] = _truncate(v, max_list=max_list, max_str=max_str)
        return out
    if isinstance(obj, list):
        return [_truncate(x, max_list=max_list, max_str=max_str) for x in obj[:max_list]]
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + "…"
    return obj


if __name__ == "__main__":
    asyncio.run(main())
