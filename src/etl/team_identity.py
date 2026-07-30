"""Parse volleyball team names into age / tier / cross-year program identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


AGE_RE = re.compile(
    r"(?P<age>\b1[0-9]\b|\b[89]\b)(?:\s*U|\s*[-/]?\s*[A-Z0-9]{0,3})?",
    re.I,
)
# Common patterns:
#   Absolute 18 Black
#   Vision 17-1 Blue
#   SPVC 18 Adidas
#   Diablo 17-1 Black
#   Delta Valley 18 Blue
NAME_RE = re.compile(
    r"^(?P<club>.+?)\s+(?P<age>1[0-9]|[89])(?:U)?(?:\s*[-/]?\s*(?P<sub>[A-Z0-9]{1,3}))?\s+(?P<tier>.+)$",
    re.I,
)
NAME_RE_TIER_FIRST = re.compile(
    r"^(?P<club>.+?)\s+(?P<tier>[A-Za-z].+?)\s+(?P<age>1[0-9]|[89])(?:U)?$",
    re.I,
)


@dataclass
class ParsedTeamName:
    raw: str
    club_hint: Optional[str]
    age_num: Optional[int]
    tier: Optional[str]
    sub: Optional[str]
    gender_code: Optional[str]
    program_key: Optional[str]
    program_label: Optional[str]


def normalize_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def parse_alt_code(alt: Optional[str]) -> dict:
    """Parse TM2 alternate_identifier like G18ABSOL1NC -> gender/age/clubcode/slot/region."""
    if not alt:
        return {}
    m = re.match(r"^([GB])(\d{2})([A-Z0-9]+?)(\d)([A-Z]{2})$", alt.strip().upper())
    if not m:
        return {"alt_code": alt}
    gender, age, clubcode, slot, region = m.groups()
    return {
        "alt_code": alt.strip().upper(),
        "gender_code": gender,
        "age_num": int(age),
        "club_code": clubcode,
        "slot": slot,
        "region_code": region,
        # same-age continuity key across seasons
        "age_team_key": f"{gender}|{age}|{clubcode}|{slot}|{region}",
        # cross-year program key (age removed)
        "program_key_from_alt": f"{gender}|{clubcode}|{slot}|{region}",
    }


def parse_team_name(
    name: str,
    club_name: Optional[str] = None,
    alt_code: Optional[str] = None,
    gender_hint: Optional[str] = None,
) -> ParsedTeamName:
    raw = (name or "").strip()
    alt = parse_alt_code(alt_code)
    gender = alt.get("gender_code") or _gender_from_hint(gender_hint)

    club_hint = None
    age_num = alt.get("age_num")
    tier = None
    sub = None

    m = NAME_RE.match(raw)
    if m:
        club_hint = m.group("club").strip()
        age_num = age_num or int(m.group("age"))
        sub = m.group("sub")
        tier = m.group("tier").strip()
    else:
        m2 = NAME_RE_TIER_FIRST.match(raw)
        if m2:
            club_hint = m2.group("club").strip()
            tier = m2.group("tier").strip()
            age_num = age_num or int(m2.group("age"))
        else:
            # fallback age extract
            am = re.search(r"\b(1[0-9]|[89])\b", raw)
            if am:
                age_num = age_num or int(am.group(1))

    if not club_hint and club_name:
        club_hint = club_name.strip()

    # Prefer club_name for program identity when available
    club_for_key = (club_name or club_hint or "UNK").strip()
    tier_for_key = (tier or "OPEN").strip()

    program_label = None
    program_key = None
    if alt.get("program_key_from_alt"):
        program_key = alt["program_key_from_alt"]
        # Label from human name without age
        if club_hint and tier:
            program_label = f"{club_hint} {tier}".strip()
        elif club_name and tier:
            program_label = f"{club_name} {tier}".strip()
        else:
            program_label = re.sub(r"\b1[0-9]\b|\b[89]\b", "", raw).strip()
            program_label = re.sub(r"\s+", " ", program_label)
    elif club_for_key and tier_for_key:
        program_key = f"{normalize_token(club_for_key)}|{normalize_token(tier_for_key)}|{gender or 'U'}"
        program_label = f"{club_hint or club_for_key} {tier_for_key}".strip()

    return ParsedTeamName(
        raw=raw,
        club_hint=club_hint,
        age_num=age_num,
        tier=tier,
        sub=sub,
        gender_code=gender,
        program_key=program_key,
        program_label=program_label,
    )


def _gender_from_hint(hint: Optional[str]) -> Optional[str]:
    if not hint:
        return None
    h = hint.lower()
    if "girl" in h or h == "g" or h.startswith("g"):
        return "G"
    if "boy" in h or h == "b" or h.startswith("b"):
        return "B"
    return None


def age_label(age_num: Optional[int]) -> Optional[str]:
    if age_num is None:
        return None
    return f"{age_num}U"
