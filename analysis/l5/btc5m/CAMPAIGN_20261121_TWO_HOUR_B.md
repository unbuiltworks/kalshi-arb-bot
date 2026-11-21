# Two-hour campaign B, 2026-11-21 — armed 18:07Z for 18:30–20:30Z, stopped by its reconciliation rule at 19:30:36Z, disarmed 19:53Z

Label: LIVE PROBE, SETTLED. Lane 373f23bd then 82fef206 (hotfix deployed while armed, 19:12:19Z); account service 7c3d69ba.
Owner-approved for this run: campaign loss stop $1.50, lane daily allowance $2.00, account drawdown allowance $3.00;
50 orders a window; budgets 1,200 / 150 / 450 / 900; consecutive-window overlap ON (10-minute reconciliation deadline).
Unchanged: maker-only, $0.05 clips, $0.30 a side, $0.10 unmatched, $0.60 exposure, $0.20 a window.

| Window | Result | Orders | Fills | UP qty / cost | DOWN qty / cost | Settled net |
|---|---|---|---|---|---|---|
| 1830z | traded | 44 | 18 | 0.70 / 0.1360 | 0.41 / 0.2798 | -0.0058 |
| 1835z | traded | 35 | 12 | 0.51 / 0.2532 | 0.52 / 0.2803 | -0.0235 |
| 1840z | sat out: overlap check could not verify 1835z (venue row gone, settlement not yet terminal) | | | | | |
| 1845z | traded | 41 | 11 | 0.34 / 0.1118 | 0.38 / 0.2810 | -0.0128 |
| 1850z | sat out: same gap | | | | | |
| 1855z | traded | 47 | 17 | 0.43 / 0.2576 | 0.71 / 0.2082 | -0.0358 |
| 1900z | sat out: same gap | | | | | |
| 1905z | traded | 41 | 15 | 0.40 / 0.2795 | 0.63 / 0.2001 | +0.1504 |
| 1910z | unarmed: hotfix restart; the old process had already claimed this window's authorization | | | | | |
| 1915z | traded | 50 (ceiling reached, 3 refused) | 14 | 0.60 / 0.2305 | 0.47 / 0.2904 | -0.0509 |
| 1920z | traded | 40 | 17 | 0.66 / 0.1452 | 0.38 / 0.2715 | -0.0367 |
| 1925z | traded, WHILE 1920z WAS UNSETTLED (overlap working) | 35 | 17 | 0.45 / 0.2759 | 0.74 / 0.2271 | -0.0530 |
| 1930z–1950z | sat out: `CAMPAIGN_LEDGER_NOT_RECONCILED` (1915z) | 2 (cancelled) | 0 | | | |

**Total: -$0.0681 over 8 traded windows. Cash 321.3819 -> 321.3138 = -$0.0681.** Seven of eight lost; 1905z's gain was
its unmatched side winning. Fees 0. No halt, latch, uncertain order, order left open, or lane/venue disagreement.
Watchdog: lane registry OK on every sweep, including both restarts; no cancels or read errors.
Order capacity: 335 of 1,200 used. Day (12 traded windows across all runs): about -$0.14.

## Why it stopped: the account ledger never booked 1915z
The account service books a lane market only by adopting the venue's position row during a reconcile. Reconciles ran
(periodic, ~5 min) at 19:15:14.2Z and 19:20:21.7Z and neither showed a 1915z row; the next was after settlement.
Inference: the first read fell before the first fill was visible (~19:15:10Z), the second after the venue had dropped
the row at the close. So the market was never adopted, never retired, and has no settlement line. Not a size effect
(1835z was adopted at 0.01). At 19:30:36Z, ten minutes after the lane settled 1915z, the rule refused; every later
window was sat out. The lane's own limits already count that -0.0509; the account ledger's day figure does not.

## What ran for the first time, and how it went
- Campaign document, per-window order ceiling, raised budgets: as designed.
- Value kept published during adoption (account service): no portfolio-value refusal occurred in the whole run.
- Overlap, first version (373f23bd): refused in the gap between the venue dropping the row and reporting settlement —
  three windows lost. Fixed in 82fef206 (accept this process's own close-time verification); then 1925z traded with
  1920z unsettled and 1920z was settled 37 s later from inside the window.
- Restart while armed: clean (no order resting, registry readable throughout); cost the 1910z window.
- Reconciliation deadline: stopped the campaign on a genuinely unbooked market.
- 1905z: one transient `ORDER_UNCERTAIN ... ORDER_READ_FAILED:HTTPError` for ~7 s; cleared by itself; report shows 0.

## Open defects
1. Account service can miss a lane market entirely (above). It needs to learn of lane-settled markets from the lane's
   feed, not only from position snapshots. Related: `venue_basis` lists 49 UNBOOKED markets.
2. A window after a traded one starts ~30 s late: the previous window's close-out report takes about a minute.
3. BLOCKED lines for campaign waits carry no detail (`risk: {}`, no `pending`).
