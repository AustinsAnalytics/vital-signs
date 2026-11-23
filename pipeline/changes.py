"""Real change or noise? How fast each detector confirms a genuine velocity change, at the same false-alarm rate.

    python pipeline/changes.py     -> data/out/changes.json

Semi-synthetic test: take every real pitcher-season velocity sequence (real noise, gaps, rest patterns), plant a
known step change of size delta at a random outing tau, and run three online detectors that only see the past:

    filter     Kalman filter (fitted noise model) + one-sided CUSUM on its standardized errors      (this project)
    rolling3   last-3-outing average vs his season-to-date average
    single     one outing vs his season-to-date average ("down X mph today")

Each detector's threshold is set so it raises the same share of false alarms on unchanged outings. Then: how often
is the change confirmed within 3 and within 6 outings, and after how many outings on average.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "vitals.duckdb"
OUT = ROOT / "data" / "out"
FALSE_RATE = 0.02
DELTAS = [-0.75, -1.0, -1.5]
K = 0.5
RNG = np.random.default_rng(11)


def kf_z(y, r, days, q0, q1, s):
    x, P, z = np.nan, np.nan, np.full(len(y), np.nan)
    for t in range(len(y)):
        if np.isnan(y[t]):
            continue
        R = r[t] + s
        if np.isnan(x):
            x, P = y[t], R
            continue
        Pp = P + q0 + q1 * min(max(days[t], 0), 200)
        S = Pp + R
        z[t] = (y[t] - x) / np.sqrt(S)
        Kg = Pp / S
        x, P = x + Kg * (y[t] - x), (1 - Kg) * Pp
    return z


def stats_filter(y, r, days, prm):
    z = kf_z(y, r, days, prm["q0"], prm["q1"], prm["s"])
    c, out = 0.0, np.zeros(len(y))
    for t in range(len(y)):
        c = max(0.0, c - (0 if np.isnan(z[t]) else z[t]) - K) if t >= 2 else 0.0
        out[t] = c
    return out


def stats_rolling(y, w):
    s = pd.Series(y)
    base = s.shift().expanding(min_periods=3).mean()
    roll = s.rolling(w, min_periods=w).mean()
    return (base - roll).fillna(0).values          # positive = below his season average


def run(series, params):
    rows = []
    for (pitcher, season, role), g in series:
        y0 = g.velo.values
        n = len(y0)
        if n < 18:
            continue
        r = (g.sd2.values / np.clip(g.fb_n.values, 1, None))
        days = np.nan_to_num(g.days_rest.values, nan=4.0)
        prm = params[f"{'starter' if role else 'reliever'}_velo"]
        for delta in [0.0] + DELTAS:
            tau = int(RNG.integers(8, n - 6))
            y = y0 + (delta * (np.arange(n) >= tau) if delta else 0)
            rows.append({"pitcher": pitcher, "season": season, "role": role, "delta": delta, "tau": tau, "n": n,
                         "filter": stats_filter(y, r, days, prm), "rolling3": stats_rolling(y, 3),
                         "single": stats_rolling(y, 1)})
    return rows


def evaluate(rows, det, role):
    rs = [x for x in rows if x["role"] == role]
    clean = np.concatenate([x[det][3:] for x in rs if x["delta"] == 0.0])
    h = float(np.quantile(clean, 1 - FALSE_RATE))
    out = {"threshold": h}
    for delta in DELTAS:
        d3, d6, delay = [], [], []
        for x in (x for x in rs if x["delta"] == delta):
            after = np.nonzero(x[det][x["tau"]:] > h)[0]
            first = int(after[0]) if len(after) else None
            d3.append(first is not None and first < 3)
            d6.append(first is not None and first < 6)
            if first is not None:
                delay.append(first + 1)
        out[str(delta)] = {"confirmed_within_3": float(np.mean(d3)), "confirmed_within_6": float(np.mean(d6)),
                           "median_outings_to_confirm": float(np.median(delay)) if delay else None, "n": len(d3)}
    return out


def main():
    con = duckdb.connect(str(DB), read_only=True)
    params = json.loads((OUT / "signal_params.json").read_text())["params"]
    df = con.execute("""
        SELECT a.pitcher, a.season, a.game_date, a.game_pk, a.velo, a.fb_n, a.days_rest, s.sp_role
        FROM appearances a JOIN signals s USING (pitcher, game_pk) WHERE a.fb_n >= 3 AND a.velo IS NOT NULL
        ORDER BY a.pitcher, a.game_date, a.game_pk""").df()
    sd2 = con.execute("""
        SELECT pitcher, avg(v) AS sd2 FROM (SELECT p.pitcher, p.game_pk, var_samp(p.release_speed) AS v
        FROM pitches p JOIN primary_fb f USING (season, pitcher) WHERE p.pitch_type = f.fb_type GROUP BY ALL
        HAVING count(*) >= 5) GROUP BY 1""").df()
    df = df.merge(sd2, on="pitcher", how="left")
    df["sd2"] = df.sd2.fillna(df.sd2.median())
    rows = run(df.groupby(["pitcher", "season", "sp_role"]), params)
    res = {"false_alarm_rate": FALSE_RATE, "pitcher_seasons": len({(x["pitcher"], x["season"]) for x in rows})}
    for role in (True, False):
        res["starters" if role else "relievers"] = {det: evaluate(rows, det, role) for det in ("filter", "rolling3", "single")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "changes.json").write_text(json.dumps(res, indent=1))
    for role in ("starters", "relievers"):
        for det, v in res[role].items():
            print(role, f"{det:9s}", {d: (round(v[d]["confirmed_within_3"], 2), round(v[d]["confirmed_within_6"], 2),
                                        v[d]["median_outings_to_confirm"]) for d in map(str, DELTAS)})


if __name__ == "__main__":
    main()
