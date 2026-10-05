# PMUS BTC15m underdog — PARTIAL discovery on archived Oct 2+ chunks

**Status: partial discovery. No candidate selected. No threshold moved.**
Run 37260221140 · code pinned at `ae80e9d5` · archive run 37253257157

Produced with the frozen framework exactly as pre-registered
(`PMUS_BTC15M_UNDERDOG_PREREGISTRATION.md`): the ten checkpoints, the nine
executable-ask buckets with out-of-range kept separate, the causal
last-quote-at-or-before rule, the canonical PMUS fee schedule and the $5-net
sizing. The analysis code is the programme's own
`scripts/pmus_underdog_calibration.py`, borrowed read-only at a pinned commit
rather than re-implemented.

## Sample and provenance

| | |
|---|---|
| Archive chunks used | the artifacts **complete at dispatch** (58 hourly parts) |
| Dates covered | **2026-10-02 → 2026-10-05** (Oct 5 partial) |
| WS frames replayed | 2,475,690 → 1,541,668 book rows, 934,022 trade rows, 203 slugs |
| BTC15m windows with resolved settlement | 1,167 |
| …with book rows in this sample | 232 |
| **Windows usable (both)** | **228** |
| **Observations** | **2,280 of 2,280 possible** |
| Excluded / fabricated | **0 / 0** |
| Causal staleness | median 0.057s, p90 0.131s, max 6.0s |
| Cheaper side | DOWN 1,286 / UP 994 |
| **Oct-1 leak gate** | **2,280 observations, 0 with a pre-Oct-2 window end** |

Oct. 1 was quarantined mechanically: every part dated before 2026-10-02 was
deleted before any analysis step could open it, and the gate re-proved it
afterwards from the observation dump.

Every window×checkpoint had a causal quote, so nothing was excluded and nothing
filled in: 228 windows × 10 checkpoints exactly.

## Observation counts per cell (checkpoint × bucket)

```
cp        <10c   10-15c   15-20c   20-25c   25-30c   30-35c   35-40c   40-45c   45-50c  >=50c   tot
14min        0        0        1        7        3       25       41       55       78      18   228
12min        1        6       10       20       26       32       41       41       43       8   228
10min       11       17       15       26       33       34       23       32       31       6   228
8min        32       22       27       23       19       37       17       20       26       5   228
6min        54       31       32       27       16       16       15       17       16       4   228
5min        82       19       21       22       21       18       15       13       17       0   228
4min        94       23       26       18       13       16       13       13       10       2   228
3min       120       24       21       14        9       11        9        8        9       3   228
2min       147       15       14        9        9        8        9        7        7       3   228
1min       185       14        4        4        2        4        6        5        3       1   228
ALL        726      171      171      170      151      201      189      211      240      50  2280
```

The underdog drifts cheaper as the window closes — a mechanical consequence of
time decay, not a finding. By 1 minute out, 81% of windows have the cheaper
side under 10c.

## Calibration, pooled by bucket

Pooled across all ten checkpoints. **These are not independent observations** —
they come from at most 228 distinct windows, so the Wilson intervals below are
narrower than the real uncertainty.

| bucket | n | avg ask | win % | implied % | Wilson 95% | obs − implied |
|---|---:|---:|---:|---:|---|---:|
| <10c | 726 | 0.0353 | 4.96 | 3.53 | [3.60, 6.79] | +1.43 pp |
| 10–15c | 171 | 0.1187 | 12.87 | 11.87 | [8.65, 18.71] | +1.00 pp |
| 15–20c | 171 | 0.1679 | 20.47 | 16.79 | [15.10, 27.13] | +3.68 pp |
| 20–25c | 170 | 0.2188 | 20.59 | 21.88 | [15.19, 27.28] | −1.29 pp |
| 25–30c | 151 | 0.2713 | 29.14 | 27.13 | [22.48, 36.83] | +2.01 pp |
| 30–35c | 201 | 0.3207 | 34.33 | 32.07 | [28.11, 41.13] | +2.25 pp |
| 35–40c | 189 | 0.3712 | 36.51 | 37.12 | [29.98, 43.58] | −0.61 pp |
| 40–45c | 211 | 0.4193 | 33.65 | 41.93 | [27.62, 40.27] | **−8.28 pp** |
| 45–50c | 240 | 0.4705 | 40.00 | 47.05 | [34.01, 46.31] | −7.05 pp |
| ≥50c (separate) | 50 | 0.5002 | 50.00 | 50.02 | [36.64, 63.36] | −0.02 pp |

