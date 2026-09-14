# The residual / pair-cost frontier

How much temporary directional inventory does an L5 5m cycle need to reach each
level of matched-pair economics, and what does it cost when the repair never
arrives? Read-only. Production untouched, no order placed, no limit changed.

## A units error I made first, and had to fix

My first run capped **raw shares**. L5's median 5m fill is **31.16 shares**, so
a "6" cap blocked the first fill of almost every cycle and reported 2.3%
completion — it measured the units mistake, not an economic frontier.

The live shell's `residual_cap_units = 6` is in **behavioral units**. The
probe's own log pins the conversion: `residual_units -0.3333` alongside
`residual_shares_behavioral -3.45`, so **1 unit = 10.36 shares** and a 6-unit
cap is ~62 shares. Every number below caps in behavioral units.

## The clone

Fail-closed. It follows L5's own fill sequence in order and takes the next fill
only while the resulting absolute residual stays inside the cap; the moment the
next fill would breach it, it stops and holds whatever book it has to the real
settlement. It never takes a fill L5 did not take, never reorders, never
assumes a later fill. The decomposition is exact, not apportioned:

```
matched_pnl  = matched  * (1 - (avg_up + avg_dn))
residual_pnl = residual * [residual side won] - residual * avg(residual side)
matched_pnl + residual_pnl == settlement - cost        (asserted in tests)
```

**Fees are UNKNOWN**: `usdcSize` is the cash that moved and carries no fee
field. Every net here is gross of any unobserved fee, and no fee was invented.

## Result 1 — without a gate, the "profit" is entirely directional

Copying L5's fills including the pairs he bought above par:

| cap (u) | TRAIN matched $ | TEST matched $ | TEST residual $ | sign stable |
|---|---|---|---|---|
| 2 | +12 | -180 | +843 | **flips** |
| 6 | **-2,485** | **-1,563** | +5,275 | yes (both negative) |
| 10 | **-3,061** | -1,322 | +7,694 | yes |
| 12 | -2,801 | -590 | +7,948 | yes |
| 15 | -1,931 | +186 | +7,208 | **flips** |
| 20 | -954 | +1,269 | +6,005 | **flips** |
| unrestricted | -882 | +1,366 | +5,659 | **flips** |

Matched-pair P&L is **negative on TRAIN at every cap**. The test-set positives
above cap 15 are not corroborated. The entire apparent profit is directional
residual — the component with no demonstrated skill.

## Result 2 — with the gate production already runs, it is positive and stable

Adding the completion gate (refuse a fill that would push the effective pair
above the ceiling) changes the answer completely. Net is positive on **train and
test at every cap, with no sign flips**.

One thing must not be oversold: gating at a ceiling <= 1.00 makes the matched
leg non-negative **by construction**, since `matched_pnl = matched * (1 - pair)`.
So only the NET, including the residual the gate strands, can answer anything.

**The curve, pair ceiling 0.96, TEST (trading-only):**

| cap (u) | % pair <=.90 | % completed | matched $ | residual $ | net $ | TRAIN bp | TEST bp | capital $ | worst cycle |
|---|---|---|---|---|---|---|---|---|---|
| 2 | 2.5 | 8.3 | 160 | 409 | 570 | 782 | **691** | 8,252 | -18 |
| 4 | 5.7 | 14.8 | 734 | 1,048 | 1,782 | **914** | **810** | 22,003 | -34 |
| **6** | 10.4 | 22.7 | 2,191 | 1,333 | **3,524** | **812** | **613** | 57,521 | **-47** |
| 8 | 13.8 | 27.7 | 3,719 | 338 | 4,057 | 733 | 455 | 89,063 | -73 |
| 10 | 16.2 | 32.1 | 4,759 | -165 | 4,594 | 700 | 428 | 107,368 | -87 |
| 12 | 17.3 | 35.0 | 5,662 | -103 | 5,559 | 662 | 472 | 117,759 | -104 |
| 15 | 18.2 | 38.3 | 6,802 | -995 | 5,807 | 625 | 444 | 130,927 | -129 |
| 20 | 20.0 | 42.1 | 8,375 | -2,398 | 5,977 | 597 | 409 | 145,985 | -167 |
| 30 | 20.8 | 44.1 | 9,143 | -3,021 | 6,122 | 570 | 395 | 154,803 | -241 |

The shape is unambiguous and train agrees with test on it:

* **Return per dollar peaks early** — TEST bp 691 / **810** / 613 / 455 …, TRAIN
  bp 782 / **914** / 812 / 733 … Both peak at cap 4 and fall away after 6.
