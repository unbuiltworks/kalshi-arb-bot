# L5 September 2026 harvest — raw, append-only

`l5_activity_sep2026_raw.json` holds RAW rows from
`https://data-api.polymarket.com/activity`, verbatim, with `fetched_at`,
the exact query params, and the window start.

This is an APPEND alongside the Apr–Aug canonical harvest
(`analysis/l5/l5_ledger.json`, `l5_ledger_windows.csv.gz`, generated
2026-08-31T17:59Z). That source data is NOT replaced, re-fetched or
reinterpreted by anything here.

`REDEEM` is settlement cashflow. It is never incentive income.

## Pagination note

`?user=&limit=&offset=` caps out around offset 5,500, which reaches back
only ~9 hours for this account. The full harvest therefore windows by
`start`/`end` in 3-hour slices and pages inside each window — the same
approach the original Apr–Aug harvest used.
