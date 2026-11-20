# Venue-equity accounting and one BTC5m live window — proposal for the owner

2026-11-20. Nothing here is applied. No limit, ledger figure or arming state
has changed. BTC5m is disarmed. No new fills, no new settled result.

## 1. Diagnostic result (deployed read-only, owner-approved)

`l5-pmus-live` ran `3390246e0` (deployment `4efa8e28`). Ledger identical
before and after: total -136.7845, 116 corrections, 97 rows at the bound,
468 rows. All 97 rows compared; raw output in
`evidence/venue_cost_diagnostic_rows_20261120.json`.

| finding | |
|---|---|
| money in or out since the re-anchor (2026-11-16 00:58Z) | **none.** Feed read to its end; 4 deposits, 8 transfers, 1 bonus, all earlier |
| corrections posted after the re-anchor for earlier rows | none. The suspected unearned credit does not exist |
| cause of the mismatch | 94 of 97: the venue holds fills the ledger does not. In 89 the venue shows the same quantity on both sides (a bought pair) where the ledger holds a one-sided leftover costed at $1.00 |
| rows inside the gate's figure | 42 of 97 (the other 55 settled before the re-anchor) |
| those 42, as booked | -108.20 |
| the 40 of them the venue could be read for | booked -107.20, on the venue's fills **-9.88** |
| effect on the gate's figure if those 40 were corrected | -136.7845 to **about -39.47**. Not applied |
| venue balance over the same period | 338.3456 to 321.4782: **-16.87** |

So the ledger overstates the period's loss by about $97 on evidence in hand.
About $22 between the corrected figure and cash is still unexplained.

## 2. What is built (code only, not deployed)

**`l5-pmus-live`** — commit `b06342976e698a2155ae771b5f5ecf13534f38e1`
(tree `e3dd6a2e…`), one commit on the deployed `3390246e0`; kept here as
`patches/pmus-live-equity-baseline.on-3390246e0.patch`.

* Baseline record `L5_EQUITY_BASELINE`: id, timestamp, buying power,
  positions, open orders (must be 0), equity (must equal buying power plus
  matched pairs), the allowed drawdown, the historical figures (preserved),
  and a verbatim statement that it resets and replaces no limit.
* Deposits and withdrawals: detected from the venue feed. Because none has
  occurred since the re-anchor, the venue's amount field is unverified, so
  amounts are not interpreted: any such row after the baseline blocks until
  a new baseline is written.
* Period result: only rows with a measured cost count. Uncertain rows are
  listed beside it and added to nothing.
* The account risk record now also publishes the existing portfolio drawdown
  limit and the baseline, each with its own verdict.

**`l5-btc5m-live`** — on this branch. Its account-risk read now requires both
blocks in the record and refuses on either one's refusal, on a state it does
not know, or on no remaining room. The daily and cumulative checks run first
and are unchanged. Remaining room is the tightest of all four.

**What blocks trading, each tested end to end across both services:**

| condition | result |
|---|---|
| buying power never read, or older than 180s | blocked |
| order state uncertain; positions unreadable | blocked |
| transfers not yet read, read stale (>15 min), feed error, feed not read back to the baseline | blocked |
| any deposit, transfer or bonus after the baseline; an undated one | blocked |
| baseline malformed, equity overstated, read with orders resting | blocked |
| drawdown at or past either limit; room unreadable or zero | blocked |
| the older service not publishing the blocks | blocked |
| daily or cumulative ledger limit breached | blocked, as before |

Tests: 94 new on the service side (185 with the adjacent suites; regression
subset 12 failed / 2445 passed, the same twelve as the base); 15 new on the
BTC5m side (BTC5m subset: the same 11 pre-existing failures before and
after). 26 deliberate breakages, each fails at least one test.

One existing test fixture was changed: the BTC5m account-risk fixture now
includes the two blocks, because the reader requires them.

Not shown: any of this against the real venue. The transfer check stops
after two consecutive feed pages older than the baseline by a day; the feed
is not strictly ordered, so that margin is an assumption.

## 3. What still blocks a window, and the decision each needs

| gate | reads now | to clear it |
|---|---|---|
| cumulative loss, $75 | -136.78 | **A.** correct the 40 rows from the venue evidence (to about -39.47); needs the correction rule built and approved. **B.** an explicit policy transition replacing it with the equity baseline. Not both needed |
| portfolio drawdown, $15 from $336.68 | $15.20 | once BTC5m honours it, the owner must set a new anchor and allowance. This is the risk authorization |
| equity baseline | not set | the owner's document |

Recommendation on the first: **A**. The evidence exists, it keeps the
ceiling in force instead of replacing it, and it is the smaller change in
policy. It costs one more build and one approved ledger write.

## 4. The concrete proposal

| | |
|---|---|
| starting equity | **$321.4782** as read 2026-11-20 00:42Z; to be re-read when the baseline is written. 0 open orders. Ledger positions: two stale unmatched legs from October, valued at 0 |
| maximum additional loss | **$1.00**, proposed. Set as both the baseline's `max_drawdown_usd` and the drawdown limit (anchor moved to the starting equity, allowance $1.00). The dollar loss allowed from here falls from "none" to $1.00; the old anchor's $15.20 stays on the record |
| why $1.00 | the window's own ceiling is $0.20; the campaign's approved exposure ($0.60) must fit inside the remaining room or the lane refuses to start |
| window | `cpc-btc-updown-5m-2026-11-20-1500z`, 15:00-15:05Z, or any later single window |
| BTC5m build | this branch at the commit that adds the equity gate (stated in the report); supersedes `35224ec` |
| service build | `b06342976e` |
| orders | maker only, UP and DOWN together, requote at 2 ticks, 240s order age, clip $0.05, per side $0.30, unmatched $0.10, two live orders, window loss ceiling $0.20, 60 submissions |
| watchdog | independent, 300s order age, exact-id cancel, healthy at 00:11Z with 0 open orders |
| unchanged | $50 daily halt; $75 cumulative ceiling (subject to section 3) |

**Order of approvals:** release branches for both builds; deploy
`l5-pmus-live` (disarmed); deploy `l5-btc5m-live` (disarmed); the cumulative
gate decision; the baseline document and drawdown setting; the BTC5m
configuration; arming for the one window. After the window: actual fills and
settled net P&L from venue cashflows.
