# Pre-registered candidate results — round 1, 3 new windows. **No candidate passes.**

2026-10-02. Pre-registration `82714061`, committed before these windows existed.
`eb706bcc` frozen. No cutoff altered after seeing results. No live orders.
Recorder, strategy and fill assumptions untouched.

## Sample

**3 new complete windows**, 2026-10-02 **1115z / 1130z / 1145z**. Discovery day
(2026-09-30) excluded by name. Baseline accepts **95 first legs** across them.

**This is thin — 3 windows, 95 legs — and nothing below is a conclusion.**

## All 13 configurations, as pre-registered

| config | acc | rej | qual | forced | guard | fRej | avoided | sacrificed | net | **net/leg** | net/win | worst lot | capital |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **BASELINE** | 95 | 0 | 82 | 13 | 0 | 2 | +0.00 | +0.04 | **−80.45** | **−0.8469** | −26.82 | **−17.55** | 929 |
| A300 | 36 | 65 | 32 | 4 | 0 | 46 | +62.16 | +14.81 | −15.84 | −0.4401 | −5.28 | −17.55 | 356 |
| **A420** | **30** | 71 | 30 | **0** | 0 | 48 | +72.20 | +16.73 | **+9.83** | **+0.3277** | +3.28 | **0.00** | 281 |
| A540 | 23 | 78 | 23 | 0 | 0 | 54 | +72.20 | +20.67 | +5.56 | +0.2415 | +1.85 | 0.00 | 220 |
| **B5-1c** | **95** | 0 | 49 | **0** | 46 | 36 | +82.17 | +31.13 | **−21.69** | **−0.2283** | −7.23 | **−4.59** | 929 |
| B5-2c | 95 | 0 | 67 | 4 | 24 | 18 | +48.41 | +20.82 | −45.03 | −0.4740 | −15.01 | −9.03 | 929 |
| B5-3c | 95 | 0 | 72 | 4 | 19 | 13 | +48.41 | +15.70 | −39.91 | −0.4201 | −13.30 | −9.03 | 929 |
| B10-1c | 100 | 0 | 48 | 0 | 52 | 40 | +79.53 | +40.60 | −33.89 | −0.3389 | −11.30 | −5.05 | 938 |
| B10-2c | 100 | 0 | 57 | 0 | 43 | 31 | +79.53 | +35.92 | −29.03 | −0.2903 | −9.68 | −5.05 | 938 |
| B10-3c | 100 | 0 | 63 | 4 | 33 | 26 | +46.79 | +28.70 | −54.46 | −0.5446 | −18.15 | −9.03 | 938 |
| C | 30 | 71 | 24 | 0 | 6 | 54 | +72.20 | +23.58 | +2.98 | +0.0992 | +0.99 | −1.09 | 281 |
| A300+B5-2c | 36 | 65 | 26 | 0 | 10 | 52 | +77.66 | +21.67 | +0.60 | +0.0166 | +0.20 | −2.73 | 356 |
| A540+B5-2c | 23 | 78 | 17 | 0 | 6 | 60 | +72.20 | +27.53 | −1.30 | −0.0565 | −0.43 | −1.09 | 220 |

## Verdict against the pre-registered criterion

> A candidate is interesting only if it beats BASELINE on net per accepted leg
> **and** does not do so purely by rejecting almost everything. A candidate
> accepting fewer than half the baseline's first legs is a capacity reduction,
> not an improvement.

**No candidate passes.**

- **Every configuration that turns positive** — A420 (+0.3277), A540 (+0.2415),
  C (+0.0992), A300+B5-2c (+0.0166) — accepts **23–36 of 95 legs, i.e. 24–38%**.
  All are below the 50% line and are therefore **capacity reductions by the
  criterion I fixed in advance**, whatever their sign.
- **Every full-capacity configuration** (the B series, 95–100 legs) remains
  **negative**, though all improve markedly on baseline.

## Per-window check — this time the aggregate is not one window

| window | BASELINE legs / net / forced | A420 | B5-1c | C |
|---|---|---|---|---|
| 1115z | 27 / −33.20 / 4 | **0 / +0.00 / 0** | 27 / −3.11 / 0 | **0 / +0.00 / 0** |
| 1130z | 17 / −8.78 / 2 | 8 / +2.60 / 0 | 17 / −11.61 / 0 | 8 / +0.42 / 0 |
| 1145z | 51 / −38.48 / 7 | 22 / +7.23 / 0 | 51 / −6.97 / 0 | 22 / +2.56 / 0 |

**BASELINE is negative in all 3 of 3.** Not an outlier effect.

**But A420's "3/3 non-negative" is misleading and I will not present it as a
win:** on 1115z it accepted **zero legs**, so +0.00 means *it did not trade*,
not that it won. A420 traded only two windows, 8 and 22 legs. **Thirty legs
total cannot support a claim.**

**B5-1c is the honest standout, and it still loses.** At full capacity (95 legs,
same as baseline) it improves net/leg from −0.8469 to **−0.2283** — a $58.76
improvement on identical flow — eliminates all 13 forced exits, and cuts worst
lot from **−$17.55 to −$4.59 (74%)**. It is better in 2 of 3 windows and worse
in 1130z (−11.61 vs −8.78). The 74% tail cut matches the counterfactual
frontier's prediction almost exactly, which is at least a consistency check on
the discovery study.

## Out-of-sample baseline is 2.7× worse than in discovery

BASELINE net/leg: **−0.3115** on 6 discovery windows, **−0.8469** on 3 new
windows. Same policy, same fill rules, same ceiling. That spread is the real
headline: **window-to-window variance dominates every effect measured here**,
and it is why 3 windows settle nothing and why I am not touching a cutoff.

## What I am not doing

Not altering any cutoff. Not adding a configuration. Not selecting B5-1c, A420
or anything else. Not re-reading the discovery windows. The grid looks
reasonably placed — the positive/negative boundary falls inside it on both
rules rather than at an edge — so there is no "badly placed grid" finding to
report.

Collection continues on the existing recorder. The next report adds windows to
this same table with the same 13 configurations and the same cutoffs.
