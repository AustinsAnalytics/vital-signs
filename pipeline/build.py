"""Build the Vital Signs database: one row per pitcher appearance, and every pitcher injured-list placement.

    python pipeline/build.py     -> data/vitals.duckdb, data/out/build_checks.json

appearances: per pitcher-game, the primary fastball's velocity, spin, extension, release height/side and arm angle
             (means and counts), workload, days since the previous appearance, starter flag, park.
il_events:   pitcher IL placements parsed from MLB transactions: injury date (retroactive date when given), list
             length, injury text and body-part group (elbow/forearm, shoulder/lat, other arm, non-arm).
"""
import json
import re
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "vitals.duckdb"
OUT = ROOT / "data" / "out"
FASTBALLS = ("FF", "SI", "FC")

BODY = [
    ("impact", r"contusion|laceration|bruis"),          # hit by a ball or a collision, not wear on the arm
    ("elbow_forearm", r"elbow|ucl|ulnar|tommy john|flexor|forearm|pronator|internal brace"),
    ("shoulder_lat", r"shoulder|rotator|labrum|capsul|lat\b|latissimus|teres|scapula|subscap"),
    ("other_arm", r"biceps|triceps|wrist|hand|finger|blister|thumb|radial nerve|thoracic outlet|arm\b"),
]
PLACED = re.compile(r"placed (RHP|LHP|P) .*? on the (\d+)-day injured list")
RETRO = re.compile(r"retroactive to ([A-Z][a-z]+ \d{1,2}, \d{4})")


def body_group(text):
    t = (text or "").lower()
    for name, pat in BODY:
        if re.search(pat, t):
            return name
    return "non_arm" if t.strip() else "unspecified"


def parse_placement(t):
    """One MLB transaction -> an injured-list placement record, or None if it isn't a pitcher placement."""
    d = t.get("description") or ""
    m = PLACED.search(d)
    if not m or "transferred" in d.lower():
        return None
    retro = RETRO.search(d)
    injury = d.split("injured list", 1)[1]
    injury = re.sub(r"^\s*(retroactive to [A-Z][a-z]+ \d{1,2}, \d{4})?\.?\s*", "", injury).strip(" .")
    date = datetime.strptime(retro.group(1), "%B %d, %Y").date() if retro else \
        pd.to_datetime(t.get("effectiveDate") or t["date"]).date()
    return {"player_id": t["person"]["id"], "player": t["person"].get("fullName"),
            "team": (t.get("toTeam") or t.get("fromTeam") or {}).get("name"),
            "il_date": date, "announced": pd.to_datetime(t["date"]).date(), "list_days": int(m.group(2)),
            "injury": injury, "body": body_group(injury), "description": d}


def il_events():
    rows = []
    for f in sorted((RAW / "transactions").glob("*.json")):
        for t in json.loads(f.read_text()).get("transactions", []):
            rec = parse_placement(t)
            if rec:
                rows.append(rec)
    return pd.DataFrame(rows).drop_duplicates(["player_id", "il_date"])


