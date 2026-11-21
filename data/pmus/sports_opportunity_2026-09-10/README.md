# Live sports opportunity ranking — captured 2026-09-10 17:22–18:28Z

Evidence behind `analysis/l5/PMUS_SPORTS_OPPORTUNITY_RANK.md`.

| file | what it is |
|---|---|
| `snapshot_A.json.gz` / `snapshot_B.json.gz` | book + `stats` for 340 active game markets, taken 18:15:21Z and 18:25:17Z. Differencing `notionalTraded` between them is the real traded-flow measurement. |
| `ranked_universe.json.gz` | all 340 markets scored: flow, both-sides movement, touch prices and queue sizes, hour-scale movement and band occupancy, incentive flag. |
| `eligible.json` | the 32 markets passing every R5F gate, ordered by measured flow. |
| `markets_r5f_worked.json` | current venue state of all 18 markets the probe worked this deployment. |

All reads public and keyless via `https://gateway.polymarket.us`. Nothing
authenticated, nothing submitted.

## Limits that matter

- **One 10-minute window on one Thursday evening.** UCL dominates the
  eligible set because six Champions League matches were live. This is a
  snapshot, not a standing ranking of leagues.
- **340 of 25,588 live game markets.** Bounded to games starting within 6
  hours. The 32 eligible is a floor, not a census.
- **No matched contemporaneous flow for the markets R5F actually worked** —
  17 of 18 are `EXPIRED` and a cumulative counter cannot be differenced
  after the fact. The lifetime-volume comparison (62,404 vs 62,501 median
  shares) is the like-for-like measurement that IS available, and it shows
  no difference.
- Price history lags the live book at the tail by minutes; hour-scale shape
  comes from history, current touch and depth come from the book.
