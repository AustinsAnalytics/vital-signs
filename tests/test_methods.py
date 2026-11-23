"""Checks on the methods with simulated data where the right answer is known, plus checks on the published page.

    python -m pytest -q
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import build  # noqa: E402
import rest  # noqa: E402
import signals  # noqa: E402
import warning  # noqa: E402


def simulate(P=400, T=40, q0=0.05, s=0.02, sigma2=0.64, seed=0):
    """Pitchers whose true velocity is a random walk, observed through outing averages of n fastballs."""
    rng = np.random.default_rng(seed)
    n = rng.integers(5, 40, size=(P, T)).astype(float)
    days = rng.choice([1.0, 2.0, 3.0, 4.0, 5.0], size=(P, T))
    x = 93 + rng.normal(0, 1.5, P)
    y = np.empty((P, T))
    for t in range(T):
        x = x + rng.normal(0, np.sqrt(q0), P)
        y[:, t] = x + rng.normal(0, np.sqrt(sigma2 / n[:, t] + s))
    return y, sigma2 / n, days


def test_filter_scores_are_standard_normal_when_the_noise_model_is_right():
    y, r, days = simulate()
    _, z, *_ = signals.kalman(y, r, days, q0=0.05, q1=0.0, s=0.02, keep=True)
    z = z[:, signals.BURN_IN:]
    z = z[np.isfinite(z)]
    assert abs(z.mean()) < 0.03
    assert abs(z.std() - 1) < 0.03
    assert abs(np.mean(np.abs(z) > 2) - 0.0455) < 0.01


def test_likelihood_prefers_the_true_noise_model():
    y, r, days = simulate(seed=1)
    best = signals.kalman(y, r, days, 0.05, 0.0, 0.02)[0]
    for q0, s in ((0.5, 0.02), (0.005, 0.02), (0.05, 0.3), (0.05, 0.0)):
        assert signals.kalman(y, r, days, q0, 0.0, s)[0] > best


def test_filter_handles_gaps_without_false_alarms():
    """A long layoff with real drift should not look surprising when drift grows with days away."""
    rng = np.random.default_rng(3)
    P, T = 300, 30
    days = np.full((P, T), 4.0)
    days[:, 15] = 150.0                                   # an off-season in the middle
    x = 93 + np.zeros(P)
    y = np.empty((P, T))
    for t in range(T):
        x = x + rng.normal(0, np.sqrt(0.002 + 0.01 * days[0, t]), P)
        y[:, t] = x + rng.normal(0, 0.5, P)
    r = np.full((P, T), 0.25)
    _, z, *_ = signals.kalman(y, r, days, q0=0.002, q1=0.01, s=0.0, keep=True)
    assert abs(np.nanstd(z[:, 15]) - 1) < 0.12           # the first outing back is scored on the right scale
    _, z_flat, *_ = signals.kalman(y, r, days, q0=0.042, q1=0.0, s=0.0, keep=True)
    assert np.nanstd(z_flat[:, 15]) > 1.5                 # a filter that ignores the layoff over-alarms


def test_2sls_recovers_a_known_effect_that_ols_gets_wrong():
    """Fatigue drives both extra rest and lower velocity; the off-day instrument moves rest only."""
    rng = np.random.default_rng(2)
    n, G = 6000, 300
    g = rng.integers(0, G, n)
    u = rng.normal(size=n)
    z = rng.binomial(1, 0.6, n).astype(float)
    d = 4 + 0.7 * z + 0.8 * u + rng.normal(0, 0.5, n)
    y = 0.25 * d - 0.6 * u + rng.normal(0, 0.8, n)
    W = rng.normal(size=(n, 1))
    dm = lambda a: a - a.mean(axis=0)
    b, se, fs, _ = rest.tsls(dm(y), dm(d), dm(z), dm(W), g)
    b_ols = rest.ols(dm(y), np.column_stack([dm(d), dm(W)]), g)[0][0]
    assert abs(b - 0.25) < 3 * se
    assert abs(b_ols - 0.25) > 0.2
    assert abs(fs - 0.7) < 0.05


def test_cusum_builds_on_repeated_drops_and_decays_with_time_off():
    df = pd.DataFrame({"pitcher": [1] * 8, "z": [0, 0, -1.5, -1.5, -1.5, 0, 0, 0], "days_rest": [4.0] * 8})
    c = warning.cusum(df, "z")
    assert c[1] == 0 and c[2] == 1.0 and c[4] > 2.5
    rested = warning.cusum(df.assign(days_rest=[4.0] * 5 + [60.0, 4.0, 4.0]), "z")
    assert rested[5] < c[5]


def test_injury_text_is_grouped_by_body_part():
    cases = {
        "Right elbow inflammation": "elbow_forearm", "Right flexor strain": "elbow_forearm",
        "Right UCL sprain": "elbow_forearm", "Right ulnar nerve irritation": "elbow_forearm",
        "Right shoulder impingement": "shoulder_lat", "Right lat muscle strain": "shoulder_lat",
        "Right arm teres major muscle injury": "shoulder_lat", "Left hamstring strain": "non_arm",
        "Sciatic nerve irritation": "non_arm", "Right forearm contusion": "impact",
        "Blister on right middle finger": "other_arm", "": "unspecified",
    }
    for text, group in cases.items():
        assert build.body_group(text) == group, text


def test_placement_parser_reads_retroactive_dates_and_skips_transfers():
    t = {"date": "2026-05-23", "person": {"id": 1, "fullName": "A Pitcher"}, "toTeam": {"name": "Detroit Tigers"},
         "description": "Detroit Tigers placed RHP A Pitcher on the 15-day injured list retroactive to May 22, 2026. "
                        "Right shoulder inflammation."}
    rec = build.parse_placement(t)
    assert str(rec["il_date"]) == "2026-05-22" and rec["list_days"] == 15
    assert rec["injury"] == "Right shoulder inflammation" and rec["body"] == "shoulder_lat"
    moved = dict(t, description="Detroit Tigers transferred RHP A Pitcher from the 15-day injured list to the 60-day "
                                "injured list. Right shoulder inflammation.")
    assert build.parse_placement(moved) is None
    hitter = dict(t, description="Detroit Tigers placed SS A Hitter on the 10-day injured list. Left oblique strain.")
    assert build.parse_placement(hitter) is None


def test_published_page_is_fully_rendered_and_matches_the_numbers():
    html = (ROOT / "site" / "index.html").read_text()
    assert "{{" not in html
    numbers = json.loads((ROOT / "data" / "out" / "numbers.json").read_text()) \
        if (ROOT / "data" / "out" / "numbers.json").exists() else {}
    tpl = (ROOT / "templates" / "index.html").read_text()
    for token in set(re.findall(r"\{\{(\w+)\}\}", tpl)):
        if numbers:
            assert numbers[token] in html, token


def test_site_data_is_complete():
    s = json.loads((ROOT / "site" / "data" / "summary.json").read_text())
    assert len(s["teams"]) == 30
    for team in s["teams"]:
        assert (ROOT / "site" / "data" / "teams" / f"{team}.json").exists()
    assert abs(s["signals"]["calibration"]["velo"]["sd"] - 1) < 0.05
    replays = json.loads((ROOT / "site" / "data" / "replays.json").read_text())
    assert replays and all(r["alert_at"] <= r["n"] for r in replays)
