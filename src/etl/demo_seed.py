"""Rewrite demo seed with clean date handling."""

from __future__ import annotations

import json
import random
from pathlib import Path

from src.db import get_connection, init_database

RNG = random.Random(42)


def seed_demo_data() -> Path:
    init_database()
    conn = get_connection()
    try:
        regions = [
            ("R-SCVA", "Southern California", "CA"),
            ("R-NCVA", "Northern California", "CA"),
            ("R-AZ", "Arizona Region", "AZ"),
            ("R-PNW", "Pacific Northwest", "WA"),
        ]
        clubs = [
            ("C-COAST", "Coast Volleyball", "R-SCVA"),
            ("C-WAVE", "Wave VC", "R-SCVA"),
            ("C-BAY", "Bay Area Elite", "R-NCVA"),
            ("C-DESERT", "Desert Heat", "R-AZ"),
            ("C-RAIN", "Rain City VBC", "R-PNW"),
            ("C-SUN", "Sunshine Juniors", "R-SCVA"),
        ]
        ages = ["14U", "15U", "16U", "17U", "18U"]
        teams = []
        for club_id, club_name, _ in clubs:
            for age in ages:
                for letter in ("A", "B"):
                    tid = f"T-{club_id.split('-')[1]}-{age}-{letter}"
                    teams.append((tid, club_id, f"{club_name} {age}-{letter}", age, 2026))

        events = [
            ("E-QLX", "Quals Classic", "2026-01-10", "2026-01-12", "Long Beach, CA", "R-SCVA"),
            ("E-PAC", "Pacific Cup", "2026-02-14", "2026-02-16", "San Jose, CA", "R-NCVA"),
            ("E-DES", "Desert Invitational", "2026-03-07", "2026-03-09", "Phoenix, AZ", "R-AZ"),
            ("E-NAT", "National Warmup", "2026-04-18", "2026-04-20", "Las Vegas, NV", "R-SCVA"),
        ]
        event_dates = {e[0]: e[2] for e in events}

        divisions = []
        for eid, *_ in events:
            for age in ages:
                divisions.append((f"D-{eid}-{age}", eid, f"{age} Open", age, "Girls"))

        conn.executemany("INSERT OR REPLACE INTO regions VALUES (?,?,?)", regions)
        conn.executemany("INSERT OR REPLACE INTO clubs VALUES (?,?,?)", clubs)
        conn.executemany("INSERT OR REPLACE INTO teams VALUES (?,?,?,?,?)", teams)
        conn.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)", events)
        conn.executemany("INSERT OR REPLACE INTO divisions VALUES (?,?,?,?,?)", divisions)

        strength = {
            "C-COAST": 0.72,
            "C-WAVE": 0.65,
            "C-BAY": 0.60,
            "C-DESERT": 0.55,
            "C-RAIN": 0.50,
            "C-SUN": 0.58,
        }

        matches = []
        rankings = []
        match_n = 0

        for did, eid, _dname, age, _gender in divisions:
            age_teams = [t for t in teams if t[3] == age]
            RNG.shuffle(age_teams)
            seeded = list(enumerate(age_teams, start=1))
            pool = age_teams[:8]
            match_date = event_dates[eid]

            for i in range(len(pool)):
                for j in range(i + 1, len(pool)):
                    a, b = pool[i], pool[j]
                    match_n += 1
                    sa, sb, sets, winner = _play_match(a, b, strength)
                    matches.append(
                        (
                            f"M-{match_n:05d}",
                            did,
                            match_date,
                            "Pool",
                            a[0],
                            b[0],
                            sa,
                            sb,
                            json.dumps(sets),
                            winner,
                            next(s for s, t in seeded if t[0] == a[0]),
                            next(s for s, t in seeded if t[0] == b[0]),
                        )
                    )

            bracket = age_teams[:4]
            pairs = [(bracket[0], bracket[3]), (bracket[1], bracket[2])]
            finalists = []
            for a, b in pairs:
                match_n += 1
                sa, sb, sets, winner = _play_match(a, b, strength)
                matches.append(
                    (
                        f"M-{match_n:05d}",
                        did,
                        match_date,
                        "Bracket",
                        a[0],
                        b[0],
                        sa,
                        sb,
                        json.dumps(sets),
                        winner,
                        next(s for s, t in seeded if t[0] == a[0]),
                        next(s for s, t in seeded if t[0] == b[0]),
                    )
                )
                finalists.append(next(t for t in (a, b) if t[0] == winner))

            a, b = finalists[0], finalists[1]
            match_n += 1
            sa, sb, sets, winner = _play_match(a, b, strength)
            matches.append(
                (
                    f"M-{match_n:05d}",
                    did,
                    match_date,
                    "Bracket",
                    a[0],
                    b[0],
                    sa,
                    sb,
                    json.dumps(sets),
                    winner,
                    next(s for s, t in seeded if t[0] == a[0]),
                    next(s for s, t in seeded if t[0] == b[0]),
                )
            )

            finish_order = sorted(
                age_teams,
                key=lambda t: (-strength[t[1]] + RNG.uniform(-0.15, 0.15), t[0]),
            )
            for seed, team in seeded:
                final_rank = finish_order.index(team) + 1
                label = {1: "Gold", 2: "Silver", 3: "Bronze"}.get(final_rank, f"{final_rank}th")
                rankings.append((eid, did, team[0], seed, final_rank, label))

        conn.executemany(
            """
            INSERT OR REPLACE INTO matches
            (match_id, division_id, match_date, stage, team_a_id, team_b_id,
             team_a_score, team_b_score, set_scores, winner_id, seed_a, seed_b)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            matches,
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO rankings
            (event_id, division_id, team_id, initial_seed, final_rank, bracket_finish)
            VALUES (?,?,?,?,?,?)
            """,
            rankings,
        )
        conn.commit()
    finally:
        conn.close()
    return Path("database/volleyball.db")


def _play_match(team_a, team_b, strength):
    pa = strength[team_a[1]]
    pb = strength[team_b[1]]
    sets = []
    wins_a = wins_b = 0
    while wins_a < 2 and wins_b < 2:
        if RNG.random() < pa / (pa + pb):
            a_pts, b_pts = 25, RNG.randint(15, 24)
            if RNG.random() < 0.18:
                b_pts = RNG.choice([23, 24])
        else:
            b_pts, a_pts = 25, RNG.randint(15, 24)
            if RNG.random() < 0.18:
                a_pts = RNG.choice([23, 24])
        if wins_a == 1 and wins_b == 1:
            if a_pts >= b_pts:
                a_pts, b_pts = 15, RNG.randint(8, 14)
                if RNG.random() < 0.25:
                    b_pts = RNG.choice([13, 14])
            else:
                b_pts, a_pts = 15, RNG.randint(8, 14)
                if RNG.random() < 0.25:
                    a_pts = RNG.choice([13, 14])
        sets.append({"a": a_pts, "b": b_pts})
        if a_pts > b_pts:
            wins_a += 1
        else:
            wins_b += 1
    winner = team_a[0] if wins_a > wins_b else team_b[0]
    return wins_a, wins_b, sets, winner


if __name__ == "__main__":
    print(f"Seeded demo data -> {seed_demo_data()}")