SQL_APPEAR = """
CREATE OR REPLACE TABLE pitches AS
SELECT game_pk, CAST(game_date AS DATE) AS game_date, CAST(game_year AS INT) AS season, pitcher, player_name,
       p_throws, pitch_type, release_speed, release_spin_rate, release_extension, release_pos_z, release_pos_x,
       arm_angle, inning, inning_topbot, at_bat_number, pitch_number, home_team, away_team
FROM read_csv('data/raw/statcast_20*/*.csv', header = true, union_by_name = true, sample_size = -1)
WHERE game_type = 'R' AND release_speed IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY game_pk, at_bat_number, pitch_number
                           ORDER BY pitcher, release_speed, pitch_type, release_spin_rate) = 1;

-- each pitcher's primary fastball for a season: his most-thrown of four-seam, sinker, cutter
-- (ties, which happen for a handful of short seasons, go to four-seam, then sinker, then cutter, so every run agrees)
CREATE OR REPLACE TABLE primary_fb AS
SELECT season, pitcher, pitch_type AS fb_type, n AS fb_n
FROM (SELECT season, pitcher, pitch_type, count(*) AS n FROM pitches
      WHERE pitch_type IN ('FF', 'SI', 'FC') GROUP BY ALL)
QUALIFY row_number() OVER (PARTITION BY season, pitcher
                           ORDER BY n DESC, CASE pitch_type WHEN 'FF' THEN 1 WHEN 'SI' THEN 2 ELSE 3 END) = 1;

CREATE OR REPLACE TABLE appearances AS
WITH first_pitch AS (            -- the pitcher who threw each team's first pitch of the game started it
    SELECT game_pk, CASE WHEN inning_topbot = 'Top' THEN home_team ELSE away_team END AS team,
           arg_min(pitcher, at_bat_number * 1000 + pitch_number) AS starter_id
    FROM pitches GROUP BY ALL
),
g AS (
    SELECT p.season, p.pitcher, any_value(p.player_name) AS name, any_value(p_throws) AS throws, p.game_pk,
           min(p.game_date) AS game_date,
           count(*) AS pitches,
           min(p.inning) AS first_inning,
           any_value(CASE WHEN p.inning_topbot = 'Top' THEN p.home_team ELSE p.away_team END) AS team,
           any_value(p.home_team) AS park,
           count(*) FILTER (WHERE p.pitch_type = f.fb_type) AS fb_n,
           avg(p.release_speed) FILTER (WHERE p.pitch_type = f.fb_type) AS velo,
           quantile_cont(p.release_speed, 0.9) FILTER (WHERE p.pitch_type = f.fb_type) AS velo_p90,
           avg(p.release_spin_rate) FILTER (WHERE p.pitch_type = f.fb_type) AS spin,
           avg(p.release_extension) FILTER (WHERE p.pitch_type = f.fb_type) AS ext,
           avg(p.release_pos_z) FILTER (WHERE p.pitch_type = f.fb_type) AS rel_z,
           avg(p.release_pos_x) FILTER (WHERE p.pitch_type = f.fb_type) AS rel_x,
           avg(p.arm_angle) FILTER (WHERE p.pitch_type = f.fb_type) AS arm_angle,
           any_value(f.fb_type) AS fb_type
    FROM pitches p JOIN primary_fb f USING (season, pitcher)
    GROUP BY p.season, p.pitcher, p.game_pk
)
SELECT g.*,
       g.pitcher = fp.starter_id AS game_starter,
       g.first_inning = 1 AND g.pitches >= 40 AS starter,
       datediff('day', lag(g.game_date) OVER (PARTITION BY g.pitcher ORDER BY g.game_date, g.game_pk), g.game_date) AS days_rest,
       row_number() OVER (PARTITION BY g.pitcher ORDER BY g.game_date, g.game_pk) AS career_app_no
FROM g LEFT JOIN first_pitch fp ON fp.game_pk = g.game_pk AND fp.team = g.team;
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DB.unlink(missing_ok=True)
    con = duckdb.connect(str(DB))
    con.execute(SQL_APPEAR.replace("data/raw", str(RAW)))
    il = il_events()
    con.register("il_df", il)
    con.execute("CREATE TABLE il_events AS SELECT * FROM il_df")
    pl = []
    for season in (2025, 2026):
        f = RAW / f"players_{season}.json"
        for p in json.loads(f.read_text())["people"]:
            pl.append({"player_id": p["id"], "name": p["fullName"], "birth": p.get("birthDate"),
                       "position": (p.get("primaryPosition") or {}).get("abbreviation"),
                       "throws": (p.get("pitchHand") or {}).get("code"), "team_id": (p.get("currentTeam") or {}).get("id"),
                       "season": season})
    con.register("pl_df", pd.DataFrame(pl))
    con.execute("CREATE TABLE players AS SELECT * FROM pl_df")
    # position players who mopped up an inning are not pitchers: keep anyone listed as P or two-way in either season
    dropped = con.execute("""SELECT count(DISTINCT pitcher) FROM appearances WHERE pitcher NOT IN
        (SELECT player_id FROM players WHERE position IN ('P', 'TWP')) AND pitcher IN (SELECT player_id FROM players)""").fetchone()[0]
    for t in ("appearances", "pitches"):
        con.execute(f"""DELETE FROM {t} WHERE pitcher NOT IN (SELECT player_id FROM players WHERE position IN ('P', 'TWP'))
                        AND pitcher IN (SELECT player_id FROM players)""")
    q = lambda s: con.execute(s).fetchone()[0]
    checks = {
        "pitches": q("SELECT count(*) FROM pitches"),
        "pitches_by_season": dict(con.execute("SELECT season, count(*) FROM pitches GROUP BY 1 ORDER BY 1").fetchall()),
        "appearances": q("SELECT count(*) FROM appearances"),
        "pitchers": q("SELECT count(DISTINCT pitcher) FROM appearances"),
        "appearances_missing_fb": q("SELECT count(*) FROM appearances WHERE fb_n = 0"),
        "arm_angle_missing_share": q("SELECT avg((arm_angle IS NULL)::INT) FROM appearances WHERE fb_n > 0"),
        "il_placements": q("SELECT count(*) FROM il_events"),
        "il_by_body": dict(con.execute("SELECT body, count(*) FROM il_events GROUP BY 1 ORDER BY 2 DESC").fetchall()),
        "il_with_retro_date": q("SELECT count(*) FROM il_events WHERE il_date < announced"),
        "position_players_dropped": int(dropped),
    }
    (OUT / "build_checks.json").write_text(json.dumps(checks, indent=1, default=str))
    print(json.dumps(checks, indent=1, default=str))


if __name__ == "__main__":
    main()
