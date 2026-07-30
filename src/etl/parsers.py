"""Flexible extractors that tolerate varied TM2Sign JSON shapes."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable, Optional
from urllib.parse import urlparse


def _as_list(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in (
            "data",
            "results",
            "items",
            "events",
            "divisions",
            "matches",
            "teams",
            "clubs",
            "regions",
            "rankings",
            "standings",
            "rows",
            "value",
        ):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        # Single object that looks like an entity
        return [payload]
    return []


def _get(obj: dict, *keys, default=None):
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    # case-insensitive
    lower = {str(k).lower(): v for k, v in obj.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] is not None:
            return lower[k.lower()]
    return default


def _id(*vals) -> Optional[str]:
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _walk(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _infer_ids_from_url(url: str) -> dict[str, str]:
    out = {}
    path = urlparse(url).path
    patterns = {
        "event_id": r"/events?/([^/]+)",
        "division_id": r"/divisions?/([^/]+)",
        "team_id": r"/teams?/([^/]+)",
        "club_id": r"/clubs?/([^/]+)",
        "match_id": r"/matches?/([^/]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, path, re.I)
        if m:
            out[key] = m.group(1)
    return out


def extract_regions(payload: Any) -> list[dict]:
    rows = []
    for obj in _walk(payload):
        rid = _id(_get(obj, "region_id", "regionId", "RegionId", "id") if _looks_like(obj, "region") else None)
        name = _get(obj, "region_name", "regionName", "name", "RegionName")
        if rid and name and _looks_like(obj, "region"):
            rows.append(
                {
                    "region_id": str(rid),
                    "region_name": str(name),
                    "state": _get(obj, "state", "State", "state_code", "stateCode"),
                }
            )
        # Nested region fields on other entities
        nested_id = _id(_get(obj, "region_id", "regionId"))
        nested_name = _get(obj, "region_name", "regionName", "region")
        if nested_id and isinstance(nested_name, str):
            rows.append(
                {
                    "region_id": str(nested_id),
                    "region_name": nested_name,
                    "state": _get(obj, "state", "State"),
                }
            )
    return _dedupe(rows, ["region_id"])


def extract_clubs(payload: Any) -> list[dict]:
    rows = []
    for obj in _walk(payload):
        cid = _id(_get(obj, "club_id", "clubId", "ClubId"))
        name = _get(obj, "club_name", "clubName", "ClubName", "club")
        if not cid and _looks_like(obj, "club"):
            cid = _id(_get(obj, "id"))
            name = name or _get(obj, "name")
        if cid and name:
            rows.append(
                {
                    "club_id": str(cid),
                    "club_name": str(name) if not isinstance(name, dict) else str(_get(name, "name", default=name)),
                    "region_id": _id(_get(obj, "region_id", "regionId")),
                }
            )
    return _dedupe(rows, ["club_id"])


def extract_teams(payload: Any) -> list[dict]:
    rows = []
    for obj in _walk(payload):
        tid = _id(_get(obj, "team_id", "teamId", "TeamId"))
        name = _get(obj, "team_name", "teamName", "TeamName", "team")
        if not tid and _looks_like(obj, "team"):
            tid = _id(_get(obj, "id"))
            name = name or _get(obj, "name")
        if isinstance(name, dict):
            name = _get(name, "name", "team_name", "teamName")
        if tid and name:
            age = _get(obj, "age_group", "ageGroup", "AgeGroup", "age", "division_age")
            cohort = _get(obj, "cohort_year", "cohortYear", "year", "season")
            try:
                cohort = int(cohort) if cohort is not None else None
            except Exception:
                cohort = None
            rows.append(
                {
                    "team_id": str(tid),
                    "club_id": _id(_get(obj, "club_id", "clubId")),
                    "team_name": str(name),
                    "age_group": str(age) if age is not None else None,
                    "cohort_year": cohort,
                }
            )
        # team_a / team_b style
        for prefix in ("team_a", "team_b", "home", "away", "teamA", "teamB"):
            nested = _get(obj, prefix)
            if isinstance(nested, dict):
                rows.extend(extract_teams(nested))
            elif nested is not None and _get(obj, f"{prefix}_id", f"{prefix}Id"):
                rows.append(
                    {
                        "team_id": str(_get(obj, f"{prefix}_id", f"{prefix}Id")),
                        "club_id": None,
                        "team_name": str(nested),
                        "age_group": None,
                        "cohort_year": None,
                    }
                )
    return _dedupe(rows, ["team_id"])


def extract_events(payload: Any, source_url: str = "") -> list[dict]:
    inferred = _infer_ids_from_url(source_url)
    rows = []
    for obj in _walk(payload):
        eid = _id(_get(obj, "event_id", "eventId", "EventId"), inferred.get("event_id"))
        name = _get(obj, "event_name", "eventName", "EventName", "title", "name")
        if not eid and _looks_like(obj, "event"):
            eid = _id(_get(obj, "id"))
        if eid and name and (_looks_like(obj, "event") or "event" in str(obj.keys()).lower()):
            rows.append(
                {
                    "event_id": str(eid),
                    "event_name": str(name),
                    "start_date": _date(_get(obj, "start_date", "startDate", "StartDate", "start")),
                    "end_date": _date(_get(obj, "end_date", "endDate", "EndDate", "end")),
                    "location": _get(obj, "location", "Location", "venue", "city"),
                    "region_id": _id(_get(obj, "region_id", "regionId")),
                }
            )
    if not rows and inferred.get("event_id"):
        rows.append(
            {
                "event_id": inferred["event_id"],
                "event_name": f"Event {inferred['event_id']}",
                "start_date": None,
                "end_date": None,
                "location": None,
                "region_id": None,
            }
        )
    return _dedupe(rows, ["event_id"])


def extract_divisions(payload: Any, source_url: str = "") -> list[dict]:
    inferred = _infer_ids_from_url(source_url)
    rows = []
    for obj in _walk(payload):
        did = _id(_get(obj, "division_id", "divisionId", "DivisionId"), inferred.get("division_id"))
        name = _get(obj, "division_name", "divisionName", "DivisionName", "name")
        eid = _id(_get(obj, "event_id", "eventId"), inferred.get("event_id"))
        if not did and _looks_like(obj, "division"):
            did = _id(_get(obj, "id"))
        if did and name and eid:
            rows.append(
                {
                    "division_id": str(did),
                    "event_id": str(eid),
                    "division_name": str(name),
                    "age_group": _get(obj, "age_group", "ageGroup", "age"),
                    "gender": _get(obj, "gender", "Gender"),
                }
            )
    return _dedupe(rows, ["division_id"])


def extract_matches(payload: Any, source_url: str = "") -> list[dict]:
    inferred = _infer_ids_from_url(source_url)
    rows = []
    for obj in _walk(payload):
        mid = _id(_get(obj, "match_id", "matchId", "MatchId", "game_id", "gameId"))
        if not mid and _looks_like(obj, "match"):
            mid = _id(_get(obj, "id"))
        team_a = _id(_get(obj, "team_a_id", "teamAId", "home_team_id", "homeTeamId", "team1_id", "team1Id"))
        team_b = _id(_get(obj, "team_b_id", "teamBId", "away_team_id", "awayTeamId", "team2_id", "team2Id"))
        # Nested teams
        for attr, setter in (
            ("team_a", "a"),
            ("teamA", "a"),
            ("home_team", "a"),
            ("homeTeam", "a"),
            ("team_b", "b"),
            ("teamB", "b"),
            ("away_team", "b"),
            ("awayTeam", "b"),
        ):
            nested = _get(obj, attr)
            if isinstance(nested, dict):
                nid = _id(_get(nested, "id", "team_id", "teamId"))
                if setter == "a" and nid:
                    team_a = team_a or nid
                if setter == "b" and nid:
                    team_b = team_b or nid
        score_a = _get(obj, "team_a_score", "teamAScore", "home_score", "homeScore", "score1", "sets_won_a")
        score_b = _get(obj, "team_b_score", "teamBScore", "away_score", "awayScore", "score2", "sets_won_b")
        did = _id(_get(obj, "division_id", "divisionId"), inferred.get("division_id"))
        if mid and did and (team_a or team_b or score_a is not None):
            set_scores = _get(obj, "set_scores", "setScores", "sets", "games")
            if set_scores is not None and not isinstance(set_scores, str):
                set_scores = json.dumps(set_scores)
            winner = _id(_get(obj, "winner_id", "winnerId", "winner"))
            if not winner and score_a is not None and score_b is not None:
                try:
                    if int(score_a) > int(score_b):
                        winner = team_a
                    elif int(score_b) > int(score_a):
                        winner = team_b
                except Exception:
                    pass
            rows.append(
                {
                    "match_id": str(mid),
                    "division_id": str(did),
                    "match_date": _datetime(_get(obj, "match_date", "matchDate", "date", "scheduled_at", "start_time")),
                    "stage": _get(obj, "stage", "round", "phase", "bracket_round", "pool"),
                    "team_a_id": team_a,
                    "team_b_id": team_b,
                    "team_a_score": _int(score_a),
                    "team_b_score": _int(score_b),
                    "set_scores": set_scores,
                    "winner_id": winner,
                    "seed_a": _int(_get(obj, "seed_a", "seedA", "home_seed")),
                    "seed_b": _int(_get(obj, "seed_b", "seedB", "away_seed")),
                }
            )
    return _dedupe(rows, ["match_id"])


def extract_rankings(payload: Any, source_url: str = "") -> list[dict]:
    inferred = _infer_ids_from_url(source_url)
    rows = []
    for obj in _walk(payload):
        tid = _id(_get(obj, "team_id", "teamId"))
        if not tid and isinstance(_get(obj, "team"), dict):
            tid = _id(_get(_get(obj, "team"), "id", "team_id", "teamId"))
        eid = _id(_get(obj, "event_id", "eventId"), inferred.get("event_id"))
        did = _id(_get(obj, "division_id", "divisionId"), inferred.get("division_id"))
        rank = _get(obj, "final_rank", "finalRank", "rank", "place", "finish")
        seed = _get(obj, "initial_seed", "initialSeed", "seed", "seeding")
        if tid and eid and did and (rank is not None or seed is not None):
            rows.append(
                {
                    "event_id": str(eid),
                    "division_id": str(did),
                    "team_id": str(tid),
                    "initial_seed": _int(seed),
                    "final_rank": _int(rank),
                    "bracket_finish": _get(obj, "bracket_finish", "bracketFinish", "finish_label", "result"),
                }
            )
    return _dedupe(rows, ["event_id", "division_id", "team_id"])


def _looks_like(obj: dict, kind: str) -> bool:
    keys = " ".join(map(str, obj.keys())).lower()
    return kind.lower() in keys or any(k.lower() == "type" and str(obj.get(k)).lower() == kind for k in obj)


def _dedupe(rows: list[dict], keys: list[str]) -> list[dict]:
    seen = set()
    out = []
    for r in rows:
        key = tuple(r.get(k) for k in keys)
        if None in key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _date(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v)
    return s[:10] if len(s) >= 10 else s


def _datetime(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except Exception:
        return None
