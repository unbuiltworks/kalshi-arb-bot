# P3 — Polymarket's minimum size, and whether to adapt around it

**Question:** Polymarket declares `orderMinSize: 5`. How much of L5's behaviour
is below it, what is that worth, and does waiting to reach 5 change the trade?

**Answer:** 16.2 % of L5's orders are unplaceable and they carry **1.19 % of the
notional**. Aggregating to reach the minimum costs **62× L5's own measured
edge**. Do not aggregate. Skip them.

All figures are a **placeability census over history**. Nothing here is a fill,
a shadow result, or realized P&L.

---

## The venue term, read rather than remembered

| | |
|---|---|
| `orderMinSize` | **5** shares |
| `orderPriceMinTickSize` | 0.01 |
| `rewardsMinSize` | 50 |
| read | live, both crypto cohorts, 2026-09-15 |

## A fill is not an order

The REPLAY intent sets `incremental_qty` from L5's own **fill**
(`service.py`). But one order filling in pieces produces several small fills,
and counting those as several small intents invents a constraint out of our own
intent construction. Both are reported; the order-level figure is the statement
about L5.

Fills are grouped into an order when they share window, outcome and price and
arrive within 2 s — deliberately conservative, since merging eagerly would
understate the constraint.

| | actions | below 5 | share of actions | share of volume | share of notional |
|---|---|---|---|---|---|
| per fill | 71,982 | 14,110 | 19.6 % | 1.94 % | **1.54 %** |
| per order | 61,931 | 10,051 | 16.2 % | 1.46 % | **1.19 %** |

Median order **18.2** shares, p10 2.2, p90 50. Total notional $728,502; the
sub-minimum orders are $8,691 of it.

**Many actions, almost no money.** Reading 16.2 % of actions as 16.2 % of the
opportunity would overstate this constraint by a factor of 14.

### By cohort

| cohort | orders | below 5 | notional below 5 |
|---|---|---|---|
| BTC15m | 53,701 | 18.1 % | 1.41 % |
| BTC5m | 6,081 | **2.2 %** | 0.18 % |
| ETH15m | 115 | 13.9 % | 2.89 % |
| ETH5m | 2,034 | 8.5 % | 1.41 % |

BTC5m is effectively unaffected. The constraint is a BTC15m phenomenon, and
BTC15m is the cohort where Gemini capacity was already shown to hold.

## Aggregating to the minimum — `L5_ADAPTED`, never FIDELITY

For each sub-minimum order, walk forward through L5's own subsequent actions in
the same market until the cumulative quantity reaches 5.

| | |
|---|---|
| reaches 5 by waiting | 7,150 |
| **never reaches 5** | 2,901 — of which **2,796 because L5 switched sides first** |
| median wait | 3.0 s (p90 13.0 s) |
| outlives the 30 s intent lifetime | 156 |
| price had moved against us by then | 2,950 of 7,150 (**41 %**) |
| median concession | **+$0.01 / share** |

### What it buys, and what it costs

Marginal on both sides — the notional of the sub-minimum action against the
concession on *those same shares*. Crediting one quantity and debiting another
would inflate the ratio in the direction of the conclusion we then draw from
it, so it is not done that way.

| | |
|---|---|
| notional recovered | **$5,859.81** |
| extra cost paid | **$135.21** |
| cost per dollar recovered | **$0.0231** |
| L5's measured trading drag | $0.00037 per dollar |
| ratio | **62.4×** |

(Charging the concession on the whole 5-share order instead gives $305.75, or
141×. The conclusion does not depend on which attribution is used.)

**Aggregation destroys 62 times more value than the edge it is chasing.** A
cent of price concession is not a rounding error in this program — it is the
same cent that made price the third-ranked blocker, at 62.9× L5's edge.

And 27.8 % of sub-minimum actions cannot be aggregated at any price: L5 changed
side before the quantity ever reached 5. Waiting through that would leave us
holding the opposite of what L5 held.

## What this means for the engine

1. **Skip sub-minimum actions on Polymarket. Do not aggregate.** The cost is
   1.19 % of L5's notional foregone. That is the cheapest of the available
   options by a factor of 62.
2. **This is a Polymarket constraint, not a strategy one.** Gemini has no such
   minimum; the same actions remain placeable there, and the dual-venue
   comparison should not treat a PM `NEITHER` from this cause as a capacity
   finding.
3. **The MODEL intent is unplaceable on Polymarket unconditionally.**
   `service.py` hardcodes `incremental_qty = 1` for FIDELITY v0.2 intents —
   below the minimum whatever L5's distribution looks like. Every MODEL intent
   fails the venue's own gate before any economics are reached. This is an
   engine defect, not a finding about L5, and it is a blocker for any
   PM-side MODEL result.
4. **Any future aggregation must be labelled `L5_ADAPTED`** and reported
   separately. It is a different policy from L5's, and this study is the
   evidence that it is a worse one.

## Provenance

- `analysis/l5/min_size/study.py` — the census
- `analysis/l5/min_size/report.json` — full output
- `tests/test_l5_min_size.py` — 19 tests
- source: `analysis/l5/l5_canonical_sequence.csv.gz`, 71,984 rows
