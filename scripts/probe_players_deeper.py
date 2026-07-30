"""Deeper probe for any public player/roster surface."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

BASE = "https://tm2sign.com/api/public"
HEADERS = {"Accept": "application/json"}


def main() -> None:
    client = httpx.Client(timeout=60, headers=HEADERS, follow_redirects=True)
    eid = 2136
    # allowed includes from prior match error: event, eventDivision, ...
    # Probe scheduler-teams includes
    for inc in [
        "event",
        "eventDivision",
        "clubTeam",
        "teamRegistration",
        "players",
        "athletes",
        "roster",
        "members",
    ]:
        url = f"{BASE}/scheduler-teams?filter[event_id]={eid}&page=1&per_page=1&include[]={inc}"
        resp = client.get(url)
        print("st include", inc, resp.status_code, resp.text[:160].replace("\n", " "))

    # completed scoresheet field on matches?
    matches = client.get(
        f"{BASE}/scheduler-matches?filter[event_id]={eid}&page=1&per_page=20"
    ).json()
    rows = matches.get("data", matches)
    with_sheet = [m for m in rows if m.get("completed_scoresheet")]
    print("with completed_scoresheet", len(with_sheet), "of", len(rows))
    if with_sheet:
        print(json.dumps(with_sheet[0].get("completed_scoresheet"), indent=2)[:1000])

    # Try result lookup / public results endpoints
    probes = [
        f"{BASE}/scheduler-result-lookups?filter[event_id]={eid}",
        f"{BASE}/result-lookups?filter[event_id]={eid}",
        f"{BASE}/public-results?filter[event_id]={eid}",
        f"{BASE}/scheduler-team-results?filter[event_id]={eid}",
        f"https://tm2sign.com/app/events/{eid}",
        f"https://tm2sign.com/app/results/{eid}",
        f"https://tm2sign.com/results/{eid}",
    ]
    for url in probes:
        resp = client.get(url)
        ctype = resp.headers.get("content-type", "")
        print(resp.status_code, ctype[:30], url.replace("https://tm2sign.com", ""), resp.text[:100].replace("\n", " "))

    # Scrape JS bundles from an event results page if any
    html = client.get("https://tm2sign.com/app/events").text
    assets = sorted(set(re.findall(r'(?:src|href)="([^"]+\.js)"', html)))
    print("js assets", len(assets), assets[:5])
    found = set()
    for asset in assets[:20]:
        url = asset if asset.startswith("http") else f"https://tm2sign.com{asset}"
        try:
            js = client.get(url).text
        except Exception:
            continue
        for m in re.findall(r"[A-Za-z0-9_\-/]*(?:player|athlete|roster|member)[A-Za-z0-9_\-/]*", js, re.I):
            if "api" in m.lower() or "player" in m.lower() or "roster" in m.lower():
                found.add(m[:120])
    print("js hits", len(found))
    for x in sorted(found)[:60]:
        print(x)


if __name__ == "__main__":
    main()
