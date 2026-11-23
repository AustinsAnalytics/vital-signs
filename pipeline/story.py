"""Fill the page template with numbers from the pipeline outputs. Fails if any {{token}} has no value.

    python pipeline/story.py     -> site/index.html, data/out/numbers.json
"""
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = json.loads((ROOT / "site" / "data" / "summary.json").read_text())


def pct(x, d=0):
    return f"{100 * x:.{d}f}%"


def sgn(x, d=2):
    return f"{x:+.{d}f}".replace("-", "−")


def sgn_ci(x, d=3):
    """An interval end: one more decimal when it would otherwise round to zero."""
    return sgn(x, d + 1 if abs(x) < 0.5 * 10 ** -d * 2 else d)


def drawing(fb):
    """Coordinates for the release drawing in the template, so the picture is drawn from the same medians it labels.
    Side view: 40 units to the foot, rubber's front edge at x = 120, field level at y = 286.
    Front view: throwing shoulder at (150, 136), arm 86 units long, angle arc of radius 44."""
    rx, ry, th = 120 + 40 * fb["ext"], 286 - 40 * fb["rel_z"], math.radians(fb["arm_angle"])
    at = lambda r, a: (150 - r * math.cos(a), 136 - r * math.sin(a))
    (ax, ay), (cx, cy), (lx, ly), (hx, hy) = at(86, th), at(44, th), at(66, th / 2), at(64, th)
    f = lambda v: f"{v:.1f}"
    return {"g_rx": f(rx), "g_ry": f(ry), "g_ext_mid": f((120 + rx) / 2), "g_relz_mid": f((286 + ry) / 2),
            "g_ax": f(ax), "g_ay": f(ay), "g_cx": f(cx), "g_cy": f(cy), "g_lx": f(lx), "g_ly": f(ly + 4),
            "g_hx": f(hx - 4), "g_hy": f(hy - 6)}


