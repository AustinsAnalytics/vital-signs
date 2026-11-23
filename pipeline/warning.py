"""Early warning: does an outing's signal predict an arm injured-list stint in the next 14 days?

    python pipeline/warning.py     -> tables features, test_scores; data/out/warning.json

Features per outing (all known by the end of that outing): the filter's standardized signals for velocity, spin,
extension, arm angle and release height; one-sided CUSUM accumulators that build up when small drops repeat and decay
with time off; the last-3-outing average velocity signal; workload (pitches in the last 7/14/30 days, outings in the
last 7, days of rest); age; and whether he had an arm IL stint in the past year.

Label: an arm IL placement (elbow/forearm or shoulder/lat) dated within 14 days after the outing.
Models: gradient boosting, logistic regression and their average (ensemble). Train on 2025, test on 2026.
Baselines on the same test set: a velocity-drop rule (1.5 mph under his season average), velocity signal alone,
workload alone. Event-level results separate a warning on the final outing (detection) from a warning earlier
(true early warning).
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "vitals.duckdb"
OUT = ROOT / "data" / "out"
HORIZON = 14
ARM = ("elbow_forearm", "shoulder_lat")
FLAG_SHARE = 0.02            # the operating point: flag the riskiest 2% of outings
SIGS = ["velo", "spin", "ext", "arm_angle", "rel_z"]


def cusum(df, col, k=0.5, tau=45.0, two_sided=False):
    out = np.zeros(len(df))
    c, last_pitcher = 0.0, None
    for i, (p, z, d) in enumerate(zip(df.pitcher.values, df[col].values, df.days_rest.values)):
        if p != last_pitcher:
            c, last_pitcher = 0.0, p
        if np.isnan(z):
            out[i] = c
            continue
        c = c * np.exp(-(0 if np.isnan(d) else d) / tau)
        c = max(0.0, c + (abs(z) if two_sided else -z) - k)
        out[i] = c
    return out


def build_features(con):
    df = con.execute("""
        SELECT s.*, a.pitches, a.fb_n, a.days_rest, a.starter, a.team, a.name, a.velo, a.spin, a.ext, a.arm_angle,
               a.rel_z, datediff('year', TRY_CAST(pl.birth AS DATE), s.game_date) AS age
        FROM signals s JOIN appearances a USING (pitcher, game_pk)
        LEFT JOIN (SELECT player_id, any_value(birth) AS birth FROM players GROUP BY 1) pl ON pl.player_id = s.pitcher
        ORDER BY s.pitcher, s.game_date, s.game_pk""").df()
    df["game_date"] = pd.to_datetime(df.game_date)
    for m, two in (("velo", False), ("spin", False), ("ext", False), ("arm_angle", True), ("rel_z", True)):
        df[f"cusum_{m}"] = cusum(df, f"z_{m}", two_sided=two)
    g = df.groupby("pitcher", sort=False)
    df["z_velo_last3"] = g.z_velo.transform(lambda s: s.rolling(3, min_periods=1).mean())
    df["velo_vs_season"] = df.velo - df.groupby(["pitcher", "season"]).velo.transform(
        lambda s: s.shift().expanding().mean())
    # workload windows by date
    for w in (7, 14, 30):
        df[f"pitches_{w}d"] = g.apply(lambda x: x.set_index("game_date").pitches.rolling(f"{w}D").sum(),
                                      include_groups=False).values
    df["outings_7d"] = g.apply(lambda x: x.set_index("game_date").pitches.rolling("7D").count(),
                               include_groups=False).values
    il = con.execute(f"SELECT player_id, il_date, body FROM il_events WHERE body IN {ARM}").df()
    il["il_date"] = pd.to_datetime(il.il_date)
    # labels and event bookkeeping
    df["y"] = 0
    df["event_id"] = -1
    df["final_outing"] = False
    df["days_to_il"] = np.nan
    prior = np.zeros(len(df), dtype=bool)
    by_p = {p: grp.index.values for p, grp in df.groupby("pitcher")}
    events = []
    for k, e in il.reset_index(drop=True).iterrows():
        rows = by_p.get(e.player_id)
        if rows is None:
            continue
        dates = df.loc[rows, "game_date"].values
        before = rows[(dates < e.il_date.to_datetime64()) & (dates >= (e.il_date - pd.Timedelta(days=HORIZON)).to_datetime64())]
        prior |= np.isin(np.arange(len(df)), rows[(dates > e.il_date.to_datetime64()) &
                                                  (dates <= (e.il_date + pd.Timedelta(days=365)).to_datetime64())])
        if len(before) == 0:
            continue
        df.loc[before, "y"] = 1
        df.loc[before, "event_id"] = k
        df.loc[before, "days_to_il"] = (e.il_date - df.loc[before, "game_date"]).dt.days
        df.loc[before[-1], "final_outing"] = True
        events.append({"event_id": k, "player_id": e.player_id, "il_date": e.il_date, "body": e.body,
                       "season": int(e.il_date.year), "outings_in_window": int(len(before))})
    df["arm_il_past_year"] = prior.astype(int)
    return df, pd.DataFrame(events)


FEATURES = (["z_velo", "z_spin", "z_ext", "z_arm_angle", "z_rel_z", "cusum_velo", "cusum_spin", "cusum_ext",
             "cusum_arm_angle", "cusum_rel_z", "z_velo_last3", "pitches", "pitches_7d", "pitches_14d", "pitches_30d",
             "outings_7d", "days_rest", "starter", "age", "arm_il_past_year"])


def evaluate(test, p, events_test, name):
    thr = np.quantile(p, 1 - FLAG_SHARE)
    flag = p >= thr
    base = test.y.mean()
    res = {"model": name, "auc": float(roc_auc_score(test.y, p)), "avg_precision": float(average_precision_score(test.y, p)),
           "base_rate": float(base), "precision_at_flags": float(test.y[flag].mean()),
           "lift_at_flags": float(test.y[flag].mean() / base)}
    t = test.assign(flag=flag)
    ev = []
    for _, e in events_test.iterrows():
        w = t[t.event_id == e.event_id]
        if w.empty:
            continue
        early = w[~w.final_outing & w.flag]
        ev.append({"final": bool(w[w.final_outing].flag.any()), "early": bool(len(early) > 0),
                   "any": bool(w.flag.any()), "lead_days": float(early.days_to_il.max()) if len(early) else np.nan})
    ev = pd.DataFrame(ev)
    seasons = t.groupby(["pitcher", "season"]).ngroups
    res.update({"events": int(len(ev)), "caught_any": float(ev["any"].mean()), "caught_final": float(ev.final.mean()),
                "caught_early": float(ev.early.mean()), "median_lead_days": float(ev.lead_days.median()),
                "flags": int(flag.sum()), "false_flags_per_pitcher_season": float((flag & (t.y == 0)).sum() / seasons)})
    return res, flag


def main():
    con = duckdb.connect(str(DB))
    df, events = build_features(con)
    con.register("feat", df)
    con.execute("CREATE OR REPLACE TABLE features AS SELECT * FROM feat")
    use = df.dropna(subset=["z_velo"]).copy()
    use["starter"] = use.starter.astype(int)
    seasons = sorted(use.season.unique())
    train_season, test_season = (2025, 2026) if 2025 in seasons and 2026 in seasons else (None, None)
    if train_season:
        train, test = use[use.season == train_season], use[use.season == test_season]
    else:                                                   # development fallback: split one season in time
        cut = use.game_date.quantile(0.55)
        train, test = use[use.game_date <= cut], use[use.game_date > cut]
    X, Xt = train[FEATURES].astype(float), test[FEATURES].astype(float)
    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.04, max_iter=400, l2_regularization=1.0,
                                        min_samples_leaf=60, random_state=7).fit(X, train.y)
    lr = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=3000)).fit(X.fillna(X.median()), train.y)
    p_gb = gb.predict_proba(Xt)[:, 1]
    p_lr = lr.predict_proba(Xt.fillna(X.median()))[:, 1]
    p_en = (p_gb + p_lr) / 2
    ev_test = events[events.event_id.isin(test.event_id.unique())]
    results = []
    for name, p in (("ensemble", p_en), ("boosting", p_gb), ("logistic", p_lr)):
        r, flag = evaluate(test, p, ev_test, name)
        results.append(r)
        if name == "ensemble":
            test = test.assign(risk=p, flag=flag)
    # baselines on the same test outings
    rule = (-test.velo_vs_season.fillna(0)).values            # bigger drop = riskier
    results.append(evaluate(test, rule + 1e-9 * np.random.default_rng(1).random(len(rule)), ev_test, "velo_drop_rule")[0])
    for name, cols in (("velocity_signal_only", ["z_velo", "cusum_velo", "z_velo_last3"]),
                       ("workload_only", ["pitches", "pitches_7d", "pitches_14d", "pitches_30d", "outings_7d", "days_rest",
                                          "starter", "age", "arm_il_past_year"])):
        m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000)).fit(X[cols].fillna(X[cols].median()), train.y)
        results.append(evaluate(test, m.predict_proba(Xt[cols].fillna(X[cols].median()))[:, 1], ev_test, name)[0])
    imp = permutation_importance(gb, Xt, test.y, scoring="average_precision", n_repeats=8, random_state=3)
    importance = sorted(({"feature": f, "importance": float(v)} for f, v in zip(FEATURES, imp.importances_mean)),
                        key=lambda d: -d["importance"])
    con.register("tst", test[["pitcher", "game_pk", "risk", "flag"]])
    con.execute("CREATE OR REPLACE TABLE test_scores AS SELECT * FROM tst")
    out = {"train": {"season": train_season, "outings": int(len(train)), "positives": int(train.y.sum())},
           "test": {"season": test_season, "outings": int(len(test)), "positives": int(test.y.sum()),
                    "events": int(len(ev_test))},
           "flag_share": FLAG_SHARE, "horizon_days": HORIZON, "results": results, "importance": importance}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "warning.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("train", "test")}, indent=1, default=str))
    for r in results:
        print(f"{r['model']:22s} AUC {r['auc']:.3f}  AP {r['avg_precision']:.3f} (base {r['base_rate']:.4f})  lift@2% "
              f"{r['lift_at_flags']:.1f}  caught any/final/early {r['caught_any']:.2f}/{r['caught_final']:.2f}/"
              f"{r['caught_early']:.2f}  lead {r['median_lead_days']}d  false/pitcher-season {r['false_flags_per_pitcher_season']:.2f}")
    print("top features:", [(d["feature"], round(d["importance"], 4)) for d in importance[:8]])


if __name__ == "__main__":
    main()
