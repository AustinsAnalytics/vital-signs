# Vital Signs

**What public pitch tracking can, and can't, tell you about a pitcher's arm.**
Site: https://austinsanalytics.github.io/vital-signs/

Every pitch is a measurement of an arm. This project scores every outing of every MLB pitcher in 2025–2026 against his own normal, using a calibrated hierarchical state-space model. It then asks the monitor three questions a front office asks:

| Question | Answer | Evidence |
|---|---|---|
| Can tracking data see an arm injury coming? | **Not in advance.** | Across the five outings before an arm injured-list stint, fastball scores differ from a non-arm placebo by −0.07 SD (95% CI −0.17 to +0.03). On the final outing the gap is −0.35 SD (−0.54 to −0.17). A boosted ensemble trained on 2025 reaches an AUC of 0.54 on all of 2026, no better than a one-line velocity rule. |
| Can it be caught during the game? | **Sometimes.** | A pitch-level CUSUM tuned to fire on 5% of ordinary outings fires during 13% of all 334 outings that ended in an arm stint (2.5× chance), a median of 5.5 fastballs before the pitcher left. When the IL placement was effective the next day, it fires during 16%. |
| Does an extra day of rest help? | **Not for velocity.** | With team off days as an instrument (first-stage F = 2,273), each extra day changes next-start velocity by −0.033 mph (95% CI −0.067 to +0.0004). |

The site also has a pitcher explorer for all 30 teams, a pitch-by-pitch replay of all 42 in-game alerts that preceded an arm stint, and a Detroit staff board. Its cover sheet replays one of those outings as a control chart, stamped when the alert fires, and a dimensioned drawing shows the five vital signs at the median MLB fastball.

## Run it

```bash
pip install -r requirements.txt
make data       # Baseball Savant + MLB Stats API, about an hour with polite request spacing
make all        # analysis, site data, page, tests (about 30 seconds once the data is downloaded)
make og         # the social share card, site/og.png (needs Google Chrome)
make serve      # http://localhost:8644
```

Every number on the page is written by `pipeline/story.py` from the pipeline's outputs (`data/out/numbers.json`). The renderer fails if a number in the template has no value. Runs are reproducible: ties are broken deterministically, and two full rebuilds agree on every quoted number.

## Pipeline

