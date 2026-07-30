"""Discover scheduler-teams mapping to registrations."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "https://tm2sign.com/api/public"
HEADERS = {"Accept": "application/json"}
OUT = Path("data")


def main() -> None:
    client = httpx.Client(timeout=60.0, follow_redirects=True, headers=HEADERS)
    eid = 2592
    probes = [
        f"{BASE}/scheduler-teams?filter[event_id]={eid}",
        f"{BASE}/scheduler-team?filter[event_id]={eid}",
        f"{BASE}/schedulerTeams?filter[event_id]={eid}",
        f"{BASE}/teams?filter[event_id]={eid}",
        f"{BASE}/event-division-teams?filter[event_id]={eid}",
        f"{BASE}/scheduler-pool-brackets?filter[event_id]={eid}",
        f"{BASE}/scheduler-pool-bracket-teams?filter[event_id]={eid}",
        f"{BASE}/scheduler-group-teams?filter[event_id]={eid}",
    ]
    for url in probes:
        resp = client.get(url)
        preview = resp.text[:220].replace("\n", " ")
        print(resp.status_code, url.replace(BASE, ""), preview[:140])
        if resp.status_code == 200 and "json" in (resp.headers.get("content-type") or ""):
            data = resp.json()
            (OUT / f"probe_{url.split('/api/public/')[1].split('?')[0].replace('/', '_')}.json").write_text(
                json.dumps(data[:3] if isinstance(data, list) else data, indent=2)[:50000],
                encoding="utf-8",
            )
            if isinstance(data, list) and data:
                print("  keys", sorted(data[0].keys()))


if __name__ == "__main__":
    main()
