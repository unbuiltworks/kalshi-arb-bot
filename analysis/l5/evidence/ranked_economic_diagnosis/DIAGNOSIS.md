# Ranked economic diagnosis: L5's decisions vs ours, on actual evidence

All figures from existing committed artifacts and existing code. Nothing
re-run, no threshold fitted, no strategy introduced.

**Source.** `analysis/l5/r4_fidelity_decomposition.json` -- the existing R4
sequential replay / action comparison: 217 deduplicated L5 decision points
against what the engine actually executed, 64 orders submitted, 10 filled, 3
markets, all settled, 316/316 campaign rows parsed, 0 truncated, 0 unreadable.
Fees from `src/pmus_fees.py` (effective-dated; these fills predate the
2026-09-17 change, so theta = 0.06 taker / 0.0125 maker).

**Sample warning, stated once and applying to everything below.** Three
markets, one day, aggregate venue-realized **+$0.0043**. This ranks *mechanisms
by demonstrated dollars*. It cannot and does not establish expectancy, and
nothing here is evidence of an edge.

---

## 1. What L5 did, and what our engine did, at each decision point

L5's decision (row) against the engine's actual action (column), all 217:

| L5 decided | engine acted | engine held, and why | agreement |
|---|---|---|---|
| **complete** (71) | 49 complete | 21 `complement_not_priceable`, 1 `complete_without_residual` | **69%** |
| **extend** (109) | 1 extend (+1 as complete) | 62 `unmatched_usd_cap`, 44 `entry_band_left`, 1 `off_distribution_hold` | **0.9%** |
| **open** (37) | 13 open | 23 `off_distribution_hold`, 1 `spend_cap_reached` | **35%** |

The headline behavioral fact: **the engine executes 1 of L5's 109 EXTEND
decisions.** We reproduce L5's completes tolerably, its opens partially, and its
extends essentially not at all.

## 2. The three real markets, fully adjusted

Settlement-adjusted, from the venue's own `realized_usd`. **Fee-adjusted: the
adjustment is exactly $0.** `src/pmus_fees.py` exposes no `maker_fee` function
at all, because on PMUS a maker pays no fee and instead accrues a rebate. All
ten fills were maker. The rebate is a credit the operating model forbids
netting into edge, and it has not been confirmed credited, so it is shown
separately and never added.

| market | submitted / filled | matched | pair cost | matched $ | residual | residual $ | **venue realized** | rebate accrual (unconfirmed) |
|---|---|---|---|---|---|---|---|---|
| petbru-alecan | 19 / **1** | 0 | — | 0 | 5.24 YES | **−1.9912** | **−$1.9912** | +0.0154 |
| aleher-tundur | 16 / 5 | 8.55 | **0.8798** | +1.0279 | 1.86 YES | +0.9676 | **+$1.9955** | +0.0558 |
| norgom-ollwal | 29 / 4 | 10.21 | **0.9992** | **+0.0082** | 0.00 | — | unconfirmed¹ | +0.0636 |
| | 64 / **10 (15.6%)** | | | | | | **+$0.0043** | +0.1348 |

¹ no position-resolution row because the account was flat; paying side read
from the venue's public settlement price and marked UNCONFIRMED.

---

## 3. The ranking, by demonstrated impact on net profitability

### #1 — THE UNMATCHED SECOND LEG. Demonstrated: **−$1.9912**

This is **100% of every realized dollar lost across all three markets.**

`petbru-alecan` submitted 19 orders and filled **one**: a single OPEN of 5.24
YES at $0.38. The engine then chose `complete` **18 times** and completed
nothing. The 5.24 YES sat naked at a cost basis of $1.9912, the market settled
NO, and the entire basis was lost.

Category: **both**, and the split is legible.
- *PMUS execution limitation*: `complement_not_priceable`, 21 refusals, **all 21
  on the NO side** -- i.e. every one of them blocking a COMPLETE leg. The venue
  would not give us a priceable complement.
- *Behavioral logic*: an in-market fill rate of 1 in 19 (5.3%) with 18 complete
  attempts is not only the venue's fault. The engine re-chose `complete` and
  re-submitted without achieving a fill.

Fee-adjusted: unchanged. Even crediting the unconfirmed $0.0154 rebate it
remains **−$1.976**.

The contrast inside the same three-market sample is the proof: a pair completed
at 0.88 made money, a pair completed at 0.9992 made ~nothing, and a leg never
completed at all lost its whole cost basis. **Completion, not entry, is where
the money went.**

### #2 — PAIR COMPLETION AT ~$1.00. Demonstrated: +$0.0082 on $10.20 deployed

`norgom-ollwal` matched 10.21 contracts -- the largest matched book of the three
-- at a pair cost of **0.9992**, returning **$0.0082 gross, 0.08% on capital.**