def main():
    n = {}
    n["outings"] = f"{S['outings']:,}"
    n["pitchers"] = f"{S['pitchers']:,}"
    n["seasons"] = "–".join(map(str, (S["seasons"][0], S["seasons"][-1]))) if len(S["seasons"]) > 1 else str(S["seasons"][0])
    n["through"] = S["through"]
    c = S["signals"]["calibration"]["velo"]
    n["calib_sd"] = f"{c['sd']:.2f}"
    n["calib_tail"] = pct(c["share_abs_gt2"], 1)
    n["calib_n"] = f"{S['calibration_n']:,}"
    wsd = S["signals"]["within_game_sd_league"]
    n["sd_velo"] = f"{wsd['velo']:.1f}"
    es = {(e["grp"], e["k"]): e for e in S["event_study"]}
    arm1, non1 = es[("arm", -1)], es[("non_arm", -1)]
    n["arm_final_velo"] = sgn(arm1["velo"])
    n["arm_final_spin"] = sgn(arm1["spin"])
    n["non_final_velo"] = sgn(non1["velo"])
    early = [es[("arm", k)]["velo"] for k in range(-6, -1) if ("arm", k) in es]
    n["arm_early_max"] = f"{max(abs(x) for x in early):.2f}"
    n["arm_events"] = f"{S['event_study_events']['arm']:,}"
    n["non_events"] = f"{S['event_study_events']['non_arm']:,}"
    W = {x["model"]: x for x in S["warning"]["results"]}
    n["w_test_season"] = str(S["warning"]["test"]["season"])
    n["w_train_season"] = str(S["warning"]["train"]["season"])
    n["w_events"] = f"{S['warning']['test']['events']:,}"
    n["w_auc"] = f"{W['ensemble']['auc']:.2f}"
    n["w_auc_rule"] = f"{W['velo_drop_rule']['auc']:.2f}"
    n["w_auc_work"] = f"{W['workload_only']['auc']:.2f}"
    n["w_early"] = pct(W["ensemble"]["caught_early"])
    n["w_any"] = pct(W["ensemble"]["caught_any"])
    n["w_lift"] = f"{W['ensemble']['lift_at_flags']:.1f}"
    n["w_base"] = pct(W["ensemble"]["base_rate"], 1)
    T = S["event_study_events"]["tests"]
    n["es_pre_diff"] = sgn(T["pre"]["diff"])
    n["es_pre_lo"] = sgn(T["pre"]["lo"])
    n["es_pre_hi"] = sgn(T["pre"]["hi"])
    n["es_final_diff"] = sgn(T["final"]["diff"])
    n["es_final_lo"] = sgn(T["final"]["lo"])
    n["es_final_hi"] = sgn(T["final"]["hi"])
    n["es_ratio"] = f"{arm1['velo'] / non1['velo']:.0f}" if non1["velo"] < 0 else "many"
    ig = S["ingame"]
    B = ig["breakdown"]
    pick = lambda role, far: next(p for p in ig[role]["points"] if p["false_alert_rate"] == far)
    rp5, sp5, sp1 = pick("relievers", 0.05), pick("starters", 0.05), pick("starters", 0.01)
    n["ig_rp_caught"] = pct(rp5["caught"])
    n["ig_sp_caught"] = pct(sp5["caught"])
    n["ig_sp1_caught"] = pct(sp1["caught"])
    n["ig_all_caught"] = pct(B["all"]["caught"])
    n["ig_all_n"] = str(B["all"]["n"])
    n["ig_all_lift"] = f"{B['all']['caught'] / 0.05:.1f}"
    n["ig_left"] = f"{B['median_fastballs_left']:g}"
    n["ig_nd_caught"] = pct(B["next_day"]["caught"])
    n["ig_nd_n"] = str(B["next_day"]["n"])
    n["ig_later_caught"] = pct(B["later"]["caught"])
    n["ig_later_n"] = str(B["later"]["n"])
    n["ig_sprain_n"] = str(B["sprain"]["n"])
    n["ig_sprain_hits"] = "none" if B["sprain"]["hits"] == 0 else str(B["sprain"]["hits"])
    ords = B["ordinary_alert_rate_by_season"]
    n["ig_ord_rates"] = " and ".join(pct(ords[k], 1) for k in sorted(ords))
    n["replay_n"] = str(S["replays"])
    tm = B["per_team_season"]
    n["ig_team_alerts"] = f"{tm['alerts_on_ordinary']:.0f}"
    n["ig_games_per_alert"] = f"{162 / tm['alerts_on_ordinary']:.0f}"
    n["ig_team_injuries"] = f"{tm['arm_injury_outings']:.1f}"
    n["ig_team_caught"] = f"{tm['caught']:.1f}"
    R = S["rest"]
    n["rest_starts"] = f"{R['starts']:,}"
    n["rest_fs"] = f"{R['first_stage']['days_rest_per_off_day']:.2f}"
    n["rest_F"] = f"{R['first_stage']['F']:,.0f}"
    n["rest_velo"] = sgn(R["velo"]["iv_per_extra_day"], 3)
    n["rest_velo_lo"] = sgn_ci(R["velo"]["iv_ci"][0])
    n["rest_velo_hi"] = sgn_ci(R["velo"]["iv_ci"][1])
    n["rest_spin"] = sgn(R["spin"]["iv_per_extra_day"], 1)
    n["rest_spin_lo"] = sgn(R["spin"]["iv_ci"][0], 1)
    n["rest_spin_hi"] = sgn(R["spin"]["iv_ci"][1], 1)
    n["rest_ols"] = sgn(R["velo"]["ols_per_extra_day"], 3)
    n["rest_offshare"] = pct(R["share_with_off_day"])
    allv = [R["velo"]] + [v["velo"] for v in R["robustness"].values()]
    n["rest_range_lo"] = sgn(min(v["iv_per_extra_day"] for v in allv), 3)
    n["rest_range_hi"] = sgn(max(v["iv_per_extra_day"] for v in allv), 3)
    cap = max(v["iv_ci"][1] for v in allv)                   # the most optimistic upper bound across versions
    n["rest_gain_cap"] = f"{math.ceil(max(cap, 0) * 1000) / 1000:.3f}"
    n["rest_versions"] = {2: "two", 3: "three", 4: "four", 5: "five"}.get(len(allv), str(len(allv)))
    ch = S["changes"]
    n["chg_filter"] = pct(ch["starters"]["filter"]["-1.5"]["confirmed_within_3"])
    n["chg_single"] = pct(ch["starters"]["single"]["-1.5"]["confirmed_within_3"])
    n["chg_rp_filter"] = pct(ch["relievers"]["filter"]["-1.5"]["confirmed_within_3"])
    n["chg_rp_single"] = pct(ch["relievers"]["single"]["-1.5"]["confirmed_within_3"])
    fb = S["fastball_median"]
    n["fb_velo"] = f"{fb['velo']:.1f}"
    n["fb_spin"] = f"{fb['spin']:,.0f}"
    n["fb_ext"] = f"{fb['ext']:.1f}"
    n["fb_relz"] = f"{fb['rel_z']:.1f}"
    n["fb_arm"] = f"{fb['arm_angle']:.1f}"
    n["fb_outings"] = f"{fb['outings']:,}"
    n.update(drawing(fb))
    n["pitches"] = f"{S['build']['pitches'] / 1e6:.1f}M"
    n["il_total"] = f"{S['build']['il_placements']:,}"
    n["il_arm"] = f"{S['build']['il_by_body'].get('elbow_forearm', 0) + S['build']['il_by_body'].get('shoulder_lat', 0):,}"
    tpl = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r"\{\{(\w+)\}\}", tpl))
    missing = sorted(used - n.keys())
    if missing:
        raise SystemExit(f"tokens without values: {missing}")
    (ROOT / "site" / "index.html").write_text(re.sub(r"\{\{(\w+)\}\}", lambda m: n[m.group(1)], tpl), encoding="utf-8")
    (ROOT / "data" / "out" / "numbers.json").write_text(json.dumps(n, indent=1))
    print(f"rendered ({len(used)} numbers); unused: {sorted(n.keys() - used)}")


if __name__ == "__main__":
    main()
