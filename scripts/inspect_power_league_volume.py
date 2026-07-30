"""Inspect match/team volume for each NCVA Power League event."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "https://tm2sign.com/api/public"
HEADERS = {"Accept": "application/json"}


def main() -> None:
    events = json.loads(Path("data/ncva_power_league_events.json").read_text(encoding="utf-8"))
    client = httpx.Client(timeout=90.0, follow_redirects=True, headers=HEADERS)

    # Broader search for missed seasons
    extras = []
    for q in ("Boys Power League", "Girls Power League", "Power League"):
        for dr in ("past", "future", "all"):
            url = f"{BASE}/events?filter[search]={q}&filter[dateRange]={dr}&per_page=100"
            payload = client.get(url).json()
            batch = payload.get("data", payload) if isinstance(payload, dict) else payload
            for ev in batch or []:
                name = (ev.get("name") or "").lower()
                if "power league" in name and "ncva" in name:
                    if not any(e["id"] == ev["id"] for e in events + extras):
                        extras.append(ev)
                        print("EXTRA", ev["id"], ev.get("start_date"), ev.get("name"))

    if extras:
        for ev in extras:
            events.append(
                {
                    "id": ev["id"],
                    "name": ev.get("name"),
                    "start_date": ev.get("start_date"),
                    "end_date": ev.get("end_date"),
                    "state": ev.get("state"),
                    "divisions": [],
                }
            )
        Path("data/ncva_power_league_events.json").write_text(
            json.dumps(events, indent=2), encoding="utf-8"
        )

    summary = []
    for ev in events:
        eid = ev["id"]
        matches = client.get(f"{BASE}/scheduler-matches?filter[event_id]={eid}").json()
        teams = client.get(f"{BASE}/scheduler-teams?filter[event_id]={eid}").json()
        regs = client.get(f"{BASE}/team-registrations?filter[event_id]={eid}").json()
        n_m = len(matches) if isinstance(matches, list) else -1
        n_t = len(teams) if isinstance(teams, list) else -1
        n_r = len(regs) if isinstance(regs, list) else -1
        row = {
            "id": eid,
            "name": ev.get("name"),
            "start_date": ev.get("start_date"),
            "matches": n_m,
            "scheduler_teams": n_t,
            "registrations": n_r,
        }
        summary.append(row)
        print(row)

    Path("data/ncva_power_league_volume.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
