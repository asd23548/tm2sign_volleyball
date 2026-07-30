"""Deep-probe TM2Sign public APIs and SPA bundles for match/team routes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

BASE = "https://tm2sign.com/api/public"
OUT = Path("data")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    client = httpx.Client(timeout=60.0, follow_redirects=True)

    # Build query manually — httpx list encoding can change API response shape.
    r = client.get(
        f"{BASE}/events?filter[dateRange]=past"
        "&include[]=eventDivisions"
        "&include[]=schedulerEventProfile"
        "&include[]=teamRegistrationEventProfile"
        "&per_page=5"
    )
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, list):
        ev = payload[0]
    else:
        ev = payload["data"][0]
    print("event", ev["id"], ev["name"])
    print("scheduler_matches_count", ev.get("scheduler_matches_count"))
    profile = ev.get("schedulerEventProfile")
    print(
        "schedulerEventProfile",
        list(profile.keys()) if isinstance(profile, dict) else profile,
    )
    (OUT / "sample_past_event.json").write_text(
        json.dumps(ev, indent=2)[:120000], encoding="utf-8"
    )

    eid = ev["id"]
    divs = ev.get("eventDivisions") or []
    did = divs[0]["id"] if divs else None
    print("division", did)

    probes = [
        f"{BASE}/events/{eid}",
        f"{BASE}/event-divisions/{did}" if did else None,
        f"{BASE}/eventDivisions/{did}" if did else None,
        f"{BASE}/scheduler-matches",
        f"{BASE}/scheduler-matches?filter[event_id]={eid}",
        f"{BASE}/scheduler-matches?filter[event_division_id]={did}" if did else None,
        f"{BASE}/scheduler/matches?filter[event_id]={eid}",
        f"{BASE}/matches?filter[event_id]={eid}",
        f"{BASE}/events/{eid}/scheduler-matches",
        f"{BASE}/events/{eid}/matches",
        f"{BASE}/event-divisions/{did}/matches" if did else None,
        f"{BASE}/event-divisions/{did}/scheduler-matches" if did else None,
        f"{BASE}/event-divisions/{did}/pools" if did else None,
        f"{BASE}/event-divisions/{did}/brackets" if did else None,
        f"{BASE}/event-divisions/{did}/standings" if did else None,
        f"{BASE}/pools?filter[event_division_id]={did}" if did else None,
        f"{BASE}/brackets?filter[event_division_id]={did}" if did else None,
        f"{BASE}/standings?filter[event_division_id]={did}" if did else None,
        f"{BASE}/teams?filter[event_id]={eid}",
        f"{BASE}/event-teams?filter[event_id]={eid}",
        f"{BASE}/team-registrations?filter[event_id]={eid}",
        f"{BASE}/clubs",
        f"{BASE}/regions",
        f"{BASE}/companies",
    ]

    results = []
    for url in probes:
        if not url:
            continue
        try:
            resp = client.get(url)
            preview = resp.text[:240].replace("\n", " ")
            results.append(
                {
                    "status": resp.status_code,
                    "url": url,
                    "content_type": resp.headers.get("content-type"),
                    "preview": preview,
                }
            )
            print(resp.status_code, url.replace("https://tm2sign.com", ""), preview[:120])
        except Exception as exc:  # noqa: BLE001
            results.append({"url": url, "error": str(exc)})
            print("ERR", url, exc)

    (OUT / "api_probe_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )

    # Scrape SPA JS for /api/public paths
    html = client.get("https://tm2sign.com/app/events").text
    (OUT / "app_events.html").write_text(html, encoding="utf-8")
    assets = sorted(set(re.findall(r'(?:src|href)="([^"]+\.js)"', html)))
    print("js assets", len(assets))
    found: set[str] = set()
    for asset in assets[:40]:
        url = asset if asset.startswith("http") else f"https://tm2sign.com{asset}"
        try:
            js = client.get(url).text
        except Exception as exc:  # noqa: BLE001
            print("js fail", url, exc)
            continue
        for m in re.findall(r"/api/public/[A-Za-z0-9_\-/]+", js):
            found.add(m)
        for m in re.findall(r"api/public/[A-Za-z0-9_\-/]+", js):
            found.add("/" + m if not m.startswith("/") else m)

    paths = sorted(found)
    print("api paths found", len(paths))
    for p in paths:
        print(p)
    (OUT / "api_paths_from_js.json").write_text(json.dumps(paths, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
