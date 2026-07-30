"""Inspect Absolute Black identity across seasons + registration include payloads."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.db import get_connection

BASE = "https://tm2sign.com/api/public"
HEADERS = {"Accept": "application/json"}


def main() -> None:
    conn = get_connection()
    rows = conn.execute(
        """
        select team_id, team_name, club_id, age_group
        from teams
        where lower(team_name) like '%absolute%black%'
        order by team_name, team_id
        limit 80
        """
    ).fetchall()
    print("db absolute", len(rows))
    for r in rows[:40]:
        print(dict(r))
    dups = conn.execute(
        """
        select team_name, count(*) c from teams
        group by team_name having count(*) > 1
        order by c desc limit 12
        """
    ).fetchall()
    print("top dups")
    for r in dups:
        print(r["c"], r["team_name"])
    print("teams", conn.execute("select count(*) c from teams").fetchone()["c"])
    print(
        "distinct names",
        conn.execute("select count(distinct team_name) c from teams").fetchone()["c"],
    )
    conn.close()

    client = httpx.Client(timeout=60, headers=HEADERS, follow_redirects=True)
    # Compare Absolute 18 Black club_team_id across seasons
    event_ids = [1238, 1545, 1823, 2136]  # girls PL history
    hits = []
    for eid in event_ids:
        page = 1
        while page <= 50:
            payload = client.get(
                f"{BASE}/scheduler-teams?filter[event_id]={eid}&page={page}&per_page=100"
            ).json()
            rows = payload.get("data", payload) if isinstance(payload, dict) else payload
            if not rows:
                break
            for t in rows:
                name = (t.get("name") or "").lower()
                if "absolute" in name and "black" in name:
                    hits.append(
                        {
                            "event_id": eid,
                            "id": t.get("id"),
                            "name": t.get("name"),
                            "club_team_id": t.get("club_team_id"),
                            "club_name": t.get("club_name"),
                            "alt": t.get("alternate_identifier"),
                            "seed": t.get("starting_seed_number"),
                            "finish": t.get("final_finish_position_number"),
                        }
                    )
            last = payload.get("last_page", page) if isinstance(payload, dict) else page
            if page >= last:
                break
            page += 1

    print("\nAbsolute Black across seasons:")
    for h in sorted(hits, key=lambda x: (x["name"], x["event_id"])):
        print(h)

    # Inspect registration include=players payload shape
    regs = client.get(
        f"{BASE}/team-registrations?filter[event_id]=2136&include[]=players"
    ).json()
    sample = regs[0] if isinstance(regs, list) and regs else regs
    Path("data/reg_include_players_sample.json").write_text(
        json.dumps(sample, indent=2)[:50000], encoding="utf-8"
    )
    print("\nreg keys", sorted(sample.keys()) if isinstance(sample, dict) else type(sample))
    for key in ("players", "athletes", "roster", "team_meta", "club_meta"):
        val = sample.get(key) if isinstance(sample, dict) else None
        print(key, type(val).__name__, (len(val) if isinstance(val, list) else None))
        if isinstance(val, list) and val:
            print("  item keys", sorted(val[0].keys()) if isinstance(val[0], dict) else val[0])
            print("  item sample", json.dumps(val[0], indent=2)[:800])
        elif isinstance(val, dict):
            print("  dict keys", sorted(val.keys())[:30])

    # Try allowed includes from error message style discovery
    for inc in (
        "players",
        "athletes",
        "roster",
        "teamPlayers",
        "teamRegistrationPlayers",
        "playerRegistrations",
        "members",
        "guests",
    ):
        url = f"{BASE}/team-registrations?filter[event_id]=2136&include[]={inc}&per_page=1"
        # unpaged returns list; take first with club_team absolute if possible
        resp = client.get(url)
        if resp.status_code != 200:
            print("include", inc, resp.status_code, resp.text[:120])
            continue
        data = resp.json()
        row = data[0] if isinstance(data, list) else (data.get("data") or [None])[0]
        if not row:
            print("include", inc, "empty")
            continue
        extra_keys = [k for k in row.keys() if k not in {"id", "event_division_id", "registration_status_id", "club_meta", "team_meta", "MODEL"}]
        print("include", inc, "extra", extra_keys, "keys", sorted(row.keys()))


if __name__ == "__main__":
    main()
