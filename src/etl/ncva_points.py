"""Discover and parse NCVA Girls Power League points (PDF + Google Sheets)."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

GIRLS_POINTS_URL = "https://ncva.com/girls-power-league-points/"
TEAM_CODE_RE = re.compile(r"\b([GB]\d{2}[A-Z0-9]+NC)\b")
AGE_FROM_URL_RE = re.compile(r"(?:Points?[_\s-]|GPL_Points_|plpoints)(\d{2})(?:_|\.|$|\?)", re.I)
DIVISION_WORDS = {"Gold", "Silver", "Bronze", "Copper", "Platinum", "Open"}

# 2026 Google Sheet tabs (from NCVA page column order)
GOOGLE_GID_AGE = {
    "239795665": 11,
    "539092209": 12,
    "476909113": 13,
    "2086291804": 14,
    "486749021": 15,
    "2061013532": 16,
    "1333744874": 17,  # 17/18 shared workbook tab on the page
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tm2sign-ncva-points/1.0)"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s or s in {"—", "-", "N/A", "NA"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s or s in {"—", "-", "N/A", "NA"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def points_id(season_year: int, gender: str, age_num: int, team_code: str) -> str:
    return f"{season_year}|{gender}|{age_num}|{team_code}"


def google_export_csv_url(edit_url: str) -> str:
    """Convert a Sheets edit URL into a CSV export URL."""
    # https://docs.google.com/spreadsheets/d/{id}/edit?gid=123#gid=123
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", edit_url)
    if not m:
        return edit_url
    sheet_id = m.group(1)
    parsed = urlparse(edit_url)
    qs = parse_qs(parsed.query)
    gid = None
    if "gid" in qs:
        gid = qs["gid"][0]
    if not gid and parsed.fragment:
        frag = parse_qs(parsed.fragment.replace("?", "&"))
        if "gid" in frag:
            gid = frag["gid"][0]
    if gid:
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


def discover_girls_points_links(html: str | None = None) -> list[dict[str, Any]]:
    """Discover PDF / Google / CSV points assets from the NCVA girls points page.

    Prefer inferring year/age from URLs (recent seasons are not always in the
    first HTML table), then enrich from table cells when available.
    """
    if html is None:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=60) as client:
            html = client.get(GIRLS_POINTS_URL).text
    soup = BeautifulSoup(html, "html.parser")

    # Map href -> (year, age) from any year×age table layout
    table_meta: dict[str, tuple[int, Optional[int]]] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        ages: list[Optional[int]] = []
        for h in header_cells:
            ages.append(int(h) if re.fullmatch(r"\d{2}", h or "") else None)
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            year_txt = cells[0].get_text(" ", strip=True)
            if not re.fullmatch(r"\d{4}", year_txt or ""):
                continue
            season_year = int(year_txt)
            for i, td in enumerate(cells[1:], start=1):
                age_num = ages[i] if i < len(ages) else None
                for a in td.find_all("a", href=True):
                    href = urljoin(GIRLS_POINTS_URL, a["href"].strip())
                    table_meta[href] = (season_year, age_num)

    out: list[dict[str, Any]] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text or text in {"—", "-", "N/A"}:
            continue
        href = urljoin(GIRLS_POINTS_URL, a["href"].strip())
        if "tableau.com" in href:
            continue
        if re.search(r"\.pdf(?:\?|$)", href, re.I):
            source_type = "pdf"
        elif "docs.google.com" in href or "spreadsheets" in href:
            source_type = "google"
        elif re.search(r"\.csv(?:\?|$)", href, re.I):
            source_type = "csv"
        else:
            continue
        # skip non-points PDFs in footer/nav
        if source_type == "pdf" and not re.search(
            r"point|gpl|pl[\s_%-]?point|plpoints", href + " " + text, re.I
        ):
            continue

        season_year = None
        age_num = None
        if href in table_meta:
            season_year, age_num = table_meta[href]

        path_only = urlparse(href).path
        if season_year is None:
            ym = re.search(r"(20\d{2})", path_only)
            if ym:
                season_year = int(ym.group(1))
        if age_num is None:
            am = re.search(
                r"(?:Points?[_\s%-]*|GPL_Points_|plpoints)(\d{2})(?:_|\.|$|\?)",
                path_only,
                re.I,
            )
            if am:
                age_num = int(am.group(1))
            else:
                am2 = re.search(r"_(\d{2})(?:_Final)?\.pdf$", path_only, re.I)
                if am2:
                    age_num = int(am2.group(1))

        if source_type == "google":
            # Live sheet is current girls season; never parse years from gid digits
            season_year = 2026
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            gid = (qs.get("gid") or [None])[0]
            if not gid and parsed.fragment:
                frag = parse_qs(parsed.fragment.replace("?", "&"))
                gid = (frag.get("gid") or [None])[0]
            if gid and gid in GOOGLE_GID_AGE:
                age_num = GOOGLE_GID_AGE[gid]

        if season_year is None:
            continue
        # Prefer single-age finals over combined 17/18 workbook
        if "1817" in href:
            continue
        if age_num is None:
            continue

        out.append(
            {
                "season_year": season_year,
                "gender": "Girls",
                "age_num": age_num,
                "label": text,
                "source_url": href,
                "source_type": source_type,
            }
        )

    # Fill missing Google ages from known gid map scraped via table when possible
    # Dedup
    seen = set()
    deduped = []
    for row in out:
        if row.get("age_num") is None:
            continue
        key = (row["season_year"], row["age_num"], row["source_url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    deduped.sort(key=lambda r: (-r["season_year"], r["age_num"] or 0, r["source_type"]))
    return deduped


def _row_from_parts(
    *,
    season_year: int,
    gender: str,
    age_num: int,
    team_code: str,
    team_name: str,
    overall_division: Any = None,
    overall_place: Any = None,
    overall_rank: Any = None,
    plq_place: Any = None,
    l1_place: Any = None,
    l1_division: Any = None,
    l1_points: Any = None,
    l2_place: Any = None,
    l2_division: Any = None,
    l2_points: Any = None,
    l3_place: Any = None,
    l3_division: Any = None,
    l3_points: Any = None,
    region_place: Any = None,
    region_division: Any = None,
    region_points: Any = None,
    season_total: Any = None,
    bid_notes: Any = None,
    source_url: str,
    source_type: str,
) -> dict[str, Any]:
    return {
        "points_id": points_id(season_year, gender, age_num, team_code),
        "season_year": season_year,
        "gender": gender,
        "age_num": age_num,
        "team_code": team_code,
        "team_name": (team_name or "").strip() or None,
        "overall_division": (str(overall_division).strip() if overall_division not in (None, "") else None),
        "overall_place": _to_int(overall_place),
        "overall_rank": _to_int(overall_rank),
        "plq_place": _to_int(plq_place),
        "l1_place": _to_int(l1_place),
        "l1_division": (str(l1_division).strip() if l1_division not in (None, "") else None),
        "l1_points": _to_float(l1_points),
        "l2_place": _to_int(l2_place),
        "l2_division": (str(l2_division).strip() if l2_division not in (None, "") else None),
        "l2_points": _to_float(l2_points),
        "l3_place": _to_int(l3_place),
        "l3_division": (str(l3_division).strip() if l3_division not in (None, "") else None),
        "l3_points": _to_float(l3_points),
        "region_place": _to_int(region_place),
        "region_division": (str(region_division).strip() if region_division not in (None, "") else None),
        "region_points": _to_float(region_points),
        "season_total": _to_float(season_total),
        "bid_notes": (str(bid_notes).strip() if bid_notes not in (None, "") else None),
        "source_url": source_url,
        "source_type": source_type,
        "fetched_at": _utc_now(),
    }


def parse_points_csv(
    text: str,
    *,
    season_year: int,
    gender: str,
    age_num: int,
    source_url: str,
    source_type: str = "google",
) -> list[dict[str, Any]]:
    """Parse NCVA Google-export / CSV points layout."""
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 2:
        return []

    # Find header row containing Team Code
    header_idx = None
    for i, row in enumerate(rows[:12]):
        joined = ",".join(row).lower()
        if "team code" in joined and "team name" in joined:
            header_idx = i
            break
    if header_idx is None:
        return []

    header = [c.strip() for c in rows[header_idx]]
    # Normalize to expected positional layout used by NCVA sheets
    # Fall back to index map by scanning header labels
    lower = [h.lower() for h in header]

    def find(*names: str) -> Optional[int]:
        for n in names:
            if n in lower:
                return lower.index(n)
        return None

    # Preferred fixed layout from 2026 sheet
    idx = {
        "overall_division": 0,
        "overall_place": 1,
        "overall_rank": 2,
        "team_name": find("team name") or 5,
        "team_code": find("team code") or 6,
        "plq_place": 7,
        "l1_place": 8,
        "l1_division": 9,
        "l1_points": 10,
        "l2_place": 11,
        "l2_division": 12,
        "l2_points": 13,
        "l3_place": 14,
        "l3_division": 15,
        "l3_points": 16,
        "region_place": 17,
        "region_division": 18,
        "region_points": 19,
        "season_total": find("total") or 20,
        "bid_notes": 21,
    }

    out: list[dict[str, Any]] = []
    for raw in rows[header_idx + 1 :]:
        if len(raw) <= idx["team_code"]:
            continue
        code = (raw[idx["team_code"]] or "").strip()
        if not TEAM_CODE_RE.fullmatch(code):
            # sometimes code is elsewhere
            joined = " ".join(raw)
            m = TEAM_CODE_RE.search(joined)
            if not m:
                continue
            code = m.group(1)

        def cell(key: str) -> Any:
            i = idx[key]
            return raw[i] if i < len(raw) else None

        out.append(
            _row_from_parts(
                season_year=season_year,
                gender=gender,
                age_num=age_num,
                team_code=code,
                team_name=str(cell("team_name") or ""),
                overall_division=cell("overall_division"),
                overall_place=cell("overall_place"),
                overall_rank=cell("overall_rank"),
                plq_place=cell("plq_place"),
                l1_place=cell("l1_place"),
                l1_division=cell("l1_division"),
                l1_points=cell("l1_points"),
                l2_place=cell("l2_place"),
                l2_division=cell("l2_division"),
                l2_points=cell("l2_points"),
                l3_place=cell("l3_place"),
                l3_division=cell("l3_division"),
                l3_points=cell("l3_points"),
                region_place=cell("region_place"),
                region_division=cell("region_division"),
                region_points=cell("region_points"),
                season_total=cell("season_total"),
                bid_notes=cell("bid_notes"),
                source_url=source_url,
                source_type=source_type,
            )
        )
    return out


def _split_tokens(line: str) -> list[str]:
    return [t for t in re.split(r"\s+", line.strip()) if t]


def parse_points_pdf_text(
    text: str,
    *,
    season_year: int,
    gender: str,
    age_num: int,
    source_url: str,
) -> list[dict[str, Any]]:
    """Heuristic parse of NCVA points PDFs via extracted text."""
    out: list[dict[str, Any]] = []
    # Work line-by-line; team rows contain a team code
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        m = TEAM_CODE_RE.search(line)
        if not m:
            continue
        code = m.group(1)
        before = line[: m.start()].strip()
        after = line[m.end() :].strip()

        # before: optional overall rank + team name
        overall_rank = None
        team_name = before
        bm = re.match(r"^(\d+)\s+(.*)$", before)
        if bm:
            overall_rank = int(bm.group(1))
            team_name = bm.group(2).strip()

        tokens = _split_tokens(after)
        # Expected: PLQ_place, L1_place, L1_div, L1_pts, L2_place, L2_div, L2_pts,
        #           L3_place, L3_div, L3_pts, Region_place, Region_div, Region_pts,
        #           season_total, bid notes...
        if len(tokens) < 14:
            continue

        def take_stage(start: int) -> tuple[Any, Any, Any, int]:
            # place, division, points
            place = tokens[start]
            if start + 1 >= len(tokens):
                return place, None, None, start + 1
            if tokens[start + 1] in DIVISION_WORDS:
                div = tokens[start + 1]
                pts = tokens[start + 2] if start + 2 < len(tokens) else None
                return place, div, pts, start + 3
            # no division word — maybe points only? uncommon
            return place, None, tokens[start + 1], start + 2

        i = 0
        plq = tokens[i]
        i += 1
        l1_place, l1_div, l1_pts, i = take_stage(i)
        l2_place, l2_div, l2_pts, i = take_stage(i)
        l3_place, l3_div, l3_pts, i = take_stage(i)
        region_place, region_div, region_pts, i = take_stage(i)
        season_total = tokens[i] if i < len(tokens) else None
        bid_notes = " ".join(tokens[i + 1 :]) if i + 1 < len(tokens) else None

        out.append(
            _row_from_parts(
                season_year=season_year,
                gender=gender,
                age_num=age_num,
                team_code=code,
                team_name=team_name,
                overall_rank=overall_rank,
                overall_place=overall_rank,
                plq_place=plq,
                l1_place=l1_place,
                l1_division=l1_div,
                l1_points=l1_pts,
                l2_place=l2_place,
                l2_division=l2_div,
                l2_points=l2_pts,
                l3_place=l3_place,
                l3_division=l3_div,
                l3_points=l3_pts,
                region_place=region_place,
                region_division=region_div,
                region_points=region_pts,
                season_total=season_total,
                bid_notes=bid_notes,
                source_url=source_url,
                source_type="pdf",
            )
        )
    # Dedup by team_code keeping first
    by_code: dict[str, dict] = {}
    for row in out:
        by_code.setdefault(row["team_code"], row)
    return list(by_code.values())


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def fetch_and_parse_source(source: dict[str, Any], client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Download one discovered source and parse rows."""
    owns = client is None
    client = client or httpx.Client(headers=HEADERS, follow_redirects=True, timeout=90)
    try:
        season_year = int(source["season_year"])
        gender = source.get("gender") or "Girls"
        age_num = source.get("age_num")
        if age_num is None:
            raise ValueError(f"missing age_num for {source['source_url']}")
        age_num = int(age_num)
        url = source["source_url"]
        stype = source["source_type"]

        if stype == "google":
            export = google_export_csv_url(url)
            text = client.get(export).text
            return parse_points_csv(
                text,
                season_year=season_year,
                gender=gender,
                age_num=age_num,
                source_url=url,
                source_type="google",
            )
        if stype == "csv":
            text = client.get(url).text
            return parse_points_csv(
                text,
                season_year=season_year,
                gender=gender,
                age_num=age_num,
                source_url=url,
                source_type="csv",
            )
        if stype == "pdf":
            data = client.get(url).content
            text = extract_pdf_text(data)
            return parse_points_pdf_text(
                text,
                season_year=season_year,
                gender=gender,
                age_num=age_num,
                source_url=url,
            )
        return []
    finally:
        if owns:
            client.close()
