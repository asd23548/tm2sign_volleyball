"""
Autonomous TM2Sign API discovery via Playwright network interception.

Usage:
    python scripts/discover_api.py
    python scripts/discover_api.py --url https://tm2sign.com/app/events --headed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_PATH = PROJECT_ROOT / "data" / "api_schema_discovered.json"
RAW_CAPTURE_PATH = PROJECT_ROOT / "data" / "api_raw_captures.jsonl"

DEFAULT_START_URLS = [
    "https://tm2sign.com/",
    "https://tm2sign.com/app/events",
    "https://tm2sign.com/events",
    "https://www.tm2sign.com/app/events",
]

API_HINTS = re.compile(
    r"(api|graphql|json|event|division|match|team|club|region|schedule|standings|bracket|pool|result)",
    re.I,
)


def _safe_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def _sample_keys(payload: Any, max_keys: int = 40) -> list[str]:
    keys: list[str] = []

    def walk(obj: Any, prefix: str = "", depth: int = 0) -> None:
        if len(keys) >= max_keys or depth > 4:
            return
        if isinstance(obj, dict):
            for k, v in list(obj.items())[:25]:
                path = f"{prefix}.{k}" if prefix else str(k)
                keys.append(path)
                if isinstance(v, (dict, list)):
                    walk(v, path, depth + 1)
        elif isinstance(obj, list) and obj:
            keys.append(f"{prefix}[]")
            walk(obj[0], f"{prefix}[]", depth + 1)

    walk(payload)
    return keys[:max_keys]


def _resource_hint(url: str, payload: Any) -> str:
    path = urlparse(url).path.lower()
    for token in (
        "events",
        "event",
        "divisions",
        "division",
        "matches",
        "match",
        "teams",
        "team",
        "clubs",
        "club",
        "regions",
        "region",
        "standings",
        "bracket",
        "pool",
        "schedule",
        "results",
        "rankings",
    ):
        if token in path:
            return token
    if isinstance(payload, dict):
        joined = " ".join(payload.keys()).lower()
        for token in ("event", "division", "match", "team", "club", "region"):
            if token in joined:
                return token
    return "unknown"


def _interesting_response(url: str, content_type: str | None, status: int) -> bool:
    if status >= 400:
        return False
    ct = (content_type or "").lower()
    if "json" in ct:
        return True
    if API_HINTS.search(url):
        return True
    path = urlparse(url).path
    if path.endswith(".json"):
        return True
    return False


async def discover(
    start_urls: list[str],
    headed: bool = False,
    max_seconds: int = 90,
    click_limit: int = 25,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright
    from src.etl.tm2_client import KNOWN_ENDPOINTS, write_known_schema

    captures: dict[str, dict[str, Any]] = {}
    raw_rows: list[dict[str, Any]] = []
    cookies_by_domain: dict[str, dict[str, str]] = defaultdict(dict)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        async def on_response(response) -> None:
            try:
                req = response.request
                if req.resource_type not in {"xhr", "fetch", "document"}:
                    return
                url = response.url
                headers = await response.all_headers()
                ct = headers.get("content-type")
                status = response.status
                if not _interesting_response(url, ct, status):
                    return

                body_text = ""
                payload = None
                try:
                    body_text = await response.text()
                    payload = _safe_json(body_text)
                except Exception:
                    payload = None

                if payload is None and "json" not in (ct or "").lower():
                    # Keep HTML documents only if they look like SPA shells with API hints
                    if req.resource_type == "document":
                        return

                req_headers = {
                    k: v
                    for k, v in req.headers.items()
                    if k.lower()
                    in {
                        "accept",
                        "authorization",
                        "content-type",
                        "x-requested-with",
                        "referer",
                        "origin",
                        "cookie",
                    }
                }
                parsed = urlparse(url)
                domain = parsed.netloc
                cookie_header = req.headers.get("cookie", "")
                if cookie_header:
                    for part in cookie_header.split(";"):
                        if "=" in part:
                            k, v = part.strip().split("=", 1)
                            cookies_by_domain[domain][k] = v

                key = f"{req.method} {parsed.scheme}://{parsed.netloc}{parsed.path}"
                entry = {
                    "url": url,
                    "method": req.method,
                    "status": status,
                    "content_type": ct,
                    "resource_hint": _resource_hint(url, payload),
                    "query_params": {k: v if len(v) > 1 else v[0] for k, v in parse_qs(parsed.query).items()},
                    "sample_keys": _sample_keys(payload) if payload is not None else [],
                    "sample_payload": _truncate_payload(payload),
                    "request_headers": req_headers,
                    "cookies": dict(cookies_by_domain.get(domain, {})),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
                # Prefer richer payloads when replacing
                existing = captures.get(key)
                if existing is None or (
                    isinstance(payload, (dict, list))
                    and len(json.dumps(entry.get("sample_payload") or {}))
                    > len(json.dumps(existing.get("sample_payload") or {}))
                ):
                    captures[key] = entry

                raw_rows.append(
                    {
                        "key": key,
                        "url": url,
                        "method": req.method,
                        "status": status,
                        "content_type": ct,
                        "payload_preview": body_text[:2000] if body_text else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                raw_rows.append({"error": str(exc), "url": getattr(response, "url", None)})

        page.on("response", on_response)

        visited: list[str] = []
        for url in start_urls:
            try:
                print(f"[nav] {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2500)
                visited.append(page.url)
                await _auto_explore(page, click_limit=click_limit)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] navigation failed for {url}: {exc}")

        # Extra dwell to catch late XHR
        await page.wait_for_timeout(3000)
        all_cookies = await context.cookies()
        await browser.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RAW_CAPTURE_PATH.open("w", encoding="utf-8") as fh:
        for row in raw_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Merge confirmed TM2 routes discovered via probing
    for ep in KNOWN_ENDPOINTS:
        key = f"{ep.get('method', 'GET')} {ep['url'].split('?')[0]}"
        captures.setdefault(
            key,
            {
                **ep,
                "status": 200,
                "content_type": "application/json",
                "query_params": {},
                "sample_payload": None,
                "request_headers": {"Accept": "application/json"},
                "cookies": {},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    endpoints = sorted(captures.values(), key=lambda e: (e.get("resource_hint") or "", e["url"]))
    result = {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "start_urls": start_urls,
        "visited_urls": visited,
        "endpoint_count": len(endpoints),
        "cookies": all_cookies,
        "endpoints": endpoints,
        "notes": (
            "Generated by scripts/discover_api.py via Playwright response interception, "
            "merged with confirmed /api/public TM2Sign routes."
        ),
        "base_url": "https://tm2sign.com/api/public",
    }
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    write_known_schema(OUTPUT_PATH)
    print(f"[ok] Wrote {len(endpoints)} endpoints -> {OUTPUT_PATH}")
    return result


def _truncate_payload(payload: Any, max_list: int = 3, max_str: int = 500) -> Any:
    if payload is None:
        return None
    if isinstance(payload, dict):
        out = {}
        for i, (k, v) in enumerate(payload.items()):
            if i >= 40:
                out["__truncated__"] = True
                break
            out[k] = _truncate_payload(v, max_list=max_list, max_str=max_str)
        return out
    if isinstance(payload, list):
        return [_truncate_payload(x, max_list=max_list, max_str=max_str) for x in payload[:max_list]]
    if isinstance(payload, str) and len(payload) > max_str:
        return payload[:max_str] + "…"
    return payload


async def _auto_explore(page, click_limit: int = 25) -> None:
    """Click pagination / event links to force more XHR traffic."""
    selectors = [
        "a[href*='event']",
        "a[href*='division']",
        "button:has-text('Next')",
        "a:has-text('Next')",
        "[aria-label='Next']",
        "button:has-text('Load more')",
        ".pagination a",
        "table a",
        "[role='row'] a",
    ]
    clicks = 0
    for sel in selectors:
        if clicks >= click_limit:
            break
        try:
            loc = page.locator(sel)
            count = await loc.count()
        except Exception:
            continue
        for i in range(min(count, 8)):
            if clicks >= click_limit:
                break
            try:
                target = loc.nth(i)
                if not await target.is_visible():
                    continue
                href = await target.get_attribute("href")
                await target.click(timeout=2500, force=False)
                await page.wait_for_timeout(1500)
                clicks += 1
                print(f"[click] {sel} #{i} href={href}")
                # Prefer staying in-app; if navigated away too far, go back
                if "tm2sign" not in page.url.lower():
                    await page.go_back(wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)
            except Exception:
                continue

    # Scroll to trigger lazy loads
    for _ in range(4):
        try:
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(800)
        except Exception:
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover TM2Sign APIs via Playwright")
    parser.add_argument("--url", action="append", dest="urls", help="Start URL (repeatable)")
    parser.add_argument("--headed", action="store_true", help="Run headed browser")
    parser.add_argument("--seconds", type=int, default=90, help="Soft budget (informational)")
    parser.add_argument("--clicks", type=int, default=25, help="Max auto-clicks")
    args = parser.parse_args()

    urls = args.urls or DEFAULT_START_URLS
    import asyncio

    t0 = time.time()
    result = asyncio.run(
        discover(
            start_urls=urls,
            headed=args.headed,
            max_seconds=args.seconds,
            click_limit=args.clicks,
        )
    )
    print(
        json.dumps(
            {
                "endpoint_count": result["endpoint_count"],
                "elapsed_sec": round(time.time() - t0, 1),
                "output": str(OUTPUT_PATH),
                "hints": sorted({e.get("resource_hint") for e in result["endpoints"]}),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
