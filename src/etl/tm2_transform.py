"""Transform TM2Sign API payloads into SQLite hierarchy rows."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional


def _sid(*vals) -> Optional[str]:
    for v in vals:
        if v is None or v == "":
            continue
        return str(v)
    return None


def _age_label(age: Any) -> Optional[str]:
    if age is None or age == "":
        return None
    s = str(age)
    if s.upper().endswith("U"):
        return s.upper()
    if re.fullmatch(r"\d+", s):
        return f"{s}U"
    return s


def region_from_team(team: dict[str, Any]) -> Optional[dict[str, Any]]:
    region = team.get("region") or ""
    state = team.get("state") or None
    if not region and not state:
        return None
    rid = _sid(region) or _sid(state)
    if not rid:
        return None
    return {
        "region_id": f"R-{rid}",
        "region_name": str(region or state),
        "state": state,
    }


def region_from_event(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    state = event.get("state")
    if not state:
        return None
    return {
        "region_id": f"R-{state}",
        "region_name": str(state),
        "state": str(state),
    }


def club_from_scheduler_team(team: dict[str, Any]) -> Optional[dict[str, Any]]:
    name = team.get("club_name")
    if not name:
        return None
    # Prefer stable club_team club if present later; here hash by name+region
    region = team.get("region") or team.get("state") or "UNK"
    slug = re.sub(r"[^A-Za-z0-9]+", "", str(name))[:24] or "CLUB"
    rid = region_from_team(team)
    return {
        "club_id": f"C-{slug}-{region}",
        "club_name": str(name),
        "region_id": rid["region_id"] if rid else None,
    }


def club_from_registration(reg: dict[str, Any]) -> Optional[dict[str, Any]]:
    meta = reg.get("club_meta") or {}
    cid = meta.get("id")
    name = meta.get("alias") or meta.get("name")
    if not cid or not name:
        return None
    region = meta.get("usav_region") or meta.get("state") or ""
    region_id = f"R-{region}" if region else None
    return {
        "club_id": f"C-{cid}",
        "club_name": str(name),
        "region_id": region_id,
    }


def team_from_scheduler_team(team: dict[str, Any], age_by_division: dict[str, str] | None = None) -> dict[str, Any]:
    from src.etl.team_identity import age_label, parse_alt_code, parse_team_name

    club = club_from_scheduler_team(team)
    age = None
    if age_by_division:
        age = age_by_division.get(str(team.get("event_division_id")))
    parsed = parse_team_name(
        str(team.get("name") or ""),
        club_name=team.get("club_name"),
        alt_code=team.get("alternate_identifier"),
    )
    alt = parse_alt_code(team.get("alternate_identifier"))
    if not age and parsed.age_num:
        age = age_label(parsed.age_num)
    return {
        "team_id": f"ST-{team['id']}",
        "club_id": club["club_id"] if club else None,
        "team_name": str(team.get("name") or f"Team {team['id']}"),
        "age_group": age,
        "cohort_year": None,
        "club_team_id": str(team["club_team_id"]) if team.get("club_team_id") is not None else None,
        "alt_code": alt.get("alt_code") or team.get("alternate_identifier"),
        "age_num": parsed.age_num,
        "tier_label": parsed.tier,
        "gender_code": parsed.gender_code,
        "program_id": parsed.program_key,
        "program_label": parsed.program_label,
        "age_team_key": alt.get("age_team_key"),
    }


def team_from_registration(reg: dict[str, Any]) -> Optional[dict[str, Any]]:
    tm = reg.get("team_meta") or {}
    tid = tm.get("id")
    if not tid:
        return None
    club = club_from_registration(reg)
    return {
        "team_id": f"CT-{tid}",
        "club_id": club["club_id"] if club else None,
        "team_name": str(tm.get("alias") or tm.get("name") or f"Team {tid}"),
        "age_group": _age_label(tm.get("age_group")),
        "cohort_year": None,
    }


def event_row(event: dict[str, Any]) -> dict[str, Any]:
    region = region_from_event(event)
    venues = event.get("venues") or []
    location = None
    if isinstance(venues, list) and venues:
        v0 = venues[0] if isinstance(venues[0], dict) else {}
        location = v0.get("name") or v0.get("city") or event.get("state")
    else:
        location = event.get("state")
    return {
        "event_id": str(event["id"]),
        "event_name": str(event.get("name") or f"Event {event['id']}"),
        "start_date": _date(event.get("start_date")),
        "end_date": _date(event.get("end_date")),
        "location": location,
        "region_id": region["region_id"] if region else None,
    }


def division_rows(event: dict[str, Any]) -> list[dict[str, Any]]:
    divs = event.get("eventDivisions") or event.get("event_divisions") or []
    rows = []
    for d in divs:
        name = str(d.get("name") or f"Division {d.get('id')}")
        age = _infer_age_from_name(name) or _age_label(d.get("abbreviation"))
        rows.append(
            {
                "division_id": str(d["id"]),
                "event_id": str(d.get("event_id") or event["id"]),
                "division_name": name,
                "age_group": age,
                "gender": _infer_gender(name),
            }
        )
    return rows


def match_row(match: dict[str, Any]) -> Optional[dict[str, Any]]:
    mid = match.get("id")
    did = match.get("event_division_id")
    if mid is None or did is None:
        return None
    a = match.get("position_one_scheduler_team_id")
    b = match.get("position_two_scheduler_team_id")
    winner = match.get("winning_scheduler_team_id")
    sets = []
    for idx, key in enumerate(["one", "two", "three", "four", "five"], start=1):
        sa = match.get(f"position_one_score_{key}")
        sb = match.get(f"position_two_score_{key}")
        if sa is None and sb is None:
            continue
        sets.append({"a": sa, "b": sb, "set": idx})
    stage_raw = (match.get("pool_bracket_type") or "").lower()
    if "bracket" in stage_raw or "playoff" in stage_raw:
        stage = "Bracket"
    elif "pool" in stage_raw:
        stage = "Pool"
    else:
        stage = stage_raw.title() or None
    match_date = None
    if match.get("start_time"):
        try:
            match_date = datetime.fromtimestamp(int(match["start_time"]), tz=timezone.utc).isoformat()
        except Exception:
            match_date = str(match.get("start_time"))
    return {
        "match_id": str(mid),
        "division_id": str(did),
        "match_date": match_date,
        "stage": stage,
        "team_a_id": f"ST-{a}" if a else None,
        "team_b_id": f"ST-{b}" if b else None,
        "team_a_score": match.get("position_one_match_set_wins"),
        "team_b_score": match.get("position_two_match_set_wins"),
        "set_scores": json.dumps(sets) if sets else None,
        "winner_id": f"ST-{winner}" if winner else None,
        "seed_a": match.get("position_one_seed_number"),
        "seed_b": match.get("position_two_seed_number"),
    }


def ranking_row(team: dict[str, Any], event_id: Any) -> Optional[dict[str, Any]]:
    tid = team.get("id")
    did = team.get("event_division_id")
    if tid is None or did is None:
        return None
    return {
        "event_id": str(event_id),
        "division_id": str(did),
        "team_id": f"ST-{tid}",
        "initial_seed": team.get("starting_seed_number"),
        "final_rank": team.get("final_finish_position_number"),
        "bracket_finish": team.get("final_finish_note"),
    }


def _infer_age_from_name(name: str) -> Optional[str]:
    m = re.search(r"(1[1-9]|1[0-9])\s*U", name, re.I)
    if m:
        return f"{m.group(1)}U"
    m = re.search(r"\b(1[1-9])\b", name)
    if m:
        return f"{m.group(1)}U"
    return None


def _infer_gender(name: str) -> Optional[str]:
    lower = name.lower()
    if "girl" in lower or re.search(r"\bg\b", lower):
        return "Girls"
    if "boy" in lower or re.search(r"\bb\b", lower):
        return "Boys"
    return None


def _date(v: Any) -> Optional[str]:
    if not v:
        return None
    return str(v)[:10]