| Step | File | What it does |
|---|---|---|
| Fetch | `pipeline/fetch.py` | Statcast pitches by day, schedules, transactions, player info |
| Build | `pipeline/build.py` | DuckDB: 1.42M pitches → 41,390 outings by 1,024 pitchers (primary fastball's velocity, spin, extension, arm angle, release height; position players who pitched are dropped); 965 pitcher IL placements parsed from transaction text, dated to their retroactive start and grouped by body part, with contusions and lacerations kept out of the arm group |
| Monitor | `pipeline/signals.py` | Kalman filter per pitcher and metric; hyperparameters by maximum marginal likelihood; 36,356 outings scored (each pitcher's first outing sets his baseline) |
| Early warning | `pipeline/warning.py` | Gradient boosting + logistic regression on 20 features, train 2025 / test 2026, against three baselines |
| Rest | `pipeline/rest.py` | Two-stage least squares with schedule off days as the instrument |
| In-game | `pipeline/ingame.py` | Pitch-level CUSUM against the filter's expectation plus the league fatigue curve; replays |
| Change detection | `pipeline/changes.py` | Planted-drop test: filter vs simple rules at a fixed false-alarm rate |
| Site | `pipeline/export.py`, `pipeline/story.py`, `tools/og.py` | Site data, event study with placebo test, page rendering, share card |

## Methods

**Monitor.** For pitcher *i*, metric *m*, outing *t*:

    level      x_t = x_{t-1} + w_t,   w_t ~ N(0, q0 + q1 · days since last outing)
    observed   y_t = x_t + v_t,       v_t ~ N(0, σ_i² / n_t + s)

σ_i² is his own pitch-to-pitch variance within a game, shrunk toward the league's (partial pooling, 20-game prior). (q0, q1, s) are shared within role and fit by maximum marginal likelihood over every pitcher, which makes this an empirical-Bayes hierarchical model. The drift term grows with days away, so the filter re-learns a baseline after rest, injured-list stints and the off-season instead of raising alarms. The score is the standardized one-step-ahead error. On 31,910 healthy outings, velocity scores have SD 1.00 and 4.7% fall beyond ±2 (a standard normal predicts 4.6%). The other four vital signs have SDs between 1.00 and 1.03.

**Event study.** For each of 343 arm stints (elbow, forearm, shoulder, lat) and 242 non-arm stints (placebo), the six outings before the placement are aligned on the final one. Comparisons are stint by stint: the mean of the five earlier outings, and the final outing.

**Early warning.** The label is an arm IL placement within 14 days. Event-level results separate a flag on the final outing (detection) from one earlier (warning).

**Rest.** The instrument is the number of team off days (scheduled or rained out) in the five calendar days after the previous start. A fixed window matters: counting off days across the whole gap lets a manager's choice to push a start back leak in, because a longer gap catches more off days. Turns whose ten-day window touches a league-wide off date (the All-Star break) are dropped by calendar rule. The previous appearance must be a full start, so a relief outing or a short start in between can't blur the rest count, and the outcome start is whoever threw the first pitch, however long he lasted. The model uses pitcher-season fixed effects, month and previous-pitch-count controls, and CR1 errors clustered by pitcher. Robustness checks: a tighter rest window, and the whole-gap count.

**In-game.** Each fastball is compared with what this pitcher should throw at that point: his filtered level coming in plus the league's average fatigue curve for his role. A one-sided CUSUM, C = max(0, C − z − 0.5), accumulates repeated shortfalls. Thresholds are set on ordinary outings only, counting every outing with a fastball however short, and hold across seasons (4.8% and 5.2% of ordinary outings alert in 2025 and 2026). The catch rate counts every arm-injury outing, including short ones.

## Design

The page is drawn as an engineering drawing set, because the two tools at its center come from engineering: the CUSUM chart from factory quality control (E. S. Page, 1954) and the Kalman filter from aerospace navigation (R. E. Kalman, 1960). Light mode is drafting vellum and dark mode is a blueprint. Each section is a sheet with detail tags and a title block that names its method and how it was checked; the release drawing is dimensioned from the median MLB fastball, with its geometry computed by `pipeline/story.py`; construction lines are non-photo blue, problems are marked in red, and checked results get a checker's yellow highlighter. The revision table lists what changed during the build and why. Type is Martian Mono for lettering, labels and data, Atkinson Hyperlegible Next for text and Architects Daughter for hand-lettered notes. Chart colors were checked with a colorblind-separation validator in both modes: one blue data series (teal on the blueprint), orange only as a second series, and red only for alarms and arm injuries, with hatching or labels so color is never the only cue. There's a matching 404 page and a share card (`make og`).

## Validation

- `tests/test_methods.py` uses simulated data where the right answer is known. The filter's scores are N(0, 1) under the true model, the likelihood prefers the true noise model, and layoffs don't trigger alarms. 2SLS recovers a planted effect that OLS gets wrong under confounding. The tests also check the CUSUM, the injury-text grouping and placement parsing, and that the published page shows every number the pipeline produced.
- Held-out season for every predictive claim (train 2025, test 2026).
- Placebo group for the event study; first-stage strength and robustness specifications for the natural experiment.

## What didn't work

- The early-warning model (above).
- Speed. In a planted-drop test, at the same false-alarm rate, the filter confirmed a 1.5-mph drop within three starts 70% of the time, against 72% for a plain "down vs his season average" rule. For relievers it was slightly faster (65% vs 59%). The filter's value is calibration: a score that means the same thing for every pitcher and sample size.

## Limits

Public data has no medical records, biomechanics or bullpen workloads, which is where a team's own model starts. Injured-list dates are when a team placed a player, not when an injury began, and some injuries are listed vaguely. The in-game alert reads only the primary fastball; scoring every pitch type against its own expected speed is the obvious next step.

## How this was built

I built this with Claude Code (Anthropic) as an AI pair programmer, using a validation-first workflow. Every model has a calibration check, a held-out season or a placebo. Every claim on the page is generated from code in this repository. Simulated-data tests check the estimators against known answers.

Data: [Baseball Savant](https://baseballsavant.mlb.com) and the MLB Stats API, for non-commercial research. Not affiliated with MLB or any club.

Austin Gentile · Mathematics and Data Science, Centre College
