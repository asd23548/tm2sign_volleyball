"""Unit tests for NCVA points CSV/PDF parsers."""

from __future__ import annotations

from src.etl.ncva_points import parse_points_csv, parse_points_pdf_text, google_export_csv_url


SAMPLE_CSV = """,,,,2025-2026 Girl's Power League ,,,,,,,,,,,,,,,,,Bid Allocations as of 2/2/2026: 1 National / 1 American,
,,,,Points,,,,,,,,,,,,,,,,,,
,,,,11's  Division,,,PLQ,L1,L1,L1,L2,L2,L2,L3,L3,L3,Region,Region,Region,Season,Bid Notes,Previous Qualification(s)
Division,Place,Overall,,,Team Name,Team Code,Place,Place,Division,Points,Place,Division,Points,Place,Division,Points,Place,Division,Points,Total,Already Has Bid / Paperwork / Accepted / Declined,
Gold,1,1,,1,Absolute 11 Black,G11ABSOL1NC,5,4,Gold,296,7,Gold,439.5,10,Silver,580,6,Gold,882,2197.50,,
Gold,2,2,,2,Vision 11 Gold,G11VSION1NC,2,3,Gold,297,2,Gold,447,2,Gold,596,3,Gold,891,2231.00,11 National - Region,
"""


def test_google_export_url():
    url = "https://docs.google.com/spreadsheets/d/1_Xog0a8Lqf6COYTfp0B8575teSsfQoy5/edit?gid=239795665#gid=239795665"
    out = google_export_csv_url(url)
    assert "export?format=csv" in out
    assert "gid=239795665" in out


def test_parse_points_csv_rows():
    rows = parse_points_csv(
        SAMPLE_CSV,
        season_year=2026,
        gender="Girls",
        age_num=11,
        source_url="https://example.test/sheet",
    )
    assert len(rows) == 2
    abs_row = next(r for r in rows if r["team_code"] == "G11ABSOL1NC")
    assert abs_row["team_name"] == "Absolute 11 Black"
    assert abs_row["l1_points"] == 296
    assert abs_row["season_total"] == 2197.5
    assert abs_row["l3_division"] == "Silver"


def test_parse_points_pdf_line():
    text = (
        "1 Absolute 14 Black G14ABSOL1NC 1 1 Gold 300 3 Gold 445.5 2 Gold 596 1 Gold 900 2241.50 "
        "14 Open - Pacific Northwest"
    )
    rows = parse_points_pdf_text(
        text,
        season_year=2024,
        gender="Girls",
        age_num=14,
        source_url="https://example.test/x.pdf",
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["team_code"] == "G14ABSOL1NC"
    assert r["season_total"] == 2241.5
    assert r["l1_points"] == 300
    assert r["plq_place"] == 1
