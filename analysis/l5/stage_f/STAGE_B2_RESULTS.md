# Stage B2 — running results (2026-10-31)

Campaign nonce digest `camp1.camp-BpS2…`, document sha256 `f721aefc…`,
session `camp.b27ba3b68c58f8ad9fde9df3`, scope digest
`7bdec6812776775a50f174dba44e251f2bdb2e7f13197b7efe2c03204a496390`,
deployment `31d90751`, engine `src`/`deploy` byte-identical to `ff00d287`.

**Labels are strict.** `L5_WINDOW_ACCOUNTING.pair_economics` is DERIVED from
our own fills and is never summed into realized. Only `L5_LIVE_SETTLED` /
`realized_net_usd` is realized. Estimated and pending rebates are excluded.

## Window ledger

| # | window | armed | outcome |
|---|---|---|---|
| 1 | 1300z | **NO** | `WINDOW_BOUNDS_UNREADABLE` — venue did not confirm `startTime` at T+10.8s; failed closed, `claim_taken: false`, **no window spent**, zero orders |
| 2 | 1315z | 13:15:10 | **SETTLED +$1.212100** |
| 3 | 1330z | 13:30:11 | armed, running |
| 4–8 | 1345z–1445z | pending | |

Window 1 was lost, not substituted. Seven authorized windows remain and the
eighth budget slot will go unused. `L5_LIVE_AUTH_NOT_AFTER = 14:58:00Z` is
what makes that automatic: the spare slot cannot be taken by a 1500z window.

## Window 2 (1315z) — CONFIRMED

| orders | |
|---|---|
| submitted / acknowledged / terminal | 10 / 10 / 10 |
| filled orders / fill events | 7 / 7 |
| partial fills | 0 |
| cancelled | 3 |
| **rejected** | **0** |
| maker / taker orders | **10 / 0** |
| `actual_fees_usd` | **0** |
| contracts filled | 14.04 (first leg 2.02, complement 12.02) |

**Pairs completed: 3**

| qty | pair cost | up_own | dn_own | edge/contract | |
|---|---|---|---|---|---|
| 2.02 | **0.9600** | 0.48 | 0.48 | **+0.04** | |
| 1.97 | **1.0300** | 0.60 | 0.43 | **−0.03** | **defensive** (risk-reducing) |
| 2.04 | **0.9600** | 0.47 | 0.49 | **+0.04** | |

derived `pair_edge_usd +0.162400`, `defensive_loss_usd −0.059100`,
`worst_pair_cost 1.0300`, `unpriceable_legs 0`.

**Venue settlement (authority):**

| line | value |
|---|---|
| matched_qty | 6.03 |
| **matched_pnl** | **+$0.14361685** — outcome-independent |
| residual | 1.98 DOWN |
| **residual_pnl** | **+$1.06848315** — directional |
| **settlement_pnl / realized_net_usd** | **+$1.21210000** |
| resolution | `VENUE_SETTLEMENT_PX`, `MARKET_STATE_EXPIRED` |
| `realized_today_after` | −9.03250000 (cumulative ledger) |

`yes_value 0.0000` — UP settled worthless, so the stranded DOWN leg won.

**Read this honestly.** **88.2% of the window's result is directional luck.**
The outcome-independent component is **+$0.14361685** (+$0.023817 per matched
contract). Had UP won instead, the window would have returned
**−$0.7679** (matched +0.1436, residual −0.9115) — inside the $1.50
per-market deficit cap, and the 1.98 residual is inside the 3-contract
absolute residual cap.

Rebates: `rebate_estimate_usd 0.0618`, **`rebate_credited_usd 0`**,
`rebate_pending_usd 0.0618`, eligibility
`UNVERIFIED_NO_VENUE_CREDIT_RECORD`. Excluded from realized, as always.

## Pre-registered statistic — pooled, not yet GO

edge = `1.00 − pair_cost`, one observation per settled pair:

| set | n | mean edge/contract |
|---|---|---|
| Stage B1 | 8 | +0.01250 |
| Stage B2 so far | 3 | +0.01667 |
| **pooled** | **11** | **+0.01364** (stdev 0.02976) |

t = 1.520, one-sided p ≈ **0.064**. GO threshold at n=11 is mean > +0.01476 —
**not met**. Drop the single best pair and the mean falls to **+0.00700**, so
the robustness condition is not satisfied either.

Direction is consistent across two independent campaigns, which is mildly
encouraging, but it is **not** significance and will not be reported as such.

## What is new versus Stage B1

Volume per window rose sharply: one window matched **6.03** contracts against
Stage B1's **16.14 across four**. Two of three pairs completed at exactly the
`0.96` configured ceiling (+$0.04/contract each), where Stage B1 had three of
eight pairs at exactly 1.0000 for zero edge. That is the submission-budget
resize doing what it was meant to do — more rounds per window — and it is an
observation about acquisition cost, not yet about profitability.
