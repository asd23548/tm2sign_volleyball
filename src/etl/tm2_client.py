"""High-speed TM2Sign public API client using discovered routes."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

BASE = "https://tm2sign.com/api/public"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}

KNOWN_ENDPOINTS = [
    {
        "url": f"{BASE}/events?filter[dateRange]=past&include[]=eventDivisions&per_page=100",
        "method": "GET",
        "resource_hint": "events",
        "sample_keys": ["data", "data[].id", "data[].name", "data[].eventDivisions"],
    },
    {
        "url": f"{BASE}/events-yearmonths",
        "method": "GET",
        "resource_hint": "events",
        "sample_keys": ["2026", "2027"],
    },
    {
        "url": f"{BASE}/scheduler-matches?filter[event_id={{event_id}}]&page=1&per_page=100",
        "method": "GET",
        "resource_hint": "matches",
        "sample_keys": [
            "data",
            "data[].id",
            "data[].event_id",
            "data[].event_division_id",
            "data[].pool_bracket_type",
            "data[].position_one_scheduler_team_id",
            "data[].winning_scheduler_team_id",
            "last_page",
            "total",
        ],
    },
    {
        "url": f"{BASE}/scheduler-teams?filter[event_id={{event_id}}]&page=1&per_page=100",
        "method": "GET",
        "resource_hint": "teams",
        "sample_keys": [
            "data",
            "data[].id",
            "data[].name",
            "data[].club_name",
            "data[].starting_seed_number",
            "data[].final_finish_position_number",
            "last_page",
            "total",
        ],
    },
    {
        "url": f"{BASE}/team-registrations?filter[event_id={{event_id}}]",
        "method": "GET",
        "resource_hint": "teams",
        "sample_keys": ["id", "club_meta", "team_meta", "event_division_id"],
    },
    {
        "url": f"{BASE}/scheduler-rounds?filter[event_id={{event_id}}]",
        "method": "GET",
        "resource_hint": "division",
    },
    {
        "url": f"{BASE}/scheduler-groups?filter[event_id={{event_id}}]",
        "method": "GET",
        "resource_hint": "division",
    },
    {
        "url": f"{BASE}/scheduler-teams/{{scheduler_team_id}}/roster",
        "method": "GET",
        "resource_hint": "players",
        "sample_keys": ["players", "players[].first_name", "players[].last_name", "players[].uniform_number", "staff", "staff[].position"],
    },
]
POWER_LEAGUE_NAME = re.compile(r"ncva.*power\s*league|power\s*league.*ncva", re.I)


class TM2Client:
    def __init__(self, timeout: float = 90.0, max_workers: int = 8) -> None:
        self.client = httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True)
        self.max_workers = max_workers

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "TM2Client":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @retry(wait=wait_exponential(multiplier=1.0, min=1, max=60), stop=stop_after_attempt(8))
    def get_json(self, url: str) -> Any:
        resp = self.client.get(url)
        if resp.status_code == 429:
            # Respect rate limit; raise so tenacity backs off
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                import time

                try:
                    time.sleep(min(float(retry_after), 60))
                except Exception:
                    time.sleep(5)
            resp.raise_for_status()
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            raise ValueError(f"Non-JSON response for {url}: {ctype}")
        return resp.json()

    def _paginate(
        self,
        url_for_page: Callable[[int], str],
        per_page: int = 100,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all Laravel-paginated pages. Page 1 discovers last_page/total."""
        first = self.get_json(url_for_page(1))
        if isinstance(first, list):
            # Unpaginated truncated list — retry with explicit page params already in url
            return first

        rows = list(first.get("data") or [])
        last_page = int(first.get("last_page") or 1)
        total = int(first.get("total") or len(rows))
        if progress:
            progress(1, last_page, total)
        if last_page <= 1:
            return rows

        def fetch(page: int) -> tuple[int, list[dict[str, Any]]]:
            payload = self.get_json(url_for_page(page))
            if isinstance(payload, list):
                return page, payload
            return page, list(payload.get("data") or [])

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(fetch, p) for p in range(2, last_page + 1)]
            by_page: dict[int, list[dict[str, Any]]] = {}
            done = 1
            for fut in as_completed(futures):
                page, batch = fut.result()
                by_page[page] = batch
                done += 1
                if progress:
                    progress(done, last_page, total)

        for page in range(2, last_page + 1):
            rows.extend(by_page.get(page) or [])
        return rows

    def iter_events(
        self,
        date_range: str = "past",
        max_pages: int = 5,
        per_page: int = 100,
        include_divisions: bool = True,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        includes = "&include[]=eventDivisions" if include_divisions else ""
        search_q = f"&filter[search]={search}" if search else ""
        for page in range(1, max_pages + 1):
            url = (
                f"{BASE}/events?filter[dateRange]={date_range}"
                f"{search_q}{includes}&per_page={per_page}&page={page}"
            )
            payload = self.get_json(url)
            batch = payload.get("data", payload) if isinstance(payload, dict) else payload
            if not batch:
                break
            events.extend(batch)
            if isinstance(payload, dict):
                last = payload.get("last_page") or page
                if page >= last:
                    break
        return events

    def find_power_league_events(self) -> list[dict[str, Any]]:
        """Discover all NCVA Power League seasons (past + future)."""
        found: dict[Any, dict[str, Any]] = {}
        for date_range in ("past", "future"):
            events = self.iter_events(
                date_range=date_range,
                max_pages=50,
                per_page=100,
                include_divisions=True,
            )
            for ev in events:
                name = ev.get("name") or ""
                if POWER_LEAGUE_NAME.search(name) or (
                    "power league" in name.lower() and "ncva" in name.lower()
                ):
                    found[ev["id"]] = ev
        # Search fallback for naming variants
        for q in ("Power League", "NCVA Power League", "Boys Power League", "Girls Power League"):
            for date_range in ("past", "future"):
                try:
                    for ev in self.iter_events(
                        date_range=date_range,
                        max_pages=5,
                        search=q,
                        include_divisions=True,
                    ):
                        name = ev.get("name") or ""
                        if "power league" in name.lower() and "ncva" in name.lower():
                            found[ev["id"]] = ev
                except Exception:
                    continue
        return sorted(found.values(), key=lambda e: (e.get("start_date") or "", e.get("id") or 0))

    def event_detail(self, event_id: int | str) -> dict[str, Any]:
        return self.get_json(f"{BASE}/events/{event_id}?include[]=eventDivisions")

    def scheduler_matches(
        self,
        event_id: int | str,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        return self._paginate(
            lambda page: (
                f"{BASE}/scheduler-matches?filter[event_id]={event_id}"
                f"&page={page}&per_page=100"
            ),
            per_page=100,
            progress=progress,
        )

    def scheduler_teams(
        self,
        event_id: int | str,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        # API often caps page size ~20 for teams; request 100 and accept whatever returns.
        return self._paginate(
            lambda page: (
                f"{BASE}/scheduler-teams?filter[event_id]={event_id}"
                f"&page={page}&per_page=100"
            ),
            per_page=100,
            progress=progress,
        )

    def team_registrations(self, event_id: int | str) -> list[dict[str, Any]]:
        data = self.get_json(f"{BASE}/team-registrations?filter[event_id]={event_id}")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.get("data") or [])
        return []

    def scheduler_team_roster(self, scheduler_team_id: int | str) -> dict[str, Any]:
        """Public roster payload: {players: [...], staff: [...]}."""
        sid = str(scheduler_team_id).removeprefix("ST-")
        data = self.get_json(f"{BASE}/scheduler-teams/{sid}/roster")
        if isinstance(data, dict):
            return {
                "players": list(data.get("players") or []),
                "staff": list(data.get("staff") or []),
            }
        return {"players": [], "staff": []}

    def scheduler_team_detail(self, scheduler_team_id: int | str) -> dict[str, Any]:
        sid = str(scheduler_team_id).removeprefix("ST-")
        data = self.get_json(f"{BASE}/scheduler-teams/{sid}")
        return data if isinstance(data, dict) else {}


def write_known_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    endpoints = existing.get("endpoints") or []
    by_url = {e.get("url"): e for e in endpoints}
    for ep in KNOWN_ENDPOINTS:
        by_url[ep["url"]] = {
            **by_url.get(ep["url"], {}),
            **ep,
            "status": 200,
            "content_type": "application/json",
        }
    out = {
        **existing,
        "discovered_at": datetime.utcnow().isoformat() + "Z",
        "endpoint_count": len(by_url),
        "endpoints": list(by_url.values()),
        "notes": (
            "Merged Playwright discovery with confirmed TM2Sign public routes "
            "(/api/public/events, paginated scheduler-matches/teams, team-registrations)."
        ),
        "base_url": BASE,
    }
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