* **Net dollars rise with the cap only by deploying more capital** — from
  $57.5k at cap 6 to $154.8k at cap 30 for +74% more net at **-36% efficiency**.
* **Residual P&L turns negative above cap 8** (+1,333 -> -3,021): the extra
  inventory the larger cap buys is stranded more often than it is repaired.
* **Tail risk roughly doubles from 6 to 12** (-47 -> -104) and quintuples by 30.
* Cheap completions do rise (<=.90 from 10.4% to 20.8%) but with falling
  marginal value: 6->8 buys +169 bp, 12->15 +188, 15->20 +113, 20->30 +165.

## Result 3 — rebates change nothing

Confirmed Ledger C only: **$8.63 credited over 686 credited fills of 997**
(rewards PAID $4.53 counted; PENDING $3.65, `estimated_not_a_credit` $8.79 and
the counterparty-side $39.51 all excluded). That is $0.008656 per fill.

Rebates are earned **per fill**, so they are scaled by each cap's own fill count
rather than held constant:

| cap (u) | fills | trading $ | rebate $ | all-in $ | trade bp | all-in bp |
|---|---|---|---|---|---|---|
| 2 | 1,430 | 570 | 12.38 | 582 | 691 | 706 |
| 6 | 4,050 | 3,524 | 35.06 | 3,559 | 613 | 619 |
| 12 | 6,469 | 5,559 | 56.00 | 5,615 | 472 | 477 |
| 30 | 7,915 | 6,122 | 68.51 | 6,190 | 395 | 400 |

**Rebates are ~1% of all-in net at every cap** and move bp by 5-15. They do not
change the ranking of any cap and **no cap becomes attractive because of them**.

*Cross-venue caveat*: the per-fill rate is OUR PMUS rate applied to L5's
Polymarket fill counts. It is a transfer, not a measured L5 credit.

## Result 4 — on PMUS the cap barely binds today

118 deduped sessions over 79 markets. Realised `|residual|` is **p50 0.0 units**,
p75 2.9, max 8.9. **91% of sessions already fit inside cap 6**; 99% inside 8.

Mechanics worth stating: in the live shell `residual_cap_units` gates **EXTEND,
not COMPLETE**, so a session can carry residual above the cap while the cap
correctly blocks further extension. The rows above are exposure measurements,
not cap violations.

*Limitation*: the ledger records only terminal inventory per session, not the
per-fill sequence, so the fail-closed clone cannot be replayed fill-by-fill on
PMUS. Nothing was simulated beyond what the state supports.

## Answer

**A bounded region with positive OOS expectancy exists, and we are already
inside it.** Against the framing that was put to me:

* `6-unit cap -> too restrictive` — **not supported.** Cap 6 sits on the
  efficiency shoulder (TEST 613 bp, TRAIN 812 bp) and binds in only ~9% of PMUS
  sessions.
* `10-12 -> materially more cheap pairs with bounded residual` — more cheap
  pairs (10.4% -> 17.3%) and more net dollars (+58%), but at **lower** return
  per dollar (613 -> 472 bp), 2x the capital and 2x the tail.
* `20+ -> little additional benefit / excessive tail` — **confirmed**: +$418 net
  beyond cap 12 for +$28k capital and a worst cycle going -104 -> -241.

So the frontier does **not** support raising the residual cap. It supports
leaving it where it is. This also corrects my own earlier reading that "the
<=0.90 edge and the residual cap are in direct tension": that tension appeared
only when forcing <=0.90 completions *without* the gate's own stopping rule.
With the gate applied, cap 6 is positive and OOS-stable.

## The observability gap that still blocks the next step

Two holes, both of the same kind — a number the decision turns on is recorded
only sometimes:

1. `econ_pair_cost` and `econ_held_avg_cost` appear on **6 of 25** production
   fills, because they are a by-product of the cross-shadow evaluation.
2. **Per-campaign rebate reconciliation is impossible.** Every incentive record
   is account-level and carries no `conditionId`, `slug` or `order_id`, so all
   $8.63 of maker rebate and $4.53 of paid rewards are **unmatched** to any
   market, order or fill. They can be stated only in aggregate.

`src/l5_decision_observability.py` closes (1): every key always present, each
unavailable component reported as null **with a reason** rather than omitted or
defaulted to a number. It has no order path and changes no trading behaviour.

It is deliberately **NOT wired in**. The integration point is
`src/pmus_spring_l5_session.py`, which the probe watches, so applying it would
redeploy the live campaign — that belongs to the normal clean-diff/preflight
process, not to this analysis.
