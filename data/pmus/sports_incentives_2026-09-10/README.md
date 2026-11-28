# PMUS sports incentive evidence — captured 2026-09-10 17:09–17:32Z

Raw artifacts behind `analysis/l5/PMUS_SPORTS_INCENTIVES.md`.

| file | what it is |
|---|---|
| `live_sports_programs.json.gz` | active liquidity programs for the full open sports slate, by market type, from `GET https://gateway.polymarket.us/v1/incentives?symbols=…` (public, no auth). Carries `fetched_at`, source URL, the query param used and the error list per batch. |
| `our_traded_slugs_programs.json` | the same query against the 239 sports slugs on our analysis record. Result: zero. |
| `depth.json` | measured order-book depth inside the Target Size range, one live market per program, 61 programs, from `GET /v1/markets/<slug>/book`. |
| `econ_shared_12.json` | payout per program under the venue's own scoring rule at 12 resting contracts, using the shared-pool allocation. |
| `credited_reconcile.json` | fill-by-fill: venue-credited incentive vs the published maker-rebate formula, for every sports market settled today. |
| `doc_incentives_*.md` | verbatim copies of the three venue documentation pages quoted in the report. |

## Limits that matter

- **The program endpoint is active-only.** `statuses=closed` returns zero
  venue-wide, so absence of a program for a settled market says nothing about
  whether one ran while we traded it.
- **`depth.json` is a single instant, and the wrong one.** NFL/CFB/UFC books
  were sampled on a Thursday afternoon; the pools they belong to pay for live
  periods on Saturday and Sunday. Depth will be far larger then and our share
  correspondingly smaller. No live-period depth measurement exists yet.
- **One market sampled per program.** Not a distribution.
- Credited figures come from the probe's own authenticated settlement rows
  (`commissionNotionalCollected`, our side only, selected by `isAggressor`).
  Everything else here is public and unauthenticated. No credential is stored
  in this directory.
