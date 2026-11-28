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
| 3 | 1330z | 13:30:11 | **SETTLED +$1.025100** — 0 pairs, pure strand |
| 4 | 1345z | 13:45:11 | **SETTLED +$0.059100** — 1 pair, **zero residual** |
| 5 | 1400z | 14:00 | armed, running (worst case $0.9246) |
| 6–8 | 1415z–1445z | pending | |

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


## Windows 3 and 4 — confirmed

**Window 3 (1330z): +$1.025100, and it is 100% luck.**

5 submitted, 5 acked, **1 filled, 4 cancelled**, 0 rejected. 5 maker, 0 taker,
fees $0. First leg 2.01 UP filled; complement **never** filled. `pairs: []`,
`pairs_completed 0`, `matched_qty 0`. Settled: matched_pnl **$0.0000**,
residual 2.01 UP → **+$1.025100**. Had UP lost it would have been
**−$0.9849**. A window can earn a dollar and contribute *nothing* to the
decision statistic; this one did.

**Window 4 (1345z): +$0.059100, and it is 0% luck.**

| line | value |
|---|---|
| up_qty / dn_qty | 1.97 / 1.97 |
| matched_qty | 1.97 |
| matched_cost | 1.910900 → **pair cost 0.9700** (edge +$0.03/contract) |
| **matched_pnl** | **+$0.059100** |
| residual | **0.00, side `null`** |
| residual_pnl | 0.000000 |
| settlement_pnl | **+$0.059100** |

The first Stage B2 window with **zero residual** — the whole result is
outcome-independent. It is also the smallest result, which is the point:
clean windows are small, lucky windows are large.

## Campaign position after 4 windows (1 lost, 3 traded)

| window | matched | residual | realized |
|---|---|---|---|
| 1315z | +0.143617 | +1.068483 | +1.212100 |
| 1330z | +0.000000 | +1.025100 | +1.025100 |
| 1345z | +0.059100 | +0.000000 | +0.059100 |
| **total** | **+0.202717** | **+2.093583** | **+2.296300** |

**91.2% of the headline is directional.** The outcome-independent component is
**$0.2027** of **$2.2963**. Three of three traded windows had a favourable or
neutral settlement; none has yet had a strand lose.

## Interim statistic — threshold crossed, verdict WITHHELD

| set | n | mean edge/contract |
|---|---|---|
| Stage B1 | 8 | +0.01250 |
| Stage B2 | 4 | +0.02000 |
| **pooled** | **12** | **+0.01500** (stdev 0.02876) |

t = 1.807, one-sided p ≈ **0.0354**. GO threshold at n=12 is +0.01366 —
**nominally met**, and robustness now survives dropping the best pair
(+0.00909).

**I am not declaring GO, and the nominal p-value is not trustworthy as it
stands.** The pre-registration set the decision at the campaign's END
(n ≈ 21 pairs), not at every window. Evaluating after each window and stopping
when the statistic first crosses 0.05 is **optional stopping**, which inflates
the true false-positive rate far above the nominal 5%. A threshold crossed at
an interim look is an observation, not a verdict. The decision stands at the
pre-registered endpoint, on all pairs the campaign produces.
