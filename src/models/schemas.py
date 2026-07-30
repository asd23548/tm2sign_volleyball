"""Pydantic schemas for the 4-level volleyball hierarchy."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class Region(BaseModel):
    region_id: str
    region_name: str
    state: Optional[str] = None


class Club(BaseModel):
    club_id: str
    club_name: str
    region_id: Optional[str] = None


class Team(BaseModel):
    team_id: str
    club_id: Optional[str] = None
    team_name: str
    age_group: Optional[str] = None
    cohort_year: Optional[int] = None


class Event(BaseModel):
    event_id: str
    event_name: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    location: Optional[str] = None
    region_id: Optional[str] = None


class Division(BaseModel):
    division_id: str
    event_id: str
    division_name: str
    age_group: Optional[str] = None
    gender: Optional[str] = None


class Match(BaseModel):
    match_id: str
    division_id: str
    match_date: Optional[datetime] = None
    stage: Optional[str] = None  # Pool / Bracket
    team_a_id: Optional[str] = None
    team_b_id: Optional[str] = None
    team_a_score: Optional[int] = None
    team_b_score: Optional[int] = None
    set_scores: Optional[list[dict[str, Any]]] = None
    winner_id: Optional[str] = None
    seed_a: Optional[int] = None
    seed_b: Optional[int] = None


class Ranking(BaseModel):
    event_id: str
    division_id: str
    team_id: str
    initial_seed: Optional[int] = None
    final_rank: Optional[int] = None
    bracket_finish: Optional[str] = None


class ApiEndpoint(BaseModel):
    url: str
    method: str = "GET"
    resource_hint: Optional[str] = None
    status: Optional[int] = None
    content_type: Optional[str] = None
    sample_keys: list[str] = Field(default_factory=list)
    sample_payload: Optional[Any] = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)