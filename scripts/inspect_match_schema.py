"""Inspect TM2Sign match + registration payload schemas."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "https://tm2sign.com/api/public"
OUT = Path("data")
HEADERS = {"Accept": "application/json"}


def main() -> None:
    client = httpx.Client(timeout=60.0, follow_redirects=True, headers=HEADERS)

    events = client.get(
        f"{BASE}/events?filter[dateRange]=past&include[]=eventDivisions&per_page=20"
    ).json()
    if isinstance(events, dict):
        event_list = events["data"]
    else:
        event_list = events

    # Prefer indoor / multi-division events with registrations
    chosen = None
    for ev in event_list:
        eid = ev["id"]
        matches = client.get(f"{BASE}/scheduler-matches?filter[event_id]={eid}").json()
        regs = client.get(f"{BASE}/team-registrations?filter[event_id]={eid}").json()
        n_m = len(matches) if isinstance(matches, list) else 0
        n_r = len(regs) if isinstance(regs, list) else 0
        print(f"event {eid} matches={n_m} regs={n_r} name={ev.get('name')}")
        if n_m >= 10 and n_r >= 4:
            chosen = (ev, matches, regs)
            break
    if chosen is None:
        # fallback first with any matches
        for ev in event_list:
            eid = ev["id"]
            matches = client.get(f"{BASE}/scheduler-matches?filter[event_id]={eid}").json()
            regs = client.get(f"{BASE}/team-registrations?filter[event_id]={eid}").json()
            if isinstance(matches, list) and matches:
                chosen = (ev, matches, regs if isinstance(regs, list) else [])
                break

    if not chosen:
        print("No matches found")
        return

    ev, matches, regs = chosen
    print("CHOSEN", ev["id"], ev["name"], "matches", len(matches), "regs", len(regs))
    (OUT / "sample_matches.json").write_text(
        json.dumps(matches[:5], indent=2), encoding="utf-8"
    )
    (OUT / "sample_registrations.json").write_text(
        json.dumps(regs[:5], indent=2), encoding="utf-8"
    )
    if matches:
        print("match keys", sorted(matches[0].keys()))
        print("match sample", json.dumps(matches[0], indent=2)[:2000])
    if regs:
        print("reg keys", sorted(regs[0].keys()))
        print("reg sample", json.dumps(regs[0], indent=2)[:2000])

    # Probe related endpoints with JSON accept
    eid = ev["id"]
    did = matches[0].get("event_division_id") if matches else None
    extra = [
        f"{BASE}/scheduler-rounds?filter[event_id]={eid}",
        f"{BASE}/scheduler-groups?filter[event_id]={eid}",
        f"{BASE}/scheduler-groups?filter[event_division_id]={did}",
        f"{BASE}/scheduler-stands?filter[event_division_id]={did}",
        f"{BASE}/scheduler-standings?filter[event_division_id]={did}",
        f"{BASE}/scheduler-seeds?filter[event_division_id]={did}",
        f"{BASE}/scheduler-brackets?filter[event_division_id]={did}",
        f"{BASE}/scheduler-courts?filter[event_id]={eid}",
        f"{BASE}/team-registrations/{regs[0]['id']}" if regs else None,
        f"{BASE}/scheduler-matches/{matches[0]['id']}" if matches else None,
        f"{BASE}/events/{eid}?include[]=eventDivisions",
    ]
    results = []
    for url in extra:
        if not url:
            continue
        resp = client.get(url)
        is_json = "json" in (resp.headers.get("content-type") or "")
        preview = resp.text[:200].replace("\n", " ")
        results.append({"status": resp.status_code, "url": url, "json": is_json, "preview": preview})
        print(resp.status_code, is_json, url.replace("https://tm2sign.com", ""), preview[:100])
    (OUT / "api_extra_probes.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
