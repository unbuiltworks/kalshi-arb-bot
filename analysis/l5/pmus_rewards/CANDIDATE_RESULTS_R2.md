# Pre-registered candidates — round 2, **6 new complete windows cumulative**

2026-10-02. Pre-registration `82714061` unchanged — no threshold or
configuration added, removed or altered. `eb706bcc` frozen. A420 and B5-1c are
**not** promoted. No live orders. Collection continues.

## Sample

**6 new complete windows**, 2026-10-02 1045z / 1100z / 1115z / 1130z / 1145z /
1200z. Discovery day (2026-09-30) excluded by name. **BASELINE accepts 236 first
legs**, up from 95 in round 1.

**A harvesting error of mine, found and fixed:** my second log pull filtered on
`PMUS_TRADE_ROWS` only, so three windows arrived with trades and no book rows and
were correctly dropped as `no_prior_book`. That was my filter, not a venue gap or
a recorder fault. Re-pulled with `PMUS_BOOK_ROWS` and the windows scored.

## All 13 configurations, cumulative

| config | acc | rej | qual | forced | guard | fRej | avoided | sacrificed | net | **net/leg** | net/win | worst | capital |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **BASELINE** | 236 | 0 | 217 | 19 | 0 | 8 | +6.83 | +1.13 | **−47.97** | **−0.2033** | −8.00 | −17.55 | 2106 |
| A300 | 108 | 144 | 103 | 5 | 0 | 102 | +75.86 | +41.46 | −2.82 | −0.0261 | −0.47 | −17.55 | 1005 |
| **A420** | 78 | 175 | 77 | 1 | 0 | 121 | +85.90 | +48.28 | **+17.49** | **+0.2243** | +2.92 | −3.48 | 722 |
| A540 | 59 | 195 | 59 | 0 | 0 | 138 | +89.37 | +55.72 | +13.20 | +0.2238 | +2.20 | 0.00 | 488 |
| B5-1c | 240 | 0 | 132 | 0 | 108 | 92 | +102.05 | +83.63 | −31.89 | −0.1329 | −5.32 | −4.59 | 2141 |
| B5-2c | 238 | 0 | 173 | 5 | 60 | 54 | +65.24 | +61.61 | −44.20 | −0.1857 | −7.37 | −9.03 | 2129 |
| B5-3c | 238 | 0 | 186 | 6 | 46 | 41 | +59.25 | +52.16 | −38.45 | −0.1616 | −6.41 | −9.03 | 2129 |
| B10-1c | 245 | 0 | 136 | 0 | 109 | 93 | +97.55 | +90.50 | −40.38 | −0.1648 | −6.73 | −5.05 | 2149 |
| **B10-2c** | 244 | 0 | 155 | 0 | 89 | 74 | +97.55 | +78.48 | **−27.14** | **−0.1112** | −4.52 | −5.05 | 2143 |
| B10-3c | 243 | 0 | 175 | 5 | 63 | 57 | +61.98 | +60.43 | −45.34 | −0.1866 | −7.56 | −9.03 | 2129 |
| C | 78 | 176 | 66 | 1 | 11 | 132 | +85.90 | +65.97 | −0.20 | −0.0026 | −0.03 | −3.48 | 722 |
| A300+B5-2c | 108 | 145 | 85 | 1 | 22 | 120 | +91.36 | +68.15 | −6.17 | −0.0572 | −1.03 | −3.48 | 1005 |
| A540+B5-2c | 59 | 196 | 50 | 0 | 9 | 147 | +89.37 | +68.66 | +0.26 | +0.0044 | +0.04 | −2.61 | 488 |

**Still no candidate passes.** A420 and A540 are positive but accept 78 and 59
of 236 legs — **33% and 25%**, below the pre-registered 50% line, so both remain
capacity reductions. Every full-capacity configuration is negative.

## B5-1c decomposition — the guard does not remove the loss, it relocates it

| component | BASELINE | **B5-1c** | B10-2c |
|---|---|---|---|
| gross profitable exits preserved | **+79.14** | **+53.56** | +59.59 |
| early-guard P&L (all of it losses) | 0.00 | **−83.52** | −85.74 |
| **forced losses remaining** | **−127.11** | **0.00** | **0.00** |
| unresolved cash | 0.00 | 0.00 | 0.00 |
| **net** | **−47.97** | **−31.89** | −27.14 |
| worst lot | −17.55 | **−4.59** | −5.05 |
| false rejects / winners cut | 8 | **92** | 74 |

Read as a ledger, B5-1c:

- **eliminates the entire −$127.11 forced-loss bucket** — all 19 forced exits gone;
- **but books −$83.52 of guard losses in its place** (every single guard close is
  a loss, by construction — the guard only fires when the mark has deteriorated);
- **and sacrifices $25.58 of profitable exits** (+79.14 → +53.56), cutting 92
  future winners.

So $127.11 of forced loss is replaced by $83.52 + $25.58 = **$109.10 of cost.**
**Net improvement $16.08 on 240 legs — about 6.7¢ per leg.** The loss is
**relocated and modestly shrunk, not removed**, which is exactly what the
discovery counterfactual frontier predicted.

Tail compression is real and large: worst lot **−$17.55 → −$4.59 (74%)**, the
same 74% the frontier projected at h=10.

## Per-window — and the guard damages the one good baseline window

| window | BASELINE | A420 | B5-1c | B10-2c | C |
|---|---|---|---|---|---|
| 1045z | 6 / +1.81 | 0 / +0.00 | 6 / +0.86 | 6 / +1.81 | 0 / +0.00 |
| 1100z | 18 / −0.57 | 16 / −1.04 | 18 / −0.21 | 18 / +0.69 | 16 / −2.60 |
| 1115z | 41 / −30.20 | 11 / +2.34 | 42 / −8.00 | 42 / −6.76 | 11 / −1.94 |
| 1130z | 17 / −8.78 | 8 / +2.60 | 17 / −11.61 | 17 / −13.73 | 8 / +0.42 |
| 1145z | 51 / −38.48 | 22 / +7.23 | 51 / −6.97 | 56 / −8.61 | 22 / +2.56 |
| **1200z** | **103 / +28.24** | 21 / +6.36 | **106 / −5.96** | 105 / −0.54 | 21 / +1.37 |
| **sum** | **−47.97** | **+17.49** | −31.89 | −27.14 | −0.20 |
| median | −4.67 | +2.47 | −6.47 | −3.65 | +0.21 |
| positive | 2/6 | 4/6 | **1/6** | 2/6 | 3/6 |

**1200z is the finding to hold onto.** BASELINE's best window by far (+$28.24 on
103 legs) is turned **negative by every guard variant** — B5-1c −$5.96, B10-2c
−$0.54. The guard fires on lots that would have recovered. B5-1c is positive in
only **1 of 6** windows against baseline's 2 of 6, despite a better total.

## Variance still dominates everything

BASELINE net/leg across samples, same policy and rules throughout:

| sample | windows | net/leg |
|---|---|---|
| discovery | 6 | −0.3115 |
| new, round 1 | 3 | **−0.8469** |
| new, round 2 | 6 | **−0.2033** |

Adding three windows moved BASELINE by **+0.64 per leg**. C moved from +0.0992
to −0.0026 over the same addition. **No ranking here is stable, and nothing
should be selected on it.**

## Not doing

Not promoting A420 or B5-1c. Not altering any cutoff. Not adding a
configuration. Not stopping because a cumulative figure moved either way.
Collection continues against the same frozen pre-registration.
