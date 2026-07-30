"""Discover pagination for scheduler-matches (appears capped at 100)."""

from __future__ import annotations

import json

import httpx

BASE = "https://tm2sign.com/api/public"
HEADERS = {"Accept": "application/json"}
EID = 2136  # 2026 Girls Power League — large


def main() -> None:
    client = httpx.Client(timeout=90.0, follow_redirects=True, headers=HEADERS)
    probes = [
        f"{BASE}/scheduler-matches?filter[event_id]={EID}",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&per_page=500",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&limit=500",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&page=1&per_page=100",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&page=2&per_page=100",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&page=3&per_page=100",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&page[number]=2&page[size]=100",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&offset=100&limit=100",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&skip=100&take=100",
        f"{BASE}/scheduler-matches?filter[event_id]={EID}&include[]=all",
    ]
    # Also try by division
    ev = client.get(f"{BASE}/events/{EID}?include[]=eventDivisions").json()
    divs = ev.get("eventDivisions") or []
    print("divisions", [(d["id"], d["name"]) for d in divs])
    for d in divs[:3]:
        did = d["id"]
        probes.append(f"{BASE}/scheduler-matches?filter[event_division_id]={did}")
        probes.append(f"{BASE}/scheduler-matches?filter[event_division_id]={did}&per_page=500")
        probes.append(
            f"{BASE}/scheduler-matches?filter[event_id]={EID}&filter[event_division_id]={did}"
        )

    for url in probes:
        resp = client.get(url)
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            print(resp.status_code, "NONJSON", url.replace(BASE, ""), resp.text[:80])
            continue
        data = resp.json()
        if isinstance(data, list):
            ids = [x.get("id") for x in data[:3]]
            print(resp.status_code, f"list n={len(data)}", url.replace(BASE, ""), "ids", ids)
        elif isinstance(data, dict):
            print(
                resp.status_code,
                "dict keys",
                list(data.keys())[:12],
                "n",
                len(data.get("data") or []),
                url.replace(BASE, ""),
            )
        else:
            print(resp.status_code, type(data), url.replace(BASE, ""))


if __name__ == "__main__":
    main()
