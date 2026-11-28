# Bounded inventory-aware completion — evaluation

**Recommendation: do NOT deploy for profit. It is approximately P&L-neutral
and buys variance reduction at roughly fair value.**

Nothing deployed, no order submitted, no service or branch created. The
mechanism already exists — `src/l5_taker_completion_policy.py` — bounded and
disarmed (`taker_crossing_reachable: false`). This evaluates it; it builds
nothing.

---

## 1. The structural result, before any data

For a held leg at own cost `h`, the complement's **taker** own price on a tight
book is `(1 − h) + spread`. So:

```
pair_taker = h + (1 − h) + spread = 1 + spread
```

**A crossed completion produces a pair above 1.00 by exactly the spread, in
every state.** A pair above 1.00 is a *certain* loss of `(pair − 1)` per
contract. Add the taker fee and the cost is **`spread + fee`, always.**

| spread | crossed pair | certain loss/contract |
|---|---|---|
| 1 tick | 1.0100 | 0.0100 |
| 2 ticks | 1.0200 | 0.0200 |

This is not a calibration result; it is arithmetic. **Aggressive completion
cannot create edge. It can only bound a tail, at a price.** The engine's own
label says so: *"a defensive exit bounds a tail, it does not create edge: at
the touch it is expected-value neutral before fees and negative by exactly the
fee."*

Confirmed in our own recorded books — every crossable pair was **above 1.00**:

| sample | held `h` | cross px | pair @touch | pair @depth-2 | pair taker |
|---|---|---|---|---|---|
| 14:48:39, book 0.20/0.21 | 0.49 | 0.80 | 1.2800 | 1.2600 | **1.2900** |
| 14:49:07, book 0.40/0.41 | 0.49 | 0.60 | 1.0800 | 1.0600 | **1.0900** |

## 2. Cross versus hold, with the unit ambiguity resolved

The comparison turns on what a shadow "residual unit" is, so I resolved it
rather than choosing:

`pair_acquisition_cost_per_matched 0.7297` implies an edge of **0.2703 per
unit**. The best possible edge per *contract* on a 1-tick book at depth 2 is
**0.0500**. **So a unit is at least 5.4 contracts**, or that figure is
impossible. The owner calibration elsewhere is 1 unit = 10.36 shares.

| | cost per contract |
|---|---|
| **hold**, at ≥5.4 contracts/unit (pessimistic bound) | **0.042823** |
| **hold**, at 10.36 contracts/unit (owner calibration) | **0.022346** |
| **cross**, 1 tick + observed taker fee | **0.022800** — certain |

**The ranges overlap.** Holding costs 0.0224–0.0429/contract; crossing costs
0.0228/contract with certainty. **Approximately neutral.**

On our seven residuals specifically: holding drew **−$0.337110**; crossing all
seven at 1 tick + fee would have cost **−$0.328320 with certainty**, or
**−$0.472320** at 2 ticks. The 1-tick figure is within $0.009 of the realized
draw — which is the point: it is the same money, with the variance removed.

**Higher turnover does not create profitability here, and I am not claiming it
does.**

## 3. Requirements 1–6, assessed against what exists

| req | status |
|---|---|
| 1. executable depth, not displayed best | **already implemented** — `depth_haircut 0.25` believes only 25% of each displayed rung; `taker_walk` computes a VWAP across rungs with `insufficient_displayed_depth` |
| 2. all-in cost incl. fees and slippage | **already implemented** — `taker_fee_usd` per cross, VWAP across levels, `cash_cost_usd` |
| 3. immediate completion vs holding, calibrated | **already implemented** — `worst_before` / `worst_after` / `worst_improvement_usd`, and the gate refuses unless **both** settlement outcomes are positive (`WORST_OUTCOME_NOT_POSITIVE`) |
| 4. marketable limits only when EV-positive | **the gate is stricter than EV-positive** — it demands both-outcomes-positive, which §1 shows is rarely satisfiable |
| 5. hard limits preserved | **unchanged** — $20 exposure, $1.50 deficit, $5 daily halt, 3-contract residual, watchdog, exact-ID cancellation |
| 6. adverse selection, partial fills, latency | **partially** — `adverse_rate 0.89` and `swept_share 0.98` exist from 3,675 shadow quotes; partial fills are modelled; **latency on a crossing order is not measured**, because no cross has ever been submitted |

## 4. Worst case, at existing limits

| bound | value |
|---|---|
| max outlay per cross | **$1.50** |
| max outlay per window | $3.00 |
| **binding per-window worst case** | **−$1.50** — the per-market deficit cap binds before the window outlay cap |
| daily | **−$5.00**, the existing settled-loss halt |
| residual cap | 3 contracts, unchanged |
| margin floor per cross | $0.25 |
| maker-first patience | 90 s before any cross is considered |

**Worst case is unchanged from today's policy**, because the deficit and daily
caps bind first. The policy adds no new worst case; it changes the *path*.

## 5. Minimum capital, and cash versus borrowing

**Minimum capital for a validated improvement: not applicable — there is no
validated improvement.** The policy is neutral, so no capital is justified by
expectancy.

If deployed purely as variance control it needs **no additional capital**: the
existing $12 cash envelope inside the $20 exposure cap is sufficient, since
crossing *reduces* open exposure.

**Available cash versus borrowing capacity — stated as unevidenced.** We read
`buying_power` from `/v1/account/balances` via `PmusTransport.buying_power`.
**I have found no evidence of any margin or borrowing facility on this venue
for this account, and I will not assume one.** Prediction-market accounts are
normally fully collateralised, so `buying_power` should be taken as *cash*, not
as authorized leverage, until a venue document or credit record says otherwise.
No figure here depends on borrowing.

## 6. The precise policy, if you want it anyway

**It is the existing mechanism, switched on, with one variable:**
`L5_TS_COMPLETION_CROSS=YES` on `l5-pmus-live`. No code change.

`TCP.authorize` then gates every cross on: flag on, owner-authorized, halt
clear and known, the underlying decision ok, margin floor $0.25,
per-cross outlay ≤$1.50, window outlay ≤$3.00, ≥90 s of maker patience first,
depth haircut 0.25, and **both settlement outcomes positive after the cross**.

**This breaks maker-only authorization and therefore needs fresh owner
approval.** It is also the switch whose state I verified as `false` before the
campaign precisely so maker-only could be asserted.

## 7. Evidence needed before deployment

1. **Crossing latency and realized slippage.** Never measured — no cross has
   been submitted. The `taker_walk` VWAP is computed from displayed depth with
   a 0.25 haircut; whether a real marketable limit fills at that VWAP is
   unknown. **This is the only genuinely missing execution datum.**
2. **How often the both-outcomes-positive gate is satisfiable.** §1 says a
   crossed pair is `1 + spread`, so the gate should almost never pass. Before
   deploying I would count its pass rate across the existing shadow records —
   read-only, no new infrastructure. **If it passes near-never, the policy is
   inert and switching it on changes nothing.** That is the cheapest next step
   and I can do it on request.
3. **A decision on variance versus expectancy.** The policy is neutral on
   money. Paying ~0.023/contract to remove a ±$1 coin flip is a risk-preference
   choice, and at a $5 daily halt the coin flip is already bounded.

## 8. Recommendation

**Do not deploy.** It does not improve expected net P&L — the two costs
overlap — it requires surrendering maker-only, and its one untested component
(crossing latency and slippage) can only be measured by crossing.

**The cheap, honest next step is (7.2):** count the gate's pass rate in the
existing records. If it is near zero, this question closes for free.

No capital increase, no new service, no deployment, no order submitted.