Reading it honestly: the cheap buckets sit 1–4 points above their implied
probability and the implied value is inside every one of their intervals, so
none of them is distinguishable from fair. The two expensive buckets sit 7–8
points BELOW implied, and 40–45c is the one bucket whose interval excludes its
implied value — i.e. the near-even underdog settled *less* often than its price
implied. That is a mispricing against the buyer, not an edge.

## Frozen $5-net sizing model, per checkpoint

One checkpoint per window, flat stakes, no loss-dependent term.

| cp | qualifying | wins | win % | mean stake | max stake | net P&L | net/trade | max DD | max consec. losses |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 14min | 206 | 85 | 41.26 | $3.84 | $5.39 | −$28.46 | −$0.14 | $116.30 | 7 |
| 12min | 218 | 77 | 35.32 | $3.10 | $5.39 | −$17.61 | −$0.08 | $95.08 | 9 |
| 10min | 220 | 62 | 28.18 | $2.61 | $5.39 | −$86.51 | −$0.39 | $111.78 | 16 |
| 8min | 221 | 54 | 24.43 | $2.18 | $5.39 | −$68.44 | −$0.31 | $85.82 | 15 |
| 6min | 220 | 50 | 22.73 | $1.71 | $5.39 | −$6.54 | −$0.03 | $51.28 | 15 |
| **5min** | 226 | 49 | 21.68 | $1.59 | $5.39 | **+$10.00** | **+$0.04** | $35.42 | 23 |
| 4min | 223 | 35 | 15.70 | $1.35 | $5.39 | −$46.31 | −$0.21 | $60.79 | 19 |
| 3min | 224 | 25 | 11.16 | $1.07 | $5.39 | −$63.38 | −$0.28 | $75.98 | 28 |
| 2min | 223 | 25 | 11.21 | $0.85 | $5.39 | −$11.80 | −$0.05 | $32.87 | 25 |
| 1min | 226 | 11 | 4.87 | $0.47 | $5.39 | −$32.79 | −$0.15 | $35.89 | **63** |

Opportunity frequency is ~55 per checkpoint per day. Capacity almost never
bound: displayed touch depth blocked only 1–4 observations per checkpoint, and
prices outside the buckets blocked 0–18.

**Against the $480/day target.** Nine of ten checkpoints lose money. The single
positive one makes **+$10.00 across four days — about $2.50/day**, or 0.5% of
the target, on a −$0.04-to-+$0.04 per-trade band that is indistinguishable from
zero. Nothing here supports the target, and the target is not assumed reachable.

The max-stake column cross-checks the independently frozen table in
`PMUS_BTC15M_ECONOMICS.md`: 49c → 11 contracts → $5.39 of premium. Two
separately written implementations of the $5-net rule agree.

## What this does NOT establish, and why nothing is selected

1. **No cell meets the pre-registered observation floor.** Criterion #2 requires
   ≥ 200 **distinct windows** per cell. This sample has 228 distinct windows in
   total, and the largest single cell holds 185 observations. Per-cell, nothing
   is close; pooled-by-bucket cells draw on at most 228 windows and the sampler
   does not yet report distinct-window counts per bucket, so even their
   eligibility is unproven rather than met.
2. **Four days is not an effect, it is a weekend.** 2026-10-02 to 2026-10-05,
   with Oct 5 partial. Day-level block bootstrap on four blocks is not
   informative, so criterion #3 cannot be satisfied as written.
3. **The one positive checkpoint is the kind of artifact criterion #5 exists to
   kill.** One of ten checkpoints positive, at +$0.04/trade, with its neighbours
   at −$0.03 and −$0.21, is what noise looks like across ten correlated cells.
   Criterion #7 (a ≥3-cell adjacent region) also fails on its face.
4. **The direction of the one statistically notable result is adverse.** The
   40–45c bucket is underpriced-against-the-buyer, which argues against, not
   for, buying near-even underdogs.

No candidate rule is proposed. The Oct. 1 holdout remains sealed and the
disclosure ledger remains INCOMPLETE, so it could not be unsealed even if a
rule existed.

## To continue

- Re-run after the archive completes: touch `.github/underdog_calibration_request`.
  Each run commits the exact part list, so passes cannot be confused.
- The sampler should report **distinct windows per cell** alongside `n`, since
  that, not the observation count, is what criterion #2 is written against.
