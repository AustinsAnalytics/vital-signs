"""Download what Vital Signs needs: Statcast pitches (2025-2026), schedules, injured-list transactions, player info.

    python pipeline/fetch.py schedule 2025      -> data/raw/schedule_2025.json
    python pipeline/fetch.py statcast 2025      -> data/raw/statcast_2025/{date}.csv (one file per game day)
    python pipeline/fetch.py transactions       -> data/raw/transactions/{yyyy-mm}.json (Feb 2025 - Oct 2026)
    python pipeline/fetch.py players 2025       -> data/raw/players_2025.json
Every step skips files it already has and spaces requests out.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
UA = {"User-Agent": "vital-signs research script (personal, non-commercial)"}


def get(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            print(f"  retry {i + 1}: {e}", flush=True)
            time.sleep(5 * (i + 1))
    raise RuntimeError(url)


def schedule(season):
    out = RAW / f"schedule_{season}.json"
    out.write_bytes(get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R&season={season}"
                        "&fields=dates,date,games,gamePk,officialDate,status,codedGameState,teams,away,home,team,id,name,venue"))
    d = json.loads(out.read_text())
    print(season, sum(1 for x in d["dates"] for g in x["games"] if g["status"]["codedGameState"] == "F"), "final games")


def statcast(season):
    d = json.loads((RAW / f"schedule_{season}.json").read_text())
    days = sorted({x["date"] for x in d["dates"] for g in x["games"] if g["status"]["codedGameState"] == "F"})
    folder = RAW / f"statcast_{season}"
    folder.mkdir(parents=True, exist_ok=True)
    for i, day in enumerate(days):
        out = folder / f"{day}.csv"
        if out.exists() and out.stat().st_size > 1000:
            continue
        q = urllib.parse.urlencode({"all": "true", "hfGT": "R|", "hfSea": f"{season}|", "player_type": "pitcher",
                                    "game_date_gt": day, "game_date_lt": day, "min_pitches": 0, "min_results": 0,
                                    "group_by": "name", "sort_col": "pitches", "sort_order": "desc", "min_abs": 0,
                                    "type": "details"})
        out.write_bytes(get("https://baseballsavant.mlb.com/statcast_search/csv?" + q))
        print(f"  [{i + 1}/{len(days)}] {day}", flush=True)
        time.sleep(1.5)


def transactions():
    folder = RAW / "transactions"
    folder.mkdir(parents=True, exist_ok=True)
    months = [(y, m) for y in (2025, 2026) for m in range(1, 13) if (y, m) >= (2025, 2) and (y, m) <= (2026, 10)]
    for y, m in months:
        out = folder / f"{y}-{m:02d}.json"
        if out.exists():
            continue
        end = date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1
        out.write_bytes(get(f"https://statsapi.mlb.com/api/v1/transactions?sportId=1&startDate={y}-{m:02d}-01"
                            f"&endDate={date.fromordinal(end).isoformat()}"))
        time.sleep(0.5)
    print("transactions:", len(list(folder.glob("*.json"))), "months")


def players(season):
    (RAW / f"players_{season}.json").write_bytes(get(
        f"https://statsapi.mlb.com/api/v1/sports/1/players?season={season}&fields=people,id,fullName,birthDate,height,"
        "weight,primaryPosition,abbreviation,pitchHand,code,currentTeam,id"))
    print("players", season)


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"schedule": lambda: schedule(int(sys.argv[2])), "statcast": lambda: statcast(int(sys.argv[2])),
     "transactions": transactions, "players": lambda: players(int(sys.argv[2]))}[cmd]()
