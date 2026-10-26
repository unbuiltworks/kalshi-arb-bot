# Strategy decision: profit first

Objective: **>= $5 average confirmed realized net per 15-minute window**,
after all costs and losses. L5 is evidence, not a constraint.

Parity for the prepared changes is verified: baseline 20 failed / 13770
passed, with both changes 20 failed / 13790 passed, **0 regressions**. The
+20 is 12 reporting-fix tests and 8 floor-fix tests.

## Ranked candidates, on existing evidence only

### 1. CONTINUOUS SEQUENTIAL PAIRING -- RECOMMENDED

The only candidate with venue-confirmed realized profit.

| | |
|---|---|
| evidence | 6 completed pairs, venue-settled, pair costs 0.82 / 0.85 / 0.93 / 0.94 / 0.95 / 0.96, **every one positive**, `commission 0.0000` on all 16 fills |
| net per window, measured | **-$0.17** at R=2 (observed -0.1506, -0.1766) |
| net per window, modelled | **+$0.018** at R=4 (break-even), **+$0.113** at R=5, +$0.588 at R=10 |
| fill probability | measured dose-response, 0% -> 84.6% in continuation; our regime 15.9-47% |
| capital | $2.99 per round measured -> **$15 for R=5, inside the $20 ceiling**; $107-138 for $5/window |
| downside | a strand realizes **-46.8%** of cost (n=378); bounded by the 3-contract residual cap, $5 daily and $20 cumulative halts |
| scalability | cash-linear; L5 moves 2,363 gross contracts/window, ~20x our $5 target, so venue capacity is not the limit |

The edge is the pair spread and nothing else. It is executable, already
executed, and already reconciled against venue settlement.

### 2. MAKER LIQUIDITY PROVISION -- ADDITIVE ONLY, DO NOT COUNT IT

| | |
|---|---|
| observed | estimate $0.0223 + $0.0160 + $0.0129 = **$0.0512** over 3 windows (~$0.017/window) |
| **credited** | **$0.00. Not one cent, ever.** `eligibility: UNVERIFIED_NO_VENUE_CREDIT_RECORD` |
| best case at scale | $0.87/window at a 10-contract clip and 28 prints |
| blocker | the rebate is banker-rounded to the cent **per print**, so at 1-3 contract clips most credits round to $0.00 |

It cannot reach $5 standalone -- that would need ~160 prints per window at 10
contracts. Treat any credit as a windfall, never as part of the plan.

### 3. SELECTIVE DIRECTIONAL TRADING -- REJECT

Tested on L5's whole book over the 103 outcome-known windows:

    all L5 fills      n=17,679   cost $165,648   realized -$503   -0.3%
      own 0.0-0.2                                                -10.0%
      own 0.2-0.4                                                 +2.1%
      own 0.4-0.6                                                 -2.2%
      own 0.6-0.8                                                 +1.8%
      own 0.8-1.0                                                 -0.5%
      elapsed 0-120s                                              -1.5%
      elapsed 120-360s                                            +3.2%
      elapsed 360-600s                                            -1.7%
      elapsed 600-900s                                            -0.9%

**-0.3% on $165,648 is zero.** No bucket holds a consistent sign. The prices
are fair and **L5 itself has no directional skill.** There is no executable
directional edge to harvest, from L5's behaviour or from ours.

This also resolves an apparent contradiction in my own earlier reporting:
unpaired legs realize -46.8% while the whole book realizes -0.3%. A strand is
not a losing bet -- it is the losing **half** of a fair bet, and the paired
half captures the spread. All the edge is in completing.

### 4. COMBINATIONS

A + rebates is A's economics plus an unverified <= $0.87. Plan on A.
A + directional adds variance and no expected return. Rejected.

## The one recommendation

**Raise rounds per window. Change nothing else.**

A window ends with at most one unfinished round, so pairs grow with R while
the strand stays near one. Raising the clip instead scales pair and strand
together and buys no dilution -- which is why frequency, not size, is first.

Measured per-unit: **+$0.095 per completed pair**, **-$0.267 per strand**.

| R | pairs | net $/window |
|--:|------:|-------------:|
| 2 | 1 | -0.172  (observed -0.1506, -0.1766) |
| **4** | 3 | **+0.018** break-even |
| **5** | 4 | **+0.113** |
| 10 | 9 | +0.588 |

## Next live experiment

**Bounded frequency test. Milestone: repeatable positive total net per
window, including stranded inventory. Not $5 yet.**

- 8 windows, one market, `cpc-btc-updown-15m-`
- R = 5 sequential rounds, clip unchanged (3-contract cap stays)
- maker-only, one outstanding order plus the existing pair slot
- stop and report after 8 windows; no automatic extension

**Prediction:** net moves from **-$0.17** to about **+$0.11** per window;
pairs rise from 1 to ~4; strands stay near 1.

**Falsification, stated in advance:** if the strand count scales with R
instead of staying near 1, the model is wrong and the experiment stops. That
assumption is validated on only two windows and is the load-bearing one.

## Exact authorization required

1. **Deploy two prepared, parity-verified commits** -- `cb22bb47` (first-leg
   floor keyed on the imbalance) and `c16ee7ec` (decision-role reporting
   field, plus the Dockerfile COPY without which the entrypoint cannot
   import).
2. **Two variables, both inside existing hard ceilings:**

       L5_MAX_CASH_DEPLOYED_USD   6.00 -> 15.00
       L5_TS_ROUND_ALLOCATIONS       2 -> 5

3. **A fresh 8-window campaign approval** with a new nonce. The previous
   campaign is exhausted at 8/8 and must not be reset or extended.
4. **Deployment timing:** the rollout must land in the T+540 to T+600 band
   per `analysis/l5/DEPLOYMENT_TIMING_SAFEGUARD.md`. Three rollouts cost two
   claimed windows this session; `scripts/l5_rollout_window_check.py` gates
   it.

**Explicitly NOT requested now:** any capital above the $20 hard ceiling,
taker execution, a larger clip, a looser pair cap, new services, or changes
to any loss halt, cutoff or runway gate.

**On the $150-300 headroom the owner is open to:** that is what $5/window
needs ($107-138 of deployed cash, nearly invariant to clip). I am not asking
for it yet. Ask again only when R=5 has produced **repeatable positive
settled net** across 8 windows -- verified settled results first, capital
second.

## What I am not claiming

- 6 completed pairs, 6 strands, 10 settled windows on our side.
- "Pairs ~ R-1, strands ~ 1" rests on 2 windows.
- Complement executability in the L5 replay is an upper bound: our order
  would queue behind L5's, and `our_position_within_level` is UNKNOWN on
  every order we have placed.
- Outcomes for L5 windows are recovered from a price-collapse rule on 103 of
  527 windows, not read from a settlement feed.
- No rebate has ever been credited, so none is in any figure above.
