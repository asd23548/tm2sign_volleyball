"""Find NCVA Power League events across TM2Sign public API pages."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

BASE = "https://tm2sign.com/api/public"
OUT = Path("data")
OUT.mkdir(exist_ok=True)
HEADERS = {"Accept": "application/json"}

PATTERNS = [
    re.compile(r"power\s*league", re.I),
    re.compile(r"\bncva\b.*power", re.I),
    re.compile(r"power.*\bncva\b", re.I),
]


def matches_league(name: str) -> bool:
    return any(p.search(name or "") for p in PATTERNS)


def main() -> None:
    client = httpx.Client(timeout=60.0, follow_redirects=True, headers=HEADERS)
    found = []
    # Scan past + future extensively
    for date_range in ("past", "future"):
        page = 1
        last_page = 1
        while page <= last_page and page <= 50:
            url = (
                f"{BASE}/events?filter[dateRange]={date_range}"
                f"&include[]=eventDivisions&per_page=100&page={page}"
            )
            payload = client.get(url).json()
            if isinstance(payload, dict):
                batch = payload.get("data") or []
                last_page = int(payload.get("last_page") or page)
                total = payload.get("total")
            else:
                batch = payload or []
                last_page = page
                total = len(batch)
            print(f"{date_range} page {page}/{last_page} total={total} batch={len(batch)}")
            for ev in batch:
                name = ev.get("name") or ""
                if matches_league(name) or ("ncva" in name.lower() and "power" in name.lower()):
                    found.append(
                        {
                            "id": ev.get("id"),
                            "name": name,
                            "state": ev.get("state"),
                            "start_date": ev.get("start_date"),
                            "end_date": ev.get("end_date"),
                            "date_range": date_range,
                            "scheduler_matches_count": ev.get("scheduler_matches_count"),
                            "divisions": [
                                {"id": d.get("id"), "name": d.get("name")}
                                for d in (ev.get("eventDivisions") or [])
                            ],
                        }
                    )
            if not batch:
                break
            page += 1

    # Also try search filter if supported
    for q in ("Power League", "NCVA Power", "NCVA Power League"):
        url = f"{BASE}/events?filter[search]={q}&filter[dateRange]=past&per_page=100&include[]=eventDivisions"
        resp = client.get(url)
        print("search", q, resp.status_code)
        try:
            payload = resp.json()
        except Exception:
            continue
        batch = payload.get("data", payload) if isinstance(payload, dict) else payload
        for ev in batch or []:
            name = ev.get("name") or ""
            if not any(x["id"] == ev.get("id") for x in found):
                if matches_league(name) or "power" in name.lower():
                    found.append(
                        {
                            "id": ev.get("id"),
                            "name": name,
                            "state": ev.get("state"),
                            "start_date": ev.get("start_date"),
                            "end_date": ev.get("end_date"),
                            "date_range": "search",
                            "scheduler_matches_count": ev.get("scheduler_matches_count"),
                            "divisions": [
                                {"id": d.get("id"), "name": d.get("name")}
                                for d in (ev.get("eventDivisions") or [])
                            ],
                        }
                    )

    found = sorted(found, key=lambda e: (e.get("start_date") or "", e.get("id") or 0))
    (OUT / "ncva_power_league_events.json").write_text(json.dumps(found, indent=2), encoding="utf-8")
    print(f"FOUND {len(found)} events")
    for e in found:
        print(e["start_date"], e["id"], e["name"][:90], "divs", len(e["divisions"]))


if __name__ == "__main__":
    main()
