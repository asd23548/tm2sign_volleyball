"""Normalize roster payloads into players/staff rows."""

from __future__ import annotations

import re
from typing import Any, Optional


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def person_id(first_name: str, last_name: str, gender_code: Optional[str] = None) -> str:
    g = (gender_code or "U").upper()[:1]
    return f"{g}|{_norm(last_name)}|{_norm(first_name)}"


def full_name(first_name: str, last_name: str) -> str:
    return f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()


def player_rows_from_roster(
    roster: dict[str, Any],
    *,
    team_id: str,
    event_id: str,
    program_id: Optional[str],
    age_group: Optional[str],
    season_year: Optional[int],
    gender_code: Optional[str],
    club_id: Optional[str],
) -> tuple[list[dict], list[dict]]:
    players = []
    stints = []
    for p in roster.get("players") or []:
        first = str(p.get("first_name") or "").strip()
        last = str(p.get("last_name") or "").strip()
        if not first and not last:
            continue
        pid = person_id(first, last, gender_code)
        players.append(
            {
                "player_id": pid,
                "full_name": full_name(first, last),
                "first_name": first,
                "last_name": last,
                "gender": gender_code,
                "grad_year": None,
            }
        )
        stints.append(
            {
                "player_id": pid,
                "event_id": str(event_id),
                "team_id": team_id,
                "program_id": program_id,
                "age_group": age_group,
                "season_year": season_year,
                "club_id": club_id,
                "uniform_number": p.get("uniform_number"),
                "role": "player",
            }
        )
    return players, stints


def staff_rows_from_roster(
    roster: dict[str, Any],
    *,
    team_id: str,
    event_id: str,
    program_id: Optional[str],
    season_year: Optional[int],
    gender_code: Optional[str],
    club_id: Optional[str],
) -> tuple[list[dict], list[dict]]:
    staff = []
    stints = []
    for s in roster.get("staff") or []:
        first = str(s.get("first_name") or "").strip()
        last = str(s.get("last_name") or "").strip()
        if not first and not last:
            continue
        sid = person_id(first, last, gender_code)
        position = s.get("position") or "staff"
        staff.append(
            {
                "staff_id": sid,
                "full_name": full_name(first, last),
                "first_name": first,
                "last_name": last,
                "gender": gender_code,
            }
        )
        stints.append(
            {
                "staff_id": sid,
                "event_id": str(event_id),
                "team_id": team_id,
                "program_id": program_id,
                "season_year": season_year,
                "club_id": club_id,
                "position": position,
            }
        )
    return staff, stints
