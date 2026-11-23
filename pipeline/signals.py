"""Read each pitcher's vital signs with a Kalman filter, outing by outing.

    python pipeline/signals.py     -> table signals, data/out/signal_params.json

For pitcher i and metric m (fastball velocity, spin, extension, arm angle, release height):
    level      x_t = x_{t-1} + w_t,   w_t ~ N(0, q0 + q1 * days since last outing)     (gaps -> more drift)
    observed   y_t = x_t + v_t,       v_t ~ N(0, sigma_i^2 / n_t + s)                  (n_t fastballs that day)
sigma_i^2 is pitcher i's own pitch-to-pitch variance within a game, shrunk toward the league (partial pooling);
(q0, q1, s) are shared by role (starter/reliever) and fitted by maximum marginal likelihood across every pitcher:
an empirical-Bayes hierarchical model. Data gaps (rest, injured list, off-season) are handled by the day-dependent
drift, so the filter re-learns a pitcher's baseline after time away instead of raising alarms.

The standardized one-step-ahead error z_t = (y_t - prediction) / sd is the signal. If the noise model is right,
z ~ N(0, 1) in healthy stretches; the saved calibration shows it is.
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "vitals.duckdb"
OUT = ROOT / "data" / "out"
METRICS = {"velo": "release_speed", "spin": "release_spin_rate", "ext": "release_extension",
           "arm_angle": "arm_angle", "rel_z": "release_pos_z"}
SHRINK_GAMES = 20
BURN_IN = 3          # the likelihood skips each pitcher's first outings (start-up transient); scores begin at his 2nd


def within_game_variance(con):
    cols = ", ".join(f"var_samp(p.{c}) AS v_{m}" for m, c in METRICS.items())
    g = con.execute(f"""
        SELECT p.pitcher, p.game_pk, count(*) AS n, {cols}
        FROM pitches p JOIN primary_fb f USING (season, pitcher)
        WHERE p.pitch_type = f.fb_type GROUP BY ALL HAVING count(*) >= 5""").df()
    out = {}
    for m in METRICS:
        per = g.groupby("pitcher").agg(v=(f"v_{m}", "mean"), games=(f"v_{m}", "size"))
        league = float(g[f"v_{m}"].median())
        out[m] = ((per.v * per.games + league * SHRINK_GAMES) / (per.games + SHRINK_GAMES)).to_dict(), league
    return out


def pad(df, cols):
    """Pitcher x outing matrices (NaN padded) for vectorized filtering."""
    df = df.sort_values(["pitcher", "game_date", "game_pk"])
    idx = df.groupby("pitcher").cumcount().values
    pitchers = df.pitcher.unique()
    row = pd.Series(np.arange(len(pitchers)), index=pitchers)[df.pitcher].values
    T = idx.max() + 1
    mats = {}
    for c in cols:
        a = np.full((len(pitchers), T), np.nan)
        a[row, idx] = df[c].values.astype(float)
        mats[c] = a
    return mats, row, idx, pitchers


def kalman(y, r, days, q0, q1, s, keep=False):
    """Vectorized local-level filter over pitchers (rows). Returns (negative log-lik, z, pred, pred_sd, level, level_sd)."""
    P_, T = y.shape
    x = np.full(P_, np.nan)
    P = np.full(P_, np.nan)
    nll = 0.0
    z = np.full_like(y, np.nan)
    pred = np.full_like(y, np.nan)
    psd = np.full_like(y, np.nan)
    lev = np.full_like(y, np.nan)
    lsd = np.full_like(y, np.nan)
    seen = np.zeros(P_, dtype=int)
    for t in range(T):
        yt, rt = y[:, t], r[:, t] + s
        has = ~np.isnan(yt)
        start = has & np.isnan(x)
        x[start], P[start] = yt[start], rt[start]           # first observed outing sets the baseline
        seen[start] += 1
        go = has & ~start
        if go.any():
            Q = q0 + q1 * np.clip(np.nan_to_num(days[:, t], nan=4.0), 0, 200)
            Pp = P + Q
            S = Pp + rt
            e = yt - x
            zt = e / np.sqrt(S)
            scored = go & (seen >= BURN_IN)
            nll += 0.5 * np.sum(np.log(S[scored]) + zt[scored] ** 2)
            if keep:
                z[go, t], pred[go, t], psd[go, t] = zt[go], x[go], np.sqrt(S[go])
            K = Pp / S
            x = np.where(go, x + K * e, x)
            P = np.where(go, (1 - K) * Pp, P)
            seen[go] += 1
        if keep:
            lev[has, t], lsd[has, t] = x[has], np.sqrt(P[has])
    return nll, z, pred, psd, lev, lsd


def main():
    con = duckdb.connect(str(DB))
    app = con.execute("""
        SELECT a.*, coalesce(avg(a.starter::INT) OVER (PARTITION BY a.pitcher), 0) >= 0.5 AS sp_role
        FROM appearances a WHERE a.fb_n >= 3""").df()
    wv = within_game_variance(con)
    params, frames = {}, []
    for role, sub in app.groupby("sp_role"):
        cols = list(METRICS) + ["fb_n", "days_rest"]
        mats, row, idx, pitchers = pad(sub, cols)
        n = np.clip(mats["fb_n"], 1, None)
        out = sub.sort_values(["pitcher", "game_date", "game_pk"])[["pitcher", "season", "game_pk", "game_date"]].copy()
        for m in METRICS:
            sig2 = np.array([wv[m][0].get(p, wv[m][1]) for p in pitchers])[:, None]
            r = sig2 / n
            y = mats[m]
            scale = np.nanvar(np.diff(y, axis=1))           # rough size of outing-to-outing changes

            def f(th):
                q0, q1, s = np.exp(th) * scale
                return kalman(y, r, mats["days_rest"], q0, q1, s)[0]
            th0 = np.log([0.05, 0.002, 0.2])
            res = minimize(f, th0, method="Nelder-Mead", options={"maxiter": 400, "xatol": 1e-3, "fatol": 1e-2})
            q0, q1, s = np.exp(res.x) * scale
            _, z, pred, psd, lev, lsd = kalman(y, r, mats["days_rest"], q0, q1, s, keep=True)
            params[f"{'starter' if role else 'reliever'}_{m}"] = {"q0": float(q0), "q1": float(q1), "s": float(s),
                                                                 "neg_loglik": float(res.fun)}
            out[f"z_{m}"] = z[row, idx]
            out[f"pred_{m}"] = pred[row, idx]
            out[f"pred_sd_{m}"] = psd[row, idx]
            out[f"level_{m}"] = lev[row, idx]
            out[f"level_sd_{m}"] = lsd[row, idx]
        out["sp_role"] = bool(role)
        frames.append(out)
    sig = pd.concat(frames, ignore_index=True)
    con.register("sig", sig)
    con.execute("CREATE OR REPLACE TABLE signals AS SELECT * FROM sig")
    # calibration: in healthy stretches z should look standard normal
    healthy = con.execute("""
        SELECT s.* FROM signals s
        WHERE NOT EXISTS (SELECT 1 FROM il_events e WHERE e.player_id = s.pitcher
                          AND e.il_date BETWEEN s.game_date - INTERVAL 30 DAY AND s.game_date + INTERVAL 30 DAY)""").df()
    calib = {}
    for m in METRICS:
        zz = healthy[f"z_{m}"].dropna()
        calib[m] = {"n": int(len(zz)), "mean": float(zz.mean()), "sd": float(zz.std()),
                    "share_abs_gt2": float((zz.abs() > 2).mean()), "normal_expectation": 0.0455,
                    "quantiles": {str(q): float(zz.quantile(q)) for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "signal_params.json").write_text(json.dumps({"params": params, "calibration": calib,
                                                        "within_game_sd_league": {m: float(np.sqrt(wv[m][1])) for m in METRICS}},
                                                       indent=1))
    print(json.dumps({m: {k: round(v, 3) for k, v in c.items() if k != "quantiles"} for m, c in calib.items()}, indent=1))
    print(json.dumps({k: {kk: round(vv, 5) for kk, vv in v.items()} for k, v in params.items()}, indent=1)[:1500])


if __name__ == "__main__":
    main()
