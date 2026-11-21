# Package for review — continuous two-hour campaign (NOT deployed, nothing armed)

## 1. l5-btc5m-live
- Branch `release/l5-btc5m-campaign-20261121h`, commit `20db834824a78e12c31da832a384839a09fcc4d7` (deployed now: 2fe56d9bf).
- Diff vs 2fe56d9bf: `src/l5_btc5m_execution.py`, `src/l5_btc5m_runner.py`, one new test file (32 tests), deploy trigger.
  Controller unchanged. 4,265 lane/probe/safety tests pass; the same two lineage tests fail with and without it.

### What changes
| Problem on 2026-11-21 | Change | Default |
|---|---|---|
| One window spent 47 of the campaign's 60 orders | Per-window order ceiling `L5_BTC5M_MAX_ORDERS_PER_WINDOW`; never refuses an order that reduces unmatched inventory | unset = none |
| Every second window sat out (previous one unsettled) | `L5_BTC5M_CAMPAIGN_OVERLAP=ONE_VERIFIED_UNSETTLED_WINDOW`: trade while ONE earlier window is unsettled, if this lane has no open order in it and the venue's position row agrees with the ledger on both legs | off |
| Loss room could be lent twice across overlapping windows | Each unsettled window's worst case (cost less guaranteed pair redemption) comes off the campaign loss room first | always on |
| Earlier window's cost occupied the exposure limit for the whole next window | The earlier window is settled during the next one (read every 15 s) | with overlap |
| Zero-grace reconciliation forced the sit-out | With overlap on: a settled window has 10 minutes to appear in the account ledger within $0.005 (measured: 2 minutes); older and missing or different refuses | with overlap |

Any failed check still sits the window out to its end; nothing joins part-way.

### Settings the campaign needs on l5-btc5m-live
| Variable | Now | Proposed | Basis |
|---|---|---|---|
| `L5_BTC5M_CAMPAIGN_OVERLAP` | unset | `ONE_VERIFIED_UNSETTLED_WINDOW` | |
| `L5_BTC5M_MAX_ORDERS_PER_WINDOW` | unset | `50` | busiest live window: 47 |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 60 | `1200` | 24 x 50 |
| `L5_LIVE_AUTH_MAX_OPEN` | 90 | `150` | 2-4 per window seen |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 90 | `450` | 8-14 per window seen |
| `L5_LIVE_AUTH_MAX_REPLACE` | 90 | `900` | 14-29 per window seen |
These are order COUNTS. Dollar limits are unchanged: $0.05 clip, $0.30 a side, $0.10 unmatched, $0.20 a window,
$0.60 campaign, $0.60 aggregate exposure.

## 2. l5-pmus-live
- Branch `release/l5-pmus-live-sibling-value-20261121`, commit `7c3d69ba055c986ec8cf7d1583eb290579644390` (deployed now: 62b54e14a).
- Setting: `L5_PUBLISH_VALUE_DURING_SIBLING_ADOPTION=OTHER_STRATEGY_POSITION_ONLY`.
- Removes the 15-22 s pause at each window's first fill; the account service's own trading gate is unchanged.

## What the owner asked to see proved
- Overlapping unsettled windows: at most one, verified against the venue row on both legs, its worst case deducted from
  the loss room, aggregate exposure still $0.60 (the shared reservation holds its cost until the venue settles it).
  Covered by tests for: two unsettled, an open order left in it, either leg disagreeing, an unreadable row, no row and
  not settled, a failed read -- each refuses and sits the window out.
- Order capacity: 50 a window x 24 = 1,200 against a total of 1,200; class budgets above the per-window maxima seen.
- Inventory-reducing orders: exempt from the per-window ceiling and from the flat total (existing design, seen live at
  16:41:32Z). They still draw on the COMPLETE class budget (450).
- NOT provable before arming: the overlap check, mid-window settlement and the ceiling have never run in production.

## What will stop it before 24 windows
- Account drawdown limit: $1.00 from the equity baseline, $0.0963 used. The lane needs room >= its $0.60 exposure
  ceiling, so it stops placing once the account is about $0.30 further down.
- Campaign loss halt $0.60.
- At about -$0.017 to -$0.03 per traded window (today's four), 24 traded windows cost roughly $0.40-$0.70. So the
  drawdown limit is likely to stop the campaign around window 10-18. Running all 24 would need the owner to change
  the drawdown allowance or the baseline -- a risk-limit change, not proposed here.

## Order
pmus first (so the value stays published), then the lane, both disarmed; verify; preflight; then the exact UTC period
for separate authorization. It must end before 00:00 UTC.

## Rollback
Reconnect each service to its current commit; remove the new variables; restore the four budgets to 60 / 90 / 90 / 90.
