# Campaign 8 — unchanged strategy, and why the replay's winner does not apply

## Track 1: ready-to-authorize campaign

Strategy, limits and execution path **unchanged** from Campaign 7
(SHA 202456d3). Both candidate flags stay off: `L5_TS_TWO_ORDER_PAIR` and
`L5_DEFENSIVE_COMPLETION`.

| field | value |
|---|---|
| series / windows | BTC15m, 8 consecutive |
| max exposure / cash envelope | $20 / $12 |
| daily / cumulative loss halt | $5 / $20 |
| residual budget | $3 |
| resting orders | 1 |
| execution | maker-only, post-only |
| pair ceiling / completion ceiling | 0.96 / 0.95 unchanged |
| OPEN / COMPLETE / REPLACE | 40 / 40 / 64 unchanged |
| **total-submission backstop** | **40 -> 72** |

### Why raising the backstop cannot widen the risk envelope

Campaign 7 ended at **36 of 40** with class budgets barely touched (OPEN 15,
COMPLETE 14, REPLACE 7). The backstop, not the strategy, would have stopped
the next window.

It is safe to raise because it is not a dollar control:

1. Its enforced purpose is completion-capacity reservation -- it refuses an
   order when "the flat backstop would not leave room for this order AND the
   obligation it creates". It reserves the ability to CLOSE, it does not
   bound exposure.
2. Every dollar bound is applied independently per order and each prices a
   resting order **as though it fills in full**: max_total_exposure_usd $20,
   max_cash_deployed_usd $12, max_usd_per_market $20, the $3 residual cap,
   and both loss halts. None of them scales with submission count.
3. 72 stays below the 144 sum of the three class budgets, preserving the
   design property that no two classes can both run to their ceiling.

Operational note: `max_total_submissions` is NOT in `SCOPE_FIELDS`, so it
cannot break a scope digest mid-campaign. It is graded by the runtime-config
verifier against the deployed `L5_LIVE_AUTH_MAX_SUBMISSIONS`, so the
variable must be set to 72 BEFORE the document is installed, per the
ordering rule that deadlocked Campaign 7.

Reporting: settled net, pairs and pair costs, defensive losses, stranded
inventory, adverse selection, refusals, and -- added -- the **repair rate**
defined below. No claim that any window count proves profitability.

## Track 2: the replay's winning treatment is a no-op for us

Run out of sample on L5's own fills (`scripts/l5_treatment_oos.py`, Sep 23
fit, Sep 24-25 held out, 521 market-days):

| policy | matched | match_pnl | residual_pnl | TOTAL | vs NATIVE |
|---|---|---|---|---|---|
| NATIVE (L5's own sequence) | 154,685 | +657.8 | **-4,587.6** | **-2,342.4** | - |
| RESIDUAL_CAP_k1 | 86,197 | +505.8 | -492.1 | +812.9 | +3,155.3 |
| RESIDUAL_AGE_120s | 79,838 | +617.3 | -358.8 | **+1,013.3** | +3,355.8 |
| COMPLEMENT_REPAIR | 65,742 | **+703.1** | -336.9 | +985.4 | +3,327.9 |

**L5's own action sequence, rated at our fee schedule, loses $2,342 over two
held-out days, and the entire loss is residual** -- matched P&L is positive.
Suppressing residual accumulation flips it positive. The mechanism is
confirmed at scale.

**But both winners only ever block a fill that DEEPENS an existing
imbalance, and Campaign 7 had none.** All 18 fills were either a new
position from flat or a repair:

    fills that deepen an existing imbalance: 0 of 18

So `COMPLEMENT_REPAIR` and `RESIDUAL_AGE` would have changed nothing. They
correct L5's extend-heavy behaviour; our engine already behaves as they
prescribe, because the $3 residual cap and the 0.96 ceiling already suppress
exactly what they suppress. **The headline +$3,355 does not transfer.**

### Our actual failure mode, measured

    9 positions opened from flat -> 5 repaired, 4 stranded = 56% repair rate

The four strandings opened at T+321, T+91, T+156 and T+496; two had over
500 s of runway and still never repaired, so runway is not the
discriminator. The cause is the complement becoming unaffordable after our
leg fills -- all four ceiling episodes priced 1.02-1.30, correctly refused.

**Our residual is a completion-fill problem, not an accumulation problem.**
No treatment in the existing replay addresses it, and I am not proposing one
without measured expectancy. The metric to move is the repair rate; Campaign
8 will record it so there is a baseline to improve against.

### Caveats on the replay, stated

Fill-for-fill counterfactual: a taken action fills as recorded and a
suppressed one does not happen, so suppression is free and taking is
guaranteed -- our orders may not fill. Incentives are MODELLED for the
treatments and real only for NATIVE; a modelled rebate is never realized.
