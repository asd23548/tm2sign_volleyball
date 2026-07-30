"""Probe TM2Sign for player/roster endpoints and team identity fields."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE = "https://tm2sign.com/api/public"
HEADERS = {"Accept": "application/json"}
OUT = Path("data")


def main() -> None:
    client = httpx.Client(timeout=60.0, follow_redirects=True, headers=HEADERS)
    # 2026 Girls Power League — rich season
    eid = 2136
    teams = client.get(f"{BASE}/scheduler-teams?filter[event_id]={eid}&page=1&per_page=5").json()
    batch = teams.get("data", teams) if isinstance(teams, dict) else teams
    print("scheduler-team keys", sorted(batch[0].keys()))
    print(json.dumps(batch[0], indent=2)[:1500])

    tid = batch[0]["id"]
    reg_id = batch[0].get("event_registration_id")
    club_team_id = batch[0].get("club_team_id")
    print("ids", tid, reg_id, club_team_id)

    probes = [
        f"{BASE}/scheduler-teams/{tid}",
        f"{BASE}/scheduler-team-players?filter[scheduler_team_id]={tid}",
        f"{BASE}/scheduler-players?filter[scheduler_team_id]={tid}",
        f"{BASE}/scheduler-players?filter[event_id]={eid}",
        f"{BASE}/players?filter[event_id]={eid}",
        f"{BASE}/team-players?filter[event_id]={eid}",
        f"{BASE}/rosters?filter[event_id]={eid}",
        f"{BASE}/event-rosters?filter[event_id]={eid}",
        f"{BASE}/team-registration-players?filter[event_registration_id]={reg_id}",
        f"{BASE}/team-registrations/{reg_id}/players" if reg_id else None,
        f"{BASE}/players?filter[team_registration_id]={reg_id}" if reg_id else None,
        f"{BASE}/club-teams/{club_team_id}" if club_team_id else None,
        f"{BASE}/club-teams/{club_team_id}/players" if club_team_id else None,
        f"{BASE}/club-team-players?filter[club_team_id]={club_team_id}" if club_team_id else None,
        f"{BASE}/members?filter[event_id]={eid}",
        f"{BASE}/athlete-profiles?filter[event_id]={eid}",
        f"{BASE}/team-registration-athletes?filter[event_id]={eid}",
        f"{BASE}/team-registrations?filter[event_id]={eid}&include[]=players",
        f"{BASE}/team-registrations?filter[event_id]={eid}&include[]=athletes",
        f"{BASE}/team-registrations?filter[event_id]={eid}&include[]=roster",
        f"{BASE}/scheduler-matches?filter[event_id]={eid}&page=1&per_page=1&include[]=players",
    ]

    # Find a club named Absolute-like for cohort test
    page = 1
    absolute = []
    while page <= 45:
        payload = client.get(
            f"{BASE}/scheduler-teams?filter[event_id]={eid}&page={page}&per_page=100"
        ).json()
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not rows:
            break
        for t in rows:
            name = (t.get("name") or "") + " " + (t.get("club_name") or "")
            if "absolute" in name.lower() and "black" in name.lower():
                absolute.append(t)
        last = payload.get("last_page", page) if isinstance(payload, dict) else page
        if page >= last:
            break
        page += 1
    print("absolute black teams", len(absolute))
    for t in absolute[:10]:
        print(t.get("id"), t.get("name"), t.get("club_name"), t.get("club_team_id"), t.get("region"))

    results = []
    for url in probes:
        if not url:
            continue
        resp = client.get(url)
        ctype = resp.headers.get("content-type", "")
        preview = resp.text[:220].replace("\n", " ")
        is_json = "json" in ctype
        n = None
        keys = None
        if is_json:
            try:
                data = resp.json()
                if isinstance(data, list):
                    n = len(data)
                    keys = sorted(data[0].keys()) if data and isinstance(data[0], dict) else None
                elif isinstance(data, dict):
                    n = len(data.get("data") or []) if "data" in data else None
                    keys = sorted(data.keys())
            except Exception:
                pass
        results.append(
            {
                "status": resp.status_code,
                "url": url,
                "json": is_json,
                "n": n,
                "keys": keys,
                "preview": preview,
            }
        )
        print(resp.status_code, "n=", n, url.replace(BASE, ""), preview[:100])

    (OUT / "player_api_probe.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (OUT / "absolute_black_sample.json").write_text(json.dumps(absolute, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
