"""What does an extra day of rest buy a starter? A natural experiment from the schedule.

    python pipeline/rest.py     -> data/out/rest.json

Rest between starts is a choice, and choices are confounded: a manager gives a tired or sore pitcher an extra day.
Off days are different. They are set when the schedule is made, months ahead (rainouts come from the weather), so
an off day that falls in a starter's normal turn pushes his next start back for reasons unrelated to his arm.

The instrument is the number of team off days in the five calendar days after his previous start: the days a normal
turn would take. It is counted over a fixed window on purpose. Counting off days over the whole gap between starts
would leak the manager's choice back in, because a start pushed back for fatigue spans more days and so catches
more off days. Turns whose fixed ten-day window touches the All-Star break are left out (a calendar rule).

    first stage:  rest_days  = a + b * off_days + controls + pitcher-season fixed effects
    second stage: outcome    = c + beta * rest_days_hat + controls + pitcher-season fixed effects
Outcomes: fastball velocity and spin at the next start. Controls: previous start's pitch count, month. Standard
errors are clustered by pitcher (CR1). OLS on the same data shows how much the confounding matters. A robustness
block repeats the estimate with the whole-gap count and with a tighter rest window.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "vitals.duckdb"
OUT = ROOT / "data" / "out"
OUTCOMES = {"velo": "mph", "spin": "rpm"}


def cluster_se(X, resid, groups):
    """Cluster-robust (CR1) standard errors for coefficients of X given residuals."""
    n, k = X.shape
    bread = np.linalg.pinv(X.T @ X)
    meat = np.zeros((k, k))
    gs = np.unique(groups)
    for g in gs:
        s = X[groups == g].T @ resid[groups == g]
        meat += np.outer(s, s)
    c = len(gs) / (len(gs) - 1) * (n - 1) / (n - k)
    return np.sqrt(np.diag(c * bread @ meat @ bread))


def ols(y, X, groups):
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    return beta, cluster_se(X, y - X @ beta, groups)


def tsls(y, d, z, W, groups):
    """2SLS of y on endogenous d with instrument z and exogenous controls W (all already demeaned).
    Returns (beta_d, se_d, first-stage coefficient, first-stage se)."""
    Z = np.column_stack([z, W])
    fs, fs_se = ols(d, Z, groups)
    Xh = np.column_stack([Z @ fs, W])
    b = np.linalg.lstsq(Xh, y, rcond=None)[0]
    resid = y - np.column_stack([d, W]) @ b                # structural residuals use the actual regressor
    return float(b[0]), float(cluster_se(Xh, resid, groups)[0]), float(fs[0]), float(fs_se[0])


def estimate(starts, inst, lo, hi):
    d = starts[(starts.rest_days >= lo) & (starts.rest_days <= hi)].copy()   # regular turns, not IL returns
    d["z"] = d[inst].astype(float)
    d["ps"] = d.pitcher.astype(str) + "_" + d.season.astype(str)
    month = pd.get_dummies(pd.to_datetime(d.game_date).dt.month, prefix="m", drop_first=True).astype(float)
    d = pd.concat([d, month], axis=1)
    ctrl = ["prev_pitches"] + list(month.columns)
    out = {"starts": int(len(d)), "pitcher_seasons": int(d.ps.nunique()), "pitchers": int(d.pitcher.nunique()),
           "share_with_off_day": float((d.z > 0).mean()), "mean_rest_days": float(d.rest_days.mean())}
    for c, unit in OUTCOMES.items():
        e = d[d[c].notna() & d.prev_pitches.notna()]
        cols = ["rest_days", "z", c] + ctrl
        dm = e[cols] - e.groupby("ps")[cols].transform("mean")             # pitcher-season fixed effects
        g, W = e.pitcher.values, dm[ctrl].values
        b, se, fs, fs_se = tsls(dm[c].values, dm.rest_days.values, dm.z.values, W, g)
        b_ols, se_ols = ols(dm[c].values, np.column_stack([dm.rest_days, W]), g)
        if c == "velo":
            out["first_stage"] = {"days_rest_per_off_day": fs, "se": fs_se, "F": (fs / fs_se) ** 2}
        out[c] = {"unit": unit, "n": int(len(e)), "iv_per_extra_day": b, "iv_se": se, "iv_ci": [b - 1.96 * se, b + 1.96 * se],
                  "ols_per_extra_day": float(b_ols[0]), "ols_se": float(se_ols[0])}
    return out


def league_breaks(con):
    """In-season dates on which no MLB team played (the All-Star break, plus any opening-week gap)."""
    league = con.execute("SELECT DISTINCT season, game_date FROM appearances").df()
    league["game_date"] = pd.to_datetime(league.game_date)
    breaks = set()
    for _, g in league.groupby("season"):
        breaks |= set(pd.date_range(g.game_date.min(), g.game_date.max()).difference(pd.DatetimeIndex(g.game_date)))
    return np.array(sorted(breaks), dtype="datetime64[D]")


def main():
    con = duckdb.connect(str(DB), read_only=True)
    starts = con.execute("""
        WITH team_days AS (SELECT DISTINCT team, game_date FROM appearances),
        s AS (
            SELECT a.pitcher, a.season, a.team, a.game_date, a.game_pk, a.velo, a.spin, a.pitches, a.fb_n, a.game_starter,
                   lag(a.game_date) OVER w AS prev_date, lag(a.pitches) OVER w AS prev_pitches,
                   lag(a.team) OVER w AS prev_team, lag(a.starter) OVER w AS prev_start, lag(a.fb_n) OVER w AS prev_fb_n
            FROM appearances a
            WINDOW w AS (PARTITION BY a.pitcher, a.season ORDER BY a.game_date, a.game_pk))
        SELECT s.*, datediff('day', s.prev_date, s.game_date) - 1 AS rest_days,
               (SELECT count(*) FROM range(1, 6) r(k)
                WHERE NOT EXISTS (SELECT 1 FROM team_days t WHERE t.team = s.team
                                  AND t.game_date = s.prev_date + CAST(r.k AS INTEGER))) AS off_days,
               (SELECT count(*) FROM range(1, datediff('day', s.prev_date, s.game_date)) r(k)
                WHERE NOT EXISTS (SELECT 1 FROM team_days t WHERE t.team = s.team
                                  AND t.game_date = s.prev_date + CAST(r.k AS INTEGER))) AS off_days_gap
        FROM s
        -- this start (whoever threw the first pitch, however long it lasted) and his previous appearance, which must
        -- have been a full start: no relief outing or short start in between to blur the rest count
        WHERE s.prev_date IS NOT NULL AND s.team = s.prev_team AND s.game_starter AND s.fb_n >= 5
          AND s.prev_start AND s.prev_fb_n >= 10""").df()
    brk = league_breaks(con)
    prev = pd.to_datetime(starts.prev_date).values.astype("datetime64[D]")
    near_break = np.zeros(len(starts), bool)
    for k in range(1, 11):
        near_break |= np.isin(prev + np.timedelta64(k, "D"), brk)
    starts = starts[~near_break]
    out = estimate(starts, "off_days", 3, 10)
    out["dropped_near_break"] = int(near_break.sum())
    out["league_off_dates"] = [str(b) for b in brk]
    keep = ("starts", "first_stage", "velo", "spin")
    out["robustness"] = {
        "rest_3_to_8": {k: v for k, v in estimate(starts, "off_days", 3, 8).items() if k in keep},
        "whole_gap_instrument": {k: v for k, v in estimate(starts, "off_days_gap", 3, 10).items() if k in keep},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rest.json").write_text(json.dumps(out, indent=1, default=str))
    v = out["velo"]
    print(f"starts {out['starts']}  first stage {out['first_stage']['days_rest_per_off_day']:.3f} "
          f"(F {out['first_stage']['F']:.0f})  velo IV {v['iv_per_extra_day']:+.3f} "
          f"[{v['iv_ci'][0]:+.3f}, {v['iv_ci'][1]:+.3f}]  OLS {v['ols_per_extra_day']:+.3f}  "
          f"spin IV {out['spin']['iv_per_extra_day']:+.2f}")
    for k, r in out["robustness"].items():
        print(f"  {k}: {r['velo']['iv_per_extra_day']:+.3f} [{r['velo']['iv_ci'][0]:+.3f}, {r['velo']['iv_ci'][1]:+.3f}]")


if __name__ == "__main__":
    main()