Two things make this a ranked finding rather than a rounding error:

1. The operating model's spring discipline prefers pair completion at **≤0.96**,
   and explicitly locks near-$1.00 tolerance as late incentive-era L5 behavior.
   At 0.9992 that discipline **did not bind**.
2. The rebate accrual on this same market, **$0.0636, is 7.8x the trading
   margin.** So the trade only "works" as rebate farming -- which the operating
   model forbids as a justification, and which is unconfirmed anyway.

Grounded counterfactual: had those same 10.21 pairs completed at **0.8798**, the
price the engine actually achieved in a sibling market the same day, matched
contribution would have been about **+$1.23** instead of $0.0082. That price is
not fitted; it is a fill we got.

Category: **behavioral logic.** No venue limitation and no safety rail was
involved -- the engine chose to complete there.

### #3 — EXTEND IS NOT EXECUTED. Demonstrated: **$0.00**

The largest *behavioral* divergence in the sample -- 108 of L5's 109 extends
never executed -- and it moved **no realized money**, because nothing traded.

The only available economics point the other way: had every refused extend
filled and been held to the venue's actual settlement, it would have **lost
$90.78** (−$29.77 one-fill-per-run). Only the paired-at-a-later-touch reading is
positive (+$11.65), and it assumes a complement fill that never happened --
the same touch=fill assumption refused everywhere else in this program. One
market's paying side is unconfirmed; flipping it moves the held figure to
+$141.21, which is the measure of how unsettled this is.

Category: **safety restriction** (62 `unmatched_usd_cap`, 1 `spend_cap_reached`)
plus **behavioral logic** (44 `entry_band_left`).

**This is why #1 outranks it.** #3 is the bigger divergence; #1 is the bigger
dollar. Ranking by suppressed behavior would put EXTEND first and would be
wrong about profitability.

### #4 — OPEN SUPPRESSION. Demonstrated: **$0.00**

23 of L5's 37 opens held by `off_distribution_hold` (62%), costing 315s of
session time. Behavioral. No realized impact.

### #5 — FILL RATE, 10 of 64 submitted (15.6%). Demonstrated: the mechanism under #1 and #2

Not an independent discrepancy but the common cause: 54 of 64 submitted orders
never filled. `entry_band_left` alone consumed 1,524s, **35.7% of all live
session time** -- the single largest time sink, larger than the cap's 26%.

---

## 4. Answer to the question asked

**The single discrepancy with the largest demonstrated impact on net
profitability is the failure to complete the second leg (#1).** It accounts for
−$1.9912, which is every realized dollar lost in the sample, and its mechanism
is a 21-refusal venue limitation on the complement side compounded by an 18-try,
zero-fill completion loop.

It is also the only item on this list whose fix direction is unambiguous from
the evidence, because the same sample contains a *working* completion at 0.88.
Items #3 and #4, the larger behavioral divergences, have zero demonstrated
profit impact and in #3's case a negative counterfactual -- so the honest
conclusion is that **our biggest behavioral gap and our biggest economic
problem are not the same thing**, and the economic problem is completion.

## 5. Cashflow attribution: checked, and it is not available

Checked against existing authenticated surfaces rather than proposed.

- **`/v1/account/balances`** (the route PMUS actually serves) returns a
  point-in-time scalar: `currentBalance` 243.11505, `buyingPower` and
  `availableToWithdraw` identical, `unsettledFunds` 0, `pendingCredit` 0,
  `balanceReservation` 0, `pendingWithdrawals` []. **No transaction list, no
  per-event ledger -- and `lastUpdated` is `null`**, so even balance-differencing
  across reads has no venue timestamp to anchor to.
- **The activity feed** is the transaction feed, and its `POSITION_RESOLUTION`
  rows carry `cashValue: "0"` with an **empty `tradeId`**.
- **`/v1/settlements`, `/v1/prediction-markets/settlements`, `/v1/transfers`**
  appear in this repo only inside `gemini_settlement_probe.py` and
  `gemini_execution/rebate_probe.py`, as *Gemini* candidates explicitly labelled
  speculative. They are not PMUS routes we hold.

**Verdict: no per-settlement cash attribution exists on PMUS today.** One thing
the balance does establish: `unsettledFunds 0` with `currentBalance ==
availableToWithdraw` means nothing is pending -- settlements have cleared into
withdrawable cash in aggregate. So the −$6.0334 is *consistent with* cleared
cash; it simply cannot be decomposed per market. Gap documented; behavioral
analysis above did not wait on it.

## 6. Against the target

Target: **$480/day confirmed realized net.** This sample: **+$0.0043** across
three markets, settlement-implied, with no attributed cashflow. That is not a
fraction of the target; it is noise at a different order of magnitude, and the
gap is not a scaling problem -- a completion mechanism that loses its whole
basis when it fails does not improve by being run more often.
