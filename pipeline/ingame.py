"""In-game alert: can the velocity drop on the day of an arm injury be caught pitch by pitch, before he leaves?

    python pipeline/ingame.py     -> data/out/ingame.json, table ingame_alerts

For every outing, each fastball is compared with what this pitcher should throw today:
    z_i = (velocity_i - expected_i) / (his pitch-to-pitch sd)
expected_i = the filter's predicted level for the outing (from his previous outings only) plus the league's normal
in-game fatigue curve for his role at that fastball count. A one-sided CUSUM accumulates repeated shortfalls,
C_i = max(0, C_{i-1} - z_i - k), and alerts when C_i > h. Thresholds are chosen for a fixed false-alert rate on
ordinary outings; we then ask how many final outings before an arm IL stint alert, and how many pitches early.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "vitals.duckdb"
OUT = ROOT / "data" / "out"
K = 0.5
MIN_FB = 1                # every outing with a fastball counts, so short injury outings stay in the denominator


def main():
    con = duckdb.connect(str(DB))
    fb = con.execute("""
        SELECT p.pitcher, p.game_pk, p.release_speed AS v,
               row_number() OVER (PARTITION BY p.pitcher, p.game_pk ORDER BY p.at_bat_number, p.pitch_number) AS i,
               row_number() OVER (PARTITION BY p.pitcher, p.game_pk ORDER BY p.at_bat_number, p.pitch_number) AS ord
        FROM pitches p JOIN primary_fb f USING (season, pitcher) WHERE p.pitch_type = f.fb_type""").df()
    meta = con.execute("""
        SELECT f.pitcher, f.game_pk, f.game_date, f.season, f.pred_velo, f.sp_role, f.final_outing, f.y, f.event_id,
               f.name, f.team
        FROM features f WHERE f.pred_velo IS NOT NULL""").df()
    sd = con.execute("""
        SELECT pitcher, sqrt(avg(v)) AS sd FROM (
          SELECT p.pitcher, p.game_pk, var_samp(p.release_speed) AS v FROM pitches p JOIN primary_fb f USING (season, pitcher)
          WHERE p.pitch_type = f.fb_type GROUP BY ALL HAVING count(*) >= 5) GROUP BY 1""").df()
    d = fb.merge(meta, on=["pitcher", "game_pk"]).merge(sd, on="pitcher", how="left")
    d["sd"] = d.sd.fillna(d.sd.median()).clip(lower=0.4)
    # normal in-game fatigue: league mean of (velocity - outing's first-5 mean) by fastball count and role,
    # estimated on outings that are not final outings before an IL stint
    first5 = d[d.i <= 5].groupby(["pitcher", "game_pk"]).v.mean().rename("first5")
    d = d.join(first5, on=["pitcher", "game_pk"])
    ok = ~d.final_outing
    curve = (d[ok].assign(dv=d.v - d.first5, ib=d.i.clip(upper=60))
             .groupby(["sp_role", "ib"]).dv.mean())
    d["fatigue"] = [curve.get((r, min(i, 60)), 0.0) for r, i in zip(d.sp_role, d.i)]
    d["z"] = (d.v - (d.pred_velo + d.fatigue)) / d.sd
    d = d.sort_values(["pitcher", "game_pk", "i"])
    c = np.zeros(len(d))
    run, key_prev = 0.0, None
    for j, (p, g, z) in enumerate(zip(d.pitcher.values, d.game_pk.values, d.z.values)):
        if (p, g) != key_prev:
            run, key_prev = 0.0, (p, g)
        if np.isfinite(z):                       # a pitch with no speed reading leaves the total unchanged
            run = max(0.0, run - z - K)
        c[j] = run
    d["cusum"] = c
    outing = d.groupby(["pitcher", "game_pk"]).agg(
        n_fb=("i", "max"), peak=("cusum", "max"), final=("final_outing", "first"), y=("y", "first"),
        role=("sp_role", "first"), season=("season", "first"), name=("name", "first"), team=("team", "first"),
        game_date=("game_date", "first")).reset_index()
    outing = outing[outing.n_fb >= MIN_FB]
    res = {}
    for role, g in outing.groupby("role"):
        normal = g[g.y == 0]
        curve_pts = []
        for far in (0.01, 0.02, 0.05, 0.10):
            h = float(np.quantile(normal.peak, 1 - far))
            fin = g[g.final]
            hit = fin.peak > h
            # pitches of warning: fastballs remaining after the first crossing, on final outings that alerted
            first_cross = (d[(d.cusum > h)].groupby(["pitcher", "game_pk"]).i.min())
            lead = []
            for _, r in fin[hit].iterrows():
                fc = first_cross.get((r.pitcher, r.game_pk))
                if fc is not None:
                    lead.append(int(r.n_fb - fc))
            curve_pts.append({"false_alert_rate": far, "threshold": h, "final_outings": int(len(fin)),
                              "caught": float(hit.mean()) if len(fin) else None,
                              "median_fastballs_left": float(np.median(lead)) if lead else None})
        res["starters" if role else "relievers"] = {"outings": int(len(g)), "points": curve_pts}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ingame.json").write_text(json.dumps(res, indent=1))
    con.register("og", outing)
    con.execute("CREATE OR REPLACE TABLE ingame_outings AS SELECT * FROM og")
    fat = curve.reset_index()
    fat.columns = ["starter", "fastball_no", "dv"]
    (OUT / "fatigue_curve.json").write_text(fat.to_json(orient="records"))
    res["breakdown"] = breakdown(con, d, outing, res)
    (OUT / "ingame.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res["breakdown"], indent=1))
    rp = replays(con, d, outing, res)
    (OUT / "replays.json").write_text(json.dumps(rp, separators=(",", ":")))
    print("replays", len(rp))
    for k in ("relievers", "starters"):
        v = res[k]
        print(k, v["outings"], [(p["false_alert_rate"], round(p["caught"] or 0, 3), p["final_outings"], p["median_fastballs_left"])
                                for p in v["points"]])


def thresholds(res, far=0.05):
    return {True: next(p["threshold"] for p in res["starters"]["points"] if p["false_alert_rate"] == far),
            False: next(p["threshold"] for p in res["relievers"]["points"] if p["false_alert_rate"] == far)}


def final_events(con, outing, res):
    """Each final outing before an arm IL stint, with its alert flag (5% setting) and the stint it preceded."""
    h = thresholds(res)
    fin = outing[outing.final].copy()
    fin["h"] = fin.role.map(h)
    fin["alert"] = fin.peak > fin.h
    fin["game_date"] = pd.to_datetime(fin.game_date).astype("datetime64[ns]")
    il = con.execute("""SELECT player_id, il_date, list_days, injury, body FROM il_events
                        WHERE body IN ('elbow_forearm', 'shoulder_lat')""").df()
    il["il_date"] = pd.to_datetime(il.il_date).astype("datetime64[ns]")
    m = fin.merge(il, left_on="pitcher", right_on="player_id")
    m["gap"] = (m.il_date - m.game_date).dt.days
    m = m[m.gap.between(1, 14)].sort_values("il_date").drop_duplicates(["pitcher", "game_pk"])
    return m


def breakdown(con, d, outing, res):
    """Where the alert works and where it can't, at the 5% setting, and what a team would see in a season."""
    m = final_events(con, outing, res)
    first = d[d.cusum > d.sp_role.map(thresholds(res))].groupby(["pitcher", "game_pk"]).i.min()
    lead = [int(r.n_fb - first[(r.pitcher, r.game_pk)]) for r in m[m.alert].itertuples() if (r.pitcher, r.game_pk) in first]
    rate = lambda g: {"caught": float(g.alert.mean()) if len(g) else None, "n": int(len(g)), "hits": int(g.alert.sum())}
    o = outing.copy()
    o["alert"] = o.peak > o.role.map(thresholds(res))
    ts = o.groupby(["team", "season"]).ngroups
    return {
        "all": rate(m), "median_fastballs_left": float(np.median(lead)) if lead else None,
        "next_day": rate(m[m.gap <= 1]), "later": rate(m[m.gap >= 2]),
        "sprain": rate(m[m.injury.str.lower().str.contains("sprain")]),
        "by_season": {str(k): rate(g) for k, g in m.groupby("season")},
        "by_body": {k: rate(g) for k, g in m.groupby("body")},
        "ordinary_alert_rate_by_season": {str(k): float(g.alert.mean()) for k, g in o[o.y == 0].groupby("season")},
        "per_team_season": {"outings": len(o) / ts, "alerts_on_ordinary": float((o.alert & (o.y == 0)).sum() / ts),
                            "arm_injury_outings": len(m) / ts, "caught": float(m.alert.sum() / ts)},
    }


def replays(con, d, outing, res):
    """Pitch-by-pitch traces of every final outing before an arm IL stint on which the alert fired (5% setting)."""
    m = final_events(con, outing, res)
    out = []
    for r in m[m.alert].itertuples(index=False):
        p = d[(d.pitcher == r.pitcher) & (d.game_pk == r.game_pk)].sort_values("i")
        first = int(p.i[p.cusum > r.h].min())
        out.append({"name": " ".join(reversed(r.name.split(", "))), "team": r.team, "date": str(r.game_date.date()),
                    "role": "SP" if r.role else "RP", "il_date": str(r.il_date.date()), "list_days": int(r.list_days),
                    "injury": r.injury, "body": r.body, "h": round(float(r.h), 2), "alert_at": first,
                    "n": int(r.n_fb), "level": round(float(p.pred_velo.iloc[0]), 2),
                    "pitches": [[int(i), None if not np.isfinite(v) else round(float(v), 1), round(float(x), 2),
                                 round(float(c), 2)]
                                for i, v, x, c in zip(p.i, p.v, p.pred_velo + p.fatigue, p.cusum)]})
    return sorted(out, key=lambda x: x["date"])


if __name__ == "__main__":
    main()
