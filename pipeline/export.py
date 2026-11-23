"""Write the site's data: one summary file with every quoted number and chart series, and one file per team for the
pitcher explorer.

    python pipeline/export.py     -> site/data/summary.json, site/data/teams/{TEAM}.json
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "vitals.duckdb"
OUT = ROOT / "data" / "out"
SITE = ROOT / "site" / "data"
ARM = ("elbow_forearm", "shoulder_lat")


def r(x, n=3):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), n)


def event_study(con):
    """Mean scores in each of the six outings (within 60 days) before every arm and non-arm injured-list placement."""
    ev = con.execute("""
        WITH e AS (SELECT player_id, il_date, body FROM il_events WHERE body IN ('elbow_forearm', 'shoulder_lat', 'non_arm')),
        w AS (SELECT e.player_id, e.il_date, e.body, f.z_velo, f.z_spin, f.z_ext, f.z_arm_angle,
                     row_number() OVER (PARTITION BY e.player_id, e.il_date, e.body
                                        ORDER BY f.game_date DESC, f.game_pk DESC) AS rn
              FROM e JOIN features f ON f.pitcher = e.player_id AND f.game_date < e.il_date
                                     AND f.game_date >= e.il_date - INTERVAL 60 DAY
              WHERE f.z_velo IS NOT NULL)
        SELECT *, -rn AS k, CASE WHEN body = 'non_arm' THEN 'non_arm' ELSE 'arm' END AS grp FROM w WHERE rn <= 6""").df()
    out = []
    for (grp, k), g in ev.groupby(["grp", "k"]):
        rec = {"grp": grp, "k": int(k), "n": int(len(g))}
        for m in ("velo", "spin", "ext", "arm_angle"):
            v = g[f"z_{m}"].dropna()
            rec[m] = r(v.mean())
            rec[f"{m}_se"] = r(v.std() / np.sqrt(len(v)))
        out.append(rec)
    events = {grp: int(ev[(ev.grp == grp) & (ev.k == -1)].shape[0]) for grp in ("arm", "non_arm")}
    # arm vs non-arm (placebo), one value per stint: the average score over the five outings before the final one,
    # and the final outing itself
    ev["event"] = ev.player_id.astype(str) + "_" + ev.il_date.astype(str) + "_" + ev.body
    per = pd.concat([ev[ev.k <= -2].groupby(["event", "grp"]).z_velo.mean().rename("pre"),
                     ev[ev.k == -1].groupby(["event", "grp"]).z_velo.mean().rename("final")], axis=1).reset_index()

    def diff(col):
        a, b = (per.loc[per.grp == g, col].dropna() for g in ("arm", "non_arm"))
        d, se = a.mean() - b.mean(), np.sqrt(a.var() / len(a) + b.var() / len(b))
        return {"arm": r(a.mean()), "non_arm": r(b.mean()), "diff": r(d), "lo": r(d - 1.96 * se), "hi": r(d + 1.96 * se),
                "n_arm": int(len(a)), "n_non_arm": int(len(b))}
    return out, {**events, "tests": {"pre": diff("pre"), "final": diff("final")}}


def calibration_hist(con):
    z = con.execute("""
        SELECT z_velo FROM signals s WHERE z_velo IS NOT NULL AND NOT EXISTS (SELECT 1 FROM il_events e
          WHERE e.player_id = s.pitcher AND e.il_date BETWEEN s.game_date - INTERVAL 30 DAY AND s.game_date + INTERVAL 30 DAY)""").df().z_velo
    edges = np.arange(-4, 4.01, 0.25)
    h, _ = np.histogram(z.clip(-3.99, 3.99), bins=edges)
    dens = h / h.sum() / 0.25
    mids = (edges[:-1] + edges[1:]) / 2
    return [{"z": r(m, 3), "observed": r(d, 4), "normal": r(norm.pdf(m), 4)} for m, d in zip(mids, dens)], int(len(z))


def team_files(con):
    s = con.execute("""
        SELECT f.pitcher, f.name, f.team, f.season, f.game_date, f.game_pk, f.sp_role, f.pitches, f.fb_n,
               a.velo, f.level_velo, f.level_sd_velo, f.pred_velo, f.z_velo, a.spin, f.level_spin, f.z_spin,
               a.arm_angle, f.level_arm_angle, a.ext, f.level_ext, f.cusum_velo,
               og.peak AS ingame_peak
        FROM features f JOIN appearances a USING (pitcher, game_pk)
        LEFT JOIN ingame_outings og USING (pitcher, game_pk)
        ORDER BY f.pitcher, f.game_date""").df()
    thr = json.loads((OUT / "ingame.json").read_text())
    h = {True: next(p["threshold"] for p in thr["starters"]["points"] if p["false_alert_rate"] == 0.05),
         False: next(p["threshold"] for p in thr["relievers"]["points"] if p["false_alert_rate"] == 0.05)}
    il = con.execute("SELECT player_id, il_date, list_days, injury, body FROM il_events").df()
    latest_team = s.sort_values("game_date").groupby("pitcher").team.last()
    SITE.joinpath("teams").mkdir(parents=True, exist_ok=True)
    index = {}
    for team, ids in latest_team.groupby(latest_team):
        pitchers = []
        for pid in ids.index:
            g = s[s.pitcher == pid]
            if len(g) < 8:
                continue
            outs = [[str(x.game_date)[:10], r(x.velo, 2), r(x.level_velo, 2), r(x.level_sd_velo, 2), r(x.z_velo, 2),
                     r(x.spin, 0), r(x.level_spin, 0), r(x.arm_angle, 1), r(x.level_arm_angle, 1), int(x.pitches),
                     int(bool(x.ingame_peak is not None and np.isfinite(x.ingame_peak) and x.ingame_peak > h[bool(x.sp_role)]))]
                    for x in g.itertuples(index=False)]
            e = il[il.player_id == pid]
            dates = pd.to_datetime(g.game_date).values

            def back(d):                      # his next outing after the placement: when the stint really ended
                later = dates[dates > np.datetime64(pd.Timestamp(d))]
                return str(later[0])[:10] if len(later) else None
            pitchers.append({"id": int(pid), "name": g.name.iloc[-1], "role": "SP" if g.sp_role.iloc[-1] else "RP",
                             "outings": outs,
                             "il": [[str(x.il_date)[:10], int(x.list_days), x.injury, x.body, back(x.il_date)]
                                    for x in e.itertuples(index=False)]})
        if pitchers:
            pitchers.sort(key=lambda p: p["name"])
            (SITE / "teams" / f"{team}.json").write_text(json.dumps(pitchers, separators=(",", ":")))
            index[team] = [p["name"] for p in pitchers]
    return index


def main():
    con = duckdb.connect(str(DB), read_only=True)
    es, es_events = event_study(con)
    calib, calib_n = calibration_hist(con)
    load = lambda f: json.loads((OUT / f).read_text())
    summary = {
        "build": load("build_checks.json"), "signals": load("signal_params.json"), "warning": load("warning.json"),
        "ingame": load("ingame.json"), "rest": load("rest.json"), "changes": load("changes.json"),
        "event_study": es, "event_study_events": es_events, "calibration": calib, "calibration_n": calib_n,
        "fatigue": load("fatigue_curve.json"), "teams": team_files(con), "replays": len(load("replays.json")),
        "outings": con.execute("SELECT count(*) FROM features WHERE z_velo IS NOT NULL").fetchone()[0],
        "pitchers": con.execute("SELECT count(DISTINCT pitcher) FROM features").fetchone()[0],
        "seasons": [int(x[0]) for x in con.execute("SELECT DISTINCT season FROM features ORDER BY 1").fetchall()],
        "through": str(con.execute("SELECT max(game_date) FROM features").fetchone()[0])[:10],
        "fastball_median": dict(zip(["velo", "spin", "ext", "rel_z", "arm_angle", "outings"], con.execute(
            "SELECT quantile_cont(velo, .5), quantile_cont(spin, .5), quantile_cont(ext, .5), quantile_cont(rel_z, .5), "
            "quantile_cont(arm_angle, .5), count(*) FROM appearances WHERE fb_n >= 1").fetchone())),
    }
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "summary.json").write_text(json.dumps(summary, separators=(",", ":"), default=str))
    (SITE / "replays.json").write_text((OUT / "replays.json").read_text())
    print("summary", (SITE / "summary.json").stat().st_size // 1024, "KB; teams", len(summary["teams"]))


if __name__ == "__main__":
    main()
